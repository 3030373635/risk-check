"""《1012-重庆 同步模板匹配规则版》专项审核能力。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from risk_audit.checks.registry import CheckContext
from risk_audit.models import Record
from risk_audit.util import measure_id_key, norm_text


_SYSTEM_MODES = {norm_text("系统待建"), norm_text("系统已建")}
_NAME_SEPARATOR = re.compile(r"[、，,;；/\n]+")
_OPINIONS = {
    "system_name_mismatch": "需核实系统规则清单中系统名称与矩阵中控制系统的一致性",
    "missing_key_points": "需核实适用的矩阵或关键控制点是否完整",
    "extra_business": "需核实该矩阵或关键控制点是否适用",
    "extra_key_point": "需核实该矩阵或关键控制点是否适用",
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


def _key_point(record: Record) -> tuple[int, ...] | None:
    """从矩阵记录中取关键控制点一级编号。

    Args:
        record: 矩阵明细记录。
    """

    for value in (record.value("key_control_point"), record.value("measure_id")):
        key = measure_id_key(value)
        if key and isinstance(key[0], int):
            return (key[0],)
    return None


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
            point = _key_point(record)
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
