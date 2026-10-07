"""生成导致审核任务部分完成的结构化未完成项。"""

from __future__ import annotations

from typing import Any

from risk_audit.models import CheckStatus, FileRecord


_BLOCKING_WARNING_TITLES = {
    "no_writable_sheet": "结果写回失败",
    "output_incompatible_file": "结果写回失败",
    "business_processing_failed": "业务处理失败",
}
_BUSINESS_ERROR_MARKERS = (
    "业务模板映射",
    "业务编号映射",
    "模板变体",
)


def _empty_item(item_type: str, title: str, stage: str) -> dict[str, Any]:
    """创建字段统一的未完成项。

    Args:
        item_type: 程序使用的类型标识。
        title: 页面展示的中文类型。
        stage: 问题发生的审核阶段。
    """

    return {
        "type": item_type,
        "title": title,
        "entity_code": "",
        "entity_name": "",
        "business_code": "",
        "business_name": "",
        "file": "",
        "sheet": "",
        "stage": stage,
        "message": "",
        "error_type": "",
        "log_reference": "",
    }


def _file_item(file: FileRecord) -> dict[str, Any]:
    """把未完整解析的材料转换为未完成项。

    Args:
        file: 存在解析错误或无可审核工作表的材料。
    """

    message = "；".join(file.parse_errors) if file.parse_errors else "未识别到可审核工作表"
    is_business_error = any(marker in message for marker in _BUSINESS_ERROR_MARKERS)
    item = _empty_item(
        "business_identification_failed" if is_business_error else "file_parsing_failed",
        "业务识别失败" if is_business_error else "文件解析失败",
        "业务识别" if is_business_error else "文件解析",
    )
    item.update({
        "entity_code": file.entity_code or "",
        "entity_name": file.relative_path.parts[0] if len(file.relative_path.parts) > 1 else "",
        "business_code": file.business_code or "",
        "business_name": file.relative_path.parent.name,
        "file": str(file.relative_path),
        "sheet": "、".join(sheet.title for sheet in file.sheets),
        "message": message,
        "error_type": "BusinessIdentityError" if is_business_error else "FileParseError",
    })
    return item


def _status_item(status: CheckStatus) -> dict[str, Any]:
    """把执行失败的规则状态转换为未完成项。

    Args:
        status: status 为 failed 的规则检查状态。
    """

    item = _empty_item("rule_execution_failed", "规则执行失败", "规则执行")
    item.update({
        "message": status.reason or "规则检查未完成",
        "error_type": "CheckExecutionError",
        "rule_id": status.rule_id,
        "check_id": status.check_id,
        "records_considered": status.records_considered,
    })
    return item


def _warning_item(warning: dict[str, Any], file_value: str = "") -> dict[str, Any]:
    """把阻断审核完成的告警转换为未完成项。

    Args:
        warning: 类型已列入阻断告警集合的告警。
        file_value: 当前未完成项对应的单个文件路径。
    """

    warning_type = str(warning.get("type", ""))
    item = _empty_item(
        warning_type,
        _BLOCKING_WARNING_TITLES[warning_type],
        str(warning.get("stage") or "结果写回"),
    )
    item.update({
        "entity_code": str(warning.get("entity_key") or warning.get("entity_code") or ""),
        "entity_name": str(warning.get("entity_name") or ""),
        "business_code": str(warning.get("business_code") or ""),
        "business_name": str(warning.get("business_name") or ""),
        "file": str(file_value or warning.get("file") or ""),
        "sheet": str(warning.get("sheet") or ""),
        "message": str(
            warning.get("message")
            or warning.get("reason")
            or warning.get("error")
            or "审核未完成"
        ),
        "error_type": str(warning.get("error_type") or "OutputError"),
        "log_reference": str(warning.get("log_reference") or ""),
    })
    return item


def _warning_items(warning: dict[str, Any]) -> list[dict[str, Any]]:
    """把一条阻断告警按受影响文件展开。

    Args:
        warning: 类型已列入阻断告警集合的告警。
    """

    raw_files = warning.get("files")
    if isinstance(raw_files, list) and raw_files:
        return [_warning_item(warning, str(file_value)) for file_value in raw_files]
    return [_warning_item(warning)]


def collect_incomplete_items(
    files: list[FileRecord],
    statuses: list[CheckStatus],
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """收集未完成项。

    Args:
        files: 本次审核的材料文件。
        statuses: 所有规则检查状态。
        warnings: 审核和结果写回告警。
    """

    blocking_warnings = [
        warning
        for warning in warnings
        if warning.get("type") in _BLOCKING_WARNING_TITLES
    ]
    business_failure_files = {
        str(file_value)
        for warning in blocking_warnings
        if warning.get("type") == "business_processing_failed"
        for file_value in warning.get("files", [])
    }
    items = [
        _file_item(file)
        for file in files
        if (
            file.material_type != "explanation"
            and str(file.relative_path) not in business_failure_files
            and (not file.sheets or file.parse_errors)
        )
    ]
    items.extend(_status_item(status) for status in statuses if status.status == "failed")
    for warning in blocking_warnings:
        items.extend(_warning_items(warning))
    return items
