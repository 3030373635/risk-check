"""0918-1 第5条和第7条规则更新的发布验收。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from openpyxl import Workbook, load_workbook

from risk_audit import __version__
from risk_audit.configuration.loader import load_pack
from risk_audit.models import FileRecord
from risk_audit.readers.confirmed_v180 import parse_workbook_v180
from risk_audit.util import sha256_file
from risk_audit.writer import write_outputs
from test_rules_0917_6 import active_pack_for, execute, make_file, make_record


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
SYSTEM_TYPE_OPINION = (
    "【第7条】请在C列插入“系统类型”列，该列填报枚举值："
    "一级部署系统、二级部署系统、三级部署系统，请根据系统的实际情况填报。"
)


def historical_pack_for(*rule_ids: str) -> dict:
    """读取 1.9.14 历史规则并筛选指定规则。

    Args:
        rule_ids: 需要执行的历史规则标识。
    """

    pack = load_pack(ROOT / "rulepacks/releases/1.9.14")
    pack["rules"] = [rule for rule in pack["rules"] if rule["rule_id"] in rule_ids]
    return pack


def parse_system_rule_book(tmp_path: Path, book: Workbook) -> FileRecord:
    """使用当前字段别名解析系统规则测试工作簿。

    tmp_path 为 pytest 临时目录，book 为待保存和解析的 openpyxl 工作簿。
    """
    path = tmp_path / "系统规则清单.xlsx"
    book.save(path)
    source_file = FileRecord(
        path,
        Path(path.name),
        sha256_file(path),
        "xlsx",
        "205H",
        [],
        False,
        "06",
        "default",
        "three_lists",
    )
    aliases = load_pack(ROOT / "rulepacks/releases/1.9.14")["field_aliases"]
    source_file.sheets = parse_workbook_v180(source_file, path, aliases)
    return source_file


def test_active_rule_accepts_metering_work_responsible_position() -> None:
    """岗位名称包含“计量工作负责人”时，不得再按人员泛称提示岗位不具体。"""
    duty = make_record("position_duty", position="高压计量工作负责人")

    findings = execute(active_pack_for("duties.completeness"), [make_file("position_duty", [duty])])

    assert all(finding.check_id != "position_specific" for finding in findings)


def test_active_rule_reports_missing_system_type_header_once() -> None:
    """系统规则清单缺“系统类型”表头时，每张工作表只输出一条指定意见。"""
    rows = [
        make_record("system_rule", row=3, measure_id="M1", system_rule_content="规则一"),
        make_record("system_rule", row=4, measure_id="M2", system_rule_content="规则二"),
    ]
    source_file = make_file("system_rule", rows)
    source_file.sheets[0].columns = {"measure_id": 1, "system_rule_content": 4}

    findings = execute(historical_pack_for("systems.type_header"), [source_file])

    assert len(findings) == 1
    assert findings[0].message == SYSTEM_TYPE_OPINION
    assert findings[0].file_path == "材料.xlsx"
    assert findings[0].sheet == "system_rule"
    assert findings[0].row == 3


def test_active_rule_accepts_system_type_header_without_checking_values() -> None:
    """“系统类型”列存在即通过；本期不扩大检查列位置或单元格枚举值。"""
    row = make_record("system_rule", measure_id="M1", system_rule_content="规则一", system_type="自定义值")
    source_file = make_file("system_rule", [row])
    source_file.sheets[0].columns = {
        "measure_id": 1,
        "system_type": 6,
        "system_rule_content": 7,
    }

    pack = historical_pack_for("systems.type_header")

    assert len(pack["rules"]) == 1
    assert execute(pack, [source_file]) == []


def test_active_rule_reports_missing_system_type_header_on_empty_sheet() -> None:
    """系统规则清单没有明细行时仍须检查表头，并将意见落在首个可填写行。"""
    source_file = make_file("system_rule", [])
    source_file.sheets[0].columns = {"measure_id": 1, "system_rule_content": 4}

    findings = execute(historical_pack_for("systems.type_header"), [source_file])

    assert len(findings) == 1
    assert findings[0].message == SYSTEM_TYPE_OPINION
    assert findings[0].row == 3


def test_active_rule_requires_exact_system_type_header_after_real_parse(tmp_path: Path) -> None:
    """真实解析可模糊映射字段，但第7条仍必须要求原始表头精确为“系统类型”。

    tmp_path 为 pytest 临时目录。
    """
    book = Workbook()
    sheet = book.active
    sheet.title = "系统控制规则清单"
    sheet.append(["控制措施编号", "系统类型说明", "规则名称", "规则内容"])
    sheet.append(["M1", "一级部署系统", "规则1", "内容1"])

    source_file = parse_system_rule_book(tmp_path, book)
    parsed_sheet = source_file.sheets[0]

    # 先证明读取器的模糊回退确实将该列映射成了逻辑字段。
    assert "system_type" in parsed_sheet.columns
    findings = execute(historical_pack_for("systems.type_header"), [source_file])

    assert len(findings) == 1
    assert findings[0].message == SYSTEM_TYPE_OPINION
    assert findings[0].row == 2


def test_active_rule_reports_each_empty_region_on_same_physical_sheet(tmp_path: Path) -> None:
    """同一物理工作表的多个空系统规则区应各自输出一条意见。

    tmp_path 为 pytest 临时目录。
    """
    book = Workbook()
    sheet = book.active
    sheet.title = "汇总"
    sheet.cell(1, 1, "系统控制规则清单")
    sheet.append(["控制措施编号", "规则名称", "规则内容"])
    sheet.cell(6, 1, "系统控制规则清单")
    sheet.cell(7, 1, "控制措施编号")
    sheet.cell(7, 2, "规则名称")
    sheet.cell(7, 3, "规则内容")

    source_file = parse_system_rule_book(tmp_path, book)
    assert [(item.title, item.first_data_row) for item in source_file.sheets] == [
        ("汇总", 3),
        ("汇总", 8),
    ]

    findings = execute(historical_pack_for("systems.type_header"), [source_file])

    assert len(findings) == 2
    assert [finding.row for finding in findings] == [3, 8]

    output_root = tmp_path / "output"
    warnings, _ = write_outputs(
        [source_file],
        findings,
        output_root,
        metadata_dir=tmp_path / "metadata",
    )
    audited = load_workbook(output_root / source_file.relative_path)
    try:
        # 空区域没有明细记录，意见仍要落到各自的首个可填行。
        assert warnings == []
        assert audited["汇总"]["D3"].value == SYSTEM_TYPE_OPINION
        assert audited["汇总"]["D8"].value == SYSTEM_TYPE_OPINION
    finally:
        audited.close()


def test_active_rule_reports_each_nonempty_side_by_side_region(tmp_path: Path) -> None:
    """同一行并排的非空系统规则区必须保留各自的逻辑区身份。

    tmp_path 为 pytest 临时目录。
    """
    book = Workbook()
    sheet = book.active
    sheet.title = "并排汇总"
    sheet.cell(1, 1, "系统控制规则清单")
    sheet.cell(1, 5, "系统控制规则清单")
    for start_column in (1, 5):
        for offset, value in enumerate(["控制措施编号", "规则名称", "规则内容"]):
            sheet.cell(2, start_column + offset, value)
        for offset, value in enumerate([f"M{start_column}", f"规则{start_column}", f"内容{start_column}"]):
            sheet.cell(3, start_column + offset, value)

    source_file = parse_system_rule_book(tmp_path, book)
    assert len(source_file.sheets) == 2
    assert [item.records[0].row for item in source_file.sheets] == [3, 3]
    # 这两条读取记录正好重现旧实现的同源身份冲突。
    assert source_file.sheets[0].records[0].record_id == source_file.sheets[1].records[0].record_id

    findings = execute(historical_pack_for("systems.type_header"), [source_file])

    assert len(findings) == 2
    assert [finding.row for finding in findings] == [3, 3]
    assert len({finding.finding_key for finding in findings}) == 2


def test_active_rule_no_longer_checks_matrix_system_revisions() -> None:
    """旧第7条已删除；矩阵控制系统红字与系统清单名称不一致不再产生意见。"""
    matrix = make_record("matrix", measure_id="业务-2.名称-控制措施02", control_system="ERP系统")
    matrix.fields["control_system"].red_spans = [{"text": "ERP系统", "start": 0, "end": 5}]
    system_rule = make_record(
        "system_rule",
        measure_id="业务-02.其他名称-控制措施2",
        system_name="财务管控系统",
        system_type="一级部署系统",
    )
    system_file = make_file("system_rule", [system_rule])
    system_file.sheets[0].columns = {"measure_id": 1, "system_type": 3, "system_name": 4}

    findings = execute(
        active_pack_for("systems.matrix_changes", "systems.type_header"),
        [make_file("matrix", [matrix]), system_file],
    )

    assert findings == []


def test_0918_1_release_is_rebuildable() -> None:
    """0918-1源文档必须冻结，历史1.9.14发布包必须可重建。"""
    assert __version__ == "1.9.19"
    active = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))
    assert active["version"] == "1.9.19"
    pack = load_pack(ROOT / "rulepacks/releases/1.9.14")
    assert pack["manifest"]["source_document_sha256"] == sha256_file(
        ROOT / "规则来源/0918-1.docx"
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_0918_1.py"), "--verify-only"],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "release": str(ROOT / "rulepacks/releases/1.9.14"),
        "activated": False,
        "verified": True,
    }
