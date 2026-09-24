from __future__ import annotations

import hashlib
import logging
import uuid
from collections import defaultdict
from typing import Any

from risk_audit.checks.registry import CapabilityRegistry, CheckContext
from risk_audit.checks.reconciliation import reconcile_deleted_carriers
from risk_audit.configuration.resolver import resolve_rules, selector_matches
from risk_audit.models import CheckStatus, FileRecord, Finding, Record
from risk_audit.opinion_text import advice, advice_v2
from risk_audit.util import sha256_json
from risk_audit.issue_routing import route_issue


class SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


def _scope_records(rule: dict[str, Any], records: list[Record]) -> list[Record]:
    s = rule["scope"]
    out = []
    for r in records:
        if r.record_type != s["record_type"]: continue
        if not selector_matches(s["business_codes"], r.business_code): continue
        if not selector_matches(s["entity_codes"], r.entity_code): continue
        if "variants" in s and not selector_matches(s["variants"], r.variant_id): continue
        out.append(r)
    return out


def run_engine(pack: dict[str, Any], registry: CapabilityRegistry, files: list[FileRecord], entities: dict[str, Any], baselines: dict, run_id: str | None = None, limitations: list[dict[str, Any]] | None = None) -> tuple[list[Finding], list[CheckStatus]]:
    """运行主体规则检查。

    pack 为单元规则包，可含 _submitted_entity_codes 扫描报送主体名单，registry 为检查能力注册表，
    files 为当前审核单元材料，entities 为主体名册，
    baselines 为冻结基准，run_id 为运行标识，limitations 为接收检查限制的列表。
    返回对外意见与检查状态；规则异常记入失败状态并输出异常日志。
    """
    run_id = run_id or str(uuid.uuid4())
    all_records = [r for f in files for s in f.sheets for r in s.records]
    scope = pack["submission_scope"]
    scope_entities = list(scope["entity_codes"])
    scope_businesses = list(scope["businesses"])
    entity_names = {k: v.name for k, v in entities.items()}
    # 报送名称只用于结果展示，不加入名册身份及适用层级判定上下文。
    entity_names.update({file.entity_code: file.preservation['entity_identity']['entity_name']
                         for file in files if file.entity_code not in entities and 'entity_identity' in file.preservation})
    resources = {"carrier_aliases": pack.get("carrier_aliases", {}), "roles_and_positions": pack.get("roles_and_positions", {}), "entity_aliases": pack.get("entity_aliases", {}), "field_aliases": pack.get("field_aliases", {}), "terminology": pack.get("terminology", {})}
    resources.update({key:pack.get(key,{}) for key in ('semantic_lexicon','semantic_config','_semantic_runtime','parser_policy','responsibility_applicability')})
    if '_submitted_entity_codes' in pack:
        # 只传递主体报送元数据，不引入其他业务的文件内容或明细记录。
        resources['submitted_entity_codes'] = pack['_submitted_entity_codes']
    from risk_audit.applicability import attach_context
    attach_context(resources,files,entities)
    findings: list[Finding] = []; statuses: list[CheckStatus] = []
    rendering_templates = {}
    seen = set()
    for rule in pack["rules"]:
        for check in rule["checks"]:
            can_enable = any(o.get("path") == rule["rule_id"] + ".enabled" and o.get("value") is True for o in pack.get("overlays", []))
            if not rule["enabled"] and not can_enable:
                statuses.append(CheckStatus(rule["rule_id"], check["check_id"], "disabled", rule.get("disabled_reason", "配置停用")))
                continue
            cap = registry.get(check["operator"], check["operator_version"])
            if cap is None or not cap.available:
                statuses.append(CheckStatus(rule["rule_id"], check["check_id"], "failed", "能力不存在或未实现")); continue
            issues = []; scoped_records = []; package_in_scope = True
            try:
                if rule["scope"]["record_type"] == "package":
                    effective_entities = [x for x in scope_entities if selector_matches(rule["scope"]["entity_codes"], x)]
                    effective_businesses = [x for x in scope_businesses if selector_matches(rule["scope"]["business_codes"], x["business_code"]) and ("variants" not in rule["scope"] or selector_matches(rule["scope"]["variants"], x.get("variant_id", "default")))]
                    if effective_entities and effective_businesses:
                        ctx = CheckContext([], files, all_records, entities, baselines, resources, effective_entities, effective_businesses)
                        issues = cap.runner(ctx, check["params"])
                    else:
                        issues = []
                        package_in_scope = False
                    for issue in issues: issue["_check"] = check; issue["_rule"] = rule
                else:
                    groups = defaultdict(list)
                    for record in all_records:
                        groups[(record.entity_code, record.business_id or record.business_code,
                                record.business_code, record.variant_id)].append(record)
                    for (ec, business_id, bc, variant), group_records in groups.items():
                        effective = next(x for x in resolve_rules(pack, ec, bc, variant) if x["rule_id"] == rule["rule_id"])
                        effective_check = next(x for x in effective["checks"] if x["check_id"] == check["check_id"])
                        if not effective["enabled"]: continue
                        selected = _scope_records(effective, group_records)
                        scoped_records.extend(selected)
                        ctx = CheckContext(selected, files, all_records, entities, baselines, resources, scope_entities, scope_businesses)
                        current_issues = cap.runner(ctx, effective_check["params"])
                        for issue in current_issues: issue["_check"] = effective_check; issue["_rule"] = effective
                        issues.extend(current_issues)
            except Exception as exc:
                # 检查异常由状态记录承接，同时保留堆栈，避免仅在最终摘要中发现失败。
                logging.getLogger(__name__).exception('规则检查失败：主体=%s，规则=%s，检查=%s',
                                                     scope_entities, rule['rule_id'], check['check_id'])
                statuses.append(CheckStatus(rule["rule_id"], check["check_id"], "failed", str(exc), len(scoped_records), 0)); continue
            emitted = 0; incomplete = 0
            for issue in issues:
                rendering_check = issue.get("_check", check)
                rendering_rule = issue.get("_rule", rule)
                issue = route_issue(issue, rendering_check, pack.get('result_policy'))
                kind = issue["kind"]
                if kind == "limitation":
                    incomplete += 1
                    record = issue.get("record")
                    detail = {"rule_id": rendering_rule["rule_id"], "check_id": rendering_check["check_id"],
                              "display_code": rendering_rule["display_code"], "evidence": issue["evidence"],
                              **{key: getattr(record, key, "") for key in ("entity_code", "business_id", "business_code", "variant_id", "file_path", "sheet", "row")}}
                    if limitations is not None: limitations.append(detail)
                    continue
                if kind == "review" and rendering_check["on_unavailable"] == "skip": continue
                if kind == "review" and rendering_check["on_unavailable"] == "fail":
                    statuses.append(CheckStatus(rule["rule_id"], check["check_id"], "failed", issue["evidence"].get("unavailable_reason", "前提不可用"), len(scoped_records), 0)); continue
                record = issue.get("record")
                ev = issue["evidence"]
                ec = record.entity_code if record else ev.get("entity_code", scope_entities[0] if len(scope_entities) == 1 else "")
                business_id = record.business_id if record else ev.get("business_id", "")
                bc = record.business_code if record else ev.get("business_code", "")
                variant = record.variant_id if record else ev.get("variant_id", "default")
                file_path = record.file_path if record else ev.get("file_path", "")
                sheet = record.sheet if record else ev.get("sheet", "")
                row = record.row if record else None
                values = SafeFormat(ev)
                values["advice"] = advice(rendering_check["check_id"], kind, ev)
                if '{advice_v2}' in rendering_check['message'][kind]:
                    values['advice_v2'] = advice_v2(rendering_check['check_id'], kind, ev)
                message = rendering_check["message"][kind].format_map(values)
                identity = record.record_id if record else f"{ec}|{business_id or bc}|{variant}|{file_path}|{sheet}|material"
                issue_identity = {k: ev[k] for k in ("field", "missing_material", "missing_sheet", "reference", "measure_id", "reason", "role", "issue_type") if k in ev}
                if ev.get('missing_fields'):
                    # 不同缺列集合分别保留，不能因同一表同一检查而隐藏另一组必需字段。
                    issue_identity['missing_fields'] = sorted(ev['missing_fields'])
                if check['operator'] == 'set_subset' and ev.get('source_field'):
                    issue_identity['source_field'] = ev['source_field']
                # One row can participate in multiple distinct counterpart pairs.
                # Keep the pair identity so deduplication cannot hide a person or row.
                if ev.get('issue_type') in {'same_duty_person_overlap', 'separation_duty_missing'}:
                    issue_identity.update({k: ev[k] for k in ('related_file', 'related_sheet', 'related_row', 'conflict_people') if k in ev})
                key_seed = f"{rule['rule_id']}|{check['check_id']}|{identity}|{kind}|{sha256_json(issue_identity)}"
                finding_key = hashlib.sha256(key_seed.encode()).hexdigest()
                if finding_key in seen: continue
                rendering_templates[finding_key] = rendering_check["message"][kind]
                seen.add(finding_key); emitted += 1
                finding_id = str(uuid.uuid5(uuid.UUID(run_id), finding_key)) if _is_uuid(run_id) else hashlib.sha256(f"{run_id}|{finding_key}".encode()).hexdigest()
                # 明确整表缺列由发布口径标记资料级，避免把问题归责于首条措施。
                findings.append(Finding(finding_key, finding_id, rendering_rule["rule_id"], rendering_check["check_id"], rendering_rule["display_code"], rendering_rule["revision"], kind, ec, entity_names.get(ec, ec), bc, variant, file_path, sheet, row, message, ev, issue.get('location_policy', rendering_check["location_policy"]), business_id=business_id or bc))
            status = "scope_excluded" if (rule["scope"]["record_type"] == "package" and not package_in_scope) or (rule["scope"]["record_type"] != "package" and not scoped_records) else "executed"
            reason = "配置范围内无记录" if status == "scope_excluded" else ""
            if incomplete:
                status = "partial"
                reason = f"有{incomplete}条核对限制，未作为材料问题写入表格；详见limitations.json"
            statuses.append(CheckStatus(rule["rule_id"], check["check_id"], status, reason, len(scoped_records), emitted, incomplete))
    def render_reconciled(finding):
        values = SafeFormat(finding.evidence)
        values["advice"] = advice(finding.check_id, finding.severity, finding.evidence)
        values["advice_v2"] = advice_v2(finding.check_id, finding.severity, finding.evidence)
        return rendering_templates[finding.finding_key].format_map(values)
    findings = reconcile_deleted_carriers(findings, statuses, render_reconciled)
    findings.sort(key=lambda x: (x.entity_code, x.business_id or x.business_code, x.variant_id,
                                 x.file_path, x.sheet, x.row or 0, x.display_code, x.check_id))
    return findings, statuses


def _is_uuid(value: str) -> bool:
    try: uuid.UUID(value); return True
    except ValueError: return False
