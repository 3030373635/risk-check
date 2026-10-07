"""验证导致任务部分完成的原因能生成统一未完成项。"""

from pathlib import Path
from types import SimpleNamespace

from risk_audit.incomplete_items import collect_incomplete_items
from risk_audit.models import CheckStatus, FileRecord


def make_file(
    relative_path: str,
    *,
    material_type: str = "matrix",
    parse_errors: list[str] | None = None,
) -> FileRecord:
    """创建最小文件记录。

    Args:
        relative_path: 文件在材料根目录下的相对路径。
        material_type: 材料类型。
        parse_errors: 文件解析错误列表。
    """

    return FileRecord(
        source=Path("/input") / relative_path,
        relative_path=Path(relative_path),
        sha256="a" * 64,
        true_format="xlsx",
        entity_code="4301",
        entity_evidence=[],
        entity_conflict=False,
        business_code="05",
        variant_id="default",
        material_type=material_type,
        parse_errors=list(parse_errors or []),
        business_id="distribution_grid_project_management",
    )


def test_collect_incomplete_items_covers_every_partial_completion_cause() -> None:
    """文件、规则和输出失败都必须生成可见的未完成项；无参数。"""
    business_error = make_file(
        "本部/05配网/附件清单.xlsx",
        parse_errors=["未找到业务模板映射"],
    )
    # 复现原问题：工作表已读取，但业务识别失败，仍必须出现在未完成项。
    business_error.sheets.append(SimpleNamespace(title="岗位职责清单"))
    unparsed_file = make_file("本部/06设备资产/矩阵.xlsx")
    failed_status = CheckStatus(
        "documents.completeness",
        "required_matrix_and_lists",
        "failed",
        "规则执行失败",
        2,
    )
    warnings = [
        {"type": "no_writable_sheet", "file": "无可写工作表.xlsx", "message": "没有可写工作表"},
        {"type": "output_incompatible_file", "file": "格式不兼容.xlsx", "message": "文件无法安全写回"},
        {
            "type": "business_processing_failed",
            "entity_key": "4301",
            "business_code": "09",
            "files": ["本部/09职工福利与薪酬/三清单.xlsx"],
            "stage": "数据预处理",
            "reason": "模板无法读取",
            "error_type": "ValueError",
            "log_reference": "report/audit.log",
        },
    ]

    items = collect_incomplete_items(
        [business_error, unparsed_file],
        [failed_status],
        warnings,
    )

    assert {item["type"] for item in items} == {
        "business_identification_failed",
        "file_parsing_failed",
        "rule_execution_failed",
        "no_writable_sheet",
        "output_incompatible_file",
        "business_processing_failed",
    }
    business_item = next(item for item in items if item["type"] == "business_identification_failed")
    assert business_item == {
        "type": "business_identification_failed",
        "title": "业务识别失败",
        "entity_code": "4301",
        "entity_name": "本部",
        "business_code": "05",
        "business_name": "05配网",
        "file": "本部/05配网/附件清单.xlsx",
        "sheet": "岗位职责清单",
        "stage": "业务识别",
        "message": "未找到业务模板映射",
        "error_type": "BusinessIdentityError",
        "log_reference": "",
    }


def test_collect_incomplete_items_excludes_limits_and_nonblocking_warnings() -> None:
    """核对限制和普通告警不得被误标为未完成项；无参数。"""
    parsed_file = make_file("本部/05配网/矩阵.xlsx")
    parsed_file.sheets.append(object())
    partial_status = CheckStatus(
        "references.current_matrix",
        "deleted_text_in_duty",
        "partial",
        "有1条核对限制",
        10,
        limitations=1,
    )

    items = collect_incomplete_items(
        [parsed_file],
        [partial_status],
        [{"type": "hidden_sheets_notice", "message": "存在隐藏工作表"}],
    )

    assert items == []
