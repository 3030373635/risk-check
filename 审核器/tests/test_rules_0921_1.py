"""0921-1 会计主体隔离与矩阵适用性规则验收。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from risk_audit import __version__
from risk_audit.configuration.loader import load_pack
from risk_audit.util import sha256_file
from test_rules_0917_6 import active_pack_for, execute, make_file, make_record


OPINION = "【第16条】请结合业务实际再次核实适用性。"
ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent


def test_existing_duty_with_empty_applicability_leaves_opinion_empty() -> None:
    """岗位清单已有编号但矩阵适用性为空时，不得生成联动意见。"""
    matrix = make_record("matrix", measure_id="业务-12.事项-控制措施05", applicability="")
    duty = make_record("position_duty", measure_id="其他名称-12.事项-控制措施05")

    findings = execute(
        active_pack_for("matrices.measure_applicability"),
        [make_file("matrix", [matrix]), make_file("position_duty", [duty])],
    )

    assert findings == []


def test_existing_duty_with_unknown_nonempty_applicability_reports_opinion() -> None:
    """已有编号且适用性填写了非空未知值时，仍须提示核实。"""
    matrix = make_record(
        "matrix",
        measure_id="业务-12.事项-控制措施05",
        applicability="待确认",
    )
    duty = make_record("position_duty", measure_id="其他名称-12.事项-控制措施05")

    findings = execute(
        active_pack_for("matrices.measure_applicability"),
        [make_file("matrix", [matrix]), make_file("position_duty", [duty])],
    )

    assert [finding.message for finding in findings] == [OPINION]


def test_other_entity_duty_does_not_satisfy_current_matrix() -> None:
    """不同会计主体的同号岗位记录不得与当前矩阵跨主体匹配。"""
    matrix = make_record(
        "matrix",
        measure_id="业务-12.事项-控制措施05",
        applicability="适用",
    )
    other_entity_duty = make_record(
        "position_duty",
        measure_id="其他名称-12.事项-控制措施05",
    )
    other_entity_duty.entity_code = "OTHER"

    findings = execute(
        active_pack_for("matrices.measure_applicability"),
        [
            make_file("matrix", [matrix]),
            make_file("position_duty", [other_entity_duty]),
        ],
    )

    assert [finding.message for finding in findings] == [OPINION]


def test_0921_1_release_is_active_and_rebuildable() -> None:
    """0921-1来源必须冻结，1.9.17历史规则包必须可重建且不改变当前激活版本。"""
    assert __version__ == "1.9.19"
    active = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))
    assert active["version"] == "1.9.19"
    pack = load_pack(ROOT / "rulepacks/releases/1.9.17")
    assert pack["manifest"]["source_document_sha256"] == sha256_file(
        ROOT / "规则来源/0921-1.docx"
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_0921_1.py"), "--verify-only"],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "release": str(ROOT / "rulepacks/releases/1.9.17"),
        "activated": False,
        "verified": True,
    }
