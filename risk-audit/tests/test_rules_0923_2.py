"""0923-2 系统类型预处理与未决提示规则验收。"""

from __future__ import annotations

from copy import copy
import json
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table

from risk_audit import __version__
from risk_audit.configuration.loader import load_pack
from risk_audit.models import FileRecord
from risk_audit.readers.confirmed_v180 import parse_workbook_v180
from risk_audit.util import sha256_file


ROOT = Path(__file__).resolve().parents[1]


def make_system_rule_file(tmp_path: Path, rows: list[list[str]], headers: list[str]) -> tuple[FileRecord, dict]:
    """构造并解析系统规则工作簿。

    Args:
        tmp_path: pytest 提供的隔离目录。
        rows: 待写入的系统规则明细。
        headers: 系统规则表头。
    """

    path = tmp_path / "系统控制规则清单.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "系统控制规则清单"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)

    aliases = json.loads(
        (ROOT / "rulepacks/releases/1.9.18/field_aliases.json").read_text(encoding="utf-8")
    )
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
    source_file.sheets = parse_workbook_v180(source_file, path, aliases)
    return source_file, aliases


def test_preprocessing_inserts_system_type_and_fills_known_owners(tmp_path: Path) -> None:
    """缺少系统类型列时应插入到系统名称与管理主体之间并自动填值。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """

    source_file, aliases = make_system_rule_file(
        tmp_path,
        [
            ["M1", "财务系统", "总部", "规则1", "内容1"],
            ["M2", "营销系统", "国网湖南省电力有限公司", "规则2", "内容2"],
            ["M3", "物资系统", "长沙供电分公司", "规则3", "内容3"],
        ],
        ["控制措施编号", "系统名称", "系统规则管理主体", "规则名称", "规则内容"],
    )

    from risk_audit.system_type_preprocessing import preprocess_system_types

    preprocess_system_types(source_file, tmp_path / "work", aliases)

    result = load_workbook(Path(source_file._preprocessed_path))
    sheet = result.active
    assert [sheet.cell(1, column).value for column in range(1, 7)] == [
        "控制措施编号",
        "系统名称",
        "系统类型",
        "系统规则管理主体",
        "规则名称",
        "规则内容",
    ]
    assert [sheet.cell(row, 3).value for row in range(2, 5)] == [
        "一级部署系统",
        "二级部署系统",
        None,
    ]


def test_preprocessing_uses_short_internal_path_on_windows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """系统类型预处理不得让内部工作文件超过 Windows 传统路径上限。

    Args:
        tmp_path: pytest 提供的隔离目录。
        monkeypatch: pytest 提供的补丁工具。
    """

    source_file, aliases = make_system_rule_file(
        tmp_path,
        [["M1", "财务系统", "总部", "规则1", "内容1"]],
        ["控制措施编号", "系统名称", "系统规则管理主体", "规则名称", "规则内容"],
    )
    source_file.relative_path = Path(
        "主业",
        "国网湖南省电力有限公司郴州供电分公司本部",
        "09 职工福利保障与薪酬管理",
        "09“三清单”-职工福利保障与薪酬管理-郴州供电本部9.22.xlsx",
    )
    legacy_suffix = Path("preprocessed", "system_types") / source_file.relative_path
    padding_length = 260 - len(str(tmp_path)) - len(str(legacy_suffix)) - 2
    assert 0 < padding_length < 256
    work_dir = tmp_path / ("w" * padding_length)
    legacy_destination = work_dir / legacy_suffix
    assert len(str(legacy_destination)) == 260

    original_save = Workbook.save

    def save_with_windows_path_limit(workbook: Workbook, filename: str | Path) -> None:
        """在非 Windows 测试环境模拟传统 MAX_PATH 限制。

        Args:
            workbook: 待保存的工作簿。
            filename: 工作簿目标路径。
        """

        if len(str(filename)) >= 260:
            raise FileNotFoundError(2, "Windows 目标路径过长", str(filename))
        original_save(workbook, filename)

    monkeypatch.setattr(Workbook, "save", save_with_windows_path_limit)

    from risk_audit.system_type_preprocessing import preprocess_system_types

    preprocess_system_types(source_file, work_dir, aliases)

    destination = Path(source_file._preprocessed_path)
    assert len(str(destination)) < 260
    assert destination.is_file()


def test_adjust_workbook_references_keeps_sparse_sheets_sparse() -> None:
    """公式引用调整只得访问已存在单元格，不得展开稀疏工作表。"""

    from risk_audit.system_type_preprocessing import _adjust_workbook_references

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "系统控制规则清单"
    worksheet["A1"] = "=D2"
    # 远端样式单元格仅用于放大声明维度，不应导致中间空白单元格被实例化。
    worksheet.cell(300, 300).font = copy(worksheet["A1"].font)
    existing_coordinates = set(worksheet._cells)

    _adjust_workbook_references(workbook, worksheet.title, [3])

    assert worksheet["A1"].value == "=E2"
    assert set(worksheet._cells) == existing_coordinates
    workbook.close()


def test_preprocessing_reuses_enum_column_and_preserves_third_level(tmp_path: Path) -> None:
    """无表头但已有枚举值的列应改名复用，三级部署系统不得覆盖。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """

    source_file, aliases = make_system_rule_file(
        tmp_path,
        [["M1", "财务系统", "三级部署系统", "总部", "规则1", "内容1"]],
        ["控制措施编号", "系统名称", "", "系统规则管理主体", "规则名称", "规则内容"],
    )

    from risk_audit.system_type_preprocessing import preprocess_system_types

    preprocess_system_types(source_file, tmp_path / "work", aliases)

    result = load_workbook(Path(source_file._preprocessed_path))
    sheet = result.active
    assert sheet.max_column == 6
    assert sheet["C1"].value == "系统类型"
    assert sheet["C2"].value == "三级部署系统"


def test_preprocessing_preserves_existing_valid_system_types(tmp_path: Path) -> None:
    """已填写的合法系统类型必须原样保留，不得按管理主体覆盖或清空。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """

    source_file, aliases = make_system_rule_file(
        tmp_path,
        [
            ["M1", "财务系统", "一级部署系统", "长沙供电分公司", "规则1", "内容1"],
            ["M2", "营销系统", "一级部署系统", "国网湖南省电力有限公司", "规则2", "内容2"],
            ["M3", "物资系统", "二级部署系统", "总部", "规则3", "内容3"],
        ],
        ["控制措施编号", "系统名称", "系统类型", "系统规则管理主体", "规则名称", "规则内容"],
    )

    from risk_audit.system_type_preprocessing import preprocess_system_types

    preprocess_system_types(source_file, tmp_path / "work", aliases)

    result = load_workbook(Path(source_file._preprocessed_path))
    assert [result.active.cell(row, 3).value for row in range(2, 5)] == [
        "一级部署系统",
        "一级部署系统",
        "二级部署系统",
    ]


def test_preprocessing_expands_merged_title_when_inserting_column(tmp_path: Path) -> None:
    """新增系统类型列时，跨越插入点的合并标题必须同步扩展。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """

    path = tmp_path / "合并标题三清单.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "汇总"
    sheet["A1"] = "系统控制规则清单"
    sheet.merge_cells("A1:E1")
    sheet.append(["控制措施编号", "系统名称", "系统规则管理主体", "规则名称", "规则内容"])
    sheet.append(["M1", "财务系统", "总部", "规则1", "内容1"])
    workbook.save(path)

    aliases = json.loads(
        (ROOT / "rulepacks/releases/1.9.18/field_aliases.json").read_text(encoding="utf-8")
    )
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
    source_file.sheets = parse_workbook_v180(source_file, path, aliases)

    from risk_audit.system_type_preprocessing import preprocess_system_types

    preprocess_system_types(source_file, tmp_path / "work", aliases)

    result = load_workbook(Path(source_file._preprocessed_path))
    assert [str(cell_range) for cell_range in result.active.merged_cells.ranges] == ["A1:F1"]


def test_preprocessing_reuses_vertically_merged_system_type_header(tmp_path: Path) -> None:
    """精确系统类型表头纵向合并时应复用合并锚点。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """

    path = tmp_path / "纵向合并表头.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "系统控制规则清单"
    headers = ["控制措施编号", "系统名称", "系统类型", "系统规则管理主体"]
    for column, header in enumerate(headers, 1):
        sheet.cell(2, column).value = header
        sheet.merge_cells(start_row=2, start_column=column, end_row=3, end_column=column)
    sheet["E2"] = "规则"
    sheet["F2"] = "规则"
    sheet["E3"] = "规则名称"
    sheet["F3"] = "规则内容"
    sheet.append(["M1", "财务系统", "", "总部", "规则1", "内容1"])
    workbook.save(path)

    aliases = json.loads(
        (ROOT / "rulepacks/releases/1.9.18/field_aliases.json").read_text(encoding="utf-8")
    )
    source_file = FileRecord(
        path, Path(path.name), sha256_file(path), "xlsx", "205H", [], False, "06", "default", "three_lists"
    )
    source_file.sheets = parse_workbook_v180(source_file, path, aliases)

    from risk_audit.system_type_preprocessing import preprocess_system_types

    preprocess_system_types(source_file, tmp_path / "work", aliases)

    result = load_workbook(Path(source_file._preprocessed_path))
    assert "C2:C3" in {str(cell_range) for cell_range in result.active.merged_cells.ranges}
    assert result.active["C2"].value == "系统类型"
    assert result.active["C4"].value == "一级部署系统"


def test_preprocessing_renames_enum_column_with_vertically_merged_blank_header(tmp_path: Path) -> None:
    """枚举列的空表头纵向合并时，应在合并锚点写入系统类型。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """

    path = tmp_path / "纵向合并空表头.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "系统控制规则清单"
    headers = ["控制措施编号", "系统名称", None, "系统规则管理主体"]
    for column, header in enumerate(headers, 1):
        sheet.cell(2, column).value = header
        sheet.merge_cells(start_row=2, start_column=column, end_row=3, end_column=column)
    sheet["E2"] = "规则"
    sheet["F2"] = "规则"
    sheet["E3"] = "规则名称"
    sheet["F3"] = "规则内容"
    sheet.append(["M1", "财务系统", "三级部署系统", "长沙供电分公司", "规则1", "内容1"])
    workbook.save(path)

    aliases = json.loads(
        (ROOT / "rulepacks/releases/1.9.18/field_aliases.json").read_text(encoding="utf-8")
    )
    source_file = FileRecord(
        path, Path(path.name), sha256_file(path), "xlsx", "205H", [], False, "06", "default", "three_lists"
    )
    source_file.sheets = parse_workbook_v180(source_file, path, aliases)

    from risk_audit.system_type_preprocessing import preprocess_system_types

    preprocess_system_types(source_file, tmp_path / "work", aliases)

    result = load_workbook(Path(source_file._preprocessed_path))
    assert "C2:C3" in {str(cell_range) for cell_range in result.active.merged_cells.ranges}
    assert result.active["C2"].value == "系统类型"
    assert result.active["C4"].value == "三级部署系统"


def test_preprocessing_splits_horizontal_blank_header_for_enum_column(tmp_path: Path) -> None:
    """枚举列位于横向合并空表头时，应拆分合并并准确命名枚举列。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """

    path = tmp_path / "横向合并空表头.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "系统控制规则清单"
    for column, header in enumerate(["控制措施编号", "系统名称"], 1):
        sheet.cell(2, column).value = header
        sheet.merge_cells(start_row=2, start_column=column, end_row=3, end_column=column)
    sheet.merge_cells("C2:D3")
    sheet["E2"] = "系统规则管理主体"
    sheet.merge_cells("E2:E3")
    sheet["F2"] = "规则"
    sheet["G2"] = "规则"
    sheet["F3"] = "规则名称"
    sheet["G3"] = "规则内容"
    sheet.append(["M1", "财务系统", "", "三级部署系统", "长沙供电分公司", "规则1", "内容1"])
    workbook.save(path)

    aliases = json.loads(
        (ROOT / "rulepacks/releases/1.9.18/field_aliases.json").read_text(encoding="utf-8")
    )
    source_file = FileRecord(
        path, Path(path.name), sha256_file(path), "xlsx", "205H", [], False, "06", "default", "three_lists"
    )
    source_file.sheets = parse_workbook_v180(source_file, path, aliases)

    from risk_audit.system_type_preprocessing import preprocess_system_types

    preprocess_system_types(source_file, tmp_path / "work", aliases)

    result = load_workbook(Path(source_file._preprocessed_path))
    assert "C2:D3" not in {str(cell_range) for cell_range in result.active.merged_cells.ranges}
    assert result.active["D2"].value == "系统类型"
    assert result.active["D4"].value == "三级部署系统"


def test_preprocessing_updates_formula_reference_after_column_insertion(tmp_path: Path) -> None:
    """新增系统类型列后，跨越插入点的公式引用必须同步右移。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """

    source_file, aliases = make_system_rule_file(
        tmp_path,
        [["M1", "财务系统", "总部", "规则1", "=C2"]],
        ["控制措施编号", "系统名称", "系统规则管理主体", "规则名称", "规则内容"],
    )

    from risk_audit.system_type_preprocessing import preprocess_system_types

    preprocess_system_types(source_file, tmp_path / "work", aliases)

    result = load_workbook(Path(source_file._preprocessed_path), data_only=False)
    assert result.active["F2"].value == "=D2"


def test_preprocessing_updates_range_structures_after_column_insertion(tmp_path: Path) -> None:
    """插列后表格、筛选、数据验证和条件格式范围必须同步平移。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """

    path = tmp_path / "带范围结构的系统规则.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "系统控制规则清单"
    sheet.append(["控制措施编号", "系统名称", "系统规则管理主体", "规则名称", "规则内容"])
    sheet.append(["M1", "财务系统", "总部", "规则1", "内容1"])
    sheet.auto_filter.ref = "A1:E2"
    validation = DataValidation(type="list", formula1="=$C$2:$C$10")
    validation.add("C2:C10")
    sheet.add_data_validation(validation)
    sheet.conditional_formatting.add("C2:C10", FormulaRule(formula=['C2="总部"']))
    sheet.add_table(Table(displayName="SystemRules", ref="A1:E2"))
    workbook.defined_names.add(
        DefinedName("OwnerValues", attr_text="'系统控制规则清单'!$C$2:$C$10")
    )
    workbook.save(path)

    aliases = json.loads(
        (ROOT / "rulepacks/releases/1.9.18/field_aliases.json").read_text(encoding="utf-8")
    )
    source_file = FileRecord(
        path, Path(path.name), sha256_file(path), "xlsx", "205H", [], False, "06", "default", "three_lists"
    )
    source_file.sheets = parse_workbook_v180(source_file, path, aliases)

    from risk_audit.system_type_preprocessing import preprocess_system_types

    preprocess_system_types(source_file, tmp_path / "work", aliases)

    result = load_workbook(Path(source_file._preprocessed_path), data_only=False)
    result_sheet = result.active
    assert result_sheet.auto_filter.ref == "A1:F2"
    assert str(result_sheet.data_validations.dataValidation[0].sqref) == "D2:D10"
    assert result_sheet.data_validations.dataValidation[0].formula1 == "=$D$2:$D$10"
    conditional_format = next(iter(result_sheet.conditional_formatting._cf_rules))
    assert str(conditional_format.sqref) == "D2:D10"
    assert result_sheet.conditional_formatting._cf_rules[conditional_format][0].formula == ['D2="总部"']
    assert result_sheet.tables["SystemRules"].ref == "A1:F2"
    assert [column.name for column in result_sheet.tables["SystemRules"].tableColumns] == [
        "控制措施编号",
        "系统名称",
        "系统类型",
        "系统规则管理主体",
        "规则名称",
        "规则内容",
    ]
    assert result.defined_names["OwnerValues"].attr_text == "'系统控制规则清单'!$D$2:$D$10"


def test_table_at_insertion_boundary_moves_without_extra_column_definition(tmp_path: Path) -> None:
    """表格左边界恰为插入点时，表格整体右移且列定义数保持一致。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """

    path = tmp_path / "表格位于插入边界.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "系统控制规则清单"
    sheet.append(["控制措施编号", "系统名称", "系统规则管理主体", "规则名称", "规则内容"])
    sheet.append(["M1", "财务系统", "总部", "规则1", "内容1"])
    sheet.add_table(Table(displayName="OwnerRules", ref="C1:E2"))
    workbook.save(path)

    aliases = json.loads(
        (ROOT / "rulepacks/releases/1.9.18/field_aliases.json").read_text(encoding="utf-8")
    )
    source_file = FileRecord(
        path, Path(path.name), sha256_file(path), "xlsx", "205H", [], False, "06", "default", "three_lists"
    )
    source_file.sheets = parse_workbook_v180(source_file, path, aliases)

    from risk_audit.system_type_preprocessing import preprocess_system_types

    preprocess_system_types(source_file, tmp_path / "work", aliases)

    result = load_workbook(Path(source_file._preprocessed_path))
    table = result.active.tables["OwnerRules"]
    assert table.ref == "D1:F2"
    assert [column.name for column in table.tableColumns] == ["系统规则管理主体", "规则名称", "规则内容"]


def test_merged_range_adjustment_uses_original_column_coordinates() -> None:
    """并排逻辑区多次插列时，局部合并范围不得重复累加左边界偏移。"""

    from risk_audit.system_type_preprocessing import _adjusted_range

    assert _adjusted_range("G1:I1", [3, 8]) == "H1:K1"


def test_owner_classification_handles_suffixes_and_negative_headquarters() -> None:
    """省级主体后缀应命中二级，否定的总部语义不得误判为一级。"""

    from risk_audit.system_type_preprocessing import classify_system_owner

    assert classify_system_owner("湖南省电力公司本部") == "二级部署系统"
    assert classify_system_owner("非总部湖南省公司") == "二级部署系统"
    assert classify_system_owner("长沙供电公司总部") is None
    assert classify_system_owner("国网总部财务部") == "一级部署系统"
    assert classify_system_owner("国家电网有限公司总部业务部门") == "一级部署系统"


def test_runner_preprocessing_applies_0923_system_type_rules(tmp_path: Path) -> None:
    """1.9.20 业务预处理入口必须继续执行系统类型规范化。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """

    source_file, aliases = make_system_rule_file(
        tmp_path,
        [["M1", "财务系统", "总部", "规则1", "内容1"]],
        ["控制措施编号", "系统名称", "系统规则管理主体", "规则名称", "规则内容"],
    )

    from risk_audit.runner import preprocess_business_file

    preprocess_business_file(
        source_file,
        {"manifest": {"version": "1.9.20"}, "field_aliases": aliases},
        {},
        tmp_path / "run",
    )

    result = load_workbook(Path(source_file._preprocessed_path))
    assert result.active["C1"].value == "系统类型"
    assert result.active["C2"].value == "一级部署系统"


def test_0923_2_release_is_preserved() -> None:
    """0923-2 来源和历史 1.9.18 规则包必须保留，当前程序使用后续版本。"""

    assert __version__ == "1.9.20"
    active = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))
    assert active["version"] == "1.9.20"
    pack = load_pack(ROOT / "rulepacks/releases/1.9.18")
    assert pack["manifest"]["source_document_sha256"] == sha256_file(
        ROOT / "规则来源/0923-2.docx"
    )
