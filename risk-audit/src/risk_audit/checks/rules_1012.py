"""2026-10-12 湖南公司版新增审核能力。"""

from __future__ import annotations

from typing import Any

from risk_audit.applicability import interpret_record
from risk_audit.checks.registry import CheckContext
from risk_audit.util import measure_id_key


def template_match_notice(
    ctx: CheckContext,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """为未唯一匹配模板的矩阵生成一次复核意见。

    Args:
        ctx: 当前审核范围内的文件、工作表和记录上下文。
        params: 规则参数；当前版本不需要额外参数。
    """

    del params
    issues: list[dict[str, Any]] = []
    for file in ctx.files:
        template_match = file.preservation.get("template_match", {})
        status = template_match.get("status", "")
        if status in {"", "matched", "not_applicable"}:
            continue
        for sheet in file.sheets:
            if sheet.sheet_type != "matrix":
                continue
            # 模板问题按工作表提示一次，避免对矩阵中的每条措施重复报错。
            issues.append({
                "record": sheet.records[0] if sheet.records else None,
                "kind": "review",
                "evidence": {
                    "issue_type": "template_match_failed",
                    "template_status": status,
                    "file_path": str(file.relative_path),
                    "sheet": sheet.title,
                    "unavailable_reason": template_match.get("reason", "模板未唯一匹配"),
                },
            })
    return issues


def financial_management_applicability(
    ctx: CheckContext,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """复核财务管理指定措施的岗位清单编号和适用性。

    Args:
        ctx: 当前审核范围及跨表记录上下文。
        params: 包含目标业务标识 business_id 和措施编号 measure_ids 的规则参数。
    """

    target_business_id = params["business_id"]
    target_measure_ids = {tuple(item) for item in params["measure_ids"]}
    duty_measure_keys = {
        (
            record.entity_code,
            record.business_id,
            record.variant_id,
            measure_id_key(record.value("measure_id")),
        )
        for record in ctx.all_records
        if record.record_type == "position_duty"
        and record.business_id == target_business_id
    }

    issues: list[dict[str, Any]] = []
    for record in ctx.records:
        if record.business_id != target_business_id:
            continue
        measure_key = measure_id_key(record.value("measure_id"))
        if measure_key not in target_measure_ids:
            continue
        duty_key = (
            record.entity_code,
            record.business_id,
            record.variant_id,
            measure_key,
        )
        duty_measure_present = duty_key in duty_measure_keys
        applicability = interpret_record(record, ctx.resources)
        if duty_measure_present and applicability.decision != "not_applicable":
            continue
        # 两项条件同时异常时仍只产生一条第17条意见。
        issues.append({
            "record": record,
            "kind": "review",
            "evidence": {
                "issue_type": "financial_management_applicability",
                "measure_id": record.value("measure_id"),
                "duty_measure_present": duty_measure_present,
                "applicability_text": record.value("applicability"),
                "applicability_state": applicability.state,
                "applicability_decision": applicability.decision,
            },
        })
    return issues
