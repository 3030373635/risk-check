from __future__ import annotations

import re
import string
from typing import Any

from risk_audit.business_identity import validate_business_registry
from risk_audit.checks.registry import CapabilityRegistry
from risk_audit.util import norm_text, sha256_json


class ConfigError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("configuration invalid:\n- " + "\n- ".join(errors))


RULE_KEYS = {"schema_version", "rule_id", "display_code", "title", "source_reference", "revision", "enabled", "disabled_reason", "scope", "checks", "_source", "retired"}
SCOPE_KEYS = {"record_type", "business_codes", "entity_codes", "variants", "batch"}
CHECK_KEYS = {"check_id", "operator", "operator_version", "params", "on_unavailable", "message", "location_policy"}
MESSAGE_KEYS = {"violation", "review"}
IDENTITY_FIELDS = {"entity_code", "business_id", "business_code", "variant_id", "file_path", "sheet", "row", "person_keys"}


def _unknown(obj: dict, allowed: set[str], where: str, errors: list[str]) -> None:
    for key in sorted(set(obj) - allowed): errors.append(f"{where}: unknown field {key!r}")


def _validate_selector(value: Any, where: str, errors: list[str]) -> None:
    if not isinstance(value, dict): errors.append(f"{where}: must be object"); return
    _unknown(value, {"include", "exclude"}, where, errors)
    if not isinstance(value.get("include"), list) or not value.get("include"):
        errors.append(f"{where}.include: must be a non-empty array")
    if not isinstance(value.get("exclude", []), list): errors.append(f"{where}.exclude: must be array")


def _template_vars(template: str) -> set[str]:
    out = set()
    try:
        for _, name, spec, conv in string.Formatter().parse(template):
            if name:
                if any(x in name for x in ".[]") or spec or conv:
                    raise ValueError("complex placeholders are forbidden")
                out.add(name)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    return out


def validate_pack(pack: dict[str, Any], registry: CapabilityRegistry) -> list[str]:
    errors: list[str] = []
    required_resources = {"manifest", "field_catalog", "field_aliases", "entity_aliases", "submission_scope", "baseline_registry", "roles_and_positions", "carrier_aliases"}
    for key in sorted(required_resources - set(pack)): errors.append(f"missing resource: {key}.json")
    manifest = pack.get("manifest", {})
    if manifest.get("status") == "released":
        expected = manifest.get("content_hash")
        hashes = {k: v for k, v in pack.get("_resource_hashes", {}).items() if k != "manifest.json"}
        actual = sha256_json(hashes)
        if not expected: errors.append("released manifest missing content_hash")
        elif expected != actual: errors.append("released rule pack content hash mismatch (immutable release was changed)")
    baseline_registry = pack.get("baseline_registry", {})
    # 新版模板目录使用全局业务 ID；历史发布包保持只读，以便旧运行快照仍可复核。
    if isinstance(baseline_registry, dict) and baseline_registry.get("schema_version") == "2.0":
        errors.extend(validate_business_registry(baseline_registry))
    elif not isinstance(baseline_registry, dict) or not isinstance(baseline_registry.get("entries"), list):
        errors.append('baseline_registry.schema_version: 仅支持 "2.0"，历史结构必须包含 entries 数组')
    catalog = pack.get("field_catalog", {})
    if 'parser_policy' in pack:
        policy=pack['parser_policy']
        if not isinstance(policy,dict) or set(policy)!={'version','scoped_header_aliases'} or policy.get('version') not in {1,2,3}:
            errors.append('parser_policy: unsupported policy')
        elif not isinstance(policy['scoped_header_aliases'],dict) or not all(isinstance(k,str) and isinstance(v,list) and v and all(isinstance(x,str) and x.strip() for x in v) for k,v in policy['scoped_header_aliases'].items()):
            errors.append('parser_policy.scoped_header_aliases: scoped nonempty names required')
    if 'semantic_config' in pack:
        from risk_audit.semantic_validation import validate_semantic_config
        errors.extend(validate_semantic_config(pack['semantic_config']))
    if 'responsibility_applicability' in pack:
        from risk_audit.configuration.responsibility_applicability import validate_responsibility_applicability
        errors.extend(validate_responsibility_applicability(pack['responsibility_applicability']))
    if 'semantic_lexicon' in pack:
        from risk_audit.semantic_validation import validate_lexicon
        errors.extend(validate_lexicon(pack['semantic_lexicon']))
    if 'result_policy' in pack and pack['result_policy'] not in [
        {'version': version, 'technical_issues': 'internal', 'unfinished_is_pass': False} for version in (1, 2)
    ]:
        errors.append('result_policy: unsupported publication policy')
    if not isinstance(catalog, dict): errors.append("field_catalog must be object"); catalog = {}
    terminology = pack.get("terminology")
    if terminology is not None:
        if not isinstance(terminology, dict): errors.append("terminology must be object")
        else:
            _unknown(terminology, {"schema_version", "terms", "metadata"}, "terminology", errors)
            if terminology.get("schema_version") != "1.0": errors.append("terminology.schema_version: unsupported version")
            terms = terminology.get("terms")
            if not isinstance(terms, dict): errors.append("terminology.terms must be object")
            else:
                _unknown(terms, {"carrier", "department", "position"}, "terminology.terms", errors)
                for field, values in terms.items():
                    if not isinstance(values, list) or not all(isinstance(x, str) and x.strip() for x in values):
                        errors.append(f"terminology.terms.{field}: non-empty strings required")
                    elif len(values) != len(set(values)):
                        errors.append(f"terminology.terms.{field}: duplicate terms")
                    elif any(re.search(r"初审|复审|审核意见", x) or x.startswith("=") for x in values):
                        errors.append(f"terminology.terms.{field}: audit text/formula is not terminology")
            if not isinstance(terminology.get("metadata", {}), dict): errors.append("terminology.metadata must be object")
    forbidden_fields = {k for k, v in catalog.items() if isinstance(v, dict) and v.get("audit_only")}
    known_fields = set(catalog) - forbidden_fields | IDENTITY_FIELDS
    input_overrides=pack.get('input_overrides',[])
    if not isinstance(input_overrides,list):errors.append('input_overrides must be array');input_overrides=[]
    override_keys=set()
    for i,item in enumerate(input_overrides):
        where=f'input_overrides[{i}]'
        if not isinstance(item,dict):errors.append(f'{where}: must be object');continue
        required={'file_path','sha256','sheet','field','column','reason'}
        _unknown(item,required,where,errors)
        if set(item)!=required:errors.append(f'{where}: all source, field, column and reason values required');continue
        if not all(isinstance(item[k],str) and item[k].strip() for k in ('file_path','sha256','sheet','field','reason')):
            errors.append(f'{where}: nonempty strings required');continue
        if item['field'] not in catalog or item['field'] in forbidden_fields:errors.append(f'{where}: unknown or audit-only field')
        if not re.fullmatch(r'[0-9a-f]{64}',item['sha256']):errors.append(f'{where}: invalid source hash')
        if type(item['column']) is not int or not 1<=item['column']<=16384:errors.append(f'{where}: invalid column')
        key=(item['file_path'],item['sheet'],item['field'])
        if key in override_keys:errors.append(f'{where}: duplicate field override')
        override_keys.add(key)
    aliases = pack.get("field_aliases", {})
    if isinstance(aliases, dict):
        for sheet_type, mappings in aliases.items():
            if not isinstance(mappings, dict): errors.append(f"field_aliases.{sheet_type}: must be object"); continue
            seen_aliases: dict[str, str] = {}
            for fid, vals in mappings.items():
                if fid not in catalog: errors.append(f"field_aliases.{sheet_type}: unknown logical field {fid!r}")
                if fid in forbidden_fields: errors.append(f"field_aliases.{sheet_type}: audit field {fid!r} cannot be business mapping")
                if not isinstance(vals, list) or not vals: errors.append(f"field_aliases.{sheet_type}.{fid}: must be non-empty array")
                else:
                    for alias in vals:
                        normalized = re.sub(r"\s+", "", str(alias))
                        if re.search(r"(?:初审|复审|审核意见|\d{1,2}[.月-]\d{1,2}.*初审)", str(alias)): errors.append(f"field_aliases.{sheet_type}.{fid}: audit-column alias is forbidden")
                        if normalized in seen_aliases and seen_aliases[normalized] != fid: errors.append(f"field_aliases.{sheet_type}: alias {alias!r} maps to both {seen_aliases[normalized]!r} and {fid!r}")
                        seen_aliases[normalized] = fid
    rule_ids: set[str] = set(); check_ids: set[tuple[str, str]] = set()
    for i, rule in enumerate(pack.get("rules", [])):
        where = f"rules[{i}]"
        if not isinstance(rule, dict): errors.append(f"{where}: must be object"); continue
        _unknown(rule, RULE_KEYS, where, errors)
        for key in ["schema_version", "rule_id", "display_code", "title", "source_reference", "revision", "enabled", "scope", "checks"]:
            if key not in rule: errors.append(f"{where}: missing {key}")
        rid = rule.get("rule_id")
        if not isinstance(rid, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]+", rid or ""): errors.append(f"{where}.rule_id: invalid stable id")
        elif rid in rule_ids: errors.append(f"{where}.rule_id: duplicate {rid}")
        else: rule_ids.add(rid)
        if not isinstance(rule.get("revision"), int) or rule.get("revision", 0) < 1: errors.append(f"{where}.revision: positive integer required")
        if not isinstance(rule.get("enabled"), bool): errors.append(f"{where}.enabled: boolean required")
        scope = rule.get("scope", {})
        if not isinstance(scope, dict): errors.append(f"{where}.scope: must be object"); scope = {}
        _unknown(scope, SCOPE_KEYS, f"{where}.scope", errors)
        if scope.get("record_type") not in {"package", "matrix", "position_duty", "system_rule", "incompatible_position"}: errors.append(f"{where}.scope.record_type: unknown record type")
        for selector in ("business_codes", "entity_codes"):
            _validate_selector(scope.get(selector), f"{where}.scope.{selector}", errors)
        if "variants" in scope: _validate_selector(scope["variants"], f"{where}.scope.variants", errors)
        checks = rule.get("checks")
        if not isinstance(checks, list) or not checks: errors.append(f"{where}.checks: non-empty array required"); continue
        for j, check in enumerate(checks):
            cw = f"{where}.checks[{j}]"
            if not isinstance(check, dict): errors.append(f"{cw}: must be object"); continue
            _unknown(check, CHECK_KEYS, cw, errors)
            for key in CHECK_KEYS: 
                if key not in check: errors.append(f"{cw}: missing {key}")
            cid = check.get("check_id")
            if not isinstance(cid, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]+", cid or ""): errors.append(f"{cw}.check_id: invalid stable id")
            elif (rid, cid) in check_ids: errors.append(f"{cw}.check_id: duplicate")
            else: check_ids.add((rid, cid))
            version = check.get("operator_version")
            cap = registry.get(check.get("operator", ""), version) if isinstance(version, int) else None
            if cap is None: errors.append(f"{cw}: unknown capability/version {check.get('operator')}@{version}"); continue
            if rule.get("enabled") and not cap.available: errors.append(f"{cw}: capability {cap.name}@{cap.version} is not implemented and cannot be enabled")
            if scope.get("record_type") not in cap.record_types: errors.append(f"{cw}: capability does not support record_type {scope.get('record_type')}")
            params = check.get("params")
            if not isinstance(params, dict): errors.append(f"{cw}.params: must be object"); params = {}
            unknown_params = set(params) - cap.required_params - cap.optional_params
            for key in sorted(unknown_params): errors.append(f"{cw}.params: unknown parameter {key!r}")
            for key in sorted(cap.required_params - set(params)): errors.append(f"{cw}.params: missing parameter {key!r}")
            for fp in cap.field_params & set(params):
                raw = params[fp]
                refs = raw if isinstance(raw, list) else [raw]
                if fp == "fields" or fp in {"key_fields", "group_by", "source_fields"}: pass
                else: refs = [raw]
                for ref in refs:
                    if not isinstance(ref, str): continue
                    if ref in forbidden_fields: errors.append(f"{cw}.params.{fp}: artificial audit field {ref!r} is forbidden")
                    elif ref not in known_fields: errors.append(f"{cw}.params.{fp}: unknown logical field {ref!r}")
            if cap.name == "required_fields":
                if not isinstance(params.get("fields"), list) or not all(isinstance(x, str) for x in params.get("fields", [])): errors.append(f"{cw}.params.fields: array of field ids required")
                when = params.get("when")
                if when is not None:
                    if not isinstance(when, dict) or set(when) != {"field", "in"} or not isinstance(when.get("in"), list): errors.append(f"{cw}.params.when: requires exactly field and in-array")
                    elif when["field"] not in known_fields: errors.append(f"{cw}.params.when.field: unknown logical field {when['field']!r}")
            if cap.name == "deleted_text_reappears":
                if params.get("source_fields") != ["control_measure", "carrier"]:
                    errors.append(f"{cw}.params.source_fields: requires control_measure and carrier")
                if params.get("target_field") != "duty": errors.append(f"{cw}.params.target_field: must be duty")
                if params.get("match_scope") not in {"business", "measure"}: errors.append(f"{cw}.params.match_scope: must be business or measure")
            if cap.name == "schema_contains":
                if "accept_split_applicability" in params and not isinstance(params["accept_split_applicability"], bool):
                    errors.append(f"{cw}.params.accept_split_applicability: boolean required")
                field_labels = params.get("field_labels")
                if field_labels is not None and (
                    not isinstance(field_labels, dict)
                    or not all(
                        field_id in params.get("required_fields", [])
                        and isinstance(label, str)
                        and label.strip()
                        for field_id, label in field_labels.items()
                    )
                ):
                    errors.append(f"{cw}.params.field_labels: required-field ids must map to non-empty labels")
            if cap.name == "required_documents" and (
                "require_entity_name" in params
                and not isinstance(params["require_entity_name"], bool)
            ):
                # 严格限定布尔值，避免非空字符串在运行时被当作开启。
                errors.append(f"{cw}.params.require_entity_name: boolean required")
            if cap.name == "applicability_values":
                if not isinstance(params.get("placeholders"), list) or not all(isinstance(x, str) for x in params.get("placeholders", [])):
                    errors.append(f"{cw}.params.placeholders: string array required")
            if cap.name == "responsibility_coverage":
                if 'responsibility_applicability' in params:
                    from risk_audit.configuration.responsibility_applicability import validate_responsibility_applicability
                    errors.extend(validate_responsibility_applicability(params['responsibility_applicability']))
                if params.get('unresolved_kind', 'limitation') not in {'limitation', 'review'}:
                    errors.append(f"{cw}.params.unresolved_kind: must be limitation or review")
                generic = params.get("generic_responsibilities")
                if not isinstance(generic, list) or not all(isinstance(x, str) and x.strip() for x in generic):
                    errors.append(f"{cw}.params.generic_responsibilities: non-empty strings required")
                mappings = params.get("confirmed_mappings")
                if not isinstance(mappings, dict): errors.append(f"{cw}.params.confirmed_mappings: object required")
                else:
                    for key, targets in mappings.items():
                        valid = isinstance(key, str) and bool(key.strip()) and isinstance(targets, list) and bool(targets)
                        if not valid or not all(isinstance(t, dict) and set(t) == {"department", "position"} and all(isinstance(v, str) and v.strip() for v in t.values()) for t in targets):
                            errors.append(f"{cw}.params.confirmed_mappings: each entry requires non-empty department/position targets")
                aliases = params.get("confirmed_aliases")
                if not isinstance(aliases, dict) or not all(isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip() for k, v in aliases.items()):
                    errors.append(f"{cw}.params.confirmed_aliases: non-empty string mapping required")
            if cap.name == 'responsibility_applicability':
                from risk_audit.configuration.responsibility_applicability import validate_responsibility_applicability
                errors.extend(validate_responsibility_applicability(params))
            if cap.name == 'responsibility_unit_specific' or (cap.name == 'responsibility_coverage' and cap.version == 2):
                names = params.get('generic_unit_names')
                if not isinstance(names, list) or not names or not all(isinstance(x, str) and x.strip() for x in names):
                    errors.append(f"{cw}.params.generic_unit_names: non-empty string array required")
                if cap.name == 'responsibility_unit_specific' and params.get('field') != 'responsibility':
                    errors.append(f"{cw}.params.field: must be responsibility")
            if cap.name == "roles_disjoint":
                if not isinstance(params.get("group_by"), list) or not isinstance(params.get("role_pairs"), list): errors.append(f"{cw}.params: group_by and role_pairs must be arrays")
                elif not {"entity_code", "business_code", "variant_id"} <= set(params["group_by"]): errors.append(f"{cw}.params.group_by: entity_code, business_code and variant_id are mandatory isolation keys")
            if cap.name in {'field_constraints', 'responsibility_phrase'}:
                keys = ['placeholders', 'placeholder_patterns', 'separators'] if cap.name == 'field_constraints' else ['attributes']
                for key in keys:
                    values = params.get(key)
                    if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
                        errors.append(f"{cw}.params.{key}: non-empty string array entries required")
                    elif key == 'placeholder_patterns':
                        for value in values:
                            try: re.compile(value)
                            except re.error: errors.append(f"{cw}.params.{key}: invalid regular expression {value!r}")
                if cap.name == 'responsibility_phrase' and not params.get('attributes'):
                    errors.append(f"{cw}.params.attributes: at least one responsibility attribute required")
                if cap.name == 'field_constraints':
                    for exception_key in ('exact_exceptions', 'prefix_exceptions', 'contains_exceptions'):
                        if exception_key not in params:
                            continue
                        exceptions = params[exception_key]
                        if not isinstance(exceptions, list) or not all(
                            isinstance(value, str) and norm_text(value)
                            for value in exceptions
                        ):
                            errors.append(f"{cw}.params.{exception_key}: non-empty string array entries required")
            if cap.name == "text_pattern":
                if not isinstance(params.get("patterns"), list): errors.append(f"{cw}.params.patterns: array required")
                else:
                    for pattern in params["patterns"]:
                        try: re.compile(pattern)
                        except (re.error, TypeError): errors.append(f"{cw}.params.patterns: invalid regular expression {pattern!r}")
            if cap.name == "value_mapping" and (not isinstance(params.get("mapping"), dict) or not all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in params.get("mapping", {}).values())):
                errors.append(f"{cw}.params.mapping: object of string arrays required")
            if check.get("on_unavailable") not in {"review", "skip", "fail"}: errors.append(f"{cw}.on_unavailable: invalid value")
            message = check.get("message")
            if not isinstance(message, dict): errors.append(f"{cw}.message: must be object"); message = {}
            _unknown(message, MESSAGE_KEYS, f"{cw}.message", errors)
            allowed_vars = set(cap.evidence_variables) | {"advice", "advice_v2"}
            for kind in ("violation", "review"):
                template = message.get(kind)
                if not isinstance(template, str) or not template: errors.append(f"{cw}.message.{kind}: non-empty string required"); continue
                try: variables = _template_vars(template)
                except ValueError as exc: errors.append(f"{cw}.message.{kind}: {exc}"); continue
                for var in sorted(variables - allowed_vars): errors.append(f"{cw}.message.{kind}: unknown template variable {var!r}")
            if check.get("location_policy") not in cap.location_policies: errors.append(f"{cw}.location_policy: unsupported by capability")
    overlays = pack.get("overlays", [])
    if not isinstance(overlays, list): errors.append("overlays must be array"); overlays = []
    accepted_overlays: list[dict[str, Any]] = []
    allowed_overlay = {"priority", "scope", "path", "value"}
    for i, overlay in enumerate(overlays):
        ow = f"overlays[{i}]"
        if not isinstance(overlay, dict): errors.append(f"{ow}: must be object"); continue
        _unknown(overlay, allowed_overlay, ow, errors)
        if not all(k in overlay for k in allowed_overlay): errors.append(f"{ow}: priority, scope, path and value required"); continue
        def overlaps(a, b):
            dims = set(a) | set(b)
            return all(a.get(d, "*") == "*" or b.get(d, "*") == "*" or a.get(d) == b.get(d) for d in dims)
        for previous in accepted_overlays:
            if previous["priority"] == overlay["priority"] and previous["path"] == overlay["path"] and previous["value"] != overlay["value"] and overlaps(previous["scope"], overlay["scope"]):
                errors.append(f"{ow}: conflicting overlay at same priority with overlapping scope/path")
        accepted_overlays.append(overlay)
        path = overlay["path"]
        target_rule = next((r for r in pack.get("rules", []) if path.startswith(r.get("rule_id", "") + ".")), None)
        subpath = path[len(target_rule["rule_id"]) + 1:] if target_rule else ""
        if target_rule is None: errors.append(f"{ow}.path: must start with an existing rule_id")
        else:
            cur: Any = target_rule
            try:
                for part in subpath.split("."):
                    cur = cur[int(part)] if isinstance(cur, list) and part.isdigit() else cur[part]
            except (KeyError, IndexError, TypeError): errors.append(f"{ow}.path: target path does not exist")
            else:
                if type(overlay["value"]) is not type(cur): errors.append(f"{ow}.value: type does not match target path")
        if target_rule is not None and subpath == "enabled" and overlay["value"] is True:
            for check in target_rule.get("checks", []):
                cap = registry.get(check.get("operator", ""), check.get("operator_version"))
                if cap and not cap.available: errors.append(f"{ow}: cannot enable rule backed by unavailable capability {cap.name}@{cap.version}")
        sc = overlay.get("scope", {})
        ec = sc.get("entity_code") if isinstance(sc, dict) else None
        if ec not in (None, "*") and ec not in pack.get("submission_scope", {}).get("entity_codes", []): errors.append(f"{ow}.scope.entity_code: must be a standard code in batch scope")
    if errors: raise ConfigError(errors)
    return []
