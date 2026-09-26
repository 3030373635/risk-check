"""0920-2 矩阵适用性联动规则验收。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from risk_audit import __version__
from risk_audit.configuration.loader import load_pack
from risk_audit.util import sha256_file
from test_rules_0917_6 import execute, make_file, make_record


OPINION = "【第16条】请结合业务实际再次核实适用性。"
ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent


def pack_for_0920_2(*rule_ids: str) -> dict:
    """读取0920-2历史规则包；rule_ids 为本次验证需执行的规则标识。"""
    pack = load_pack(ROOT / "rulepacks/releases/1.9.16")
    pack["rules"] = [rule for rule in pack["rules"] if rule["rule_id"] in set(rule_ids)]
    pack["submission_scope"]["businesses"] = [
        {"business_code": "06", "variant_id": "default", "required": True}
    ]
    return pack


def test_existing_duty_with_not_applicable_matrix_reports_opinion() -> None:
    """岗位清单已有编号但矩阵明确不适用时，应输出文档指定意见。"""
    matrix = make_record(
        "matrix",
        measure_id="业务-12.事项-控制措施05",
        applicability="不适用",
    )
    duty = make_record(
        "position_duty",
        measure_id="其他名称-12.事项-控制措施05",
    )

    findings = execute(
        pack_for_0920_2("matrices.measure_applicability"),
        [make_file("matrix", [matrix]), make_file("position_duty", [duty])],
    )

    assert [finding.message for finding in findings] == [OPINION]


def test_existing_duty_with_empty_applicability_reports_opinion() -> None:
    """岗位清单已有编号但矩阵适用性为空时，应输出文档指定意见。"""
    matrix = make_record("matrix", measure_id="业务-12.事项-控制措施05", applicability="")
    duty = make_record("position_duty", measure_id="其他名称-12.事项-控制措施05")

    findings = execute(
        pack_for_0920_2("matrices.measure_applicability"),
        [make_file("matrix", [matrix]), make_file("position_duty", [duty])],
    )

    assert [finding.message for finding in findings] == [OPINION]


def test_missing_duty_with_applicable_matrix_reports_opinion() -> None:
    """岗位清单没有对应编号但矩阵明确适用时，应输出文档指定意见。"""
    matrix = make_record(
        "matrix",
        measure_id="业务-12.事项-控制措施05",
        applicability="是",
    )
    unrelated_duty = make_record(
        "position_duty",
        measure_id="其他名称-13.事项-控制措施06",
    )

    findings = execute(
        pack_for_0920_2("matrices.measure_applicability"),
        [make_file("matrix", [matrix]), make_file("position_duty", [unrelated_duty])],
    )

    assert [finding.message for finding in findings] == [OPINION]


def test_missing_duty_sheet_with_applicable_matrix_reports_opinion() -> None:
    """岗位清单材料整体缺失时，适用矩阵行的已有编号列仍视为空值。"""
    matrix = make_record(
        "matrix",
        measure_id="业务-12.事项-控制措施05",
        applicability="适用",
    )

    findings = execute(
        pack_for_0920_2("matrices.measure_applicability"),
        [make_file("matrix", [matrix])],
    )

    assert [finding.message for finding in findings] == [OPINION]


@pytest.mark.parametrize(
    ("applicability", "has_matching_duty"),
    [
        ("是", True),
        ("适用", True),
        ("", False),
        ("否", False),
        ("不适用", False),
    ],
)
def test_matching_applicability_and_duty_states_leave_opinion_empty(
    applicability: str,
    has_matching_duty: bool,
) -> None:
    """适用且已有编号，或不适用且没有编号时，不得产生联动意见。"""
    matrix = make_record(
        "matrix",
        measure_id="业务-12.事项-控制措施05",
        applicability=applicability,
    )
    duty_measure = (
        "其他名称-12.事项-控制措施05"
        if has_matching_duty
        else "其他名称-13.事项-控制措施06"
    )
    duty = make_record("position_duty", measure_id=duty_measure)

    findings = execute(
        pack_for_0920_2("matrices.measure_applicability"),
        [make_file("matrix", [matrix]), make_file("position_duty", [duty])],
    )

    assert findings == []


def test_0920_2_release_is_rebuildable() -> None:
    """0920-2来源必须冻结，1.9.16历史修复包必须可重建。"""
    assert __version__ == "1.9.19"
    active = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))
    assert active["version"] == "1.9.19"
    pack = load_pack(ROOT / "rulepacks/releases/1.9.16")
    assert pack["manifest"]["source_document_sha256"] == sha256_file(
        ROOT / "规则来源/0920-2.docx"
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_0920_2.py"), "--verify-only"],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "release": str(ROOT / "rulepacks/releases/1.9.16"),
        "activated": False,
        "verified": True,
    }
