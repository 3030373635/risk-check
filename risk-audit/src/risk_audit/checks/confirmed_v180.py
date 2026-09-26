"""审核确认稿 V1 的新增能力，冻结版本的实现和注册保持独立。"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from typing import Any
import re

from risk_audit.applicability import interpret_record
from risk_audit.checks.deleted_content import deleted_text_reappears_v2
from risk_audit.checks.duty_details import field_constraints, roles_same_duty_v2
from risk_audit.models import FieldValue, Record
from risk_audit.responsibility import QUOTE
from risk_audit.responsibility_v170 import LOCAL, contradiction, own_statements, parse_v2, responsibility_phrase_v6
from risk_audit.util import measure_id_key, natural_key, norm_text


MODAL_NEGATIVE = r"(?:不(?:应当|应该|应|需要|必|用|再|能|会|愿)?|无需|无须|免于|未曾|从未|未尝|未能)"
CLAUSE_START = r"(?:^|[,，]|(?<=责任)(?=然而|而|但|并|且))(?:然而|而|但|并|且)?"
KNOWN_ACTOR = re.compile(CLAUSE_START + r"(?P<denial>并非|不是)?(?:由)?(?P<actor>本岗位|本人|本部门|供应商|承包商|其他部门|其他岗位|其他人员|其他单位|他人|第三方)(?=对|负责|应|需|承担|负|不|未)")
EXPLICIT_ACTOR = re.compile(CLAUSE_START + r"(?P<denial>并非|不是)?由(?P<actor>[^对,，。;；!?！？]{1,30}?)(?=对|(?:应当|应该|应|需|不)?(?:负有?|承担|担负|具有|履行|落实))")
# 从分句开头识别部门或单位主语，后面的“对……”是责任对象，不能掩盖承担者。
SUBJECT_ACTOR = re.compile(CLAUSE_START + r"(?P<denial>并非|不是)?(?:由)?(?P<actor>[^对,，。;；!?！？]{0,25}(?:人员|单位|部门|岗位|公司|相关方|承担方|部))(?=对|负责|应|需|承担|负|不|未)")
MODAL_DENIAL = re.compile(r"(?:本岗位|本人|本部门)(?:并)?" + MODAL_NEGATIVE + r"(?:再)?(?:承担|负有?|履行)(?P<object>上述|前述|该项|该|这些|任何|全部|主体|审核|审批)?责任")

# 这里只收录业务已经确认等价的完整术语，不用相似度推断新的同义关系。
DUPLICATE_DUTY_EQUIVALENTS = (
    ('系统变更审批轨迹', '系统审批轨迹'),
)
# 并列连接词仅在前项以常见事项/载体后缀结束、后项也能找到同类后缀时归一，避免改写普通词语内部的“和”“及”。
DUPLICATE_DUTY_CONNECTOR = re.compile(
    r'(?<=[表单册书据录迹件项款息])(?:以及|与|和|及)(?=[^,，。;；!?！？]{1,40}(?:表|单|册|书|据|录|迹|件|项|款|息))'
)
RESPONSIBILITY_TYPES = ("主体责任", "审核责任", "审批责任")


def _issue(row: Record, kind: str, category: str, **evidence: Any) -> dict[str, Any]:
    """创建审核意见；row 为材料行，kind 为结论，category 为类别，evidence 为依据。"""
    return {"record": row, "kind": kind, "evidence": {"issue_type": category, **evidence}}


def _location(row: Record, field: str = "measure_id") -> dict[str, Any]:
    """返回可追溯坐标；row 为材料行，field 为需要展示的字段。"""
    value = row.fields.get(field)
    return {"file": row.file_path, "sheet": row.sheet, "row": row.row,
            "cell": value.coordinate if value else "", "measure_id": row.value("measure_id")}


def _readable(row: Record, field: str) -> bool:
    """判断字段是否可读取；row 为材料行，field 为逻辑字段名。"""
    value = row.fields.get(field)
    return value is not None and value.state != "formula_no_cache"


def _effective_text(row: Record, field: str) -> str:
    """读取有效全文；row 为材料行，field 为逻辑字段，整格划删除线公式不算当前内容。"""
    if not _readable(row, field):
        return ""
    value = row.fields[field]
    return "" if value.formula and value.deleted_spans else norm_text(value.current)


def _scope(row: Record) -> tuple[str, str, str]:
    """返回关联边界；row 为材料行，主体、业务与变体均不得跨越。"""
    return row.entity_code, row.business_code, row.variant_id


def _scope_known(row: Record) -> bool:
    """判断关联边界是否可靠；row 为待检查材料行。"""
    return bool(row.entity_code and row.business_code and row.variant_id and row.variant_id != "unknown")


def _scope_has_sheet_or_record(
    ctx: Any,
    scope: tuple[str, str, str],
    record_type: str,
) -> bool:
    """判断指定范围是否报送某类记录或工作表。

    ctx 为审核上下文，scope 为主体、业务和变体边界，record_type
    为需要确认的记录或工作表类型。
    """
    if any(row.record_type == record_type and _scope(row) == scope for row in ctx.all_records):
        return True
    for file in ctx.files:
        file_scope = (file.entity_code or "", file.business_code or "", file.variant_id)
        if file_scope != scope:
            continue
        # 空表也表示该载体已报送，不能因没有解析出记录而跳过核对。
        if any(sheet.sheet_type == record_type for sheet in file.sheets):
            return True
    return False


def _reliable_measure_key(row: Record) -> tuple[int, ...]:
    """返回可靠的数字措施编号；row 为待关联的材料行。"""
    if not _readable(row, "measure_id") or not norm_text(row.value("measure_id")):
        return ()
    key = measure_id_key(row.value("measure_id"))
    return key if key and all(isinstance(value, int) for value in key) else ()


def _no_incompatible_detail(row: Record) -> bool:
    """识别空清单或整行无事项；row 为不相容岗位清单行，序号不作为业务明细。"""
    values = [value for key, value in row.fields.items() if key not in {"sequence", "row_number"}]
    # 不可读取公式不能被当作空清单，仍应保留技术限制。
    return all(value.state != "formula_no_cache" and norm_text(value.current) in {"", "无", "不涉及"}
               for value in values)


def field_constraints_v2(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """核查具体部门岗位；ctx 为审核上下文，p 为字段、泛称、模式与分隔符配置。"""
    out = []
    for row in ctx.records:
        incompatible = row.record_type == "incompatible_position"
        if incompatible and _no_incompatible_detail(row):
            continue
        requested = p["field"]
        fields = [requested]
        if incompatible and requested == "position":
            # 各种模板只检查已存在的岗位字段，不凭空要求岗位A/B同时出现。
            fields = [key for key in ("position", "position_a", "position_b", "position_name") if key in row.fields]
        elif incompatible and requested.startswith("position") and requested not in row.fields:
            fields = []
        for field_id in fields:
            value = row.fields.get(field_id)
            if value is None or value.state == "formula_no_cache":
                out.append(_issue(row, "review", "field_unavailable", field=field_id,
                                  unavailable_reason=f"字段“{field_id}”未识别或公式没有可用缓存"))
                continue
            generic = {norm_text(text) for text in p["placeholders"]}
            text = norm_text(value.current)
            tested_row = row
            if field_id == "department":
                generic.update({"项目管理部门", "资产使用保管部门", "管理部门", "相关部门", "有关部门"})
                parts = [part for part in re.split(r"[-—－]", text) if part]
                # 泛称后必须有实际部门；追加另一个泛称不会变为具体部门。
                effective = parts[-1] if len(parts) > 1 else text
                tested_row = replace(row, fields={**row.fields, field_id: replace(value, current=effective)})
            checked = field_constraints(replace(ctx, records=[tested_row]),
                                        {**p, "field": field_id, "placeholders": sorted(generic)})
            for item in checked:
                item["record"] = row
                item["evidence"].update(actual_text=value.current, source_cell=value.coordinate)
                out.append(item)
    return out


def _positive_role(row: Record) -> str:
    """解析肯定本岗位责任角色；row 为材料行，不从动作、他人或否定语句推定角色。"""
    if not _readable(row, "duty"):
        return ""
    facts = _responsibility_facts(row.value("duty"))
    if contradiction(facts) or any(statement.get("scope_unresolved") for statement in facts["statements"]):
        return ""
    actual = {role for statement in _own_responsibility(facts) for role in statement["roles"]
              if role in {"主体责任", "审核责任", "审批责任"}}
    return {"主体责任": "经办", "审核责任": "审核", "审批责任": "审批"}.get(next(iter(actual)), "") if len(actual) == 1 else ""


def _own_responsibility(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """筛选肯定本岗位事实；facts 为职责解析结果，未确定承担者或否定范围的分句不计入。"""
    return [statement for statement in own_statements(facts) if not statement.get("scope_unresolved")]


def _responsibility_facts(text: str, attributes: tuple[str, ...] = ()) -> dict[str, Any]:
    """读取肯定本岗位责任事实；text 为职责原文，attributes 为可接受的责任属性。"""
    facts = deepcopy(parse_v2(text, attributes))
    source = facts["normalized_text"]
    quotes = [(match.start(), match.end()) for match in QUOTE.finditer(source)]
    for denial in MODAL_DENIAL.finditer(source):
        if any(start <= denial.start() < end for start, end in quotes):
            continue
        if any(existing["start"] == denial.start() and existing["end"] == denial.end() for existing in facts["self_denials"]):
            continue
        facts["self_denials"].append({"start": denial.start(), "end": denial.end(), "text": denial[0],
                                      "role": denial["object"] + "责任" if denial["object"] in {"主体", "审核", "审批"} else None})
    for statement in facts["statements"]:
        start = max((source.rfind(char, 0, statement["predicate_start"]) for char in "。;；!?！？"), default=-1) + 1
        prefix = source[start:statement["predicate_start"]]
        # 原解析器在谓语之前截断，导致“由供应商负责任”无法匹配承担者；此处纳入谓语。
        actor_text = prefix + statement["predicate"]
        # 同一起点优先明确的短主体匹配，避免泛主体模式把“并非”吞成部门名称的一部分。
        matches = sorted([*KNOWN_ACTOR.finditer(actor_text), *EXPLICIT_ACTOR.finditer(actor_text), *SUBJECT_ACTOR.finditer(actor_text)],
                         key=lambda match: (match.start(), -len(match["actor"])))
        matches = [match for match in matches if not any(left <= start + match.start() < right for left, right in quotes)]
        unknown_subject = False
        if matches:
            match = matches[-1]
            actor = match["actor"]
            other_actor = actor in {"供应商", "承包商", "他人", "第三方", "相关方"} or actor.startswith("其他")
            unknown_subject = actor not in LOCAL and not other_actor
            # 具体部门可能就是本行承担部门，未确认身份时不能默认本岗位或断言其他单位。
            statement.update(actor=actor, other_actor=other_actor, actor_basis=match[0],
                             actor_scope={"start": start + match.start(), "end": start + match.end(), "text": match[0]})
            # “并非本岗位”仅否定该承担者分句，不扩散到后面重新明确主体的肯定分句。
            if match["denial"] and start + match.start() >= statement["start"]:
                statement["negative"] = True
                statement["negative_scope"] = {"start": start + match.start(), "end": statement["predicate_start"], "text": match[0]}
        direct_prefix = source[statement["start"]:statement["predicate_start"]]
        negative = re.search(MODAL_NEGATIVE + r"(?:再|直接)?$", direct_prefix)
        if negative:
            statement["negative"] = True
            statement["negative_scope"] = {"start": statement["start"] + negative.start(), "end": statement["predicate_start"], "text": negative[0]}
        unknown_actor = re.search(CLAUSE_START + r"(?P<actor>[^对,，。;；]{0,25}(?:人员|单位|部门|岗位|公司|相关方))$", direct_prefix)
        direct_actor = any(start + match.start() >= statement["start"] for match in matches)
        scope_start = statement["actor_scope"]["start"] if direct_actor else statement["start"]
        # 后续明确承担者会重新限定作用范围；引述中的否定不属于本岗位的责任断言。
        scope_prefix = "".join("·" if any(left <= index < right for left, right in quotes) else source[index]
                               for index in range(scope_start, statement["predicate_start"]))
        uncertain = re.search(r"是否|可能|不一定|并非", scope_prefix)
        double_negative = re.search(r"(?:并非|不是)[^,，。;；]{0,30}(?:不|未|没有|无需|无须)", scope_prefix)
        unresolved_reasons = []
        if uncertain and not statement["negative"]:
            unresolved_reasons.append("uncertain_expression")
        if double_negative:
            unresolved_reasons.append("double_negative")
        if unknown_subject:
            unresolved_reasons.append("actor_identity")
        if unknown_actor and not direct_actor:
            unresolved_reasons.append("actor_scope")
        statement["scope_unresolved_reasons"] = unresolved_reasons
        statement["scope_unresolved"] = bool(unresolved_reasons)
        if statement["scope_unresolved"]:
            statement["unresolved_scope"] = {"start": statement["start"], "end": statement["predicate_start"], "text": direct_prefix}
        if statement["negative"] and not statement["scope_unresolved"] and not statement["quoted"] and not statement["other_actor"]:
            for role in statement["roles"]:
                facts["self_denials"].append({"start": statement["start"], "end": statement["end"], "text": statement["text"], "role": role})
    # 范围未确定的复合否定不充当后文明确撤销责任的依据。
    facts["self_denials"] = [denial for denial in facts["self_denials"] if not any(
        statement.get("scope_unresolved") and statement["start"] <= denial["start"] < statement["end"]
        for statement in facts["statements"])]
    for statement in facts["statements"]:
        statement["denied_later"] = [denial for denial in facts["self_denials"] if denial["start"] >= statement["end"]
                                     and (not denial["role"] or denial["role"] in statement["roles"])]
    facts["confirmation_version"] = "1.8.0"
    return facts


def _matrix_correspondence(row: Record, matrices: list[Record]) -> tuple[str, str, list[Record]]:
    """确定岗位行与矩阵的对应；row 为岗位行，matrices 为同范围矩阵记录。"""
    raw_mid = norm_text(row.value("measure_id")) if _readable(row, "measure_id") else ""
    mid = _reliable_measure_key(row)
    by_id = [matrix for matrix in matrices if _readable(matrix, "measure_id")
             and raw_mid and mid and _reliable_measure_key(matrix) == mid]
    if by_id:
        return ":".join(str(value) for value in mid), "measure_id", by_id
    text = _effective_text(row, "control_measure")
    if not text or text in {"无", "不涉及", "-", "/"}:
        return "", "control_measure_unavailable", []
    exact = [matrix for matrix in matrices if _effective_text(matrix, "control_measure") == text]
    ids = {_reliable_measure_key(matrix) for matrix in exact if _reliable_measure_key(matrix)}
    # 同文但存在两个真实编号时，无法判断属于哪条措施，不得合并职责组。
    if len(ids) > 1:
        return "", "control_measure_ambiguous", exact
    if not exact:
        return "", "control_measure_unmatched", []
    identifier = ":".join(str(value) for value in next(iter(ids))) if ids else "content:" + text
    return identifier, "control_measure_exact", exact


def _list_correspondence(row: Record, duties: list[Record]) -> tuple[str, str, list[Record]]:
    """确定三清单内部对应；row 为岗位行，duties 为同范围岗位职责记录。"""
    measure_key = _reliable_measure_key(row)
    if measure_key:
        return ":".join(str(value) for value in measure_key), "list_measure_id", []
    text = _effective_text(row, "control_measure")
    if not text or text in {"无", "不涉及", "-", "/"}:
        return "", "list_control_measure_unavailable", []
    same_text_keys = {
        _reliable_measure_key(duty)
        for duty in duties
        if _effective_text(duty, "control_measure") == text and _reliable_measure_key(duty)
    }
    # 缺号行只能回退到唯一同文编号，避免将同文的多个措施误合并。
    if len(same_text_keys) > 1:
        return "", "list_control_measure_ambiguous", []
    if same_text_keys:
        key = next(iter(same_text_keys))
        return ":".join(str(value) for value in key), "list_control_measure_exact", []
    return "content:" + text, "list_control_measure_exact", []


def roles_same_duty_v3(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """核查同职责人员分离；ctx 为审核上下文，p 沿用同职责能力的空参数配置。"""
    matrices = defaultdict(list)
    for matrix in ctx.all_records:
        if matrix.record_type == "matrix":
            matrices[_scope(matrix)].append(matrix)
    duties = defaultdict(list)
    for duty in ctx.all_records:
        if duty.record_type == "position_duty":
            duties[_scope(duty)].append(duty)
    out = []; prepared = []; originals = {}; bases = {}
    for row in ctx.records:
        originals[id(row)] = row
        explicit = norm_text(row.value("role")) if _readable(row, "role") else ""
        if not _readable(row, "role"):
            out.append(_issue(row, "review", "separation_field_unavailable", field="role",
                              unavailable_reason="角色字段未识别或公式没有可用缓存，仍须补充明确角色"))
        elif not explicit:
            out.append(_issue(row, "violation", "explicit_role_missing", field="role"))
        elif explicit not in {"经办", "审核", "审批"}:
            out.append(_issue(row, "violation", "invalid_role", actual_text=row.value("role")))
        role = explicit if explicit in {"经办", "审核", "审批"} else (_positive_role(row) if not explicit else "")
        if not role:
            continue
        valid_people = [person for person in row.person_keys if person.get("name")
                        and not re.search(r"等|…|\.{3}|待定", person["name"]) and person["name"] not in {"/", "-", "无"}]
        if not valid_people:
            missing = "person_names" not in row.fields
            out.append(_issue(row, "review", "separation_field_unavailable" if missing else "person_missing",
                              **({"field": "person_names"} if missing else {})))
        if not _scope_known(row):
            out.append(_issue(row, "review", "separation_scope_missing",
                              unavailable_reason="主体、业务或变体未确认，不能通过控制措施内容跨范围关联"))
            continue
        matrix_available = _scope_has_sheet_or_record(ctx, _scope(row), "matrix")
        if matrix_available:
            mid, basis, related = _matrix_correspondence(row, matrices[_scope(row)])
        else:
            mid, basis, related = _list_correspondence(row, duties[_scope(row)])
        if not mid:
            unavailable_reason = (
                "编号无法关联当前矩阵，控制措施有效全文也未建立唯一可靠对应，暂不能核实人员分离"
                if matrix_available
                else "三清单自身编号不可靠，控制措施有效全文也未建立唯一对应，暂不能核实人员分离"
            )
            out.append(_issue(row, "review", "separation_correspondence_unresolved", comparison_basis=basis,
                              original_measure_id=row.value("measure_id"), control_measure=row.value("control_measure"),
                              matrix_rows=[_location(matrix, "control_measure") for matrix in related],
                              unavailable_reason=unavailable_reason))
            continue
        # 只为分组创建替身；原材料编号和缺失角色证据均保持原样。
        clone = replace(row, fields={**row.fields, "measure_id": FieldValue(mid, mid, ""),
                                     "role": FieldValue(role, role, "")})
        prepared.append(clone); originals[id(clone)] = row
        bases[id(clone)] = (basis, related)
    reused = roles_same_duty_v2(replace(ctx, records=prepared), p)
    for item in reused:
        if item["evidence"]["issue_type"] not in {"same_duty_person_overlap", "separation_duty_missing"}:
            continue
        clone = item["record"]
        item["record"] = originals[id(clone)]
        basis, related = bases[id(clone)]
        item["evidence"].update(comparison_basis=basis, original_measure_id=item["record"].value("measure_id"),
                                matrix_rows=[_location(matrix, "control_measure") for matrix in related])
        out.append(item)
    return out


def reference_exists_v2(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """核查编号及适用性；ctx 为审核上下文，p 指定来源编号与目标矩阵类型和字段。"""
    grouped = defaultdict(list)
    for matrix in ctx.all_records:
        if matrix.record_type == p["target_record_type"]:
            grouped[_scope(matrix)].append(matrix)
    out = []
    for row in ctx.records:
        if row.record_type == "incompatible_position" and _no_incompatible_detail(row):
            continue
        reference = row.value(p["source_field"])
        if not _scope_known(row):
            out.append(_issue(row, "review", "reference_field_unavailable", reference=reference,
                              unavailable_reason="编号字段或主体、业务、变体不能可靠读取，暂不能对应当前矩阵"))
            continue
        scope = _scope(row)
        matrices = grouped.get(scope, [])
        # 只有目标载体真正未报送时才跳过；空表属于已报送但无可用记录。
        if not _scope_has_sheet_or_record(ctx, scope, p["target_record_type"]):
            continue
        if not _readable(row, p["source_field"]):
            out.append(_issue(row, "review", "reference_field_unavailable", reference=reference,
                              unavailable_reason="编号字段或主体、业务、变体不能可靠读取，暂不能对应当前矩阵"))
            continue
        if not norm_text(reference):
            out.append(_issue(row, "violation", "reference_missing", reference=reference))
            continue
        reference_key = measure_id_key(reference)
        matches = [matrix for matrix in matrices if _readable(matrix, p["target_field"])
                   and measure_id_key(matrix.value(p["target_field"])) == reference_key]
        if not matches:
            unavailable = not matrices or any(not _readable(matrix, p["target_field"]) for matrix in matrices)
            out.append(_issue(row, "review" if unavailable else "violation",
                              "reference_matrix_unavailable" if unavailable else "reference_not_found", reference=reference,
                              unavailable_reason="同主体、同业务、同变体的当前矩阵不存在或编号字段不完整" if unavailable else ""))
            continue
        interpretations = [interpret_record(matrix, ctx.resources) for matrix in matches]
        evidence = {"reference": reference, "matrix_rows": [_location(matrix, "applicability") for matrix in matches],
                    "applicability_facts": [value.to_dict() for value in interpretations]}
        decisions = {value.decision for value in interpretations}
        if any(value.state in {"source_missing", "source_unavailable"} for value in interpretations):
            category, kind, reason = "reference_applicability_unavailable", "review", "对应矩阵适用性字段缺失或公式没有可用缓存"
        elif decisions == {"applicable"}:
            continue
        elif decisions == {"not_applicable"}:
            category, kind, reason = "reference_not_applicable", "violation", "当前矩阵已明确本主体不适用该措施"
        else:
            category, kind, reason = "reference_applicability_unresolved", "review", "对应矩阵适用性为空、未知或重复矩阵填写矛盾，不能仅凭编号存在通过"
        out.append(_issue(row, kind, category, **evidence, unavailable_reason=reason if kind == "review" else "", reason=reason))
    return out


def responsibility_phrase_v7(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """核查标准责任表述；ctx 为审核上下文，p.attributes 为已确认责任属性词表。"""
    out = []
    previous = {id(item["record"]): item for item in responsibility_phrase_v6(ctx, p)}
    for row in ctx.records:
        item = previous.get(id(row))
        if not _readable(row, "duty"):
            out.append(item)
            continue
        if not norm_text(row.value("duty")):
            continue
        facts = _responsibility_facts(row.value("duty"), tuple(p["attributes"]))
        positive = _own_responsibility(facts)
        # 明确肯定本岗位主体/templates/审批责任即可，不额外强制对象属性或“性”字。
        if not contradiction(facts) and any((statement["qualities"] and statement["object_present"])
                                            or set(statement["roles"]) & {"主体责任", "审核责任", "审批责任"}
                                            for statement in positive):
            continue
        if item is None:
            category = "responsibility_self_contradiction" if contradiction(facts) else (
                "responsibility_other_actor" if any(statement["other_actor"] for statement in facts["statements"]) else "responsibility_assertion_missing")
            item = _issue(row, "violation", category, field="duty")
        evidence = item["evidence"]
        if any(statement.get("scope_unresolved") for statement in facts["statements"]):
            item["kind"] = "review"
            evidence.update(issue_type="responsibility_structure_unresolved", unavailable_reason="职责分句的承担者或否定作用范围尚不能确定", interpretation_status="unsupported")
        evidence["responsibility_facts"] = facts
        evidence["source_cell"] = row.fields["duty"].coordinate
        evidence["original_issue_type"] = evidence["issue_type"]
        evidence["issue_type"] = "responsibility_phrase_missing"
        out.append(item)
    return out


def value_mapping_v4(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """核查明确角色对应责任；ctx 为审核上下文，p 包含角色字段、职责字段和责任映射。"""
    # 缺失明确角色由第8条报告，第11条不得重复生成独立角色一致性意见。
    out = []
    mapped = {role_type for types in p["mapping"].values() for role_type in types}
    for row in ctx.records:
        if not _readable(row, p["source_field"]) or not norm_text(row.value(p["source_field"])):
            continue
        role = norm_text(row.value(p["source_field"]))
        expected = p["mapping"].get(role)
        if expected is None:
            out.append(_issue(row, "review", "responsibility_role_unresolved", field=p["source_field"],
                              unavailable_reason="明确填写的角色不在经办、审核、审批范围内"))
            continue
        if not _readable(row, p["text_field"]):
            out.append(_issue(row, "review", "responsibility_unavailable", field=p["text_field"],
                              unavailable_reason="岗位职责字段未识别或公式没有可用缓存"))
            continue
        if not norm_text(row.value(p["text_field"])):
            continue
        facts = _responsibility_facts(row.value(p["text_field"]))
        actual = sorted({role_type for statement in _own_responsibility(facts) for role_type in statement["roles"] if role_type in mapped})
        represented = {role_type for statement in facts["statements"] for role_type in statement["roles"]}
        unsupported = set(facts["role_mentions"]) - represented
        kind = "violation"
        if contradiction(facts):
            category = "responsibility_self_contradiction"
        elif any(statement.get("scope_unresolved") for statement in facts["statements"]):
            category, kind = "responsibility_role_unresolved", "review"
        elif any(statement["other_actor"] for statement in facts["statements"]) and not _own_responsibility(facts):
            category = "responsibility_other_actor"
        elif facts["malformed_spans"] and not actual:
            category = "responsibility_expression_malformed"
        elif unsupported or (any(statement["delegated"] for statement in facts["statements"]) and not actual):
            category, kind = "responsibility_role_unresolved", "review"
        elif len(actual) > 1:
            category, kind = "responsibility_multiple_types", "review"
        elif actual and any(role_type in expected for role_type in actual):
            continue
        else:
            category = "responsibility_role_conflict" if actual else "responsibility_role_missing"
        out.append(_issue(row, kind, category, role=role, expected="/".join(expected),
                          field=p["text_field"], source_cell=row.fields[p["text_field"]].coordinate,
                          actual_responsibilities=actual, responsibility_facts=facts,
                          interpretation_status="unsupported" if kind == "review" else "decided",
                          unavailable_reason="责任类型同时存在多个或有未支持的表达，暂不能确定本岗位对应责任" if kind == "review" else ""))
    return out


def deleted_text_reappears_v3(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """核查删除原文再次使用；ctx 为含不相容清单的上下文，p 指定来源和职责目标字段。"""
    sources = defaultdict(list)
    for source in ctx.all_records:
        if source.record_type in {"matrix", "deleted_matrix"}:
            field = source.fields.get("measure_id")
            raw_mid = norm_text(field.current or (str(field.raw) if field.raw is not None else "")) if field else ""
            mid = measure_id_key(raw_mid)
            sources[(*_scope(source), mid)].append(source)
    rows = []
    for row in ctx.records:
        raw_mid = norm_text(row.value("measure_id")) if _readable(row, "measure_id") else ""
        mid = measure_id_key(raw_mid)
        # 编号漏填或没有当前同范围编号，不在第13条重复提示，由第5/9条单独核查。
        if not raw_mid:
            continue
        if _scope_known(row) and not sources[(*_scope(row), mid)]:
            continue
        rows.append(row)
    return deleted_text_reappears_v2(replace(ctx, records=rows), {**p, "match_scope": "measure"})


def ordered_records_v3(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """逐行报告排序问题；ctx 为清单审核上下文，p.field 为控制措施排序字段。"""
    from risk_audit.checks.registry import ordered_records

    old = ordered_records(ctx, p)
    out = []; reported = set()
    for row in ctx.records:
        baseline = ctx.baselines.get((row.business_id or row.business_code, row.variant_id), {}).get("measure_order", [])
        mid = measure_id_key(row.value(p["field"]))
        # 基准存在时优先该基准；不能定位的编号待核实，不把自然序号掺入基准排序。
        if baseline and norm_text(row.value(p["field"])) and mid not in {measure_id_key(value) for value in baseline}:
            out.append(_issue(row, "review", "order_reference_unresolved", reason="控制措施编号无法在可用基准顺序中定位，顺序待核实",
                              measure_id=row.value(p["field"]), related_file=row.file_path, related_sheet=row.sheet, related_row=row.row,
                              problem_locations=[_location(row, p["field"])], source_cell=row.fields[p["field"]].coordinate,
                              unavailable_reason="可用基准未包含本行编号，暂不能确定其相对排序位置"))
    for item in old:
        source = item["record"]; reason = item["evidence"]["reason"]
        baseline_order = ctx.baselines.get((source.business_id or source.business_code, source.variant_id), {}).get("measure_order", [])
        if reason == "措施顺序倒置" and baseline_order and measure_id_key(source.value(p["field"])) not in {measure_id_key(value) for value in baseline_order}:
            # 所有明细都不在基准时，旧版会退回自然排序；新版仅保留基准定位待核实。
            continue
        rows = sorted([row for row in ctx.records if (row.file_path, row.sheet) == (source.file_path, source.sheet)], key=lambda row: row.row)
        mid = measure_id_key(source.value(p["field"]))
        numbered = [row for row in rows if norm_text(row.value(p["field"]))]
        if reason == "同一措施记录不连续":
            matching = [row for row in numbered if measure_id_key(row.value(p["field"])) == mid]
            involved = [row for row in rows if matching[0].row <= row.row <= matching[-1].row]
        else:
            blocks = []
            for row in numbered:
                identifier = measure_id_key(row.value(p["field"]))
                if not blocks or measure_id_key(blocks[-1][0].value(p["field"])) != identifier:
                    blocks.append([])
                blocks[-1].append(row)
            baseline = ctx.baselines.get((source.business_id or source.business_code, source.variant_id), {}).get("measure_order", [])
            known = {measure_id_key(value) for value in baseline}
            filtered = [block for block in blocks if measure_id_key(block[0].value(p["field"])) in known] if any(measure_id_key(block[0].value(p["field"])) in known for block in blocks) else blocks
            rank = {measure_id_key(value): index for index, value in enumerate(baseline)}
            # 旧能力将重复编号倒序都落在第一次出现的行；这里还原每一个实际倒序位置。
            groups = []
            for index in range(1, len(filtered)):
                previous_id = measure_id_key(filtered[index - 1][0].value(p["field"]))
                current_id = measure_id_key(filtered[index][0].value(p["field"]))
                current_key = rank[current_id] if current_id in rank else natural_key(filtered[index][0].value(p["field"]))
                previous_key = rank[previous_id] if previous_id in rank else natural_key(filtered[index - 1][0].value(p["field"]))
                if current_id == mid and current_key < previous_key:
                    groups.append([*filtered[index - 1], *filtered[index]])
        if reason == "同一措施记录不连续":
            groups = [involved]
        for involved in groups:
            locations = [_location(row, p["field"]) for row in involved]
            # 同一原因只在相关行逐行输出一次，其他行同时提供对方坐标便于改正。
            for row in involved:
                key = (row.file_path, row.sheet, row.row, reason, tuple(location["row"] for location in locations))
                if key in reported:
                    continue
                reported.add(key)
                other = next((candidate for candidate in involved if candidate is not row), row)
                out.append({"record": row, "kind": item["kind"], "evidence": {**item["evidence"],
                            "measure_id": row.value(p["field"]), "problem_locations": locations,
                            "related_file": other.file_path, "related_sheet": other.sheet, "related_row": other.row,
                            "source_cell": row.fields[p["field"]].coordinate if p["field"] in row.fields else ""}})
    return out


def measure_numbers(text: str) -> tuple[int, ...] | tuple[str, str]:
    """提取编号数字；text 为措施编号，直接复用全局统一的编号规则。"""
    return measure_id_key(text)


def field_constraints_v4(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """检查具体部门岗位；ctx 为材料上下文，p 为泛称及分隔符配置，支持岗位C。"""
    out = field_constraints_v2(ctx, p)
    if p['field'] == 'position':
        rows = [row for row in ctx.records if row.record_type == 'incompatible_position'
                and 'position_c' in row.fields and not _no_incompatible_detail(row)]
        out.extend(field_constraints_v2(replace(ctx, records=rows), {**p, 'field': 'position_c'}))
    return out


def field_constraints_v5(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """按包含关键词检查具体部门岗位；ctx 为材料上下文，p 为关键词、分隔符和精确例外。"""
    issues = field_constraints_v4(ctx, p)
    exceptions = {norm_text(value) for value in p.get("exact_exceptions", [])}
    if not exceptions:
        return issues

    out = []
    for item in issues:
        field = item["evidence"].get("field", p["field"])
        value = item["record"].fields.get(field)
        # 新规则仅放行整格精确例外；带前后缀的泛称仍按关键词检查。
        exact_exception = value is not None and norm_text(value.current) in exceptions
        if exact_exception and item["evidence"].get("issue_type") == "field_not_concrete":
            continue
        out.append(item)
    return out


def field_constraints_v6(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """按前缀或包含例外放行具体性意见；ctx 为材料上下文，p 为关键词及三类例外配置。"""
    issues = field_constraints_v5(ctx, p)
    prefix_exceptions = tuple(norm_text(value) for value in p.get("prefix_exceptions", []))
    contains_exceptions = tuple(norm_text(value) for value in p.get("contains_exceptions", []))
    if not prefix_exceptions and not contains_exceptions:
        return issues

    out = []
    for item in issues:
        if item["evidence"].get("issue_type") != "field_not_concrete":
            # 名称例外只影响具体性，多个岗位同格填写仍按原规则提示拆分。
            out.append(item)
            continue
        field = item["evidence"].get("field", p["field"])
        value = item["record"].fields.get(field)
        text = norm_text(value.current) if value is not None else ""
        prefix_match = any(text.startswith(prefix) for prefix in prefix_exceptions)
        contains_match = any(exception in text for exception in contains_exceptions)
        if not prefix_match and not contains_match:
            out.append(item)
    return out


def roles_same_duty_v4(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """按完整同职责行检查分离；ctx 为清单上下文，p 为能力参数，仅保留同部门同完整姓名的冲突。"""
    issues = roles_same_duty_v3(ctx, p)
    locations = {(row.file_path, row.sheet, row.row): row for row in ctx.records}
    out = []
    for item in issues:
        if item['evidence']['issue_type'] != 'same_duty_person_overlap':
            out.append(item)
            continue
        row = item['record']; evidence = item['evidence']
        other = locations[(evidence['related_file'], evidence['related_sheet'], evidence['related_row'])]
        missing = next((field for field in ('department', 'person_names')
                        if any(not _readable(value, field) or not norm_text(value.value(field)) for value in (row, other))), '')
        if missing:
            out.append(_issue(row, 'review', 'separation_field_unavailable', field=missing,
                              unavailable_reason='同人职责分离检查缺少可读取的部门或完整姓名'))
            continue
        # 仅重合一个姓名、部门不同，不能满足新版要求的整行一致条件。
        if any(norm_text(row.value(field)) != norm_text(other.value(field)) for field in ('department', 'person_names')):
            continue
        out.append(item)
    return out


def responsibility_phrase_v8(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """检查国网标准责任句式；ctx 为职责上下文，p.attributes 为可接受的责任属性。"""
    out = []
    for row in ctx.records:
        if not _readable(row, 'duty'):
            out.append(_issue(row, 'review', 'responsibility_unavailable', field='duty',
                              unavailable_reason='岗位职责字段缺失或公式没有可用缓存'))
            continue
        if not norm_text(row.value('duty')):
            continue
        facts = _responsibility_facts(row.value('duty'), tuple(p['attributes']))
        # 第10条只检查句式骨架；具名岗位主体的身份未关联不影响句式成立。
        phrase_statements = [statement for statement in own_statements(facts)
                             if set(statement.get('scope_unresolved_reasons', [])) <= {'actor_identity'}]
        # 明确责任属性和肯定承担责任须在同一责任表述中，不接受孤立的“负主体责任”。
        if not contradiction(facts) and any(statement['qualities'] for statement in phrase_statements):
            continue
        out.append(_issue(row, 'violation', 'responsibility_phrase_missing', field='duty', responsibility_facts=facts))
    return out


def responsibility_phrase_v9(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """按0917-9双条件检查岗位职责；ctx 为职责上下文，p.attributes 为允许的责任属性。"""
    out = []
    for row in ctx.records:
        if not _readable(row, "duty"):
            out.append(_issue(row, "review", "responsibility_unavailable", field="duty",
                              unavailable_reason="岗位职责字段缺失或公式没有可用缓存"))
            continue
        duty = norm_text(row.value("duty"))
        if not duty:
            continue
        # 0917-9 只要求两类关键词同时出现，不再校验句式结构和词语间的对应关系。
        attributes = [attribute for attribute in p["attributes"] if norm_text(attribute) in duty]
        responsibility_types = [value for value in RESPONSIBILITY_TYPES if value in duty]
        if attributes and responsibility_types:
            continue
        out.append(_issue(
            row,
            "violation",
            "responsibility_phrase_missing",
            field="duty",
            responsibility_attributes=attributes,
            responsibility_types=responsibility_types,
            fixed_rule_0917_9=True,
        ))
    return out


def value_mapping_v5(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """检查明确角色与责任类型；ctx 为职责上下文，p 为字段和对应关系，匹配任一肯定责任即可。"""
    rows = []
    for row in ctx.records:
        role = norm_text(row.value(p['source_field']))
        expected = set(p['mapping'].get(role, []))
        facts = _responsibility_facts(row.value(p['text_field']))
        actual = {value for statement in _own_responsibility(facts) for value in statement['roles']}
        if expected & actual and not contradiction(facts):
            continue
        rows.append(row)
    return value_mapping_v4(replace(ctx, records=rows), p)


def _duplicate_duty_key(text: str) -> str:
    """生成职责判重键；text 为岗位职责原文，仅应用已确认且可解释的等价规则。"""
    key = norm_text(text)
    for source, target in DUPLICATE_DUTY_EQUIVALENTS:
        key = key.replace(source, target)
    # “负有/负/承担审核责任”等肯定承担句式只统一谓语，否定词和责任类型仍保留。
    key = re.sub(r'(?:负有?|承担|担负|具有)(?=(?:主体|审核|审批|复核)责任)', '负', key)
    return DUPLICATE_DUTY_CONNECTOR.sub('、', key)


def duplicate_duties(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """筛查岗位重复项；ctx 为岗位上下文，p 为保留的空配置，按主体业务及完整六字段隔离。"""
    groups = defaultdict(list)
    fields = ('measure_id', 'duty', 'department', 'position', 'person_names', 'role')
    for row in ctx.records:
        if not _scope_known(row) or any(not _readable(row, field) or not norm_text(row.value(field)) for field in fields):
            continue
        values = tuple(_duplicate_duty_key(row.value(field)) if field == 'duty' else
                       measure_id_key(row.value(field)) if field == 'measure_id' else norm_text(row.value(field))
                       for field in fields)
        groups[(*_scope(row), *values)].append(row)
    return [_issue(row, 'violation', 'duplicate_duty', duplicate_locations=[_location(other) for other in rows if other is not row])
            for rows in groups.values() if len(rows) > 1 for row in rows]


def duplicate_duties_v2(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """在同一文件同一工作表内筛查重复项；ctx 为岗位上下文，p 为保留的空配置。"""
    groups = defaultdict(list)
    fields = ('measure_id', 'duty', 'department', 'position', 'person_names', 'role')
    for row in ctx.records:
        if not _scope_known(row) or any(
            not _readable(row, field) or not norm_text(row.value(field))
            for field in fields
        ):
            continue
        values = tuple(
            _duplicate_duty_key(row.value(field)) if field == 'duty'
            else measure_id_key(row.value(field)) if field == 'measure_id'
            else norm_text(row.value(field))
            for field in fields
        )
        # “同一个 sheet”同时要求来源文件一致，避免同名工作表跨文件判重。
        groups[(row.file_path, row.sheet, *_scope(row), *values)].append(row)
    return [
        _issue(
            row,
            'violation',
            'duplicate_duty',
            duplicate_locations=[_location(other) for other in rows if other is not row],
        )
        for rows in groups.values()
        if len(rows) > 1
        for row in rows
    ]


def missing_duty_measures(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """列出清单未覆盖的矩阵编号；ctx 为矩阵上下文，p 为保留空配置，数字匹配不跨主体业务。"""
    present = {(*_scope(row), measure_numbers(row.value('measure_id'))) for row in ctx.all_records
               if row.record_type == 'position_duty' and _readable(row, 'measure_id') and norm_text(row.value('measure_id'))}
    return [_issue(row, 'review', 'missing_duty_measure', measure_id=row.value('measure_id'))
            for row in ctx.records if _scope_has_sheet_or_record(ctx, _scope(row), 'position_duty')
            and _scope_known(row) and _readable(row, 'measure_id') and norm_text(row.value('measure_id'))
            and (*_scope(row), measure_numbers(row.value('measure_id'))) not in present]


def system_rule_changes_v1(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """核查矩阵控制系统修订对应的系统规则；ctx 为矩阵上下文，p 为保留配置参数。"""
    indexed = defaultdict(list)
    for row in ctx.all_records:
        if row.record_type != 'system_rule' or not _scope_known(row):
            continue
        if _readable(row, 'measure_id'):
            indexed[(*_scope(row), measure_numbers(row.value('measure_id')))].append(row)
    out = []
    for row in ctx.records:
        # 未报送系统规则表时跳过；已报送空表时仍需核对矩阵修订。
        if not _scope_has_sheet_or_record(ctx, _scope(row), 'system_rule'):
            continue
        system = row.fields.get('control_system')
        # 只有矩阵控制系统出现红字或删除线时，才启用第七条例外核查。
        if not system or not (system.red_spans or system.deleted_spans):
            continue
        matches = indexed.get((*_scope(row), measure_numbers(row.value('measure_id'))), [])
        matrix_name = norm_text(row.value('control_system'))
        names = [norm_text(match.value('system_name')) for match in matches if _readable(match, 'system_name')]
        consistent = bool(matrix_name and any(name == matrix_name or name in matrix_name or matrix_name in name for name in names))
        if not matches or not consistent:
            out.append(_issue(row, 'review', 'system_rule_change_mismatch', measure_id=row.value('measure_id'),
                              matrix_system=matrix_name, system_rule_names=names))
    return out


def _has_exact_system_type_header(sheet: Any) -> bool:
    """判断原始表头是否精确包含“系统类型”。

    sheet 为已解析的逻辑工作表；有原始表头路径时以叶子名称为准，
    仅在测试或旧调用方没有保留路径时回退到逻辑列映射。
    """
    if not sheet.business_header_paths:
        return "system_type" in sheet.columns
    expected = norm_text("系统类型")
    return any(
        path.get("path") and norm_text(path["path"][-1].get("name", "")) == expected
        for path in sheet.business_header_paths
    )


def system_type_header_v1(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """核查系统规则清单的“系统类型”表头。

    ctx 为整包审核上下文，p 为保留的空配置参数；每个缺少精确原始表头
    “系统类型”的系统规则逻辑区仅返回一条行级意见。
    """
    issues = []
    for source_file in ctx.files:
        for sheet_index, sheet in enumerate(source_file.sheets):
            if sheet.sheet_type != "system_rule" or _has_exact_system_type_header(sheet):
                continue
            first_record = sheet.records[0] if sheet.records else None
            location_row = (
                first_record.row
                if first_record
                else sheet.first_data_row or max(sheet.header_rows, default=2) + 1
            )
            # 始终使用区域级记录：并排非空区的首条明细可能位于同一物理行。
            record = Record(
                record_type="system_rule",
                entity_code=source_file.entity_code or "",
                business_code=source_file.business_code or "",
                variant_id=source_file.variant_id,
                file_path=str(source_file.relative_path),
                sheet=sheet.title,
                row=location_row,
                fields=first_record.fields if first_record else {},
                # 逻辑区序号区分同名、同行的并排区域，避免引擎去重。
                record_id=(
                    f"system-type-header:{source_file.relative_path}:"
                    f"{sheet_index}:{sheet.title}:{location_row}"
                ),
                business_id=source_file.business_id or "",
            )
            # 缺表头属于整张清单的确定性问题，不逐明细行重复提示。
            issues.append(_issue(record, "violation", "system_type_header_missing", missing_fields=["system_type"]))
    return issues


def system_type_completion_v1(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """提示无法自动判断系统类型的系统规则记录。

    Args:
        ctx: 当前系统规则检查上下文。
        p: 规则包保留的空配置参数。
    """

    from risk_audit.system_type_preprocessing import (
        SYSTEM_TYPE_THIRD,
        classify_system_owner,
    )

    opinion = (
        "请补充系统类型，该列填报枚举值：一级部署系统、二级部署系统、"
        "三级部署系统，请根据系统的实际情况填报。"
    )
    issues = []
    for row in ctx.records:
        # 三级部署系统是文档明确要求优先保留的人工结论，不再要求管理主体可推断。
        if norm_text(row.value("system_type")) == SYSTEM_TYPE_THIRD:
            continue
        if classify_system_owner(row.value("system_owner")) is not None:
            continue
        issues.append(_issue(row, "violation", "system_type_unresolved", opinion=opinion))
    return issues


def measure_applicability_alignment_v1(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """核查岗位编号存在性与矩阵适用性；ctx 为矩阵上下文，p 为保留配置参数。"""
    present = {(*_scope(row), measure_numbers(row.value('measure_id'))) for row in ctx.all_records
               if row.record_type == 'position_duty' and _scope_known(row)
               and _readable(row, 'measure_id') and norm_text(row.value('measure_id'))}
    out = []
    for row in ctx.records:
        if not _scope_has_sheet_or_record(ctx, _scope(row), 'position_duty'):
            continue
        if not _scope_known(row) or not _readable(row, 'measure_id') or not norm_text(row.value('measure_id')):
            continue
        exists = (*_scope(row), measure_numbers(row.value('measure_id'))) in present
        applicability = interpret_record(row, ctx.resources)
        # 字段缺失或公式不可读取属于资料结构问题，由字段检查统一提示，不能逐行判为适用性矛盾。
        if applicability.state in {'source_missing', 'source_unavailable'}:
            continue
        if (exists and applicability.decision == 'applicable') or (not exists and applicability.decision == 'not_applicable'):
            continue
        out.append(_issue(row, 'review', 'measure_applicability_mismatch', measure_id=row.value('measure_id'),
                          measure_present=exists, applicability_text=row.value('applicability'),
                          applicability_state=applicability.state, applicability_decision=applicability.decision))
    return out


def measure_applicability_alignment_v2(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """按0920-2四态口径核查岗位编号与矩阵适用性；ctx 为矩阵上下文，p 为保留配置参数。"""
    _ = p
    present = {
        (*_scope(row), measure_numbers(row.value('measure_id')))
        for row in ctx.all_records
        if row.record_type == 'position_duty'
        and _scope_known(row)
        and _readable(row, 'measure_id')
        and norm_text(row.value('measure_id'))
    }
    out = []
    for row in ctx.records:
        if not _scope_known(row) or not _readable(row, 'measure_id') or not norm_text(row.value('measure_id')):
            continue
        exists = (*_scope(row), measure_numbers(row.value('measure_id'))) in present
        applicability = interpret_record(row, ctx.resources)
        # 字段缺失或公式不可读取属于资料结构问题，由字段检查统一提示。
        if applicability.state in {'source_missing', 'source_unavailable'}:
            continue
        # 有编号时仅“适用”通过；无编号时仅“适用”需要再次核实。
        requires_review = (
            applicability.decision != 'applicable'
            if exists
            else applicability.decision == 'applicable'
        )
        if not requires_review:
            continue
        out.append(_issue(
            row,
            'review',
            'measure_applicability_mismatch',
            measure_id=row.value('measure_id'),
            measure_present=exists,
            applicability_text=row.value('applicability'),
            applicability_state=applicability.state,
            applicability_decision=applicability.decision,
        ))
    return out


def measure_applicability_alignment_v3(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """按0921-1口径核查岗位编号与矩阵适用性。

    ctx 为矩阵审核上下文，p 为保留的空配置参数；已有岗位编号且
    适用性为空时不生成本条意见，其余组合按文档规则判定。
    """
    _ = p
    present = {
        (*_scope(row), measure_numbers(row.value('measure_id')))
        for row in ctx.all_records
        if row.record_type == 'position_duty'
        and _scope_known(row)
        and _readable(row, 'measure_id')
        and norm_text(row.value('measure_id'))
    }
    out = []
    for row in ctx.records:
        if not _scope_known(row) or not _readable(row, 'measure_id') or not norm_text(row.value('measure_id')):
            continue
        exists = (*_scope(row), measure_numbers(row.value('measure_id'))) in present
        applicability = interpret_record(row, ctx.resources)
        # 字段缺失或公式不可读仍由字段检查统一提示。
        if applicability.state in {'source_missing', 'source_unavailable'}:
            continue
        if exists:
            # 0921-1明确将空值从已有岗位编号的核实意见中排除。
            requires_review = (
                applicability.state != 'empty'
                and applicability.decision != 'applicable'
            )
        else:
            requires_review = applicability.decision == 'applicable'
        if not requires_review:
            continue
        out.append(_issue(
            row,
            'review',
            'measure_applicability_mismatch',
            measure_id=row.value('measure_id'),
            measure_present=exists,
            applicability_text=row.value('applicability'),
            applicability_state=applicability.state,
            applicability_decision=applicability.decision,
        ))
    return out


def responsibility_department_alignment_v1(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """核查矩阵责任关键词与岗位部门；ctx 为矩阵上下文，p.keyword_mappings 为确认的关键词同义表。"""
    duties = defaultdict(list)
    for row in ctx.all_records:
        if row.record_type == 'position_duty' and _scope_known(row) and _readable(row, 'measure_id'):
            duties[(*_scope(row), measure_numbers(row.value('measure_id')))].append(row)
    mappings = {norm_text(key): [norm_text(value) for value in values]
                for key, values in p.get('keyword_mappings', {}).items()}
    out = []
    for row in ctx.records:
        if not _scope_has_sheet_or_record(ctx, _scope(row), 'position_duty'):
            continue
        responsibility = norm_text(row.value('responsibility'))
        required = [key for key in mappings if key and key in responsibility]
        if not required:
            continue
        departments = [norm_text(item.value('department')) for item in duties.get(
            (*_scope(row), measure_numbers(row.value('measure_id'))), []) if _readable(item, 'department')]
        missing = [key for key in required if not any(
            synonym and synonym in department for synonym in mappings[key] for department in departments)]
        if missing:
            out.append(_issue(row, 'review', 'responsibility_department_mismatch', measure_id=row.value('measure_id'),
                              missing_keywords=missing, duty_departments=departments))
    return out


def ordered_records_v4(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    """核验清单数字顺序及连续性；ctx 为清单上下文，p.field 为编号字段，与排序预处理使用同一基准。"""
    grouped = defaultdict(list); out = []; reported = set()
    for row in ctx.records:
        text = row.value(p['field'])
        if _no_incompatible_detail(row) or not _readable(row, p['field']) or not norm_text(text) or re.match(r'^(?:填表说明|填报说明|说明[:：]|注[:：]|合计)', text):
            continue
        grouped[(row.file_path, row.sheet)].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: row.row)
        rank = {measure_numbers(value): index for index, value in enumerate(ctx.baselines.get((rows[0].business_id or rows[0].business_code, rows[0].variant_id), {}).get('measure_order', []))}
        blocks = []
        for row in rows:
            key = measure_numbers(row.value(p['field']))
            if not blocks or blocks[-1][0] != key:
                blocks.append((key, []))
            blocks[-1][1].append(row)
            if rank and key not in rank:
                out.append(_issue(row, 'review', 'order_reference_unresolved', reason='控制措施编号无法在可用基准顺序中定位，顺序待核实',
                                  measure_id=row.value(p['field']), related_file=row.file_path, related_sheet=row.sheet, related_row=row.row,
                                  unavailable_reason='指定基准没有本行编号，暂不能核实基准位置'))
        relevant = [(key, block) for key, block in blocks if not rank or key in rank]
        problems = []
        for index in range(1, len(relevant)):
            previous, left = relevant[index - 1]; current, right = relevant[index]
            left_key = rank[previous] if rank else (0, previous) if all(isinstance(value, int) for value in previous) else (1, natural_key(left[0].value(p['field'])))
            right_key = rank[current] if rank else (0, current) if all(isinstance(value, int) for value in current) else (1, natural_key(right[0].value(p['field'])))
            if right_key < left_key:
                problems.append(('措施顺序倒置', [*left, *right], {}))
        positions = defaultdict(list)
        for index, (key, block) in enumerate(blocks):
            positions[key].append(index)
        for indices in positions.values():
            if len(indices) > 1:
                duplicate_blocks = [blocks[index][1] for index in indices]
                involved = [row for block in duplicate_blocks for row in block]
                related_rows = {}
                for block_index, block in enumerate(duplicate_blocks):
                    # 关联行必须来自另一个编号块，才能准确说明两段记录被其它编号分隔。
                    counterpart = duplicate_blocks[(block_index + 1) % len(duplicate_blocks)][0]
                    related_rows.update({id(row): counterpart for row in block})
                problems.append(('同一措施记录不连续', involved, related_rows))
        for reason, involved, related_rows in problems:
            for row in involved:
                key = (row.file_path, row.sheet, row.row, reason)
                if key in reported:
                    continue
                reported.add(key); other = related_rows.get(id(row)) or next(value for value in involved if value is not row)
                out.append(_issue(row, 'violation', 'measure_order', reason=reason, measure_id=row.value(p['field']),
                                  related_file=other.file_path, related_sheet=other.sheet, related_row=other.row,
                                  problem_locations=[_location(value, p['field']) for value in involved]))
    return out
