"""0917-3 第10条诊断式审核意见的发布验收。"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from risk_audit.configuration.loader import load_pack
from risk_audit.engine import run_engine
from risk_audit.models import FieldValue, FileRecord, ParsedSheet, Record
from risk_audit.util import sha256_file


ROOT = Path(__file__).resolve().parents[1]


def responsibility_record(text: str) -> Record:
    """构造第10条岗位职责记录；text 为待审核的岗位职责原文。"""
    fields = {
        "duty": FieldValue(text, text, "F3"),
    }
    return Record("position_duty", "205H", "06", "default", "x.xlsx", "岗位职责清单", 3,
                  fields, "rules-0917-3")


def responsibility_file(record: Record) -> list[FileRecord]:
    """构造包含单条职责的真实文件边界；record 为第10条输入记录。"""
    sheet = ParsedSheet("岗位职责清单", "position_duty", [2], {}, {}, 9, 3, [record])
    return [FileRecord(Path("x.xlsx"), Path("x.xlsx"), "hash", "xlsx", "205H", [], False,
                       "06", "default", "three_lists", [sheet])]


def test_historical_rulepack_renders_diagnostic_responsibility_opinion(registry) -> None:
    """历史1.9.8判定仍输出已识别、缺少和当前修改建议三部分。"""
    pack = load_pack(ROOT / "rulepacks/releases/1.9.8")
    selected = copy.deepcopy(pack)
    selected["rules"] = [rule for rule in selected["rules"] if rule["rule_id"] == "duties.responsibility_phrase"]
    record = responsibility_record("对申请资料负有主体责任")

    findings, _ = run_engine(
        selected,
        registry,
        responsibility_file(record),
        {"205H": type("Entity", (), {"name": "测试单位"})()},
        {},
    )

    assert [finding.message for finding in findings] == [
        "【第10条】岗位职责责任句式不完整：已识别“负有主体责任”和责任对象，但未明确责任属性。"
        "请结合实际补充“一致性、准确性、真实性、完整性、有效性、及时性、合规性”等至少一项责任属性。"
        "建议句式：“对……的一致性/准确性/真实性/完整性/有效性/及时性/合规性负主体/templates/审批责任。”"
    ]
    assert pack["manifest"]["source_document_sha256"] == sha256_file(ROOT / "规则来源/0917-3.docx")


def test_0917_3_publish_script_rebuilds_release() -> None:
    """1.9.8规则包应能从冻结来源和1.9.7基线完整重建；无参数。"""
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_0917_3.py"), "--verify-only"],
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
