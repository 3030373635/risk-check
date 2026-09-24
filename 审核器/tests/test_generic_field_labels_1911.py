"""通用缺失字段提示修复的发布验收。"""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from risk_audit import __version__
from risk_audit.checks.registry import CheckContext
from risk_audit.checks.schema_v2 import schema_contains_v2
from risk_audit.configuration.loader import load_pack
from risk_audit.models import FileRecord
from risk_audit.readers.excel import parse_workbook
from risk_audit.util import sha256_file


ROOT = Path(__file__).resolve().parents[1]


def test_active_rulepack_outputs_generic_missing_field_label(tmp_path: Path) -> None:
    """tmp_path 为临时目录；当适用性列缺失时，激活规则必须输出通用名称。"""
    assert __version__ == "1.9.19"
    active = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))
    assert active["version"] == "1.9.19"
    pack = load_pack(ROOT / "rulepacks/releases" / active["version"])
    schema_rule = next(rule for rule in pack["rules"] if rule["rule_id"] == "schema.applicability")
    schema_check = schema_rule["checks"][0]
    assert schema_check["operator_version"] == 3

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "风控矩阵"
    worksheet.append(["风控矩阵"])
    worksheet.append(["控制措施编号", "控制措施", "控制系统", "控制载体", "责任主体"])
    worksheet.append(["M1", "核对资料", "ERP", "审批单", "财务部"])
    source = tmp_path / "matrix.xlsx"
    workbook.save(source)
    file = FileRecord(source, Path(source.name), sha256_file(source), "xlsx", "E", [], False, "06", "default", "matrix")
    file.sheets = parse_workbook(file, source, pack["field_aliases"])
    context = CheckContext([], [file], [], {}, {}, {"field_aliases": pack["field_aliases"]}, ["E"], [])

    issues = schema_contains_v2(context, schema_check["params"])
    reason = issues[0]["evidence"]["unavailable_reason"]

    assert "是否适用/是否适用及原因" in reason
    assert "信通公司" not in reason
