"""0917-4 第10条规范性责任属性的发布验收。"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from risk_audit import __version__
from risk_audit.configuration.loader import load_pack
from risk_audit.engine import run_engine
from risk_audit.models import FieldValue, FileRecord, ParsedSheet, Record
from risk_audit.util import sha256_file


ROOT = Path(__file__).resolve().parents[1]


def responsibility_record(text: str, row: int) -> Record:
    """构造第10条岗位职责记录；text 为职责原文，row 为来源行号。"""
    fields = {
        "duty": FieldValue(text, text, f"F{row}"),
    }
    return Record(
        "position_duty",
        "205H",
        "06",
        "default",
        "x.xlsx",
        "岗位职责清单",
        row,
        fields,
        "rules-0917-4",
    )


def responsibility_file(records: list[Record]) -> list[FileRecord]:
    """构造岗位职责文件；records 为第10条待审核记录。"""
    sheet = ParsedSheet("岗位职责清单", "position_duty", [2], {}, {}, 9, 3, records)
    return [
        FileRecord(
            Path("x.xlsx"),
            Path("x.xlsx"),
            "hash",
            "xlsx",
            "205H",
            [],
            False,
            "06",
            "default",
            "three_lists",
            [sheet],
        )
    ]


def test_historical_1911_rulepack_accepts_normativity_only_as_a_responsibility_attribute(registry) -> None:
    """历史1.9.11规则包识别规范性责任属性，但不误认普通业务名词。"""
    assert __version__ == "1.9.19"
    pack = load_pack(ROOT / "rulepacks/releases/1.9.11")
    selected = copy.deepcopy(pack)
    selected["rules"] = [
        rule for rule in selected["rules"]
        if rule["rule_id"] == "duties.responsibility_phrase"
    ]
    records = [
        responsibility_record("对制度文件的规范性负主体责任。", 3),
        responsibility_record("参与验收并核对技术规范书，负有主体责任。", 4),
    ]

    findings, _ = run_engine(
        selected,
        registry,
        responsibility_file(records),
        {"205H": type("Entity", (), {"name": "测试单位"})()},
        {},
    )

    assert [finding.row for finding in findings] == [4]
    assert "未明确责任属性" in findings[0].message
    assert pack["manifest"]["source_document_sha256"] == sha256_file(
        ROOT / "规则来源/0917-4.docx"
    )


def test_0917_4_publish_script_rebuilds_release() -> None:
    """1.9.9规则包应能从冻结来源和1.9.8基线完整重建；无参数。"""
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_0917_4.py"), "--verify-only"],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["verified"] is True
    assert payload["activated"] is False
    assert json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))["version"] == "1.9.19"


def test_active_0917_4_rulepack_uses_current_audit_pipeline(tmp_path) -> None:
    """1.9.9应继续使用当前读取、排序、写回和反馈报告流程。"""
    from openpyxl import Workbook

    from run_audit import configure_soffice
    from risk_audit.runner import audit

    configure_soffice()
    input_root = tmp_path / "input/信通公司"
    input_root.mkdir(parents=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "风控矩阵"
    worksheet.append(["控制措施编号", "控制措施", "责任主体", "是否适用及原因"])
    worksheet.append(["设备业务-1.设备验收-控制措施01", "核对设备", "信通公司-财务部", "适用"])
    workbook.save(input_root / "06信通公司风控矩阵.xlsx")

    result = audit(
        input_root.parent,
        tmp_path / "output",
        ROOT / "rulepacks/releases/1.9.9",
        ROOT.parent / "审核/会计主体清单20260907.xlsx",
        ROOT.parent,
        tmp_path / "runs",
    )

    assert result["parsed_files"] == 1
    assert "feedback_report" not in result
    assert "feedback_html_report" not in result
    assert "audit_summary_report" not in result
