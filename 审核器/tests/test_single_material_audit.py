"""验证业务仅报送风控矩阵或三清单时的独立审核边界。"""
from pathlib import Path

from risk_audit.checks.confirmed_v180 import (
    measure_applicability_alignment_v1,
    missing_duty_measures,
    reference_exists_v2,
    responsibility_department_alignment_v1,
    roles_same_duty_v4,
    system_rule_changes_v1,
)
from risk_audit.checks.materials_v180 import required_documents_v3
from risk_audit.checks.registry import CheckContext, sheet_exists
from risk_audit.models import FieldValue, FileRecord, ParsedSheet, Record


def make_record(record_type: str, row: int = 3, **values: str) -> Record:
    """构造审核记录；record_type 为记录类型，row 为行号，values 为字段值。"""
    fields = {
        field: FieldValue(value, value, f"A{row}")
        for field, value in values.items()
    }
    return Record(
        record_type,
        "205H",
        "09",
        "default",
        "09材料.xlsx",
        record_type,
        row,
        fields,
        f"{record_type}-{row}",
    )


def make_file(material_type: str, sheet_types: list[str]) -> FileRecord:
    """构造业务文件；material_type 为材料类别，sheet_types 为已识别工作表类型。"""
    sheets = [
        ParsedSheet(sheet_type, sheet_type, [1], {}, {}, 9, 2, [])
        for sheet_type in sheet_types
    ]
    return FileRecord(
        Path(f"09{material_type}.xlsx"),
        Path(f"09{material_type}.xlsx"),
        "hash",
        "xlsx",
        "205H",
        [],
        False,
        "09",
        "default",
        material_type,
        sheets=sheets,
    )


def make_context(
    records: list[Record],
    files: list[FileRecord] | None = None,
    all_records: list[Record] | None = None,
) -> CheckContext:
    """构造审核上下文。

    records 为当前规则记录，files 为当前业务文件，all_records 为可选全量记录。
    """
    return CheckContext(
        records,
        files or [],
        records if all_records is None else all_records,
        {},
        {},
        {},
        ["205H"],
        [{"business_code": "09", "variant_id": "default", "required": True}],
    )


def test_only_three_lists_satisfies_business_material_requirement() -> None:
    """仅报送三清单时，不得再生成缺少风控矩阵的第1条意见。"""
    file = make_file("three_lists", ["position_duty"])

    issues = required_documents_v3(
        make_context([], [file]),
        {"material_types": ["matrix", "three_lists"]},
    )

    assert not any(issue["evidence"].get("missing_material") for issue in issues)


def test_only_matrix_satisfies_business_material_requirement() -> None:
    """仅报送风控矩阵时，不得再生成缺少三清单的第1条意见。"""
    file = make_file("matrix", ["matrix"])

    issues = required_documents_v3(
        make_context([], [file]),
        {"material_types": ["matrix", "three_lists"]},
    )

    assert not any(issue["evidence"].get("missing_material") for issue in issues)


def test_matrix_only_skips_three_list_sheet_requirement() -> None:
    """仅报送风控矩阵时，不得执行三清单内部的不相容岗位表存在性检查。"""
    file = make_file("matrix", ["matrix"])

    issues = sheet_exists(
        make_context([], [file]),
        {"sheet_types": ["incompatible_position"]},
    )

    assert issues == []


def test_three_lists_still_checks_its_own_missing_sheet() -> None:
    """已报送三清单时，仍须检查其内部缺少的不相容岗位清单。"""
    file = make_file("three_lists", ["position_duty"])

    issues = sheet_exists(
        make_context([], [file]),
        {"sheet_types": ["incompatible_position"]},
    )

    assert len(issues) == 1
    assert issues[0]["evidence"]["missing_sheet"] == "incompatible_position"


def test_separation_uses_three_list_identifiers_without_matrix() -> None:
    """没有矩阵时，第8条仍按三清单自身编号审核经办人与审核人分离。"""
    rows = [
        make_record(
            "position_duty",
            row=3,
            measure_id="职工福利保障与薪酬管理业务-1.员工信息管理-控制措施01",
            control_measure="核对员工信息。",
            department="党委组织部",
            position="人资专责",
            person_names="张三",
            role="经办",
            duty="对员工信息的准确性负主体责任",
        ),
        make_record(
            "position_duty",
            row=4,
            measure_id="职工福利保障与薪酬管理业务-1.员工信息管理-控制措施01",
            control_measure="核对员工信息。",
            department="党委组织部",
            position="部门主任",
            person_names="张三",
            role="审核",
            duty="对员工信息的准确性负审核责任",
        ),
    ]
    for record in rows:
        # 人员编号一致时，同人冲突属于可直接确认的问题。
        record.person_keys = [{"name": "张三", "person_id": "P001"}]

    issues = roles_same_duty_v4(make_context(rows), {})
    overlaps = [
        issue
        for issue in issues
        if issue["evidence"]["issue_type"] == "same_duty_person_overlap"
    ]

    assert len(overlaps) == 2
    assert all(issue["kind"] == "violation" for issue in overlaps)
    assert not any(
        issue["evidence"]["issue_type"] == "separation_correspondence_unresolved"
        for issue in issues
    )


def test_separation_does_not_merge_placeholder_identifiers() -> None:
    """没有矩阵时，“未关联”等占位编号必须按控制措施全文分组。"""
    rows = [
        make_record(
            "position_duty",
            row=3,
            measure_id="未关联",
            control_measure="核对员工信息。",
            department="党委组织部",
            person_names="张三",
            role="经办",
            duty="对员工信息的准确性负主体责任",
        ),
        make_record(
            "position_duty",
            row=4,
            measure_id="未关联",
            control_measure="审核薪酬发放。",
            department="党委组织部",
            person_names="张三",
            role="审核",
            duty="对员工信息的准确性负审核责任",
        ),
    ]
    for record in rows:
        record.person_keys = [{"name": "张三", "person_id": "P001"}]

    issues = roles_same_duty_v4(make_context(rows), {})

    assert not any(
        issue["evidence"]["issue_type"] == "same_duty_person_overlap"
        for issue in issues
    )


def test_separation_matches_missing_identifier_by_unique_control_text() -> None:
    """没有矩阵时，缺少编号的行应关联到唯一同文的清单编号。"""
    rows = [
        make_record(
            "position_duty",
            row=3,
            measure_id="控制措施01",
            control_measure="核对员工信息。",
            department="党委组织部",
            person_names="张三",
            role="经办",
            duty="对员工信息的准确性负主体责任",
        ),
        make_record(
            "position_duty",
            row=4,
            measure_id="",
            control_measure="核对员工信息。",
            department="党委组织部",
            person_names="张三",
            role="审核",
            duty="对员工信息的准确性负审核责任",
        ),
    ]
    for record in rows:
        record.person_keys = [{"name": "张三", "person_id": "P001"}]

    issues = roles_same_duty_v4(make_context(rows), {})

    assert sum(
        issue["evidence"]["issue_type"] == "same_duty_person_overlap"
        for issue in issues
    ) == 2


def test_separation_keeps_matrix_mode_when_empty_matrix_sheet_exists() -> None:
    """矩阵工作表已报送但无可用记录时，第8条仍应提示对应关系无法核实。"""
    row = make_record(
        "position_duty",
        measure_id="控制措施01",
        control_measure="核对员工信息。",
        department="党委组织部",
        person_names="张三",
        role="经办",
    )
    row.person_keys = [{"name": "张三", "person_id": "P001"}]

    issues = roles_same_duty_v4(make_context([row], [make_file("matrix", ["matrix"])]), {})

    assert any(
        issue["evidence"]["issue_type"] == "separation_correspondence_unresolved"
        for issue in issues
    )


def test_reference_rule_skips_when_matrix_is_absent() -> None:
    """没有矩阵时，依赖矩阵的第9条编号对应检查不得生成待核实意见。"""
    row = make_record(
        "position_duty",
        measure_id="职工福利保障与薪酬管理业务-1.员工信息管理-控制措施01",
    )

    issues = reference_exists_v2(
        make_context([row]),
        {
            "source_field": "measure_id",
            "target_record_type": "matrix",
            "target_field": "measure_id",
        },
    )

    assert issues == []


def test_reference_rule_skips_unreadable_source_when_matrix_is_absent() -> None:
    """范围可靠但未报送矩阵时，第9条不得因来源编号不可读而启动。"""
    missing = make_record("position_duty")
    formula = make_record("position_duty", row=4, measure_id="")
    formula.fields["measure_id"].formula = "=A4"
    formula.fields["measure_id"].state = "formula_no_cache"
    params = {
        "source_field": "measure_id",
        "target_record_type": "matrix",
        "target_field": "measure_id",
    }

    assert reference_exists_v2(make_context([missing]), params) == []
    assert reference_exists_v2(make_context([formula]), params) == []


def test_reference_rule_checks_when_empty_matrix_sheet_exists() -> None:
    """矩阵工作表已报送但无可用记录时，第9条应保留待核实意见。"""
    row = make_record("position_duty", measure_id="控制措施01")

    issues = reference_exists_v2(
        make_context([row], [make_file("matrix", ["matrix"])]),
        {
            "source_field": "measure_id",
            "target_record_type": "matrix",
            "target_field": "measure_id",
        },
    )

    assert len(issues) == 1
    assert issues[0]["evidence"]["issue_type"] == "reference_matrix_unavailable"


def test_reference_rule_reports_unreliable_source_scope_before_carrier_check() -> None:
    """来源行范围不可靠时，即使无法按范围命中矩阵也不得静默跳过。"""
    row = make_record("position_duty", measure_id="控制措施01")
    row.entity_code = ""
    matrix = make_record("matrix", measure_id="控制措施01")

    issues = reference_exists_v2(
        make_context([row], all_records=[row, matrix]),
        {
            "source_field": "measure_id",
            "target_record_type": "matrix",
            "target_field": "measure_id",
        },
    )

    assert len(issues) == 1
    assert issues[0]["evidence"]["issue_type"] == "reference_field_unavailable"


def test_system_change_rule_skips_when_system_rules_are_absent() -> None:
    """没有系统规则清单时，矩阵依赖三清单的第7条检查不得生成意见。"""
    matrix = make_record(
        "matrix",
        measure_id="职工福利保障与薪酬管理业务-1.员工信息管理-控制措施01",
        control_system="人资2.0",
    )
    matrix.fields["control_system"].red_spans = [{"text": "人资2.0"}]

    issues = system_rule_changes_v1(make_context([matrix]), {})

    assert issues == []


def test_system_change_rule_still_checks_when_system_rules_exist() -> None:
    """已报送系统规则清单时，第7条仍须核对矩阵修订与系统规则是否对应。"""
    matrix = make_record(
        "matrix",
        measure_id="职工福利保障与薪酬管理业务-1.员工信息管理-控制措施01",
        control_system="人资2.0",
    )
    matrix.fields["control_system"].red_spans = [{"text": "人资2.0"}]
    system_rule = make_record(
        "system_rule",
        row=4,
        measure_id="职工福利保障与薪酬管理业务-2.工资总额计划管理-控制措施01",
        system_name="智慧人资",
    )

    issues = system_rule_changes_v1(make_context([matrix, system_rule]), {})

    assert len(issues) == 1
    assert issues[0]["evidence"]["issue_type"] == "system_rule_change_mismatch"


def test_system_change_rule_checks_when_empty_system_rule_sheet_exists() -> None:
    """系统规则清单工作表已报送但为空时，第7条仍应核对并提示。"""
    matrix = make_record(
        "matrix",
        measure_id="控制措施01",
        control_system="人资2.0",
    )
    matrix.fields["control_system"].red_spans = [{"text": "人资2.0"}]

    issues = system_rule_changes_v1(
        make_context([matrix], [make_file("three_lists", ["system_rule"])]),
        {},
    )

    assert len(issues) == 1
    assert issues[0]["evidence"]["issue_type"] == "system_rule_change_mismatch"


def test_matrix_only_skips_position_duty_dependent_checks() -> None:
    """仅报送矩阵时，依赖岗位职责清单的矩阵侧规则均不执行。"""
    matrix = make_record(
        "matrix",
        measure_id="控制措施01",
        applicability="是",
        responsibility="党委组织部负责审核",
    )
    context = make_context([matrix], [make_file("matrix", ["matrix"])])

    assert missing_duty_measures(context, {}) == []
    assert measure_applicability_alignment_v1(context, {}) == []
    assert responsibility_department_alignment_v1(
        context,
        {"keyword_mappings": {"党委组织部": ["组织部"]}},
    ) == []
