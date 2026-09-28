"""验证 0928-1 删除第7条并保留系统类型预处理提示。"""

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


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "rulepacks/releases/1.9.20"
PREPROCESSING_OPINION = (
    "【预处理】请补充系统类型，该列填报枚举值：一级部署系统、二级部署系统、"
    "三级部署系统，请根据系统的实际情况填报。"
)


def test_1920_removes_rule_7_and_keeps_preprocessing_notice() -> None:
    """新规则包不得包含第7条，但必须保留无编号的系统类型预处理提示。"""

    pack = load_pack(RELEASE)

    assert all(rule["display_code"] != "R07" for rule in pack["rules"])
    notice_rule = next(
        rule for rule in pack["rules"]
        if rule["rule_id"] == "preprocessing.system_type_notice"
    )
    assert notice_rule["display_code"] == "P01"
    assert notice_rule["checks"][0]["message"] == {
        "review": PREPROCESSING_OPINION,
        "violation": PREPROCESSING_OPINION,
    }


def test_unknown_system_owner_emits_preprocessing_notice() -> None:
    """无法判断系统类型时输出预处理提示，不再生成第7条意见。"""

    pack = load_pack(RELEASE)
    pack["rules"] = [
        rule for rule in pack["rules"]
        if rule["rule_id"] == "preprocessing.system_type_notice"
    ]
    row = make_record(
        "system_rule",
        system_owner="长沙供电分公司",
        system_type="",
    )

    findings = execute(pack, [make_file("system_rule", [row])])

    assert [(finding.display_code, finding.message) for finding in findings] == [
        ("P01", PREPROCESSING_OPINION),
    ]


@pytest.mark.parametrize(
    ("system_owner", "system_type"),
    [("总部", "一级部署系统"), ("长沙供电分公司", "三级部署系统")],
)
def test_resolved_or_preserved_system_type_has_no_preprocessing_notice(
    system_owner: str,
    system_type: str,
) -> None:
    """可自动识别或人工确认三级部署时不得生成预处理提示。

    Args:
        system_owner: 系统规则管理主体。
        system_type: 预处理后的系统类型。
    """

    pack = load_pack(RELEASE)
    pack["rules"] = [
        rule for rule in pack["rules"]
        if rule["rule_id"] == "preprocessing.system_type_notice"
    ]
    row = make_record(
        "system_rule",
        system_owner=system_owner,
        system_type=system_type,
    )

    assert execute(pack, [make_file("system_rule", [row])]) == []


def test_0928_source_and_release_are_rebuildable() -> None:
    """0928-1 来源须冻结，1.9.20 发布包须可重复构建。"""

    source = ROOT / "规则来源/0928-1.docx"
    pack = load_pack(RELEASE)
    active = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))

    assert __version__ == "1.9.20"
    assert active == {
        "version": "1.9.20",
        "content_hash": pack["manifest"]["content_hash"],
    }
    assert pack["manifest"]["source_document_sha256"] == sha256_file(source)

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_0928_1.py"), "--verify-only"],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "release": str(RELEASE),
        "activated": False,
        "verified": True,
    }
