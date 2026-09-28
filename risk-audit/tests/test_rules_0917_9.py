"""0917-9 第10条与意见表删除的发布验收。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from risk_audit import __version__
from risk_audit.configuration.loader import load_pack
from risk_audit.util import sha256_file
from test_rules_0917_6 import active_pack_for, execute, make_file, make_record


ROOT = Path(__file__).resolve().parents[1]
STANDARD_OPINION = (
    "岗位职责请按照国网标准句式编制，“对……的一致性/准确性/真实性/"
    "完整性/有效性/及时性/合规性负主体/审核/审批责任。”"
)


@pytest.mark.parametrize(
    "attribute",
    ["一致性", "准确性", "真实性", "完整性", "有效性", "及时性", "合规性"],
)
def test_active_rule_accepts_each_responsibility_attribute(attribute: str) -> None:
    """attribute 为0917-9允许的责任属性；七类属性均应与任一责任类型组成通过条件。"""
    duty = make_record("position_duty", duty=f"{attribute}，主体责任。")

    assert execute(
        active_pack_for("duties.responsibility_phrase"),
        [make_file("position_duty", [duty])],
    ) == []


@pytest.mark.parametrize("responsibility_type", ["主体责任", "审核责任", "审批责任"])
def test_active_rule_accepts_each_responsibility_type(responsibility_type: str) -> None:
    """responsibility_type 为0917-9允许的责任类型；三类责任均应与任一属性组成通过条件。"""
    duty = make_record("position_duty", duty=f"真实性，{responsibility_type}。")

    assert execute(
        active_pack_for("duties.responsibility_phrase"),
        [make_file("position_duty", [duty])],
    ) == []


@pytest.mark.parametrize(
    ("duty_text", "expected_attributes", "expected_types"),
    [
        ("准确性。", ["准确性"], []),
        ("主体责任。", [], ["主体责任"]),
        ("办理日常工作。", [], []),
    ],
)
def test_active_rule_requires_both_responsibility_conditions(
    duty_text: str,
    expected_attributes: list[str],
    expected_types: list[str],
) -> None:
    """duty_text 为职责文本，expected_attributes/expected_types 为应识别的两类关键词。"""
    duty = make_record("position_duty", duty=duty_text)

    findings = execute(
        active_pack_for("duties.responsibility_phrase"),
        [make_file("position_duty", [duty])],
    )

    assert len(findings) == 1
    assert findings[0].evidence["responsibility_attributes"] == expected_attributes
    assert findings[0].evidence["responsibility_types"] == expected_types


def test_active_rule_uses_0917_9_standard_opinion() -> None:
    """第10条缺少责任属性时仅输出0917-9指定的统一提示。"""
    duty = make_record("position_duty", duty="承担主体责任。")

    findings = execute(active_pack_for("duties.responsibility_phrase"), [make_file("position_duty", [duty])])

    assert len(findings) == 1
    assert findings[0].message == f"【第10条】{STANDARD_OPINION}"


def test_0917_9_release_is_rebuildable() -> None:
    """0917-9源文档必须冻结，1.9.13历史发布包必须可重建。"""
    assert __version__ == "1.9.19"
    active = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))
    assert active["version"] == "1.9.19"
    pack = load_pack(ROOT / "rulepacks/releases/1.9.13")
    assert pack["manifest"]["source_document_sha256"] == sha256_file(
        ROOT / "规则来源/0917-9.docx"
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_0917_9.py"), "--verify-only"],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "release": str(ROOT / "rulepacks/releases/1.9.13"),
        "activated": False,
        "verified": True,
    }
