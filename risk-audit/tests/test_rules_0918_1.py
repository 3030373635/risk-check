"""0918-1 第5条历史规则发布验收。"""

from __future__ import annotations

import json
from pathlib import Path

from risk_audit import __version__
from risk_audit.configuration.loader import load_pack
from risk_audit.util import sha256_file
from test_rules_0917_6 import active_pack_for, execute, make_file, make_record


ROOT = Path(__file__).resolve().parents[1]


def test_active_rule_accepts_metering_work_responsible_position() -> None:
    """岗位名称包含“计量工作负责人”时，不得再按人员泛称提示岗位不具体。"""
    duty = make_record("position_duty", position="高压计量工作负责人")

    findings = execute(active_pack_for("duties.completeness"), [make_file("position_duty", [duty])])

    assert all(finding.check_id != "position_specific" for finding in findings)


def test_0918_1_release_is_preserved() -> None:
    """0918-1 源文档和历史 1.9.14 发布包必须保持可追溯。"""
    assert __version__ == "1.9.20"
    active = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))
    assert active["version"] == "1.9.20"
    pack = load_pack(ROOT / "rulepacks/releases/1.9.14")
    assert pack["manifest"]["source_document_sha256"] == sha256_file(
        ROOT / "规则来源/0918-1.docx"
    )
