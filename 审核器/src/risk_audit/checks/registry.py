from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable

from risk_audit.checks.carriers import parse_carrier_references, split_carrier_list, terminology_carriers
from risk_audit.checks.deleted_content import deleted_text_reappears, deleted_text_reappears_v2
from risk_audit.checks.coverage import responsibility_coverage, responsibility_coverage_v2, responsibility_coverage_v3
from risk_audit.checks.responsibility_names import responsibility_unit_specific
from risk_audit.checks.applicability import applicability_values
from risk_audit.checks.duty_details import field_constraints, roles_same_duty, roles_same_duty_v2, responsibility_phrase, responsibility_phrase_v2, responsibility_phrase_v3
from risk_audit.checks.dual_carriers import set_subset_v2, set_subset_v3
from risk_audit.checks.schema_v2 import schema_contains_v2
from risk_audit.models import FileRecord, Record
from risk_audit.util import measure_id_key, natural_key, norm_text


@dataclass(frozen=True)
class Capability:
    name: str
    version: int
    record_types: frozenset[str]
    required_params: frozenset[str]
    optional_params: frozenset[str]
    field_params: frozenset[str]
    evidence_variables: frozenset[str]
    location_policies: frozenset[str]
    runner: Callable[["CheckContext", dict[str, Any]], list[dict[str, Any]]]
    available: bool = True


@dataclass
class CheckContext:
    records: list[Record]
    files: list[FileRecord]
    all_records: list[Record]
    entities: dict[str, Any]
    baselines: dict[tuple[str, str], dict[str, Any]]
    resources: dict[str, Any]
    scope_entity_codes: list[str]
    scope_businesses: list[dict[str, str]]


class CapabilityRegistry:
    def __init__(self) -> None:
        self._items: dict[tuple[str, int], Capability] = {}

    def register(self, capability: Capability) -> None:
        key = (capability.name, capability.version)
        if key in self._items:
            raise ValueError(f"duplicate capability: {key}")
        self._items[key] = capability

    def get(self, name: str, version: int) -> Capability | None:
        return self._items.get((name, version))

    def versions(self) -> list[dict[str, Any]]:
        return [
            {"name": c.name, "version": c.version, "available": c.available}
            for c in sorted(self._items.values(), key=lambda x: (x.name, x.version))
        ]


def _issue(record: Record | None, kind: str, **evidence: Any) -> dict[str, Any]:
    return {"record": record, "kind": kind, "evidence": evidence}


def required_fields(ctx: CheckContext, p: dict[str, Any]) -> list[dict[str, Any]]:
    fields = p["fields"]
    placeholders = {norm_text(x) for x in p.get("placeholders", ["无", "待定", "-"])}
    out = []
    condition = p.get("when", {})
    for r in ctx.records:
        if condition:
            cv = norm_text(r.value(condition["field"]))
            if cv not in {norm_text(x) for x in condition.get("in", [])}:
                continue
        for fid in fields:
            fv = r.fields.get(fid)
            if fv is None:
                out.append(_issue(r, "review", unavailable_reason=f"模板未识别字段“{fid}”", field=fid))
            elif fv.state == "formula_no_cache":
                out.append(_issue(r, "review", unavailable_reason=f"字段“{fid}”为公式但没有可用缓存值", field=fid))
            elif not norm_text(fv.current):
                out.append(_issue(r, "violation", field=fid, field_label=p.get("labels", {}).get(fid, fid), reason="未填写"))
            elif norm_text(fv.current) in placeholders:
                out.append(_issue(r, "violation", field=fid, field_label=p.get("labels", {}).get(fid, fid), reason="内容不具体"))
    return out


def schema_contains(ctx: CheckContext, p: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    required = set(p.get("required_fields", []))
    for f in ctx.files:
        for s in f.sheets:
            if s.sheet_type not in p.get("sheet_types", []):
                continue
            missing = sorted(required - set(s.columns))
            if p.get("compare_baseline") and f.business_code:
                baseline = ctx.baselines.get((f.business_id or f.business_code, f.variant_id), {})
                baseline_fields = set(baseline.get("fields", []))
                ignored = set(p.get("ignore_fields", []))
                missing = sorted(set(missing) | (baseline_fields - set(s.columns) - ignored))
                expected_headers = baseline.get("business_header_paths", [])
                actual_by_coordinate = {x["coordinate"]: x for x in s.business_header_paths}
                missing_headers = []
                for expected in expected_headers:
                    if p.get("accept_split_applicability"):
                        aliases = ctx.resources.get("field_aliases", {}).get("matrix", {})
                        expected_leaf = norm_text(expected["path"][-1]["name"])
                        if any(expected_leaf in {norm_text(x) for x in aliases.get(fid, [])} for fid in ignored):
                            continue  # Tail/value checks cover an equivalent combined or split field.
                    actual = actual_by_coordinate.get(expected["coordinate"])
                    expected_names = [norm_text(x["name"]) for x in expected["path"]]
                    actual_names = [norm_text(x["name"]) for x in actual["path"]] if actual else []
                    if actual_names != expected_names:
                        missing_headers.append("/".join(x["name"] for x in expected["path"]) + f"（{expected['coordinate']}）")
                if missing_headers:
                    out.append(_issue(s.records[0] if s.records else None, "review", unavailable_reason=f"工作表“{s.title}”与冻结基准相比缺少或改变业务表头：{'、'.join(missing_headers)}", missing_fields=missing_headers, file_path=str(f.relative_path), sheet=s.title))
            if p.get("check_applicability_tail"):
                field_aliases = ctx.resources.get("field_aliases", {}).get("matrix", {})
                combined_name = norm_text("是否适用及原因")
                applicability_names = {norm_text(x) for x in field_aliases.get("applicability", [])} - {combined_name}
                reason_names = {norm_text(x) for x in field_aliases.get("applicability_reason", [])}
                leaves = [(x["column"], norm_text(x["path"][-1]["name"]), x["path"][-1]["name"]) for x in s.business_header_paths]
                last_two = leaves[-2:]
                if leaves and leaves[-1][1] == combined_name:
                    pass
                elif len(last_two) == 2 and last_two[0][1] in applicability_names and last_two[1][1] in reason_names:
                    if not p.get("accept_split_applicability"):
                        names = f"{last_two[0][2]}、{last_two[1][2]}"
                        out.append(_issue(s.records[0] if s.records else None, "review", unavailable_reason=f"最后实际业务字段采用“{names}”拆分形式，拆分符合性尚未确认", missing_fields=[], file_path=str(f.relative_path), sheet=s.title))
                else:
                    present_app = next((x[2] for x in leaves if x[1] in applicability_names), None)
                    present_reason = next((x[2] for x in leaves if x[1] in reason_names), None)
                    absent = []
                    if not present_app: absent.append("是否适用")
                    if not present_reason: absent.append("原因/备注")
                    detail = "、".join(absent) if absent else "是否适用及原因（联合字段须位于最后，拆分形式须为最后两列）"
                    out.append(_issue(s.records[0] if s.records else None, "review", unavailable_reason=f"工作表“{s.title}”缺少或未正确放置字段：{detail}", missing_fields=absent, file_path=str(f.relative_path), sheet=s.title))
            if missing:
                out.append(_issue(s.records[0] if s.records else None, "review", unavailable_reason=f"工作表“{s.title}”缺少或未识别逻辑字段：{','.join(missing)}", missing_fields=missing, file_path=str(f.relative_path), sheet=s.title))
    return out


def forbidden_reference(ctx: CheckContext, p: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    terms = [norm_text(x) for x in p["terms"]]
    fields = p["fields"]
    target_measure = norm_text(p.get("measure_contains", ""))
    negations = [norm_text(x) for x in p.get("negation_markers", ["删除", "不使用", "停用", "示例"])]
    for r in ctx.records:
        if target_measure and target_measure not in norm_text(r.value("measure_id") + r.value("key_control_point")):
            continue
        text = " ".join(r.value(fid) for fid in fields)
        ntext = norm_text(text)
        if any(term in ntext for term in terms):
            if any(n in ntext for n in negations):
                out.append(_issue(r, "review", unavailable_reason="禁用平台出现在否定、删除或示例语境，需确认是否仍为有效内容", matched_text=text[:200]))
            else:
                out.append(_issue(r, "violation", matched_text=text[:200], forbidden_terms=[t for t in p["terms"] if norm_text(t) in ntext]))
    return out


def sheet_exists(ctx: CheckContext, p: dict[str, Any]) -> list[dict[str, Any]]:
    """检查要求的业务表是否存在。

    参数 ctx 为审核上下文，p 为包含 sheet_types 的规则参数；返回缺失表问题。
    """
    wanted = p["sheet_types"]
    present = {(f.entity_code, f.business_id or f.business_code, f.variant_id, s.sheet_type)
               for f in ctx.files for s in f.sheets}
    three_list_types = {"position_duty", "incompatible_position", "system_rule"}
    checks_three_lists = bool(wanted) and set(wanted).issubset(three_list_types)
    out = []
    for ec in ctx.scope_entity_codes:
        for b in ctx.scope_businesses:
            business_id = b.get("business_id") or b["business_code"]
            variant = b.get("variant_id", "default")
            if checks_three_lists:
                # 仅报送矩阵时，不启动三清单内部的工作表完整性检查。
                has_three_lists = any(
                    file.entity_code == ec
                    and (file.business_id or file.business_code) == business_id
                    and file.variant_id == variant
                    and (
                        file.material_type == "three_lists"
                        or any(sheet.sheet_type in three_list_types for sheet in file.sheets)
                    )
                    for file in ctx.files
                )
                if not has_three_lists:
                    continue
            for st in wanted:
                if (ec, business_id, variant, st) not in present:
                    out.append(_issue(None, "violation", entity_code=ec, business_id=business_id,
                                      business_code=b["business_code"], variant_id=variant, missing_sheet=st))
    return out


def required_documents(ctx: CheckContext, p: dict[str, Any], *, include_standard_short_names=False) -> list[dict[str, Any]]:
    out = []
    categories = p.get("material_types", [])
    present: dict[tuple[str | None, str | None, str, str | None], list[FileRecord]] = {}
    for f in ctx.files:
        if f.material_type == "explanation": continue
        present.setdefault((f.entity_code, f.business_id or f.business_code, f.variant_id, f.material_type), []).append(f)
        if f.entity_conflict:
            out.append(_issue(None, "review", entity_code=f.entity_code or "", business_id=f.business_id or f.business_code or "", business_code=f.business_code or "", variant_id=f.variant_id, file_path=str(f.relative_path), unavailable_reason="文件名、目录或表内主体证据冲突"))
        if f.parse_errors:
            out.append(_issue(None, "violation", entity_code=f.entity_code or "", business_id=f.business_id or f.business_code or "", business_code=f.business_code or "", variant_id=f.variant_id, file_path=str(f.relative_path), unavailable_reason="；".join(f.parse_errors)))
    for ec in ctx.scope_entity_codes:
        for b in ctx.scope_businesses:
            business_id = b.get("business_id") or b["business_code"]
            bc, variant = b["business_code"], b.get("variant_id", "default")
            for cat in categories:
                matches = present.get((ec, business_id, variant, cat), [])
                if not matches:
                    out.append(_issue(None, "violation", entity_code=ec, business_id=business_id, business_code=bc, variant_id=variant, missing_material=cat))
                elif len(matches) > 1:
                    out.append(_issue(None, "review", entity_code=ec, business_id=business_id, business_code=bc, variant_id=variant, unavailable_reason=f"存在多个{cat}版本，不能任择", files=[str(x.relative_path) for x in matches]))
    if p.get("naming_mode"):
        for f in ctx.files:
            if f.material_type not in {"matrix", "three_lists"}: continue
            defaults = {"matrix": ["矩阵", "取证"], "three_lists": ["三清单"]}
            keywords = p.get("naming_keywords", {}).get(f.material_type, defaults[f.material_type])
            aliases = ctx.resources.get("entity_aliases", {})
            entity = ctx.entities.get(f.entity_code or "")
            names = [a for a, info in aliases.items() if (info.get("entity_code") if isinstance(info, dict) else info) == f.entity_code]
            if entity: names.append(entity.name)
            if include_standard_short_names:
                from risk_audit.readers.header_semantics import unit_names
                names.extend(unit_names(f))
            unit_named = any(norm_text(x) in norm_text(f.source.name) for x in names if x)
            problems = []
            if not any(keyword in f.source.name for keyword in keywords): problems.append(f"缺少“{'/ '.join(keywords)}”材料类别关键字")
            # 新口径允许从目录或表内证据唯一归属，因此可单独关闭主体命名提示。
            if p.get("require_entity_name", True) and not unit_named:
                problems.append("缺少会计主体名称或确认简称")
            if problems:
                out.append(_issue(None, "violation", entity_code=f.entity_code or "", business_code=f.business_code or "", variant_id=f.variant_id, file_path=str(f.relative_path), naming_issue="；".join(problems)))
    if p.get("explanation_mode"):
        present_entities = {f.entity_code for f in ctx.files if f.material_type in {"matrix", "three_lists"} and f.entity_code}
        differences = set(ctx.scope_entity_codes) ^ present_entities
        docs = [f for f in ctx.files if f.material_type == "explanation" and f.true_format in {"doc", "docx"} and not f.entity_conflict]
        for ec in sorted(differences):
            entity = ctx.entities.get(ec)
            markers = [ec, entity.name if entity else ""]
            related = [d for d in docs if d.entity_code == ec or any(x and norm_text(x) in norm_text(str(d.relative_path)) for x in markers)]
            if not related:
                out.append(_issue(None, "violation", entity_code=ec, business_code="", variant_id="default", missing_material="主体差异说明"))
    return out


def required_documents_v2(ctx,p):
    return required_documents(ctx,p,include_standard_short_names=True)


def coverage(ctx: CheckContext, p: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    duties = [r for r in ctx.all_records if r.record_type == "position_duty"]
    duty_groups: dict[tuple[str, str, str, str], list[Record]] = {}
    for d in duties:
        duty_groups.setdefault((d.entity_code, d.business_code, d.variant_id, measure_id_key(d.value("measure_id"))), []).append(d)
    generic = [norm_text(x) for x in p.get("generic_responsibilities", [])]
    for r in ctx.records:
        applicability = norm_text(r.value("applicability"))
        if applicability in {norm_text(x) for x in p.get("not_applicable_values", ["否", "不适用"])}:
            continue
        raw_mid = norm_text(r.value("measure_id"))
        if not raw_mid:
            continue
        mid = measure_id_key(raw_mid)
        related = duty_groups.get((r.entity_code, r.business_code, r.variant_id, mid), [])
        if not related:
            out.append(_issue(r, "review", unavailable_reason="适用措施未找到对应岗位职责记录", measure_id=r.value("measure_id")))
            continue
        responsibility = norm_text(r.value("responsibility"))
        if any(g and g in responsibility for g in generic):
            out.append(_issue(r, "review", unavailable_reason="矩阵责任主体为泛称，缺少已确认映射，无法自动断言岗位覆盖", responsibility=r.value("responsibility")))
            continue
        responsibility_items = [x.strip() for x in re.split(r"[、,，;；\n]+", r.value("responsibility")) if x.strip()]
        duty_pairs = {(norm_text(x.value("department")), norm_text(x.value("position"))) for x in related}
        missing = []
        for item in responsibility_items:
            parts = [norm_text(x) for x in re.split(r"[-—]", item) if norm_text(x)]
            if len(parts) < 2:
                missing.append(item); continue
            dept, pos = parts[-2], parts[-1]
            if (dept, pos) not in duty_pairs: missing.append(item)
        if missing:
            out.append(_issue(r, "review", unavailable_reason=f"以下矩阵责任主体没有已确认的部门岗位覆盖映射：{'、'.join(missing)}", responsibility=r.value("responsibility")))
    return out


def _people(record: Record, role: str) -> tuple[set[str], bool]:
    names: set[str] = set()
    reliable = True
    for person in record.person_keys:
        key = person.get("person_id") or person.get("name")
        if key:
            names.add(norm_text(key))
        if not person.get("person_id"):
            reliable = False
    return names, reliable


def roles_disjoint(ctx: CheckContext, p: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[Record]] = {}
    for r in ctx.records:
        key = tuple(r.entity_code if x == "entity_code" else r.business_code if x == "business_code" else
                    r.variant_id if x == "variant_id" else measure_id_key(r.value(x)) if x == "measure_id" else
                    norm_text(r.value(x)) for x in p["group_by"])
        groups.setdefault(key, []).append(r)
    out = []
    for rows in groups.values():
        def resolved_role(record: Record) -> str:
            explicit = norm_text(record.value(p["role_field"]))
            if explicit: return explicit
            duty = norm_text(record.value("duty"))
            inferred = [role for role, marker in (("经办", "主体责任"), ("审核", "审核责任"), ("审批", "审批责任")) if marker in duty]
            return inferred[0] if len(inferred) == 1 else ""
        for row in [r for r in rows if not resolved_role(r)]:
            out.append(_issue(row, "review", unavailable_reason="角色字段为空且职责中没有唯一明确的主体/审核/审批责任类型，未从岗位或普通动作词推断"))
        for left, right in p["role_pairs"]:
            lrows = [r for r in rows if resolved_role(r) == norm_text(left)]
            rrows = [r for r in rows if resolved_role(r) == norm_text(right)]
            if not lrows or not rrows: continue
            ln, lr = set(), True
            rn, rr = set(), True
            for row in lrows:
                n, reliable = _people(row, left); ln |= n; lr &= reliable
            for row in rrows:
                n, reliable = _people(row, right); rn |= n; rr &= reliable
            if not ln or not rn:
                out.append(_issue(rows[0], "review", unavailable_reason="同一措施已识别经办和审核角色，但姓名缺失，无法完成身份分离检查")); continue
            overlap = sorted(ln & rn)
            if overlap:
                kind = "violation" if lr and rr else "review"
                ev = {"conflict_people": "、".join(overlap)} if kind == "violation" else {"unavailable_reason": f"经办与审核姓名重合（{'、'.join(overlap)}），但无人员ID，可能存在同名"}
                for row in lrows + rrows:
                    out.append(_issue(row, kind, **ev))
    return out


def reference_exists(ctx: CheckContext, p: dict[str, Any]) -> list[dict[str, Any]]:
    target_type = p["target_record_type"]
    targets = {(r.entity_code, r.business_code, r.variant_id, measure_id_key(r.value(p["target_field"]))) for r in ctx.all_records if r.record_type == target_type and r.value(p["target_field"])}
    out = []
    for r in ctx.records:
        raw = r.value(p["source_field"])
        if not raw:
            continue
        key = (r.entity_code, r.business_code, r.variant_id, measure_id_key(raw))
        group_has_target = any(x[:3] == key[:3] for x in targets)
        if not group_has_target:
            out.append(_issue(r, "review", unavailable_reason="同主体、同业务、同变体的当前矩阵不存在，无法断言编号错误", reference=raw))
        elif key not in targets:
            out.append(_issue(r, "violation", reference=raw))
    return out


def _split_carriers(text: str) -> set[str]:
    return split_carrier_list(text)


def set_subset(ctx: CheckContext, p: dict[str, Any], *, reference_parser=parse_carrier_references, alias_builder=None) -> list[dict[str, Any]]:
    matrices: dict[tuple[str, str, str, str], list[Record]] = {}
    for matrix in ctx.all_records:
        if matrix.record_type == p["target_record_type"]:
            key = (matrix.entity_code, matrix.business_code, matrix.variant_id, measure_id_key(matrix.value("measure_id")))
            matrices.setdefault(key, []).append(matrix)
    aliases = {norm_text(k): norm_text(v) for k, v in ctx.resources.get(p.get("alias_resource", "carrier_aliases"), {}).items()}
    terminology = terminology_carriers(ctx.resources.get("terminology"))
    out = []
    for r in ctx.records:
        if not r.entity_code or not r.business_code or r.variant_id == "unknown":
            out.append(_issue(r, "review", issue_type="carrier_scope_unconfirmed",
                              unavailable_reason="主体、业务或矩阵类型尚未明确，无法关联矩阵核对载体引用"))
            continue
        measure_field = r.fields.get("measure_id")
        if measure_field is None or measure_field.state == "formula_no_cache" or not norm_text(measure_field.current):
            out.append(_issue(r, "review", issue_type="carrier_measure_id_missing",
                              unavailable_reason="本行没有可读取的控制措施编号，无法关联矩阵核对载体引用",
                              source_cell=measure_field.coordinate if measure_field else ""))
            continue
        key = (r.entity_code, r.business_code, r.variant_id, measure_id_key(r.value("measure_id")))
        matrix_rows = matrices.get(key, [])
        if not matrix_rows:
            out.append(_issue(
                r, "review", issue_type="matrix_ambiguous",
                unavailable_reason="同主体、同业务、同变体的当前措施矩阵不存在，无法核对载体引用",
                original_sentence=r.value(p.get("fallback_explicit_field", "")), matrix_rows=[],
            ))
            continue
        unavailable_targets = [row for row in matrix_rows if p["target_field"] not in row.fields
                               or row.fields[p["target_field"]].state == "formula_no_cache"]
        if unavailable_targets:
            out.append(_issue(r, "review", issue_type="carrier_target_unavailable",
                              unavailable_reason="对应矩阵的控制载体栏目缺失或公式没有计算结果，无法确认允许引用的载体范围",
                              matrix_rows=[f"{row.file_path}:{row.sheet}!{row.row}" for row in unavailable_targets]))
            continue
        carrier_sets = [frozenset(aliases.get(x, x) for x in _split_carriers(row.value(p["target_field"]))) for row in matrix_rows]
        distinct_sets = sorted(set(carrier_sets), key=lambda values: tuple(sorted(values)))
        if len(distinct_sets) != 1:
            out.append(_issue(
                r, "review", issue_type="matrix_ambiguous",
                unavailable_reason="同一措施的重复矩阵行载体集合冲突，不能合并为允许范围",
                matrix_carrier_sets=[sorted(x) for x in distinct_sets],
                matrix_rows=[f"{x.file_path}:{x.sheet}!{x.row}" for x in matrix_rows],
                original_sentence=r.value(p.get("fallback_explicit_field", "")),
            ))
            continue
        allowed = set(distinct_sets[0])
        row_aliases = alias_builder(allowed, aliases) if alias_builder else aliases
        source_text = r.value(p["source_field"])
        if source_text:
            explicit = {row_aliases.get(x, x) for x in _split_carriers(source_text)}
            parsed = {
                "references": sorted(explicit), "unresolved": [],
                "status": "matched" if explicit <= allowed else "unresolved",
                "evidence": [{"kind": "carrier_field", "text": source_text}],
                "original_sentence": source_text, "unavailable_fragments": [],
            }
        elif p.get("fallback_explicit_field"):
            parsed = reference_parser(
                r.value(p["fallback_explicit_field"]), allowed=allowed, aliases=row_aliases, terminology=terminology
            )
        else:
            parsed = {"references": [], "unresolved": [], "status": "not_applicable", "evidence": [], "original_sentence": "", "unavailable_fragments": []}
        if parsed["status"] == "unavailable":
            fragments = parsed["unavailable_fragments"]
            out.append(_issue(
                r, "review", issue_type="carrier_parse_unavailable",
                unavailable_reason=f"责任句的载体对象无法按确定性规则解析：{'、'.join(fragments)}",
                extracted_references=parsed["references"], original_sentence=parsed["original_sentence"],
                parse_evidence=parsed["evidence"], unavailable_fragments=fragments,
            ))
        explicit = set(parsed["references"])
        missing = sorted(explicit - allowed)
        if missing:
            out.append(_issue(
                r, "review", issue_type="carrier_reference_unmatched",
                unavailable_reason=f"明确引用载体未在当前矩阵载体集合中找到：{'、'.join(missing)}",
                missing_references=missing, extracted_references=parsed["references"],
                original_sentence=parsed["original_sentence"], parse_evidence=parsed["evidence"],
            ))
    return out


def text_pattern(ctx: CheckContext, p: dict[str, Any]) -> list[dict[str, Any]]:
    patterns = [re.compile(x) for x in p["patterns"]]
    out = []
    for r in ctx.records:
        text = r.value(p["field"])
        if text and not any(rx.search(text) for rx in patterns):
            out.append(_issue(r, "violation", field=p["field"], text=text[:200]))
    return out


def value_mapping(ctx: CheckContext, p: dict[str, Any]) -> list[dict[str, Any]]:
    mapping = {norm_text(k): [norm_text(x) for x in v] for k, v in p["mapping"].items()}
    out = []
    for r in ctx.records:
        role = norm_text(r.value(p["source_field"]))
        if not role:
            out.append(_issue(r, "review", unavailable_reason="角色字段为空或不可识别，未从岗位名或普通动作词推断"))
            continue
        allowed = mapping.get(role)
        if allowed is None:
            out.append(_issue(r, "review", unavailable_reason=f"角色“{r.value(p['source_field'])}”不在已确认映射中"))
            continue
        text = norm_text(r.value(p["text_field"]))
        matched_roles = [mapped_role for mapped_role, terms in mapping.items() if any(x in text for x in terms)]
        if len(matched_roles) > 1:
            out.append(_issue(r, "review", unavailable_reason=f"职责同时出现多种责任类型（{'、'.join(matched_roles)}），无法确定与角色的唯一对应"))
        elif not any(x in text for x in allowed):
            out.append(_issue(r, "violation", role=r.value(p["source_field"]), expected="/".join(p["mapping"].get(r.value(p["source_field"]), allowed))))
    return out


def ordered_records(ctx: CheckContext, p: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    by_sheet: dict[tuple[str, str], list[Record]] = {}
    for r in ctx.records:
        by_sheet.setdefault((r.file_path, r.sheet), []).append(r)
    for rows in by_sheet.values():
        rows.sort(key=lambda r: r.row)
        ids = [measure_id_key(r.value(p["field"])) for r in rows if r.value(p["field"])]
        positions: dict[tuple, list[int]] = {}
        for i, mid in enumerate(ids): positions.setdefault(mid, []).append(i)
        for mid, pos in positions.items():
            if max(pos) - min(pos) + 1 != len(pos):
                row = next(r for r in rows if measure_id_key(r.value(p["field"])) == mid)
                out.append(_issue(row, "violation", reason="同一措施记录不连续", measure_id=row.value(p["field"])))
        distinct = []
        for mid in ids:
            if not distinct or distinct[-1] != mid: distinct.append(mid)
        baseline = ctx.baselines.get((rows[0].business_id or rows[0].business_code, rows[0].variant_id), {}).get("measure_order", []) if rows else []
        rank = {measure_id_key(x): i for i, x in enumerate(baseline)}
        known = [(x, rank[x]) for x in distinct if x in rank]
        order_keys = [x[1] for x in known] if known else [
            (0, x) if all(isinstance(value, int) for value in x) else (1, natural_key(str(x)))
            for x in distinct
        ]
        for i in range(1, len(order_keys)):
            if order_keys[i] < order_keys[i - 1]:
                target_id = known[i][0] if known else distinct[i]
                row = next(r for r in rows if measure_id_key(r.value(p["field"])) == target_id)
                out.append(_issue(row, "violation", reason="措施顺序倒置", measure_id=row.value(p["field"])))
    return out


def records_unique(ctx: CheckContext, p: dict[str, Any]) -> list[dict[str, Any]]:
    seen: dict[tuple, Record] = {}; out = []
    for r in ctx.records:
        values = []
        for f in p["key_fields"]:
            if f == "entity_code": values.append(r.entity_code)
            elif f == "business_code": values.append(r.business_code)
            elif f == "variant_id": values.append(r.variant_id)
            elif f == "person_keys": values.append(tuple(sorted(norm_text(x.get("person_id") or x.get("name")) for x in r.person_keys)))
            elif f == "measure_id": values.append(measure_id_key(r.value(f)))
            else: values.append(norm_text(r.value(f)))
        key = tuple(values)
        if key in seen:
            uncertain_people = "person_keys" in p["key_fields"] and any(not x.get("person_id") for x in r.person_keys + seen[key].person_keys)
            if uncertain_people: out.append(_issue(r, "review", unavailable_reason=f"与{seen[key].sheet}!{seen[key].row}仅姓名相同但没有人员ID，不能确认重复", duplicate_locations=f"{seen[key].sheet}!{seen[key].row}"))
            else: out.append(_issue(r, "violation", duplicate_locations=f"{seen[key].sheet}!{seen[key].row}"))
        else: seen[key] = r
    return out


def build_registry() -> CapabilityRegistry:
    """构建审核能力版本表；无参数，新规则版本独立注册，冻结版本仍可验证。"""
    r = CapabilityRegistry()
    def reg(name, types, req, opt, fields, evidence, locations, fn, available=True, version=1):
        r.register(Capability(name, version, frozenset(types), frozenset(req), frozenset(opt), frozenset(fields), frozenset(evidence) | {"unavailable_reason"}, frozenset(locations), fn, available))
    locations = ["row", "related_rows", "material"]
    reg("required_documents", ["package"], [], ["material_types", "explanation_mode", "naming_mode", "naming_keywords"], [], ["missing_material", "files", "file_path", "entity_code", "business_code", "variant_id", "naming_issue"], ["material"], required_documents)
    reg("required_fields", ["matrix", "position_duty", "system_rule", "incompatible_position"], ["fields"], ["placeholders", "labels", "when"], ["fields"], ["field", "field_label", "reason"], locations, required_fields)
    reg("schema_contains", ["package"], ["required_fields", "sheet_types"], ["compare_baseline", "ignore_fields", "check_applicability_tail", "accept_split_applicability"], ["required_fields", "ignore_fields"], ["missing_fields", "file_path", "sheet"], ["material"], schema_contains)
    reg("applicability_values", ["matrix"], ["placeholders"], [], [], ["issue_type", "actual_text"], ["row"], applicability_values)
    reg("forbidden_reference", ["matrix", "system_rule"], ["terms", "fields"], ["measure_contains", "negation_markers"], ["fields"], ["matched_text", "forbidden_terms"], locations, forbidden_reference)
    reg("coverage", ["matrix"], [], ["generic_responsibilities", "not_applicable_values"], [], ["measure_id", "responsibility"], locations, coverage)
    reg("responsibility_coverage", ["matrix"], ["generic_responsibilities", "confirmed_mappings", "confirmed_aliases"], ["unresolved_kind"], [], ["issue_type", "measure_id", "missing_responsibilities", "responsibility_items"], ["row"], responsibility_coverage)
    reg("responsibility_coverage", ["matrix"], ["generic_responsibilities", "confirmed_mappings", "confirmed_aliases", "generic_unit_names"], ["unresolved_kind"], [], ["issue_type", "measure_id", "missing_responsibilities", "responsibility_items", "mapping_details"], ["row"], responsibility_coverage_v2, version=2)
    reg("responsibility_unit_specific", ["matrix"], ["field", "generic_unit_names"], [], ["field"], ["issue_type", "field", "generic_unit_names", "matches", "source_cell"], ["row"], responsibility_unit_specific)
    reg("field_constraints", ["position_duty"], ["field", "placeholders", "placeholder_patterns", "separators"], [], ["field"], ["issue_type", "field", "actual_text"], ["row"], field_constraints)
    reg("roles_same_duty", ["position_duty"], [], [], [], ["issue_type", "actual_text", "conflict_people", "related_file", "related_sheet", "related_row", "positions", "duty_signature"], ["related_rows"], roles_same_duty)
    reg("responsibility_phrase", ["position_duty"], ["attributes"], [], [], ["issue_type"], ["row"], responsibility_phrase)
    reg("responsibility_phrase", ["position_duty"], ["attributes"], [], [], ["issue_type"], ["row"], responsibility_phrase_v2, version=2)
    reg("sheet_exists", ["package"], ["sheet_types"], [], [], ["missing_sheet", "entity_code", "business_code", "variant_id"], ["material"], sheet_exists)
    reg("roles_disjoint", ["position_duty"], ["group_by", "role_field", "person_field", "role_pairs"], [], ["role_field", "person_field", "group_by"], ["conflict_people"], ["related_rows"], roles_disjoint)
    reg("reference_exists", ["position_duty", "system_rule", "incompatible_position"], ["source_field", "target_record_type", "target_field"], [], ["source_field", "target_field"], ["reference"], locations, reference_exists)
    reg("set_subset", ["position_duty", "system_rule"], ["source_field", "target_record_type", "target_field"], ["alias_resource", "fallback_explicit_field"], ["source_field", "target_field", "fallback_explicit_field"], ["missing_references", "issue_type", "extracted_references", "original_sentence", "parse_evidence", "matrix_carrier_sets", "matrix_rows"], locations, set_subset)
    reg("set_subset", ["position_duty", "system_rule"], ["source_field", "target_record_type", "target_field"], ["alias_resource", "fallback_explicit_field"], ["source_field", "target_field", "fallback_explicit_field"], ["missing_references", "issue_type", "extracted_references", "original_sentence", "parse_evidence", "matrix_carrier_sets", "matrix_rows", "source_field", "source_kind", "source_cell"], locations, set_subset_v2, version=2)
    reg("deleted_text_reappears", ["position_duty"], ["source_fields", "target_field", "match_scope"], [], ["source_fields", "target_field"], ["issue_type", "deleted_texts", "matches", "duty_text", "target_cell", "match_scope", "normalization"], ["row"], deleted_text_reappears)
    reg("deleted_text_reappears", ["position_duty"], ["source_fields", "target_field", "match_scope"], [], ["source_fields", "target_field"], ["issue_type", "deleted_texts", "matches", "duty_text", "target_cell", "match_scope", "normalization", "conflicting_texts"], ["row"], deleted_text_reappears_v2, version=2)
    reg("text_pattern", ["position_duty"], ["field", "patterns"], [], ["field"], ["field", "text"], locations, text_pattern)
    reg("value_mapping", ["position_duty"], ["source_field", "text_field", "mapping"], [], ["source_field", "text_field"], ["role", "expected"], locations, value_mapping)
    reg("ordered_records", ["position_duty", "system_rule", "incompatible_position"], ["field"], [], ["field"], ["reason", "measure_id"], locations, ordered_records)
    reg("records_unique", ["position_duty", "system_rule", "incompatible_position", "matrix"], ["key_fields"], [], ["key_fields"], ["duplicate_locations"], locations, records_unique)
    reg("system_rule_content", ["system_rule"], [], [], [], [], locations, lambda c, p: [], available=False)
    r.register(replace(r.get('set_subset',2),version=3,runner=set_subset_v3))
    r.register(replace(r.get('schema_contains',1),version=2,runner=schema_contains_v2,evidence_variables=r.get('schema_contains',1).evidence_variables|{'issue_type'}))
    schema_contains_v2_capability = r.get("schema_contains", 2)
    # v3 将用户展示名与识别别名分离，避免在通用意见中显示特定单位名。
    r.register(replace(
        schema_contains_v2_capability,
        version=3,
        optional_params=schema_contains_v2_capability.optional_params | {"field_labels"},
    ))
    r.register(replace(r.get('required_documents',1),version=2,runner=required_documents_v2))
    r.register(replace(r.get('responsibility_phrase',2),version=3,runner=responsibility_phrase_v3))
    r.register(replace(r.get('responsibility_coverage',2),version=3,runner=responsibility_coverage_v3))
    r.register(replace(r.get('roles_same_duty',1),version=2,runner=roles_same_duty_v2,
                       evidence_variables=r.get('roles_same_duty',1).evidence_variables|{'field'}))
    from risk_audit.checks.quality_checks import set_subset_v4, ordered_records_v2, responsibility_coverage_v4, responsibility_phrase_v4
    r.register(replace(r.get('set_subset', 3), version=4, runner=set_subset_v4))
    r.register(replace(r.get('ordered_records', 1), version=2, runner=ordered_records_v2))
    r.register(replace(r.get('responsibility_coverage', 3), version=4, runner=responsibility_coverage_v4))
    r.register(replace(r.get('responsibility_phrase', 3), version=4, runner=responsibility_phrase_v4))
    from risk_audit.responsibility import responsibility_phrase_v5, value_mapping_v2
    r.register(replace(r.get('responsibility_phrase', 4), version=5, runner=responsibility_phrase_v5))
    r.register(replace(r.get('value_mapping', 1), version=2, runner=value_mapping_v2))
    from risk_audit.responsibility_v170 import responsibility_phrase_v6, value_mapping_v3
    r.register(replace(r.get('responsibility_phrase', 5), version=6, runner=responsibility_phrase_v6))
    r.register(replace(r.get('value_mapping', 2), version=3, runner=value_mapping_v3))
    from risk_audit.checks.applicability import applicability_values_v2
    from risk_audit.checks.quality_checks import responsibility_coverage_v5
    r.register(replace(r.get('applicability_values',1),version=2,runner=applicability_values_v2))
    r.register(replace(r.get('responsibility_coverage',4),version=5,runner=responsibility_coverage_v5))
    from risk_audit.semantic_matching import responsibility_coverage_v6
    r.register(replace(r.get('responsibility_coverage',5),version=6,runner=responsibility_coverage_v6))
    from risk_audit.checks.coverage_v170 import responsibility_coverage_v7
    r.register(replace(r.get('responsibility_coverage',6),version=7,runner=responsibility_coverage_v7))
    from risk_audit.checks.carriers_v5 import set_subset_v5
    r.register(replace(r.get('set_subset',4),version=5,runner=set_subset_v5))
    from risk_audit.checks.applicability_v180 import responsibility_applicability_check, responsibility_coverage_v8
    from risk_audit.checks.confirmed_v180 import field_constraints_v2, roles_same_duty_v3, reference_exists_v2, responsibility_phrase_v7, value_mapping_v4
    from risk_audit.checks.materials_v180 import applicable_ordered_records
    from risk_audit.checks.materials_v180 import required_documents_v3, required_documents_v4, incompatible_constraints, applicable_deleted_text
    # 确认稿新能力单独版本化，避免改写冻结规则的判定行为。
    evidence = {'source_cell', 'comparison_basis', 'original_measure_id', 'control_measure', 'matrix_rows',
                'reference', 'applicability_facts', 'reason', 'responsibility_facts', 'original_issue_type',
                'actual_responsibilities', 'interpretation_status', 'problem_locations',
                'related_file', 'related_sheet', 'related_row', 'responsibility_text', 'restriction_ids',
                'entity_name', 'responsibility_items', 'applicability_text', 'applicability_cell',
                'expected_applicability', 'actual_applicability', 'matching_basis'}
    for name, old_version, new_version, runner in [
        ('required_documents', 2, 3, required_documents_v3),
        ('field_constraints', 1, 2, field_constraints_v2),
        ('roles_same_duty', 2, 3, roles_same_duty_v3),
        ('reference_exists', 1, 2, reference_exists_v2),
        ('responsibility_phrase', 6, 7, responsibility_phrase_v7),
        ('value_mapping', 3, 4, value_mapping_v4),
        ('ordered_records', 2, 3, applicable_ordered_records),
        ('deleted_text_reappears', 2, 3, applicable_deleted_text),
    ]:
        old = r.get(name, old_version)
        types = old.record_types | {'incompatible_position'} if name in {'field_constraints', 'deleted_text_reappears'} else old.record_types
        r.register(replace(old, version=new_version, runner=runner, record_types=frozenset(types), evidence_variables=old.evidence_variables | evidence))
    required_documents_capability = r.get('required_documents', 3)
    r.register(replace(
        required_documents_capability,
        version=4,
        runner=required_documents_v4,
        optional_params=required_documents_capability.optional_params | {'require_entity_name'},
    ))
    r.register(replace(r.get('field_constraints', 2), version=3, runner=incompatible_constraints, record_types=frozenset({'incompatible_position'})))
    coverage_capability = r.get('responsibility_coverage', 7)
    r.register(replace(coverage_capability, version=8, runner=responsibility_coverage_v8,
                       optional_params=coverage_capability.optional_params | {'responsibility_applicability'},
                       evidence_variables=coverage_capability.evidence_variables | evidence))
    reg('responsibility_applicability', ['matrix'], ['restrictions'], ['known_entity_patterns'], [], evidence | {'issue_type'}, ['row'], responsibility_applicability_check)
    from risk_audit.checks.confirmed_v180 import (field_constraints_v4, field_constraints_v5, field_constraints_v6, roles_same_duty_v4,
        responsibility_phrase_v8, responsibility_phrase_v9, value_mapping_v5, duplicate_duties, duplicate_duties_v2, missing_duty_measures, ordered_records_v4,
        system_rule_changes_v1, system_type_header_v1, system_type_completion_v1,
        measure_applicability_alignment_v1,
        measure_applicability_alignment_v2, measure_applicability_alignment_v3,
        responsibility_department_alignment_v1)
    for name, old_version, new_version, runner in [
        ('field_constraints', 2, 4, field_constraints_v4),
        ('roles_same_duty', 3, 4, roles_same_duty_v4),
        ('responsibility_phrase', 7, 8, responsibility_phrase_v8),
        ('value_mapping', 4, 5, value_mapping_v5),
        ('ordered_records', 3, 4, ordered_records_v4),
    ]:
        r.register(replace(r.get(name, old_version), version=new_version, runner=runner))
    r.register(replace(r.get('responsibility_phrase', 8), version=9, runner=responsibility_phrase_v9))
    field_constraints_capability = r.get('field_constraints', 4)
    r.register(replace(field_constraints_capability, version=5, runner=field_constraints_v5,
                       optional_params=field_constraints_capability.optional_params | {'exact_exceptions'}))
    r.register(replace(r.get('field_constraints', 5), version=6, runner=field_constraints_v6,
                       optional_params=r.get('field_constraints', 5).optional_params | {'prefix_exceptions', 'contains_exceptions'}))
    reg('duplicate_duties', ['position_duty'], [], [], [], ['issue_type', 'duplicate_locations'], ['row'], duplicate_duties)
    r.register(replace(r.get('duplicate_duties', 1), version=2, runner=duplicate_duties_v2))
    reg('missing_duty_measures', ['matrix'], [], [], [], ['issue_type', 'measure_id'], ['row'], missing_duty_measures)
    reg('system_rule_changes', ['matrix'], [], [], [],
        ['issue_type', 'measure_id', 'matrix_system', 'system_rule_names'], ['row'], system_rule_changes_v1)
    reg('system_type_header', ['package'], [], [], [],
        ['issue_type', 'missing_fields'], ['row'], system_type_header_v1)
    reg('system_type_completion', ['system_rule'], [], [], [],
        ['issue_type', 'opinion'], ['row'], system_type_completion_v1)
    reg('measure_applicability_alignment', ['matrix'], [], [], [],
        ['issue_type', 'measure_id', 'measure_present', 'applicability_text', 'applicability_state', 'applicability_decision'],
        ['row'], measure_applicability_alignment_v1)
    r.register(replace(
        r.get('measure_applicability_alignment', 1),
        version=2,
        runner=measure_applicability_alignment_v2,
    ))
    r.register(replace(
        r.get('measure_applicability_alignment', 2),
        version=3,
        runner=measure_applicability_alignment_v3,
    ))
    reg('responsibility_department_alignment', ['matrix'], ['keyword_mappings'], [], [],
        ['issue_type', 'measure_id', 'missing_keywords', 'duty_departments'], ['row'], responsibility_department_alignment_v1)
    return r
