"""验证 1020 重庆第14条更新及 1.9.35 规则包。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from risk_audit.configuration.loader import load_pack
from risk_audit.pipeline_versions import (
    CONFIRMED_RULE_VERSIONS,
    PREPROCESSING_RULE_VERSIONS,
    SYSTEM_TYPE_PREPROCESSING_RULE_VERSIONS,
)
from risk_audit.util import sha256_file


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "rulepacks/releases/1.9.35"
SOURCE = ROOT / "规则来源/风控矩阵1020-重庆.docx"
SOURCE_SHA256 = "7430356d3fe147bde75f95a04c28a640f2579431712d25f8aeb4e5487767de2c"


def test_release_matches_1020_rule14_and_source_document() -> None:
    """1.9.35 应只升级第14条并绑定 1020 冻结来源文档。"""

    pack = load_pack(RELEASE)
    rules = {rule["display_code"]: rule for rule in pack["rules"]}
    rule = rules["R14"]

    assert rule["rule_id"] == "chongqing.system_control_consistency_1020"
    assert rule["revision"] == 2
    assert rule["checks"][0]["check_id"] == "system_control_consistency_1020"
    assert rule["checks"][0]["params"] == {
        "compare_system_names": False,
        "matrix_missing_opinion": "需核实矩阵中是否有该条措施或控制方式是否正确",
        "validate_build_status": True,
    }
    assert pack["manifest"]["source_document_sha256"] == sha256_file(SOURCE)
    assert pack["manifest"]["source_document_sha256"] == SOURCE_SHA256


def test_publish_script_rebuilds_1935_deterministically() -> None:
    """1020 重庆发布脚本应能从 1.9.34 稳定重建 1.9.35。"""

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_1020_chongqing_1935.py"), "--verify-only"],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verified"] is True


def test_chongqing_region_activates_1935_pipeline() -> None:
    """重庆地区索引和三类处理管线应同时切换到 1.9.35。"""

    index = json.loads((ROOT / "regions/index.json").read_text(encoding="utf-8"))
    profile = next(item for item in index["regions"] if item["region_code"] == "chongqing")
    manifest = json.loads((RELEASE / "manifest.json").read_text(encoding="utf-8"))

    assert profile["rulepack_version"] == "1.9.35"
    assert profile["rulepack_content_hash"] == manifest["content_hash"]
    assert "1.9.35" in CONFIRMED_RULE_VERSIONS
    assert "1.9.35" in PREPROCESSING_RULE_VERSIONS
    assert "1.9.35" in SYSTEM_TYPE_PREPROCESSING_RULE_VERSIONS
