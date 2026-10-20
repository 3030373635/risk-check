"""测量高频同文本 Finding 的统计汇总与渲染耗时。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from risk_audit.audit_statistics import (
    collect_audit_statistics,
    write_audit_statistics,
    write_audit_statistics_html,
)
from risk_audit.models import FileRecord, Finding


def _benchmark_file() -> FileRecord:
    """构建不依赖真实磁盘工作簿的基准文件；无参数。"""
    return FileRecord(
        source=Path("benchmark.xlsx"),
        relative_path=Path("测试单位/01业务/benchmark.xlsx"),
        sha256="benchmark",
        true_format="xlsx",
        entity_code="BENCHMARK",
        entity_evidence=[],
        entity_conflict=False,
        business_code="01",
        variant_id="default",
        material_type="matrix",
        business_id="benchmark_business",
        business_directory=Path("测试单位/01业务"),
    )


def _benchmark_finding(file: FileRecord, row: int, message: str) -> Finding:
    """构建一个精确到行的基准 Finding。

    Args:
        file: Finding 所属的基准业务文件。
        row: Finding 所属行号。
        message: 高频重复审核意见文本。
    """
    return Finding(
        finding_key=f"benchmark-{row}",
        finding_id=f"benchmark-{row}",
        rule_id="benchmark.rule",
        check_id="benchmark_check",
        display_code="R1",
        revision=1,
        severity="violation",
        entity_code=file.entity_code or "",
        entity_name="测试单位",
        business_code=file.business_code or "",
        variant_id=file.variant_id,
        file_path=str(file.relative_path),
        sheet="风控矩阵",
        row=row,
        message=message,
        evidence={},
        location_policy="row",
        business_id=file.business_id or "",
    )


def run_benchmark(findings_count: int, ownership_count: int) -> dict[str, int | float]:
    """运行一次合成终态基准。

    Args:
        findings_count: 需要构造的 Finding 数量。
        ownership_count: 需要构造的意见单元格数量。

    Returns:
        数量和分阶段耗时组成的稳定指标字典。
    """
    if findings_count < 0 or ownership_count < 0:
        raise ValueError("基准数量不能为负数")
    file = _benchmark_file()
    message = "【第1条】请核实高频重复审核意见。"
    findings = [
        _benchmark_finding(file, row, message)
        for row in range(2, findings_count + 2)
    ]
    ownership = {
        str(file.relative_path): [
            {
                "sheet": "风控矩阵",
                "cell": f"H{row}",
                "program_text": message,
            }
            for row in range(2, ownership_count + 2)
        ],
    }
    total_started = perf_counter()
    collect_started = perf_counter()
    statistics = collect_audit_statistics(
        [file],
        ownership,
        "基准报送包",
        findings=findings,
    )
    collect_seconds = perf_counter() - collect_started
    with TemporaryDirectory(prefix="risk-audit-benchmark-") as directory:
        output_root = Path(directory)
        excel_started = perf_counter()
        write_audit_statistics(output_root / "审核统计表.xlsx", statistics)
        excel_seconds = perf_counter() - excel_started
        html_started = perf_counter()
        write_audit_statistics_html(output_root / "审核统计表.html", statistics)
        html_seconds = perf_counter() - html_started
    return {
        "findings": findings_count,
        "ownership": ownership_count,
        "collect_seconds": float(collect_seconds),
        "excel_seconds": float(excel_seconds),
        "html_seconds": float(html_seconds),
        "total_seconds": float(perf_counter() - total_started),
    }


def main() -> int:
    """解析命令行参数、运行基准并输出 JSON；无参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings", type=int, default=200_000)
    parser.add_argument("--ownership", type=int, default=60_000)
    args = parser.parse_args()
    metrics = run_benchmark(args.findings, args.ownership)
    print(json.dumps(metrics, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
