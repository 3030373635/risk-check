"""Match responsibility pairs independently of department/position specificity."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import re
from typing import Any

from risk_audit.util import measure_id_key, norm_text
from risk_audit.checks.responsibility_names import generic_unit_segments


_GRADE_NUMBER = {word: str(number) for number, word in enumerate("一二三四五六七八九十", 1)}
_GRADE_TOKEN = r"(?:[一二三四五六七八九十]|[1-9][0-9]*)级职员"
_GRADE_ONLY = re.compile(rf"({_GRADE_TOKEN})")
_POSITION_WITH_GRADE = re.compile(rf"(?P<title>[^()、,;/]+)\((?P<grade>{_GRADE_TOKEN})\)")


def _grade_number(grade: str) -> str:
    number = grade.removesuffix("级职员")
    return _GRADE_NUMBER.get(number, number)


def _grade_candidates(position: str, department: str, pairs: set[tuple[str, str]]) -> list[str]:
    """Use only explicit trailing grade annotations, within the caller's scope.

    Group repeated rows and equivalent Chinese/Arabic grade spellings by title.
    No substring, general bracket stripping, or duty-text inference is used.
    """
    grade = _GRADE_ONLY.fullmatch(position)
    if not grade:
        return []
    candidates: dict[str, str] = {}
    for dept, name in sorted(pairs):
        if dept != department:
            continue
        parsed = _POSITION_WITH_GRADE.fullmatch(name)
        if parsed and _grade_number(parsed["grade"]) == _grade_number(position):
            candidates.setdefault(parsed["title"], name)
    return list(candidates.values())


def _issue(row, kind, category, **evidence):
    return {"record": row, "kind": kind, "evidence": {"issue_type": category, **evidence}}


def responsibility_coverage(ctx: Any, p: dict) -> list[dict]:
    issues = _coverage(ctx, p)
    if p.get('unresolved_kind', 'limitation') == 'review':
        for item in issues:
            if item['kind'] == 'limitation':
                item['kind'] = 'review'
    return issues


def responsibility_coverage_v2(ctx: Any, p: dict) -> list[dict]:
    """Keep coverage decisions; explain generic-unit entries using actual duty pairs."""
    issues = responsibility_coverage(ctx, p)
    names = {norm_text(x) for x in p['generic_unit_names']}
    aliases = {norm_text(k): norm_text(v) for k, v in p['confirmed_aliases'].items()}
    canonical = lambda text: aliases.get(norm_text(text), norm_text(text))
    for issue in issues:
        evidence = issue['evidence']
        if evidence.get('issue_type') != 'coverage_mapping_unconfirmed':
            continue
        matrix = issue['record']
        related = [r for r in ctx.all_records if r.record_type == 'position_duty'
                   and (r.entity_code, r.business_code, r.variant_id, measure_id_key(r.value('measure_id')))
                   == (matrix.entity_code, matrix.business_code, matrix.variant_id, measure_id_key(matrix.value('measure_id')))]
        details = []
        for item in evidence['responsibility_items']:
            if not generic_unit_segments(item, names):
                continue
            parts = [norm_text(x) for x in re.split(r"[-—－]", item) if norm_text(x)]
            if len(parts) < 3:
                continue
            dept, position = parts[-2:]
            same_dept = [r for r in related if canonical(r.value('department')) == canonical(dept)]
            details.append({'responsibility': item, 'matrix_department': dept, 'matrix_position': position,
                            'duty_positions': sorted({r.value('position') for r in same_dept if r.value('position')}),
                            'duty_rows': [{'file': r.file_path, 'sheet': r.sheet, 'row': r.row,
                                           'department': r.value('department'), 'position': r.value('position')}
                                          for r in same_dept]})
        if details:
            evidence['mapping_details'] = details
    return issues


def responsibility_coverage_v3(ctx: Any, p: dict) -> list[dict]:
    from risk_audit.readers.header_semantics import unit_names
    names=defaultdict(set)
    for f in ctx.files:
        if f.entity_code:names[f.entity_code].update(unit_names(f))
    rows=[]
    for r in ctx.all_records:
        department=r.fields.get('department')
        if r.record_type!='position_duty' or department is None:
            rows.append(r);continue
        text=norm_text(department.current)
        for prefix in sorted(names[r.entity_code],key=lambda x:-len(x)):
            if text.startswith(prefix) and len(text)>len(prefix):
                text=text[len(prefix):].lstrip('-—:：');break
        rows.append(replace(r,fields={**r.fields,'department':replace(department,current=text)}))
    return responsibility_coverage_v2(replace(ctx,all_records=rows),p)


def _coverage(ctx: Any, p: dict) -> list[dict]:
    groups = defaultdict(list); departments = defaultdict(set); positions = defaultdict(set)
    for row in ctx.all_records:
        if row.record_type != "position_duty": continue
        scope = (row.entity_code, row.business_code, row.variant_id)
        groups[(*scope, measure_id_key(row.value("measure_id")))].append(row)
        departments[scope].add(norm_text(row.value("department")))
        positions[scope].add(norm_text(row.value("position")))
    generics = {norm_text(x) for x in p.get("generic_responsibilities", [])}
    mappings = {norm_text(k): {(norm_text(x["department"]), norm_text(x["position"])) for x in v}
                for k, v in p.get("confirmed_mappings", {}).items()}
    aliases = {norm_text(k): norm_text(v) for k, v in p.get("confirmed_aliases", {}).items()}
    canonical = lambda text: aliases.get(norm_text(text), norm_text(text))
    out = []
    for row in ctx.records:
        if not row.entity_code or not row.business_code or row.variant_id == "unknown":
            out.append(_issue(row, "limitation", "coverage_scope_unconfirmed", unavailable_reason="主体、业务或模板类型未确认，未进行跨表岗位责任覆盖核对"))
            continue
        applicability = norm_text(row.value("applicability"))
        if re.match(r"^(?:不适用|否)", applicability): continue
        if not re.match(r"^(?:适用|是)(?:$|[,:;。])", applicability):
            out.append(_issue(row, "limitation", "coverage_applicability_unknown", unavailable_reason="适用范围未明确，未判断本措施岗位责任覆盖")); continue
        raw_mid = norm_text(row.value("measure_id"))
        if not raw_mid:
            out.append(_issue(row, "review", "coverage_measure_id_missing", unavailable_reason="矩阵本行缺少控制措施编号，无法对应岗位责任清单")); continue
        mid = measure_id_key(raw_mid)
        scope = (row.entity_code, row.business_code, row.variant_id)
        related = groups.get((*scope, mid), [])
        if not related:
            out.append(_issue(row, "violation", "duty_record_missing", measure_id=row.value("measure_id"))); continue
        pairs = {(canonical(r.value("department")), canonical(r.value("position"))) for r in related}
        known_departments = {canonical(x) for x in departments[scope]}
        known_positions = {canonical(x) for x in positions[scope]}
        items = [x.strip() for x in re.split(r"[、,，;；\n]+", row.value("responsibility")) if x.strip()]
        missing = []; uncertain = []; ambiguous = []
        for item in items:
            if norm_text(item) in mappings:
                wanted = {(canonical(d), canonical(pos)) for d, pos in mappings[norm_text(item)]}
                if not wanted <= pairs: missing.append(item)
                continue
            parts = [norm_text(x) for x in re.split(r"[-—－]", item) if norm_text(x)]
            if len(parts) < 2:
                uncertain.append(item); continue
            dept, pos = (canonical(x) for x in parts[-2:])
            # An exact pair in the same measure is already covered, even when
            # its name is generic. Separate field checks still require concrete names.
            if (dept, pos) in pairs: continue
            candidates = _grade_candidates(pos, dept, pairs)
            if len(candidates) == 1:
                continue
            if len(candidates) > 1:
                ambiguous.append({"responsibility": item, "department": dept, "grade": pos,
                                  "positions": candidates})
                continue
            if any(x in generics for x in parts) or parts[-2].endswith("部门"):
                uncertain.append(item); continue
            # Known exact field values provide an identifiable missing pair. Unknown aliases do not.
            if dept in known_departments and pos in known_positions: missing.append(item)
            else: uncertain.append(item)
        if missing:
            out.append(_issue(row, "review", "specific_responsibility_missing", missing_responsibilities=missing,
                              measure_id=row.value("measure_id")))
        if uncertain or not items:
            out.append(_issue(row, "limitation", "coverage_mapping_unconfirmed", responsibility_items=uncertain,
                              unavailable_reason="其余泛称、简称或责任主体结构尚无已确认映射，未认定遗漏，也未认定全部责任主体已覆盖"))
        if ambiguous:
            out.append(_issue(row, "limitation", "coverage_position_ambiguous", ambiguous_positions=ambiguous,
                              measure_id=row.value("measure_id")))
    return out
