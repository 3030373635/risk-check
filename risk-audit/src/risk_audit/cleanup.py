"""审核结果工作簿的程序输出列清理。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from risk_audit.readers.ooxml import (
    WORKSHEET_DIMENSION_CELL_LIMIT,
    load_compatible_workbook,
    prepare_oversized_workbook,
)
from risk_audit.util import norm_text


AUDIT_OUTPUT_HEADERS = frozenset({
    norm_text("审核意见"),
    norm_text("省公司版本责任主体（核对后删除）"),
    norm_text("岗位清单已有的控制措施编号"),
})
CLEANUP_DIMENSION_CELL_LIMIT = WORKSHEET_DIMENSION_CELL_LIMIT


def _target_columns(worksheet: object) -> list[int]:
    """返回当前工作表中需删除的程序输出列。

    Args:
        worksheet: openpyxl 工作表对象。
    """

    # 直接检查已存在的单元格，避免异常最大维度导致遍历大量空单元格。
    columns = {
        cell.column
        for cell in worksheet._cells.values()
        if norm_text(cell.value) in AUDIT_OUTPUT_HEADERS
    }
    return sorted(columns, reverse=True)


def _clean_workbook(source: Path, destination: Path) -> int:
    """清理单个工作簿，并返回删除的列数。

    Args:
        source: 待清理的 OOXML 工作簿。
        destination: 清理后工作簿的保存路径。
    """

    with tempfile.TemporaryDirectory(prefix="risk-audit-cleanup-workbook-") as temporary_directory:
        prepared, _ = prepare_oversized_workbook(
            source,
            Path(temporary_directory) / source.name,
            worksheet_dimension_cell_limit=CLEANUP_DIMENSION_CELL_LIMIT,
        )
        workbook = load_compatible_workbook(
            prepared,
            data_only=False,
            rich_text=True,
            keep_links=True,
        )
        removed_columns = 0
        try:
            for worksheet in workbook.worksheets:
                columns = _target_columns(worksheet)
                for column in columns:
                    # 必须从右向左删除，防止前面列号因位移失效。
                    worksheet.delete_cols(column, 1)
                removed_columns += len(columns)
            if removed_columns:
                destination.parent.mkdir(parents=True, exist_ok=True)
                workbook.save(destination)
        finally:
            workbook.close()
    return removed_columns


def _is_temporary_file(path: Path) -> bool:
    """判断文件是否为 Office/WPS 或系统临时文件。

    Args:
        path: 待检查的文件路径。
    """

    return path.name.startswith((".", "~$"))


def clean_audit_directory(input_root: str | Path, output_root: str | Path) -> dict[str, int]:
    """将审核结果清理到新目录。

    Args:
        input_root: 待清理的审核结果目录。
        output_root: 不存在的清理结果目录。
    """

    source_directory = Path(input_root).expanduser().resolve()
    destination_directory = Path(output_root).expanduser().resolve()
    if not source_directory.exists():
        raise FileNotFoundError(f"输入目录不存在：{source_directory}")
    if not source_directory.is_dir():
        raise ValueError(f"输入路径必须是目录：{source_directory}")
    if (
        source_directory == destination_directory
        or source_directory in destination_directory.parents
        or destination_directory in source_directory.parents
    ):
        raise ValueError("输入目录与输出目录不能相同或相互包含")
    if destination_directory.exists():
        raise FileExistsError(f"输出目录已存在：{destination_directory}")

    summary = {
        "processed_workbooks": 0,
        "unchanged_files": 0,
        "removed_columns": 0,
    }
    destination_directory.parent.mkdir(parents=True, exist_ok=True)
    staging_directory = Path(tempfile.mkdtemp(
        prefix=f".{destination_directory.name}-",
        dir=destination_directory.parent,
    ))
    try:
        for source in sorted(path for path in source_directory.rglob("*") if path.is_file()):
            if _is_temporary_file(source):
                continue
            destination = staging_directory / source.relative_to(source_directory)
            removed_columns = 0
            if source.suffix.lower() == ".xlsx":
                removed_columns = _clean_workbook(source, destination)
            if removed_columns:
                summary["processed_workbooks"] += 1
                summary["removed_columns"] += removed_columns
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            summary["unchanged_files"] += 1
        # 只有全部文件成功后才公开最终输出目录，避免留下半成品。
        staging_directory.rename(destination_directory)
    except Exception:
        shutil.rmtree(staging_directory, ignore_errors=True)
        raise
    return summary
