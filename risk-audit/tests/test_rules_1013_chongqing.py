"""验证 1013 重庆规则同步。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from openpyxl import Workbook, load_workbook

from risk_audit.checks.registry import CheckContext, build_registry
from risk_audit.configuration.loader import load_pack
from risk_audit.models import Entity, FieldValue, FileRecord, Record
from risk_audit.output_0916 import EXISTING_HEADER, enrich_matrices
from risk_audit.readers.confirmed_v180 import parse_workbook_v180
from risk_audit.util import sha256_file


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "规则来源/风控矩阵1013-重庆.docx"
RELEASE = ROOT / "rulepacks/releases/1.9.28"


def make_record(record_type: str, row: int, **values: str) -> Record:
    """构造编号引用测试记录。

    Args:
        record_type: 审核记录类型。
        row: 原始工作表行号。
        values: 按逻辑字段名给出的字段值。

    Returns:
        使用同一主体、业务和变体边界的审核记录。
    """

    fields = {
        name: FieldValue(value, value, f"A{row}")
        for name, value in values.items()
    }
    return Record(
        record_type,
        "CQ001",
        "02",
        "default",
        "财务管理.xlsx",
        record_type,
        row,
        fields,
        f"{record_type}-{row}",
        business_id="financial_management",
    )


def test_reference_exists_v3_ignores_matrix_applicability() -> None:
    """岗位编号已在当前矩阵中存在时，不得再依据适用性产生第9条意见。"""

    capability = build_registry().get("reference_exists", 3)
    assert capability is not None
    duty = make_record("position_duty", 8, measure_id="财务管理-1-控制措施01")
    matrix = make_record(
        "matrix",
        3,
        measure_id="财务管理-1-控制措施01",
        applicability="不适用",
    )
    context = CheckContext(
        [duty],
        [],
        [duty, matrix],
        {"CQ001": Entity("CQ001", "国网重庆市电力公司")},
        {},
        {},
        ["CQ001"],
        [{
            "business_id": "financial_management",
            "business_code": "02",
            "variant_id": "default",
        }],
    )

    issues = capability.runner(
        context,
        {
            "source_field": "measure_id",
            "target_field": "measure_id",
            "target_record_type": "matrix",
        },
    )

    assert issues == []


def test_matrix_enrichment_removes_existing_measure_column(tmp_path: Path) -> None:
    """1013 输出不得新增或保留岗位清单已有措施编号列。

    Args:
        tmp_path: pytest 提供的临时目录。
    """

    path = tmp_path / "02财务管理矩阵.xlsx"
    workbook = Workbook()
    matrix = workbook.active
    matrix.title = "风控矩阵"
    matrix.append([
        "控制措施编号",
        "控制措施",
        "责任主体",
        "是否适用",
        "省公司版本责任主体（核对后删除）",
        EXISTING_HEADER,
        "审核意见",
    ])
    matrix.append([
        "财务管理-1-控制措施01",
        "复核资料",
        "重庆公司-财务部-会计",
        "不适用",
        "旧责任主体",
        "旧措施编号",
        "人工意见",
    ])
    workbook.save(path)

    file = FileRecord(
        path,
        Path(path.name),
        sha256_file(path),
        "xlsx",
        "CQ001",
        [],
        False,
        "02",
        "default",
        "matrix",
        business_id="financial_management",
        preservation={"template_match": {"status": "matched"}},
    )
    aliases = load_pack(ROOT / "rulepacks/releases/1.9.27")["field_aliases"]
    file._parser_policy = {"version": 3}
    file.sheets = parse_workbook_v180(file, path, aliases)

    enrich_matrices(
        file,
        path,
        {},
        [],
        [file],
        include_existing_measure_column=False,
    )

    output = load_workbook(path).active
    headers = [output.cell(1, column).value for column in range(1, output.max_column + 1)]
    assert EXISTING_HEADER not in headers
    assert "旧措施编号" not in [
        output.cell(2, column).value
        for column in range(1, output.max_column + 1)
    ]
    assert "人工意见" in [
        output.cell(2, column).value
        for column in range(1, output.max_column + 1)
    ]


def test_1013_release_is_active_and_rebuildable() -> None:
    """重庆 1.9.28 应冻结 1013 来源、移除第16条并可确定性复验。"""

    assert SOURCE.is_file()
    pack = load_pack(RELEASE)
    rules = {rule["rule_id"]: rule for rule in pack["rules"]}
    reference_check = rules["references.current_matrix"]["checks"][0]
    profile = json.loads((ROOT / "regions/index.json").read_text(encoding="utf-8"))["regions"][0]

    assert pack["manifest"]["version"] == "1.9.28"
    assert pack["manifest"]["source_document_sha256"] == sha256_file(SOURCE)
    assert "matrices.measure_applicability" not in rules
    assert reference_check["operator_version"] == 3
    assert "适用性" not in reference_check["message"]["violation"]
    assert profile["rulepack_version"] == "1.9.28"
    assert profile["rulepack_content_hash"] == pack["manifest"]["content_hash"]

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/publish_rules_1013_chongqing_1928.py"),
            "--verify-only",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verified"] is True
