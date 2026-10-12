"""《风控矩阵1012-重庆》专项审核能力。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from risk_audit.checks.registry import CheckContext
from risk_audit.models import Record
from risk_audit.util import measure_id_key, norm_text


_SYSTEM_MODES = {norm_text("系统待建"), norm_text("系统已建")}
_NAME_SEPARATOR = re.compile(r"[、，,;；/\n]+")
_RESPONSIBILITY_ITEM_SEPARATORS = {"、", "，", ",", ";", "；", "\n"}
_RESPONSIBILITY_SEGMENT_SEPARATORS = {"-", "—", "－"}
_RESPONSIBILITY_BRACKETS = {"(": ")", "（": "）", "[": "]", "【": "】"}
_OPINIONS = {
    "system_name_mismatch": "需核实系统规则清单中系统名称与矩阵中控制系统的一致性",
    "missing_key_points": "需核实适用的矩阵或关键控制点是否完整",
    "extra_business": "需核实该矩阵或关键控制点是否适用",
    "extra_key_point": "需核实该矩阵或关键控制点是否适用",
    "responsibility_generic_company": "请将责任主体修改为“公司名称-部门名称-岗位名称”的形式",
    "responsibility_company_missing": "需在矩阵责任主体中补充公司名称",
    "responsibility_department_mismatch": "请核实岗位责任清单中的部门名称与矩阵责任主体中的部门名称是否一致",
    "responsibility_position_mismatch": "请核实岗位责任清单中的岗位名称与矩阵责任主体中的岗位名称是否一致",
    "control_measure_incomplete": "请核实该关键控制点下控制措施是否完整",
}
_DEFAULT_SYSTEM_OPINION = "需核实系统规则清单中建设状态与矩阵中控制方式的一致性"
_ENTITY_COMPLETENESS_BUSINESS_ID = "__entity_completeness__"


def _issue(record: Record | None, issue_type: str, **evidence: Any) -> dict[str, Any]:
    """构造重庆专项规则问题。

    Args:
        record: 可定位的原始记录，资料级问题可为空。
        issue_type: 稳定的问题类型。
        evidence: 用于展示和去重的证据字段。
    """

    opinion = _OPINIONS.get(issue_type, _DEFAULT_SYSTEM_OPINION)
    return {
        "record": record,
        "kind": "violation",
        "location_policy": "row" if record is not None else "material",
        "evidence": {"issue_type": issue_type, "opinion": opinion, **evidence},
    }


def _record_key(record: Record) -> tuple[str, str, str, tuple[Any, ...]]:
    """返回跨表关联措施的稳定键。

    Args:
        record: 矩阵或系统规则记录。
    """

    business_id = record.business_id or record.business_code
    return record.entity_code, business_id, record.variant_id, measure_id_key(record.value("measure_id"))


def _system_state(value: str) -> str:
    """将建设状态收敛为「系统已建」或「系统待建」。

    Args:
        value: 矩阵控制方式或系统规则建设状态。
    """

    normalized = norm_text(value)
    if "待建" in normalized:
        return norm_text("系统待建")
    if "已建" in normalized:
        return norm_text("系统已建")
    return normalized


def _system_names(value: str) -> set[str]:
    """拆分并归一化一个或多个系统名称。

    Args:
        value: 系统名称单元格文本。
    """

    return {norm_text(item) for item in _NAME_SEPARATOR.split(value) if norm_text(item)}


def system_control_consistency(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """核对矩阵控制方式、控制系统与系统规则清单。

    Args:
        ctx: 包含矩阵与系统规则清单的审核上下文。
        params: 规则参数，当前版本无额外参数。
    """

    del params
    matrices = [record for record in ctx.all_records if record.record_type == "matrix"]
    system_rows: dict[tuple[str, str, str, tuple[Any, ...]], list[Record]] = {}
    for record in ctx.all_records:
        if record.record_type == "system_rule":
            system_rows.setdefault(_record_key(record), []).append(record)

    issues: list[dict[str, Any]] = []
    matrix_keys = {_record_key(record) for record in matrices}
    for matrix in matrices:
        rows = system_rows.get(_record_key(matrix), [])
        control_mode = _system_state(matrix.value("control_mode"))
        if control_mode in _SYSTEM_MODES and not rows:
            issues.append(_issue(
                matrix,
                "system_rule_missing",
                measure_id=matrix.value("measure_id"),
                control_mode=matrix.value("control_mode"),
            ))
            continue
        if rows and control_mode not in _SYSTEM_MODES:
            issues.append(_issue(
                matrix,
                "control_mode_mismatch",
                measure_id=matrix.value("measure_id"),
                actual_control_mode=matrix.value("control_mode"),
            ))
            continue

        expected_names = _system_names(matrix.value("control_system"))
        # 同一措施可在系统清单中拆成多行，系统名称需合并后再比较。
        actual_names = {
            name
            for row in rows
            for name in _system_names(row.value("system_name"))
        }
        for row in rows:
            if _system_state(row.value("build_status")) != control_mode:
                issues.append(_issue(
                    row,
                    "build_status_mismatch",
                    measure_id=row.value("measure_id"),
                    expected_build_status=matrix.value("control_mode"),
                    actual_build_status=row.value("build_status"),
                ))
        if rows and actual_names != expected_names:
            issues.append(_issue(
                rows[0],
                "system_name_mismatch",
                measure_id=rows[0].value("measure_id"),
                expected_system_names=sorted(expected_names),
                actual_system_names=sorted(actual_names),
            ))

    # 系统规则行本身存在时，矩阵必须有对应措施且标记为系统控制。
    for key, rows in system_rows.items():
        if key not in matrix_keys:
            for row in rows:
                issues.append(_issue(
                    row,
                    "matrix_measure_missing",
                    measure_id=row.value("measure_id"),
                ))
    return issues


def _split_responsibility_text(
    value: str,
    separators: set[str],
    max_splits: int | None = None,
) -> list[str]:
    """只在括号外按指定字符拆分责任主体文本。

    Args:
        value: 待拆分的责任主体正文。
        separators: 可作为顶层分隔符的字符集合。
        max_splits: 最大拆分次数；为空时不限制。
    """

    items: list[str] = []
    current: list[str] = []
    bracket_stack: list[str] = []
    split_count = 0
    for character in value:
        if character in _RESPONSIBILITY_BRACKETS:
            bracket_stack.append(_RESPONSIBILITY_BRACKETS[character])
            current.append(character)
            continue
        if bracket_stack and character == bracket_stack[-1]:
            bracket_stack.pop()
            current.append(character)
            continue
        # 组织或岗位说明中的分隔符属于名称正文，不能用于拆分顶层结构。
        can_split = max_splits is None or split_count < max_splits
        if character in separators and not bracket_stack and can_split:
            item = "".join(current).strip()
            if item:
                items.append(item)
                split_count += 1
            current = []
            continue
        current.append(character)

    final_item = "".join(current).strip()
    if final_item:
        items.append(final_item)
    return items


def _responsibility_items(value: str) -> list[str]:
    """拆分矩阵责任主体中的多个公司、部门、岗位组合。

    Args:
        value: 矩阵责任主体单元格的有效正文。
    """

    return _split_responsibility_text(value, _RESPONSIBILITY_ITEM_SEPARATORS)


def _responsibility_segments(value: str) -> list[str]:
    """拆分单个责任主体的公司、部门和岗位层级。

    Args:
        value: 单个责任主体组合。
    """

    return [
        norm_text(item)
        for item in _split_responsibility_text(
            value,
            _RESPONSIBILITY_SEGMENT_SEPARATORS,
            max_splits=2,
        )
        if norm_text(item)
    ]


def responsibility_alignment(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """核对重庆矩阵责任主体格式以及岗位清单中的部门和岗位。

    Args:
        ctx: 包含当前业务矩阵和岗位内控责任清单的审核上下文。
        params: 规则参数，当前版本无额外参数。
    """

    del params
    duties: dict[tuple[str, str, str, tuple[Any, ...]], list[Record]] = {}
    for record in ctx.all_records:
        if record.record_type == "position_duty":
            duties.setdefault(_record_key(record), []).append(record)

    issues: list[dict[str, Any]] = []
    for matrix in ctx.all_records:
        if matrix.record_type != "matrix":
            continue
        related_duties = duties.get(_record_key(matrix), [])
        duty_departments = {norm_text(row.value("department")) for row in related_duties if norm_text(row.value("department"))}
        duty_positions = {norm_text(row.value("position")) for row in related_duties if norm_text(row.value("position"))}
        for item in _responsibility_items(matrix.value("responsibility")):
            segments = _responsibility_segments(item)
            if len(segments) < 3:
                issues.append(_issue(matrix, "responsibility_company_missing", responsibility=item))
            elif segments[0] == norm_text("各级单位"):
                issues.append(_issue(matrix, "responsibility_generic_company", responsibility=item))

            # 没有同措施岗位记录时由资料完整性规则负责，本规则避免重复输出两个名称问题。
            if len(segments) < 2 or not related_duties:
                continue
            department, position = segments[-2:]
            if department not in duty_departments:
                issues.append(_issue(
                    matrix,
                    "responsibility_department_mismatch",
                    responsibility=item,
                    matrix_department=department,
                    duty_departments=sorted(duty_departments),
                ))
            if position not in duty_positions:
                issues.append(_issue(
                    matrix,
                    "responsibility_position_mismatch",
                    responsibility=item,
                    matrix_position=position,
                    duty_positions=sorted(duty_positions),
                ))
    return issues


def _key_point(value: str, measure_id: str) -> tuple[int, ...] | None:
    """返回关键控制点的一级数字编号。

    Args:
        value: 关键控制点字段正文。
        measure_id: 控制措施编号，用于字段为空时回退解析。
    """

    for candidate in (value, measure_id):
        key = measure_id_key(candidate)
        if key and isinstance(key[0], int):
            return (key[0],)
    return None


def control_measure_completeness(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """按关键控制点比较报送矩阵与重庆冻结模板的控制措施数量。

    Args:
        ctx: 包含当前业务矩阵和唯一冻结模板的审核上下文。
        params: 规则参数，当前版本无额外参数。
    """

    del params
    issues: list[dict[str, Any]] = []
    for business in ctx.scope_businesses:
        business_id = str(business.get("business_id") or business.get("business_code") or "")
        variant_id = str(business.get("variant_id") or "default")
        baseline = ctx.baselines.get((business_id, variant_id), {})
        if not baseline.get("available", False):
            continue

        expected: dict[tuple[int, ...], set[tuple[Any, ...]]] = {}
        for row in baseline.get("matrix_rows", []):
            if norm_text("各级单位") not in norm_text(str(row.get("responsibility", ""))):
                continue
            point = _key_point(str(row.get("key_control_point", "")), str(row.get("measure_id", "")))
            measure_key = measure_id_key(str(row.get("measure_id", "")))
            if point is not None and measure_key:
                expected.setdefault(point, set()).add(measure_key)

        actual_by_entity: dict[str, dict[tuple[int, ...], dict[tuple[Any, ...], Record]]] = {}
        for record in ctx.all_records:
            record_business_id = record.business_id or record.business_code
            if (
                record.record_type != "matrix"
                or record_business_id != business_id
                or record.variant_id != variant_id
                or record.entity_code not in ctx.scope_entity_codes
            ):
                continue
            point = _key_point(record.value("key_control_point"), record.value("measure_id"))
            measure_key = measure_id_key(record.value("measure_id"))
            if point is not None and measure_key:
                entity_rows = actual_by_entity.setdefault(record.entity_code, {})
                entity_rows.setdefault(point, {}).setdefault(measure_key, record)

        # 缺少整项关键控制点由第十八条负责；本条只比较已选择关键控制点下的措施数。
        for entity_code in ctx.scope_entity_codes:
            actual = actual_by_entity.get(entity_code, {})
            for point in sorted(set(expected) & set(actual)):
                expected_count = len(expected[point])
                actual_count = len(actual[point])
                if actual_count >= expected_count:
                    continue
                first_record = min(actual[point].values(), key=lambda item: (item.file_path, item.sheet, item.row))
                issues.append(_issue(
                    first_record,
                    "control_measure_incomplete",
                    business_id=business_id,
                    key_point=list(point),
                    expected_count=expected_count,
                    actual_count=actual_count,
                ))
    return issues


def _entity_name(ctx: CheckContext, entity_code: str) -> str:
    """返回名册或报送元数据中的主体名称。

    Args:
        ctx: 审核上下文。
        entity_code: 主体稳定编码。
    """

    entity = ctx.entities.get(entity_code)
    if entity is not None:
        return entity.name
    for file in ctx.files:
        if file.entity_code != entity_code:
            continue
        identity = file.preservation.get("entity_identity", {})
        if identity.get("entity_name"):
            return str(identity["entity_name"])
    return ""


def _business_id_from_text(value: str, aliases: dict[str, list[str]]) -> str | None:
    """从文件的直接上级目录返回唯一命中的业务标识。

    Args:
        value: 待识别文件的包内路径。
        aliases: 业务标识到可识别名称的显式映射。
    """

    # 文件名可能残留其他模板名称，归属判断只允许读取业务目录。
    haystack = norm_text(Path(value).parent.name)
    matches = {
        business_id
        for business_id, names in aliases.items()
        if any(norm_text(name) and norm_text(name) in haystack for name in names)
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _business_id(record: Record, aliases: dict[str, list[str]]) -> str | None:
    """返回记录已匹配或通过唯一别名识别的业务标识。

    Args:
        record: 待识别的矩阵记录。
        aliases: 业务标识到可识别名称的显式映射。
    """

    if record.business_id and not record.business_id.startswith("unmatched_"):
        return record.business_id
    return _business_id_from_text(record.file_path, aliases)


def _record_key_point(record: Record) -> tuple[int, ...] | None:
    """从矩阵记录中取关键控制点一级编号。

    Args:
        record: 矩阵明细记录。
    """

    return _key_point(record.value("key_control_point"), record.value("measure_id"))


def power_supply_branch_key_points(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """核对供电分公司报送的矩阵及关键控制点。

    Args:
        ctx: 包含报送主体、文件和矩阵记录的审核上下文。
        params: 包含 expected_key_points 和 business_aliases 的冻结参数。
    """

    expected = {
        business_id: {tuple(item) for item in points}
        for business_id, points in params["expected_key_points"].items()
    }
    aliases = params["business_aliases"]
    issues: list[dict[str, Any]] = []
    for entity_code in ctx.scope_entity_codes:
        if "供电分公司" not in norm_text(_entity_name(ctx, entity_code)):
            continue
        actual: dict[str, dict[tuple[int, ...], list[Record]]] = {}
        business_records: dict[str, list[Record]] = {}
        for record in ctx.all_records:
            if record.entity_code != entity_code or record.record_type != "matrix":
                continue
            business_id = _business_id(record, aliases)
            point = _record_key_point(record)
            if business_id:
                business_records.setdefault(business_id, []).append(record)
            if business_id in expected and point is not None:
                actual.setdefault(business_id, {}).setdefault(point, []).append(record)

        # 执行器按业务分割审核单元，只校验当前单元，避免 17 个业务互相重复报缺失。
        scope_business_ids = {
            str(item.get("business_id") or item.get("business_code") or "")
            for item in ctx.scope_businesses
        }
        target_business_ids = (scope_business_ids | set(actual)) & set(expected)
        raw_submitted_business_ids = {
            str(item) for item in ctx.resources.get("_entity_business_ids", []) if item
        }
        business_files = ctx.resources.get("_entity_business_files", {})
        submitted_business_ids: set[str] = set()
        canonical_by_raw: dict[str, set[str]] = {}
        for business_id in raw_submitted_business_ids:
            canonical_ids = {
                resolved
                for path in business_files.get(business_id, [])
                if (resolved := _business_id_from_text(str(path), aliases))
            }
            if business_id in expected:
                canonical_ids.add(business_id)
            canonical_by_raw[business_id] = canonical_ids
            # 同一 unmatched_* 单元可包含多份不同的来源业务文件。
            submitted_business_ids.update(canonical_ids or {business_id})
        raw_current_business_id = str(
            ctx.resources.get("_current_business_id") or next(iter(scope_business_ids), "")
        )
        actual_business_ids = set(actual) | set(business_records)
        current_canonical_ids = canonical_by_raw.get(raw_current_business_id, set()) | (
            actual_business_ids & set(expected)
        )
        current_business_id = (
            raw_current_business_id
            if raw_current_business_id in expected
            else next(iter(current_canonical_ids), raw_current_business_id)
        )
        # runner 使用含变体的唯一审核单元顺序，显式指定每个主体的唯一完整性轮次。
        completeness_pass = ctx.resources.get("_entity_completeness_pass") is True
        if completeness_pass:
            for business_id in sorted(set(expected) - submitted_business_ids):
                issues.append(_issue(
                    None,
                    "missing_key_points",
                    entity_code=entity_code,
                    business_id=business_id,
                    business_code="",
                    variant_id="default",
                    missing_key_points=[list(point) for point in sorted(expected[business_id])],
                ))
        if (
            raw_current_business_id
            and raw_current_business_id not in expected
            and raw_current_business_id != _ENTITY_COMPLETENESS_BUSINESS_ID
            and not current_canonical_ids
        ):
            records = business_records.get(current_business_id, [])
            issues.append(_issue(
                records[0] if records else None,
                "extra_business",
                entity_code=entity_code,
                business_id=current_business_id,
                business_code="",
                variant_id="default",
            ))
        for business_id in sorted(target_business_ids):
            expected_points = expected[business_id]
            actual_points = set(actual.get(business_id, {}))
            missing = sorted(expected_points - actual_points)
            if missing:
                issues.append(_issue(
                    None,
                    "missing_key_points",
                    entity_code=entity_code,
                    business_id=business_id,
                    business_code="",
                    variant_id="default",
                    missing_key_points=[list(point) for point in missing],
                ))
            for point in sorted(actual_points - expected_points):
                # 多报关键控制点定位到第一条对应原始记录。
                record = actual[business_id][point][0]
                issues.append(_issue(
                    record,
                    "extra_key_point",
                    business_id=business_id,
                    key_point=list(point),
                ))
    return issues
