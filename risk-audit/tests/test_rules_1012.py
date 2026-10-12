"""验证 1012 湖南公司版模板匹配与财务管理专项规则。"""

from __future__ import annotations

import json
import copy
import subprocess
import sys
from pathlib import Path

import pytest

from risk_audit import __version__
from risk_audit.checks.registry import CheckContext, build_registry
from risk_audit.configuration.loader import load_pack
from risk_audit.engine import run_engine
from risk_audit.models import Entity, FieldValue, FileRecord, ParsedSheet, Record
from risk_audit.util import sha256_file


ENTITY_CODE = "E001"
FINANCIAL_BUSINESS_ID = "financial_management"
FINANCIAL_BUSINESS_CODE = "02"
ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "rulepacks/releases/1.9.21"
TEMPLATE_MATCH_OPINION = "【第3条】未匹配到对应模板，请确认模板对应关系。"
FINANCIAL_OPINION = (
    "【第17条】29.项目可研经济性、财务合规性检查和45.现金收费解缴控制"
    "关键控制点的适用性请再次核实"
)


def make_record(record_type: str, row: int = 3, **values: str) -> Record:
    """构造财务管理审核记录。

    Args:
        record_type: 矩阵或岗位职责清单记录类型。
        row: 原工作表行号。
        values: 按逻辑字段名给出的单元格文字。
    """

    fields = {
        name: FieldValue(value, value, f"A{row}")
        for name, value in values.items()
    }
    return Record(
        record_type,
        ENTITY_CODE,
        FINANCIAL_BUSINESS_CODE,
        "default",
        "02财务管理.xlsx",
        record_type,
        row,
        fields,
        f"{record_type}-{row}",
        business_id=FINANCIAL_BUSINESS_ID,
    )


def make_context(
    records: list[Record],
    *,
    all_records: list[Record] | None = None,
    files: list[FileRecord] | None = None,
) -> CheckContext:
    """构造规则能力上下文。

    Args:
        records: 当前能力直接检查的记录。
        all_records: 用于跨表关联的全部记录。
        files: 当前业务的材料文件。
    """

    return CheckContext(
        records,
        files or [],
        records if all_records is None else all_records,
        {},
        {},
        {},
        [ENTITY_CODE],
        [{
            "business_id": FINANCIAL_BUSINESS_ID,
            "business_code": FINANCIAL_BUSINESS_CODE,
            "variant_id": "default",
            "required": True,
        }],
    )


def execute_release_rule(rule_id: str, file: FileRecord) -> list:
    """执行 1.9.21 中指定规则。

    Args:
        rule_id: 需要单独验收的稳定规则标识。
        file: 带解析结果和模板匹配状态的业务文件。
    """

    pack = copy.deepcopy(load_pack(RELEASE))
    pack["rules"] = [rule for rule in pack["rules"] if rule["rule_id"] == rule_id]
    pack["submission_scope"] = {
        "batch": "second",
        "entity_codes": [ENTITY_CODE],
        "businesses": [{
            "business_id": file.business_id,
            "business_code": file.business_code or "",
            "variant_id": file.variant_id,
            "required": True,
        }],
    }
    findings, _ = run_engine(
        pack,
        build_registry(),
        [file],
        {ENTITY_CODE: Entity(ENTITY_CODE, "测试单位")},
        {},
        run_id="1012-test",
    )
    return findings


def test_template_match_notice_reports_unmatched_matrix_once() -> None:
    """未匹配模板的矩阵应在首条有效记录产生一条指定意见。"""
    capability = build_registry().get("template_match_notice", 1)
    assert capability is not None
    record = make_record("matrix", measure_id="未知业务-1-控制措施01")
    sheet = ParsedSheet("矩阵", "matrix", [2], {}, {}, 8, 3, [record])
    file = FileRecord(
        Path("风控矩阵.xlsx"),
        Path("风控矩阵.xlsx"),
        "hash",
        "xlsx",
        ENTITY_CODE,
        [],
        False,
        "",
        "default",
        "matrix",
        [sheet],
        business_id="unmatched_1234",
        preservation={
            "template_match": {
                "status": "unmatched",
                "reason": "未找到业务模板映射",
            }
        },
    )

    issues = capability.runner(make_context([record], files=[file]), {})

    assert len(issues) == 1
    assert issues[0]["record"] is record
    assert issues[0]["evidence"]["template_status"] == "unmatched"


@pytest.mark.parametrize("applicability", ["", "否", "不适用"])
def test_financial_key_measure_missing_duty_always_reports(
    applicability: str,
) -> None:
    """财务关键措施没有岗位清单编号时应始终提示。"""
    capability = build_registry().get("financial_management_applicability", 1)
    assert capability is not None
    matrix = make_record(
        "matrix",
        measure_id="财务管理-45.现金收费解缴控制-控制措施03",
        applicability=applicability,
    )

    issues = capability.runner(
        make_context([matrix]),
        {
            "business_id": FINANCIAL_BUSINESS_ID,
            "measure_ids": [[45, 1], [45, 2], [45, 3], [45, 4], [45, 5], [29, 1]],
        },
    )

    assert len(issues) == 1
    assert issues[0]["evidence"]["duty_measure_present"] is False


@pytest.mark.parametrize("applicability", ["不适用", "否（不适用）", "不适用（无此业务）"])
def test_financial_key_measure_not_applicable_reports_with_existing_duty(
    applicability: str,
) -> None:
    """财务关键措施即使已有岗位记录，明确不适用仍应提示。"""
    capability = build_registry().get("financial_management_applicability", 1)
    assert capability is not None
    matrix = make_record(
        "matrix",
        measure_id="财务管理-29.项目可研经济性、财务合规性检查-控制措施01",
        applicability=applicability,
    )
    duty = make_record(
        "position_duty",
        row=4,
        measure_id="财务管理-29.项目可研经济性、财务合规性检查-控制措施01",
    )

    issues = capability.runner(
        make_context([matrix], all_records=[matrix, duty]),
        {
            "business_id": FINANCIAL_BUSINESS_ID,
            "measure_ids": [[45, 1], [45, 2], [45, 3], [45, 4], [45, 5], [29, 1]],
        },
    )

    assert len(issues) == 1
    assert issues[0]["evidence"]["duty_measure_present"] is True
    assert issues[0]["evidence"]["applicability_decision"] == "not_applicable"


@pytest.mark.parametrize("applicability", ["", "是", "适用"])
def test_financial_key_measure_with_duty_and_no_negative_value_passes(
    applicability: str,
) -> None:
    """岗位编号存在且未明确否定适用性时不生成第17条意见。"""
    capability = build_registry().get("financial_management_applicability", 1)
    assert capability is not None
    matrix = make_record(
        "matrix",
        measure_id="财务管理-45.现金收费解缴控制-控制措施01",
        applicability=applicability,
    )
    duty = make_record(
        "position_duty",
        row=4,
        measure_id="财务管理-45.现金收费解缴控制-控制措施01",
    )

    issues = capability.runner(
        make_context([matrix], all_records=[matrix, duty]),
        {
            "business_id": FINANCIAL_BUSINESS_ID,
            "measure_ids": [[45, 1], [45, 2], [45, 3], [45, 4], [45, 5], [29, 1]],
        },
    )

    assert issues == []


def test_financial_rule_ignores_same_display_code_from_first_batch() -> None:
    """第一批同为02编号的交易与购电不得执行财务专项规则。"""
    capability = build_registry().get("financial_management_applicability", 1)
    assert capability is not None
    matrix = make_record(
        "matrix",
        measure_id="交易与购电-45.测试-控制措施01",
        applicability="不适用",
    )
    matrix.business_id = "power_purchase_trading"

    issues = capability.runner(
        make_context([matrix]),
        {
            "business_id": FINANCIAL_BUSINESS_ID,
            "measure_ids": [[45, 1], [45, 2], [45, 3], [45, 4], [45, 5], [29, 1]],
        },
    )

    assert issues == []


def test_1921_rule_pack_contains_1012_changes() -> None:
    """1.9.21 应包含模板匹配提示和财务管理第17条。"""

    pack = load_pack(RELEASE)
    rule_3 = next(rule for rule in pack["rules"] if rule["display_code"] == "R03")
    template_check = next(
        check for check in rule_3["checks"]
        if check["operator"] == "template_match_notice"
    )
    rule_17 = next(rule for rule in pack["rules"] if rule["display_code"] == "R17")

    assert template_check["message"] == {
        "review": TEMPLATE_MATCH_OPINION,
        "violation": TEMPLATE_MATCH_OPINION,
    }
    assert rule_17["checks"][0]["message"] == {
        "review": FINANCIAL_OPINION,
        "violation": FINANCIAL_OPINION,
    }
    assert rule_17["checks"][0]["params"]["business_id"] == FINANCIAL_BUSINESS_ID
    assert rule_17["checks"][0]["params"]["measure_ids"] == [
        [45, 1], [45, 2], [45, 3], [45, 4], [45, 5], [29, 1],
    ]


def test_1921_engine_renders_exact_template_match_opinion() -> None:
    """未匹配模板时引擎应输出文档指定的第3条意见。"""

    record = make_record("matrix", measure_id="未知业务-1.控制点-控制措施01")
    file = FileRecord(
        Path("风控矩阵.xlsx"),
        Path("风控矩阵.xlsx"),
        "hash",
        "xlsx",
        ENTITY_CODE,
        [],
        False,
        "",
        "default",
        "matrix",
        [ParsedSheet("风控矩阵", "matrix", [2], {}, {}, 8, 3, [record])],
        business_id="unmatched_1234",
        preservation={"template_match": {"status": "unmatched", "reason": "未匹配"}},
    )
    record.business_id = file.business_id
    record.business_code = ""

    findings = execute_release_rule("schema.applicability", file)

    assert TEMPLATE_MATCH_OPINION in [finding.message for finding in findings]


def test_1921_engine_renders_exact_financial_opinion() -> None:
    """财务管理指定措施缺岗位编号时应输出文档指定的第17条意见。"""

    record = make_record(
        "matrix",
        measure_id="财务管理-45.现金收费解缴控制-控制措施01",
        applicability="适用",
    )
    file = FileRecord(
        Path("02财务管理矩阵.xlsx"),
        Path("02财务管理矩阵.xlsx"),
        "hash",
        "xlsx",
        ENTITY_CODE,
        [],
        False,
        FINANCIAL_BUSINESS_CODE,
        "default",
        "matrix",
        [ParsedSheet("风控矩阵", "matrix", [2], {}, {}, 8, 3, [record])],
        business_id=FINANCIAL_BUSINESS_ID,
        preservation={"template_match": {"status": "matched"}},
    )

    findings = execute_release_rule("financial_management.key_measure_applicability", file)

    assert [finding.message for finding in findings] == [FINANCIAL_OPINION]


def test_1012_source_and_release_are_rebuildable() -> None:
    """1012 来源须冻结，1.9.21 发布包须可重复构建。"""

    source = ROOT / "规则来源/1012-湖南_模板匹配规则修订版.docx"
    pack = load_pack(RELEASE)
    active = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))

    assert __version__ == "2.2.0"
    assert active == {
        "version": "1.9.21",
        "content_hash": pack["manifest"]["content_hash"],
    }
    assert pack["manifest"]["source_document_sha256"] == sha256_file(source)

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_1012_hunan.py"), "--verify-only"],
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
