"""《重庆公司 1015 审核确认稿》的专项审核能力。"""

from __future__ import annotations

from collections import defaultdict
from itertools import product
import re
from typing import Any

from risk_audit.checks.registry import CheckContext
from risk_audit.models import Record
from risk_audit.util import measure_id_key, norm_text


SYSTEM_MODES = {norm_text("系统待建"), norm_text("系统已建")}
VALID_ROLES = {"经办", "审核", "审批", "监督"}
ROLE_RESPONSIBILITIES = {
    "经办": "主体责任",
    "审核": "审核责任",
    "审批": "审批责任",
    "监督": "监督责任",
}
QUALITY_TERMS = ("一致性", "准确性", "真实性", "完整性", "有效性", "及时性", "合规性")
RESPONSIBILITY_TYPES = tuple(ROLE_RESPONSIBILITIES.values())
INCOMPATIBLE_EMPTY_VALUES = {"", "无", "不涉及", "-", "/"}
RESPONSIBILITY_ITEM_SEPARATOR = re.compile(r"[、，,;；\n]+")
SYSTEM_NAME_SEPARATOR = re.compile(r"[、，,;；/\n]+")
RESPONSIBILITY_SEGMENT_SEPARATOR = re.compile(r"[-—－]", re.UNICODE)


def _issue(record: Record, issue_type: str, opinion: str, **evidence: Any) -> dict[str, Any]:
    """构造可直接渲染的重庆专项审核问题。

    Args:
        record: 意见需要写回的原始业务记录。
        issue_type: 稳定的问题类型。
        opinion: Word 确认稿规定的整改提示正文。
        evidence: 用于去重、统计和复核的附加证据。

    Returns:
        供规则引擎消费的结构化问题。
    """

    return {
        "record": record,
        "kind": "violation",
        "evidence": {"issue_type": issue_type, "opinion": opinion, **evidence},
    }


def _scope_key(record: Record) -> tuple[str, str, str]:
    """返回跨表核对的主体、业务和变体边界。

    Args:
        record: 当前矩阵或清单记录。

    Returns:
        不允许跨越的主体、业务和变体组合。
    """

    return record.entity_code, record.business_id or record.business_code, record.variant_id


def _measure_key(record: Record) -> tuple[Any, ...]:
    """提取控制措施编号中的数字比较键。

    Args:
        record: 含控制措施编号的矩阵或清单记录。

    Returns:
        仅由编号数字构成的稳定比较键；无法提取时为空元组。
    """

    key = measure_id_key(record.value("measure_id"))
    # 确认稿只以编号中的数字比对；空白和纯文字占位值不建立关联关系。
    return key if key and all(isinstance(value, int) for value in key) else ()


def _separation_measure_key(record: Record) -> tuple[Any, ...]:
    """生成第6条人员分离检查的措施关联键。

    Args:
        record: 岗位内控责任清单记录。

    Returns:
        优先使用编号数字；编号不可用时使用有效控制措施正文。
    """

    key = _measure_key(record)
    if key:
        return ("number", *key)
    control_measure = norm_text(record.value("control_measure"))
    if control_measure and control_measure not in {"无", "不涉及", "-", "/", "待补充", "待定"}:
        return "text", control_measure
    return ()


def _role(record: Record, infer_from_duty: bool = True) -> str:
    """读取明确角色，并按规则版本决定是否从责任类型推定。

    Args:
        record: 岗位内控责任清单记录。
        infer_from_duty: 明确角色为空时，是否允许根据职责中的责任类型推定。

    Returns:
        经办、审核、审批或监督；无法唯一确认时返回空字符串。
    """

    explicit = norm_text(record.value("role"))
    if explicit:
        return explicit if explicit in VALID_ROLES else ""
    if not infer_from_duty:
        return ""
    duty = norm_text(record.value("duty"))
    matched = [role for role, responsibility in ROLE_RESPONSIBILITIES.items() if responsibility in duty]
    return matched[0] if len(matched) == 1 else ""


def _person_names(record: Record, placeholder_terms: tuple[str, ...] = ()) -> set[str]:
    """返回岗位行中可核对的人员姓名集合。

    Args:
        record: 岗位内控责任清单记录。
        placeholder_terms: 规则版本新增的人员占位关键词。

    Returns:
        已归一化且去除占位值的姓名集合。
    """

    names = {
        norm_text(person.get("person_id") or person.get("name") or "")
        for person in record.person_keys
    }
    if not names:
        names = {
            norm_text(name)
            for name in RESPONSIBILITY_ITEM_SEPARATOR.split(record.value("person_names"))
        }
    return {
        name
        for name in names
        if name
        and name not in {"无", "-", "/", "待定"}
        and not any(term and term in name for term in placeholder_terms)
    }


def _duty_signature(value: str) -> str:
    """移除职责中的责任类型差异，保留其余完整正文。

    Args:
        value: 岗位职责正文。

    Returns:
        用于比对经办、审核和监督同职责的归一化文本。
    """

    return re.sub(r"(?:主体|审核|监督)(?=责任)", "角色", norm_text(value))


def duty_separation(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """检查经办与审核、经办与监督的同人同职责冲突。

    Args:
        ctx: 当前主体业务内的岗位清单审核上下文。
        params: 可控制是否从职责反推角色，并配置人员占位关键词。

    Returns:
        角色缺失、角色非法、人员缺失及同人冲突问题。
    """

    infer_role_from_duty = bool(params.get("infer_role_from_duty", True))
    person_placeholder_terms = tuple(
        norm_text(value) for value in params.get("person_placeholders", [])
    )
    issues: list[dict[str, Any]] = []
    groups: dict[tuple[str, str, str, tuple[Any, ...]], list[Record]] = defaultdict(list)
    for record in ctx.all_records:
        if record.record_type != "position_duty":
            continue
        explicit = norm_text(record.value("role"))
        if not explicit:
            issues.append(_issue(record, "explicit_role_missing", "请补充明确角色"))
        elif explicit not in VALID_ROLES:
            issues.append(_issue(record, "invalid_role", "请核实角色。", actual_role=record.value("role")))
        if not _person_names(record, person_placeholder_terms):
            issues.append(_issue(record, "person_missing", "请补充具体人员后核实岗位分离"))
        key = _separation_measure_key(record)
        if key and _role(record, infer_role_from_duty):
            groups[(*_scope_key(record), key)].append(record)

    for rows in groups.values():
        handlers = [record for record in rows if _role(record, infer_role_from_duty) == "经办"]
        for counterpart_role, conflict_type in (("审核", "经办审核"), ("监督", "经办监督")):
            counterparts = [
                record
                for record in rows
                if _role(record, infer_role_from_duty) == counterpart_role
            ]
            for handler, counterpart in product(handlers, counterparts):
                shared_people = (
                    _person_names(handler, person_placeholder_terms)
                    & _person_names(counterpart, person_placeholder_terms)
                )
                if not shared_people:
                    continue
                # 确认稿要求部门、完整人员和除责任类型外的职责正文均一致。
                if norm_text(handler.value("department")) != norm_text(counterpart.value("department")):
                    continue
                if norm_text(handler.value("person_names")) != norm_text(counterpart.value("person_names")):
                    continue
                if _duty_signature(handler.value("duty")) != _duty_signature(counterpart.value("duty")):
                    continue
                opinion = f"同一条措施，同样的岗位职责，{conflict_type}不能同一人"
                for record, related in ((handler, counterpart), (counterpart, handler)):
                    issues.append(_issue(
                        record,
                        "same_duty_person_overlap",
                        opinion,
                        conflict_type=conflict_type,
                        conflict_people="、".join(sorted(shared_people)),
                        related_file=related.file_path,
                        related_sheet=related.sheet,
                        related_row=related.row,
                    ))
    return issues


def matrix_duty_alignment(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """双向核对矩阵和岗位内控责任清单中的控制措施编号。

    Args:
        ctx: 包含矩阵和岗位清单记录的审核上下文。
        params: 保留的规则参数，本版本不需要额外配置。

    Returns:
        分别落在矩阵缺岗位和岗位缺矩阵原始行的问题。
    """

    del params
    matrices = [record for record in ctx.all_records if record.record_type == "matrix" and _measure_key(record)]
    duties = [record for record in ctx.all_records if record.record_type == "position_duty" and _measure_key(record)]
    duty_keys = {(*_scope_key(record), _measure_key(record)) for record in duties}
    matrix_keys = {(*_scope_key(record), _measure_key(record)) for record in matrices}
    issues = [
        _issue(
            record,
            "duty_measure_missing",
            "请核实岗位内控责任清单编制是否完整",
            measure_id=record.value("measure_id"),
        )
        for record in matrices
        if (*_scope_key(record), _measure_key(record)) not in duty_keys
    ]
    issues.extend(
        _issue(
            record,
            "matrix_measure_missing",
            "矩阵中没有该条控制措施请核实是否需要保留",
            measure_id=record.value("measure_id"),
        )
        for record in duties
        if (*_scope_key(record), _measure_key(record)) not in matrix_keys
    )
    return issues


def responsibility_phrase(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """检查职责是否同时包含责任属性和四类责任类型之一。

    Args:
        ctx: 岗位内控责任清单审核上下文。
        params: 可覆盖责任属性、责任类型，并控制是否分别输出缺项意见。

    Returns:
        不满足确认稿双条件的岗位职责问题。
    """

    qualities = tuple(params.get("qualities", QUALITY_TERMS))
    responsibility_types = tuple(params.get("responsibility_types", RESPONSIBILITY_TYPES))
    combined_opinion = (
        "岗位职责请按照国网标准句式编制，‘对……的一致性/准确性/真实性/完整性/有效性/"
        "及时性/合规性负主体/审核/审批/监督责任。’"
    )
    split_opinions = bool(params.get("split_opinions", False))
    issues: list[dict[str, Any]] = []
    for record in ctx.records:
        duty = norm_text(record.value("duty"))
        if not duty:
            continue
        quality_missing = not any(value in duty for value in qualities)
        responsibility_type_missing = not any(value in duty for value in responsibility_types)
        if not split_opinions and (quality_missing or responsibility_type_missing):
            issues.append(_issue(record, "responsibility_phrase_missing", combined_opinion))
            continue
        if quality_missing:
            issues.append(_issue(
                record,
                "responsibility_quality_missing",
                "对……的一致性/准确性/真实性/完整性/有效性/及时性/合规性……",
            ))
        if responsibility_type_missing:
            issues.append(_issue(
                record,
                "responsibility_type_missing",
                "……负主体/审核/审批/监督责任。",
            ))
    return issues


def _incompatible_position(value: str) -> tuple[str, str] | None:
    """解析“部门-岗位”形式的不相容岗位。

    Args:
        value: 岗位 A 或岗位 B 的原始文字。

    Returns:
        规范化后的部门和岗位；格式不完整时返回 ``None``。
    """

    parts = [norm_text(part) for part in RESPONSIBILITY_SEGMENT_SEPARATOR.split(value, maxsplit=1)]
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


def _has_incompatible_content(record: Record) -> bool:
    """判断不相容岗位清单行是否包含需要审核的有效内容。

    Args:
        record: 不相容岗位清单记录。

    Returns:
        编号或岗位字段包含非占位内容时返回 ``True``。
    """

    values = (
        norm_text(record.value("measure_id")),
        norm_text(record.value("position_a")),
        norm_text(record.value("position_b")),
        norm_text(record.value("position_c")),
    )
    return any(value not in INCOMPATIBLE_EMPTY_VALUES for value in values)


def incompatible_position_alignment(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """核对矩阵不相容标记、清单编号及岗位 A、B 的填写内容。

    Args:
        ctx: 包含矩阵和不相容岗位清单记录的审核上下文。
        params: 保留的规则参数，本版本不需要额外配置。

    Returns:
        双向缺失、格式错误、跨部门或岗位相同问题。
    """

    del params
    matrices: dict[tuple[str, str, str, tuple[Any, ...]], list[Record]] = defaultdict(list)
    incompatible_rows: dict[tuple[str, str, str, tuple[Any, ...]], list[Record]] = defaultdict(list)
    unkeyed_incompatible_rows: list[Record] = []
    issues: list[dict[str, Any]] = []
    for record in ctx.all_records:
        key = _measure_key(record)
        if (
            record.record_type == "incompatible_position"
            and _has_incompatible_content(record)
            and not key
        ):
            issues.append(_issue(record, "incompatible_measure_id_missing", "请补充控制措施编号"))
            # 缺编号只阻断矩阵关联，岗位内容仍需继续执行格式和冲突检查。
            unkeyed_incompatible_rows.append(record)
            continue
        if not key:
            continue
        full_key = (*_scope_key(record), key)
        if record.record_type == "matrix":
            matrices[full_key].append(record)
        elif record.record_type == "incompatible_position" and _has_incompatible_content(record):
            incompatible_rows[full_key].append(record)

    for key, matrix_rows in matrices.items():
        matching_rows = incompatible_rows.get(key, [])
        complete_rows = [
            record
            for record in matching_rows
            if norm_text(record.value("position_a")) not in INCOMPATIBLE_EMPTY_VALUES
            and norm_text(record.value("position_b")) not in INCOMPATIBLE_EMPTY_VALUES
        ]
        for matrix in matrix_rows:
            marked = norm_text(matrix.value("incompatible_control")) == "√"
            if marked and not complete_rows:
                issues.append(_issue(matrix, "incompatible_list_missing", "请补充不相容岗位清单"))
            elif not marked and matching_rows:
                issues.append(_issue(matrix, "incompatible_matrix_unmarked", "请核实是否存在不相容岗位"))

    for key, rows in incompatible_rows.items():
        if key in matrices:
            continue
        for record in rows:
            issues.append(_issue(
                record,
                "incompatible_matrix_missing",
                "矩阵中没有该条控制措施，请核实是否需要保留不相容岗位清单记录",
            ))

    position_groups = [*incompatible_rows.values(), *([record] for record in unkeyed_incompatible_rows)]
    for rows in position_groups:
        positions_by_record: list[tuple[Record, list[tuple[str, str]]]] = []
        for record in rows:
            raw_position_a = norm_text(record.value("position_a"))
            raw_position_b = norm_text(record.value("position_b"))
            # 岗位为空时，确认稿指定在对应矩阵行统一提示补充清单。
            if (
                raw_position_a in INCOMPATIBLE_EMPTY_VALUES
                or raw_position_b in INCOMPATIBLE_EMPTY_VALUES
            ):
                continue
            position_values = [raw_position_a, raw_position_b]
            raw_position_c = norm_text(record.value("position_c"))
            if raw_position_c not in INCOMPATIBLE_EMPTY_VALUES:
                position_values.append(raw_position_c)
            parsed_positions = [_incompatible_position(value) for value in position_values]
            if any(position is None for position in parsed_positions):
                issues.append(_issue(
                    record,
                    "incompatible_position_format_invalid",
                    "不相容岗位请按‘部门-岗位’的形式填写",
                ))
                continue
            # 前面已排除 None，此处只保留可比较的“部门-岗位”二元组。
            valid_positions = [position for position in parsed_positions if position is not None]
            positions_by_record.append((record, valid_positions))

        all_positions = [
            position
            for _, record_positions in positions_by_record
            for position in record_positions
        ]
        departments = {department for department, _ in all_positions}
        job_counts: dict[str, int] = defaultdict(int)
        for _, job in all_positions:
            job_counts[job] += 1
        for record, record_positions in positions_by_record:
            if len(departments) > 1:
                issues.append(_issue(
                    record,
                    "incompatible_cross_department",
                    "跨部门岗位天然不相容无需填写，请核实矩阵不相容岗位标记是否准确",
                ))
            if any(job_counts[job] > 1 for _, job in record_positions):
                issues.append(_issue(
                    record,
                    "incompatible_same_position",
                    "岗位名称相同，请确认是否为不相容岗位",
                ))
    return issues


def role_responsibility_mapping(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """检查明确角色与职责中的责任类型是否对应。

    Args:
        ctx: 岗位内控责任清单审核上下文。
        params: 可覆盖角色责任映射的规则参数。

    Returns:
        明确角色与责任类型不对应的问题。
    """

    mapping = params.get("mapping", ROLE_RESPONSIBILITIES)
    opinion = "经办对应主体责任、审核对应审核责任、审批对应审批责任、监督对应监督责任。"
    issues = []
    for record in ctx.records:
        role = norm_text(record.value("role"))
        expected = mapping.get(role)
        if expected and expected not in norm_text(record.value("duty")):
            issues.append(_issue(record, "responsibility_role_mismatch", opinion, role=role, expected=expected))
    return issues


def _system_names(value: str) -> set[str]:
    """拆分一个或多个系统名称。

    Args:
        value: 矩阵控制系统或系统规则清单系统名称正文。

    Returns:
        已归一化的非空系统名称集合。
    """

    return {norm_text(item) for item in SYSTEM_NAME_SEPARATOR.split(value) if norm_text(item)}


def system_control_consistency(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """核对系统规则清单与矩阵的编号、状态、控制方式和系统名称。

    Args:
        ctx: 包含矩阵和系统规则清单记录的审核上下文。
        params: 可控制系统名称、建设状态校验，并指定矩阵缺失时的意见。

    Returns:
        确认稿第11条规定的五类一致性问题。
    """

    compare_system_names = bool(params.get("compare_system_names", True))
    validate_build_status = bool(params.get("validate_build_status", False))
    matrix_missing_opinion = params.get("matrix_missing_opinion", "需核实矩阵中控制方式是否正确")
    matrices: dict[tuple[str, str, str, tuple[Any, ...]], list[Record]] = defaultdict(list)
    systems: dict[tuple[str, str, str, tuple[Any, ...]], list[Record]] = defaultdict(list)
    for record in ctx.all_records:
        key = _measure_key(record)
        if not key:
            continue
        full_key = (*_scope_key(record), key)
        if record.record_type == "matrix":
            matrices[full_key].append(record)
        elif record.record_type == "system_rule":
            systems[full_key].append(record)

    issues: list[dict[str, Any]] = []
    for key, matrix_rows in matrices.items():
        system_rows = systems.get(key, [])
        for matrix in matrix_rows:
            matrix_mode = norm_text(matrix.value("control_mode"))
            if matrix_mode in SYSTEM_MODES and not system_rows:
                issues.append(_issue(matrix, "system_rule_missing", "需核实系统控制规则清单是否完整"))

    for key, system_rows in systems.items():
        matrix_rows = matrices.get(key, [])
        for system in system_rows:
            build_status = norm_text(system.value("build_status"))
            if build_status in SYSTEM_MODES and not matrix_rows:
                issues.append(_issue(system, "matrix_measure_missing", matrix_missing_opinion))
                continue
            for matrix in matrix_rows:
                matrix_mode = norm_text(matrix.value("control_mode"))
                if (
                    matrix_mode in SYSTEM_MODES
                    and build_status != matrix_mode
                    and (validate_build_status or build_status in SYSTEM_MODES)
                ):
                    issues.append(_issue(system, "build_status_mismatch", "需核实建设状态是否正确"))
                elif matrix_mode not in SYSTEM_MODES:
                    issues.append(_issue(
                        system,
                        "control_mode_mismatch",
                        "需核实系统规则清单中建设状态与矩阵中控制方式的一致性",
                    ))

        matrix_names = set().union(*(_system_names(row.value("control_system")) for row in matrix_rows)) if matrix_rows else set()
        system_names = set().union(*(_system_names(row.value("system_name")) for row in system_rows))
        if compare_system_names and matrix_rows and matrix_names != system_names:
            # 系统名称问题落在系统规则行，便于直接修改清单。
            for system in system_rows:
                issues.append(_issue(
                    system,
                    "system_name_mismatch",
                    "需核实系统规则清单中系统名称与矩阵中控制系统的一致性",
                    expected_system_names=sorted(matrix_names),
                    actual_system_names=sorted(system_names),
                ))
    return issues


def _responsibility_items(value: str) -> list[str]:
    """拆分矩阵责任主体中的多个责任组合。

    Args:
        value: 矩阵责任主体正文。

    Returns:
        去除空白后的责任主体组合列表。
    """

    return [item.strip() for item in RESPONSIBILITY_ITEM_SEPARATOR.split(value) if item.strip()]


def _responsibility_segments(value: str) -> list[str]:
    """拆分公司、部门、岗位三个责任主体层级。

    Args:
        value: 一个责任主体组合。

    Returns:
        最多三个已归一化层级，第三段保留后续连字符正文。
    """

    return [norm_text(item) for item in RESPONSIBILITY_SEGMENT_SEPARATOR.split(value, maxsplit=2) if norm_text(item)]


def _contains_exception(value: str, exceptions: set[str]) -> bool:
    """判断名称是否包含确认稿列明的任一例外称呼。

    Args:
        value: 待核对的部门或岗位名称。
        exceptions: 已归一化的确认稿例外名称集合。

    Returns:
        名称包含任一非空例外称呼时返回 ``True``。
    """

    return any(exception and exception in value for exception in exceptions)


def responsibility_format(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """检查矩阵责任主体的公司、部门、岗位格式。

    Args:
        ctx: 包含矩阵记录的审核上下文。
        params: 保留的规则参数，本版本不需要额外配置。

    Returns:
        缺公司、使用各级单位或部门名称含公司的格式问题。
    """

    del params
    issues = []
    for record in ctx.all_records:
        if record.record_type != "matrix":
            continue
        for item in _responsibility_items(record.value("responsibility")):
            segments = _responsibility_segments(item)
            if len(segments) < 3:
                issues.append(_issue(record, "responsibility_company_missing", "需在矩阵责任主体中补充公司名称"))
                continue
            if segments[0] == norm_text("各级单位"):
                issues.append(_issue(
                    record,
                    "responsibility_generic_company",
                    "请将责任主体修改为‘公司名称-部门名称-岗位名称’的形式",
                ))
            if "公司" in segments[1]:
                issues.append(_issue(record, "responsibility_department_has_company", "请核实部门名称是否准确"))
    return issues


def responsibility_name_alignment(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """双向核对矩阵责任主体与岗位清单的部门、岗位名称。

    Args:
        ctx: 包含矩阵和岗位内控责任清单记录的审核上下文。
        params: 可包含矩阵侧部门及岗位例外名单。

    Returns:
        双向缺失名称以及岗位清单泛化名称问题。
    """

    department_exceptions = {norm_text(value) for value in params.get("department_exceptions", [])}
    position_exceptions = {norm_text(value) for value in params.get("position_exceptions", [])}
    matrices_by_scope: dict[tuple[str, str, str], list[Record]] = defaultdict(list)
    duties_by_scope: dict[tuple[str, str, str], list[Record]] = defaultdict(list)
    for record in ctx.all_records:
        if record.record_type == "matrix":
            matrices_by_scope[_scope_key(record)].append(record)
        elif record.record_type == "position_duty":
            duties_by_scope[_scope_key(record)].append(record)

    issues: list[dict[str, Any]] = []
    for scope in set(matrices_by_scope) | set(duties_by_scope):
        matrix_rows = matrices_by_scope.get(scope, [])
        duty_rows = duties_by_scope.get(scope, [])
        duty_departments = {norm_text(row.value("department")) for row in duty_rows if norm_text(row.value("department"))}
        duty_positions = {norm_text(row.value("position")) for row in duty_rows if norm_text(row.value("position"))}
        matrix_departments: set[str] = set()
        matrix_positions: set[str] = set()
        for matrix in matrix_rows:
            for item in _responsibility_items(matrix.value("responsibility")):
                segments = _responsibility_segments(item)
                if len(segments) < 2:
                    continue
                department, position = segments[-2:]
                matrix_departments.add(department)
                matrix_positions.add(position)
                if not _contains_exception(department, department_exceptions) and department not in duty_departments:
                    issues.append(_issue(
                        matrix,
                        "matrix_department_missing_in_duties",
                        "请核实岗位内控责任清单中的部门名称是否完整",
                        department=department,
                    ))
                if not _contains_exception(position, position_exceptions) and position not in duty_positions:
                    issues.append(_issue(
                        matrix,
                        "matrix_position_missing_in_duties",
                        "请核实岗位内控责任清单中的岗位名称是否完整",
                        position=position,
                    ))
        for duty in duty_rows:
            department = norm_text(duty.value("department"))
            position = norm_text(duty.value("position"))
            if _contains_exception(department, department_exceptions):
                issues.append(_issue(duty, "duty_department_not_specific", "请明确到具体部门"))
            elif department and department not in matrix_departments:
                issues.append(_issue(duty, "duty_department_missing_in_matrix", "请核实矩阵中的部门名称是否完整"))
            if _contains_exception(position, position_exceptions):
                issues.append(_issue(duty, "duty_position_not_specific", "请明确到具体岗位"))
            elif position and position not in matrix_positions:
                issues.append(_issue(duty, "duty_position_missing_in_matrix", "请核实矩阵中的岗位名称是否完整"))
    return issues


def person_multiple_positions(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """检查同一人员是否对应多个不同岗位名称。

    Args:
        ctx: 岗位内控责任清单审核上下文。
        params: 保留的规则参数，本版本不需要额外配置。

    Returns:
        一人多岗涉及的全部原始岗位行。
    """

    del params
    rows_by_person: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for record in ctx.all_records:
        if record.record_type != "position_duty" or not norm_text(record.value("position")):
            continue
        for person in _person_names(record):
            # 确认稿第14条按主体核对，不以业务或电压变体切断一人多岗事实。
            rows_by_person[(record.entity_code, person)].append(record)

    issues: list[dict[str, Any]] = []
    emitted: set[str] = set()
    for rows in rows_by_person.values():
        positions = {norm_text(record.value("position")) for record in rows}
        if len(positions) <= 1:
            continue
        for record in rows:
            if record.record_id in emitted:
                continue
            emitted.add(record.record_id)
            issues.append(_issue(
                record,
                "person_multiple_positions",
                "请核实是否为一人多岗。",
                positions=sorted(positions),
            ))
    return issues
