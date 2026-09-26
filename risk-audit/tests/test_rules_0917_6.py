"""0917-6 规则差异的发布与执行验收。"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from risk_audit import __version__
from risk_audit.checks.registry import CheckContext, build_registry
from risk_audit.configuration.loader import load_pack
from risk_audit.configuration.validator import ConfigError, validate_pack
from risk_audit.engine import run_engine
from risk_audit.models import Entity, FieldValue, FileRecord, ParsedSheet, Record
from risk_audit.util import sha256_file


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
ENTITY_CODE = "205H"


def make_record(record_type: str, row: int = 3, **values: str) -> Record:
    """构造规则执行记录。

    record_type 为业务记录类型，row 为原表行号，values 为逻辑字段值。
    """
    fields = {
        name: FieldValue(value, value, f"A{row}")
        for name, value in values.items()
    }
    return Record(
        record_type,
        ENTITY_CODE,
        "06",
        "default",
        "材料.xlsx",
        record_type,
        row,
        fields,
        f"{record_type}-{row}",
    )


def make_file(sheet_type: str, records: list[Record], name: str = "材料.xlsx") -> FileRecord:
    """构造包含一张业务表的文件；sheet_type 为工作表类型，records 为明细，name 为文件名。"""
    sheet = ParsedSheet(sheet_type, sheet_type, [2], {}, {}, 10, 3, records)
    material_type = "matrix" if sheet_type == "matrix" else "three_lists"
    return FileRecord(
        Path(name),
        Path(name),
        "hash",
        "xlsx",
        ENTITY_CODE,
        [],
        False,
        "06",
        "default",
        material_type,
        [sheet],
    )


def active_pack_for(*rule_ids: str) -> dict:
    """读取并筛选激活规则包；rule_ids 为本次验证需执行的规则标识。"""
    active = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))
    pack = copy.deepcopy(load_pack(ROOT / "rulepacks/releases" / active["version"]))
    pack["rules"] = [rule for rule in pack["rules"] if rule["rule_id"] in set(rule_ids)]
    # 测试材料只表示06业务，不引入其他应报业务的缺件意见。
    pack["submission_scope"]["businesses"] = [
        {"business_code": "06", "variant_id": "default", "required": True}
    ]
    return pack


def execute(pack: dict, files: list[FileRecord]):
    """执行指定规则包；pack 为已加载配置，files 为待审文件，返回审核意见。"""
    findings, _ = run_engine(
        pack,
        build_registry(),
        files,
        {ENTITY_CODE: Entity(ENTITY_CODE, "测试单位")},
        {},
    )
    return findings


def test_required_documents_v4_ignores_entity_name_but_keeps_material_keyword() -> None:
    """第1条不得因文件名缺主体名称提示，但仍应检查材料类别关键字。"""
    capability = build_registry().get("required_documents", 4)
    assert capability is not None
    params = {
        "material_types": [],
        "naming_mode": True,
        "naming_keywords": {"matrix": ["矩阵", "取证"], "three_lists": ["三清单"]},
        "require_entity_name": False,
    }
    context = CheckContext(
        [],
        [make_file("matrix", [], "06风控矩阵.xlsx")],
        [],
        {ENTITY_CODE: Entity(ENTITY_CODE, "测试单位")},
        {},
        {"entity_aliases": {}},
        [ENTITY_CODE],
        [{"business_code": "06", "variant_id": "default", "required": True}],
    )

    assert capability.runner(context, params) == []

    context.files = [make_file("matrix", [], "06材料.xlsx")]
    issues = capability.runner(context, params)
    assert len(issues) == 1
    assert "材料类别关键字" in issues[0]["evidence"]["naming_issue"]
    assert "会计主体名称" not in issues[0]["evidence"]["naming_issue"]


def test_required_documents_v4_rejects_non_boolean_entity_name_switch() -> None:
    """require_entity_name 必须是布尔值，避免字符串“false”被当成开启。"""
    pack = copy.deepcopy(load_pack(ROOT / "rulepacks/releases/1.9.12"))
    naming_rule = next(rule for rule in pack["rules"] if rule["rule_id"] == "documents.completeness")
    naming_check = next(check for check in naming_rule["checks"] if check["check_id"] == "file_naming")
    naming_check["params"]["require_entity_name"] = "false"

    with pytest.raises(ConfigError, match="require_entity_name"):
        validate_pack(pack, build_registry())


def test_active_rules_do_not_check_incompatible_list_name_specificity() -> None:
    """第6条只检查不相容岗位清单是否存在，不再检查部门和岗位名称。"""
    row = make_record(
        "incompatible_position",
        department="管理部门",
        position_a="管理人员",
        position_b="审核人员",
    )
    pack = active_pack_for("lists.incompatible_exists", "lists.incompatible_specificity")

    assert execute(pack, [make_file("incompatible_position", [row])]) == []


def test_active_rules_restore_duty_presence_and_applicability_comparison() -> None:
    """0920-2恢复联动后，岗位清单没有对应编号但矩阵适用时应提示。"""
    matrix = make_record(
        "matrix",
        measure_id="设备业务-1.验收-控制措施01",
        applicability="适用",
    )
    pack = active_pack_for("matrices.measure_applicability")

    findings = execute(pack, [make_file("matrix", [matrix])])

    assert [finding.message for finding in findings] == ["【第16条】请结合业务实际再次核实适用性。"]


def test_active_rule_rejects_normativity_as_responsibility_attribute() -> None:
    """第10条不再把“规范性”视为有效责任属性，且意见应列出新属性口径。"""
    duty = make_record("position_duty", duty="对制度文件的规范性负主体责任。")
    pack = active_pack_for("duties.responsibility_phrase")

    findings = execute(pack, [make_file("position_duty", [duty])])

    assert len(findings) == 1
    assert "一致性/准确性/真实性/完整性/有效性/及时性/合规性" in findings[0].message
    assert findings[0].evidence["responsibility_attributes"] == []


def test_active_rule_accepts_attribute_and_responsibility_type_without_sentence_structure() -> None:
    """第10条只检查责任属性和责任类型，不再要求责任对象或承担谓语。"""
    duty = make_record("position_duty", duty="准确性，主体责任。")
    pack = active_pack_for("duties.responsibility_phrase")

    assert execute(pack, [make_file("position_duty", [duty])]) == []


def test_0917_6_release_is_rebuildable() -> None:
    """0917-6源文档必须冻结，1.9.12历史发布包必须可重建。"""
    assert __version__ == "1.9.19"
    active = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))
    assert active["version"] == "1.9.19"
    pack = load_pack(ROOT / "rulepacks/releases/1.9.12")
    assert pack["manifest"]["source_document_sha256"] == sha256_file(
        ROOT / "规则来源/0917-6.docx"
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_0917_6.py"), "--verify-only"],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "release": str(ROOT / "rulepacks/releases/1.9.12"),
        "activated": False,
        "verified": True,
    }
