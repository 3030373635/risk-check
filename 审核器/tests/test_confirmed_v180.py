"""审核确认稿条款的独立能力验收，不依赖新规则包注册。"""
from importlib import import_module
from importlib.util import find_spec

import pytest

from risk_audit.checks.registry import CheckContext
from risk_audit.models import FieldValue, Record


def run(name, ctx, params):
    """调用待实现能力；name 为能力函数名，ctx/params 为审核上下文和配置。"""
    module = "risk_audit.checks.confirmed_v180"
    assert find_spec(module) is not None, "确认稿能力尚未实现"
    return getattr(import_module(module), name)(ctx, params)


def record(kind="position_duty", row=3, **values):
    """构造材料行；kind 为清单类型，row 为行号，values 为已读取字段。"""
    return Record(kind, "205H", "06", "default", "材料.xlsx", kind, row,
                  {key: FieldValue(value, value, f"A{row}") for key, value in values.items()}, f"{kind}-{row}")


def context(rows, matrices=()):
    """构造真实数据上下文；rows 为检查行，matrices 为关联的当前或删除矩阵。"""
    return CheckContext(list(rows), [], [*rows, *matrices], {}, {}, {}, [], [])


DEPARTMENT = {"field": "department", "placeholders": ["管理部门", "各部门", "项目管理部门", "资产使用保管部门", "相关部门", "无", "-", "/"], "placeholder_patterns": [], "separators": []}
POSITION = {"field": "position", "placeholders": ["部门负责人", "经办人员", "无", "-", "/"], "placeholder_patterns": [], "separators": ["、", "，", ",", "；", ";"]}
PHRASE = {"attributes": ["真实性", "准确性", "完整性", "合规性", "规范性"]}
MAPPING = {"source_field": "role", "text_field": "duty", "mapping": {"经办": ["主体责任"], "审核": ["审核责任"], "审批": ["审批责任"]}}
REFERENCE = {"source_field": "measure_id", "target_record_type": "matrix", "target_field": "measure_id"}
DELETED = {"source_fields": ["control_measure", "carrier"], "target_field": "duty", "match_scope": "measure"}


@pytest.mark.parametrize("department", ["项目管理部门-建设部", "资产使用保管部门-变检公司", "建设部"])
def test_concrete_department_suffix_is_accepted(department):
    assert run("field_constraints_v2", context([record(department=department)]), DEPARTMENT) == []


@pytest.mark.parametrize("department", ["项目管理部门", "项目管理部门-管理部门", "资产使用保管部门-相关部门"])
def test_generic_department_remains_invalid(department):
    issues = run("field_constraints_v2", context([record(department=department)]), DEPARTMENT)
    assert [item["kind"] for item in issues] == ["violation"]


@pytest.mark.parametrize("values", [{}, {"department": "无", "position_a": "不涉及"}, {"duty": "无"}])
def test_empty_or_explicit_no_incompatibility_has_no_detail_errors(values):
    row = record("incompatible_position", **values)
    assert run("field_constraints_v2", context([row]), DEPARTMENT) == []
    assert run("field_constraints_v2", context([row]), POSITION) == []


def test_incompatibility_checks_existing_position_columns_only():
    row = record("incompatible_position", department="建设部", position_a="经办人员", position_b="项目管理专责")
    issues = run("field_constraints_v2", context([row]), POSITION)
    assert [(item["kind"], item["evidence"]["field"]) for item in issues] == [("violation", "position_a")]


def test_unreadable_column_is_distinct_from_readable_blank():
    row = record(department="")
    issues = run("field_constraints_v2", context([row]), DEPARTMENT)
    assert issues[0]["kind"] == "violation"
    row.fields["department"].state = "formula_no_cache"
    assert run("field_constraints_v2", context([row]), DEPARTMENT)[0]["kind"] == "review"


def pair(left_id="M1", right_id="M1", left_role="经办", right_role="审核", person_id=True):
    """构造同人同职责两行；编号、角色和身份可靠性由参数决定。"""
    rows = [record(row=3, measure_id=left_id, control_measure="核对完整资料。", role=left_role, duty="对资料完整性负主体责任", position="管理专责"),
            record(row=4, measure_id=right_id, control_measure="核对完整资料。", role=right_role, duty="对资料完整性负审核责任", position="审核专责")]
    for item in rows:
        item.person_keys = [{"name": "张三", **({"person_id": "P1"} if person_id else {})}]
        item.fields["person_names"] = FieldValue("张三", "张三", f"B{item.row}")
    return rows


def overlaps(issues):
    """筛选人员冲突证据；issues 为能力输出意见。"""
    return [item for item in issues if item["evidence"]["issue_type"] == "same_duty_person_overlap"]


@pytest.mark.parametrize("ids", [("", ""), ("M1", ""), ("未关联", "M1")])
def test_exact_control_measure_fallback_groups_missing_or_unlinked_identifiers(ids):
    rows = pair(*ids)
    matrix = record("matrix", measure_id="M1", control_measure="核对 完整\n资料。")
    issues = overlaps(run("roles_same_duty_v3", context(rows, [matrix]), {}))
    assert len(issues) == 2 and all(item["kind"] == "violation" for item in issues)
    assert any(item["evidence"]["comparison_basis"] == "control_measure_exact" for item in issues)
    assert all(item["evidence"]["matrix_rows"] for item in issues)


def test_true_different_matrix_identifiers_never_merge_even_with_equal_content():
    rows = pair("M1", "M2")
    matrices = [record("matrix", row=i, measure_id=mid, control_measure="核对完整资料。") for i, mid in [(3, "M1"), (4, "M2")]]
    assert overlaps(run("roles_same_duty_v3", context(rows, matrices), {})) == []


def test_separation_matches_measure_identifiers_by_numbers_only():
    """编号文字不同但业务序号和控制措施序号相同时，第8条必须按同一措施核查。"""
    rows = pair(
        "yyy福利保障与zzzz管理业务-12.123123疗养组织-控制措施05",
        "xxx福利保障与xxx管理业务-12.职adf织-控制措施05",
    )
    for item in rows:
        item.fields["department"] = FieldValue("人力资源部", "人力资源部", f"C{item.row}")
    matrix = record(
        "matrix",
        measure_id="职工福利保障与薪酬管理业务-12.职工疗养组织-控制措施05",
    )

    issues = overlaps(run("roles_same_duty_v4", context(rows, [matrix]), {}))

    assert len(issues) == 2
    assert all(item["evidence"]["comparison_basis"] == "measure_id" for item in issues)


@pytest.mark.parametrize("changed", ["missing_entity", "missing_business", "changed_text", "ambiguous_matrix"])
def test_unreliable_correspondence_produces_review_without_overlap(changed):
    rows = pair("", "")
    matrices = [record("matrix", measure_id="M1", control_measure="核对完整资料。")]
    if changed == "missing_entity": rows[0].entity_code = rows[1].entity_code = ""
    if changed == "missing_business": rows[0].business_code = rows[1].business_code = ""
    if changed == "changed_text": rows[0].fields["control_measure"].current = "核对资料。"
    if changed == "ambiguous_matrix": matrices.append(record("matrix", row=4, measure_id="M2", control_measure="核对完整资料。"))
    issues = run("roles_same_duty_v3", context(rows, matrices), {})
    assert overlaps(issues) == []
    assert any(item["kind"] == "review" and item["evidence"].get("unavailable_reason") for item in issues)


def test_missing_explicit_roles_still_report_but_positive_responsibility_can_find_overlap():
    rows = pair(left_role="", right_role="")
    for item in rows: del item.fields["role"]
    matrix = record("matrix", measure_id="M1")
    issues = run("roles_same_duty_v3", context(rows, [matrix]), {})
    assert len(overlaps(issues)) == 2
    assert sum(item["evidence"].get("field") == "role" for item in issues) == 2


@pytest.mark.parametrize("duty", ["不负主体责任", "由供应商负主体责任", "负责经办资料"])
def test_missing_role_does_not_infer_from_negative_others_or_actions(duty):
    rows = pair(left_role="")
    rows[0].fields["duty"].current = duty
    assert overlaps(run("roles_same_duty_v3", context(rows, [record("matrix", measure_id="M1")]), {})) == []


def test_same_name_without_identifier_is_only_review():
    rows = pair(person_id=False)
    issues = overlaps(run("roles_same_duty_v3", context(rows, [record("matrix", measure_id="M1")]), {}))
    assert len(issues) == 2 and all(item["kind"] == "review" for item in issues)


def test_different_person_identifiers_have_priority_over_equal_names():
    rows = pair()
    rows[1].person_keys[0]["person_id"] = "P2"
    assert overlaps(run("roles_same_duty_v3", context(rows, [record("matrix", measure_id="M1")]), {})) == []


@pytest.mark.parametrize("applicability,expected", [("是", []), ("适用", []), ("否", ["violation"]), ("不适用", ["violation"]), ("", ["review"]), ("待定", ["review"])])
def test_reference_requires_explicit_applicability(applicability, expected):
    row = record(measure_id="M1")
    matrix = record("matrix", measure_id="M1", applicability=applicability)
    assert [item["kind"] for item in run("reference_exists_v2", context([row], [matrix]), REFERENCE)] == expected


def test_reference_matches_measure_identifiers_by_numbers_only():
    """控制措施编号的两组数字相同时，第9条不得因其他文字不同报未找到。"""
    row = record(measure_id="yyy福利保障与zzzz管理业务-12.123123疗养组织-控制措施05")
    matrix = record(
        "matrix",
        measure_id="xxx福利保障与xxx管理业务-12.职adf织-控制措施05",
        applicability="是",
    )

    assert run("reference_exists_v2", context([row], [matrix]), REFERENCE) == []


def test_conflicting_duplicate_matrix_applicability_requires_review():
    matrices = [record("matrix", row=3, measure_id="M1", applicability="是"), record("matrix", row=4, measure_id="M1", applicability="否")]
    issues = run("reference_exists_v2", context([record(measure_id="M1")], matrices), REFERENCE)
    assert [item["kind"] for item in issues] == ["review"]
    assert len(issues[0]["evidence"]["matrix_rows"]) == 2


def test_reference_never_crosses_entity_and_skips_when_current_matrix_is_absent():
    """其他主体的矩阵不能作为当前主体依赖；当前主体无矩阵时跳过跨表核对。"""
    row = record(measure_id="M1")
    matrix = record("matrix", measure_id="M1")
    assert run("reference_exists_v2", context([row], [matrix]), REFERENCE)[0]["evidence"]["issue_type"] == "reference_applicability_unavailable"
    matrix.entity_code = "other"
    issues = run("reference_exists_v2", context([row], [matrix]), REFERENCE)
    assert issues == []


@pytest.mark.parametrize("duty", ["负主体责任", "本岗位负审核责任", "对资料负审批责任", "对资料完整负责", "对资料完整性承担审核责任"])
def test_positive_own_responsibility_needs_no_mandatory_quality_suffix(duty):
    assert run("responsibility_phrase_v7", context([record(duty=duty)]), PHRASE) == []


@pytest.mark.parametrize("duty", ["不负主体责任", "由供应商负主体责任", "负责审核资料", "对资料完整性負审核责任", "负主体责任；本岗位不承担主体责任"])
def test_bad_responsibility_uses_unified_phrase_issue_and_preserves_facts(duty):
    issues = run("responsibility_phrase_v7", context([record(duty=duty)]), PHRASE)
    assert len(issues) == 1
    assert issues[0]["evidence"]["issue_type"] == "responsibility_phrase_missing"
    assert issues[0]["evidence"]["responsibility_facts"]["normalized_text"]


@pytest.mark.parametrize("role,duty,expected", [("经办", "负主体责任", []), ("审核", "负审核责任", []), ("审批", "本岗位承担未知句式审批责任", ["review"]), ("审批", "负审批责任", []), ("审核", "负主体责任", ["violation"]), ("审核", "承担未知句式审核责任", ["review"]), ("审核", "负审核责任并承担主体责任", ["review"]), ("", "负主体责任", [])])
def test_role_mapping_uses_positive_explicit_type(role, duty, expected):
    issues = run("value_mapping_v4", context([record(role=role, duty=duty)]), MAPPING)
    assert [item["kind"] for item in issues] == expected


@pytest.mark.parametrize("duty", ["不负审核责任", "由供应商承担审核责任", "负责审核资料"])
def test_role_mapping_does_not_accept_negative_others_or_actions(duty):
    issues = run("value_mapping_v4", context([record(role="审核", duty=duty)]), MAPPING)
    assert len(issues) == 1 and issues[0]["kind"] == "violation"
    assert not issues[0]["evidence"]["actual_responsibilities"]


def deleted_matrix(mid="M1", retained=False):
    """构造删除原文矩阵；mid 为编号，retained 表示当前同编号是否保留该原文。"""
    matrix = record("matrix", measure_id=mid, control_measure="旧载体" if retained else "新载体", carrier="新载体")
    matrix.fields["control_measure"].deleted_spans = [{"text": "旧载体", "start": 0, "end": 3}]
    return matrix


def test_deleted_exact_text_is_checked_in_incompatible_duty_and_final_text_wins():
    row = record("incompatible_position", measure_id="M1", duty="对旧载体负主体责任")
    issues = run("deleted_text_reappears_v3", context([row], [deleted_matrix()]), DELETED)
    assert [item["kind"] for item in issues] == ["violation"]
    assert issues[0]["evidence"]["target_cell"] == "A3"
    assert run("deleted_text_reappears_v3", context([row], [deleted_matrix(retained=True)]), DELETED) == []


@pytest.mark.parametrize("mid", ["", "M2"])
def test_deletion_rule_does_not_duplicate_missing_or_unmatched_identifiers(mid):
    assert run("deleted_text_reappears_v3", context([record(measure_id=mid, duty="旧载体")], [deleted_matrix()]), DELETED) == []


def test_deletion_source_missing_still_records_limitation():
    matrix = deleted_matrix()
    del matrix.fields["carrier"]
    issues = run("deleted_text_reappears_v3", context([record(measure_id="M1", duty="新载体")], [matrix]), DELETED)
    assert [item["evidence"]["issue_type"] for item in issues] == ["deletion_source_unavailable"]


def test_ordering_reports_both_rows_and_counterpart_coordinates():
    rows = [record(row=3, measure_id="M2"), record(row=4, measure_id="M1")]
    issues = run("ordered_records_v3", context(rows), {"field": "measure_id"})
    assert {item["record"].row for item in issues} == {3, 4}
    assert all(item["evidence"]["related_row"] in {3, 4} and item["evidence"]["problem_locations"] for item in issues)


def test_noncontinuous_identifier_reports_all_involved_rows():
    rows = [record(row=3, measure_id="M1"), record(row=4, measure_id="M2"), record(row=5, measure_id="M1")]
    issues = run("ordered_records_v3", context(rows), {"field": "measure_id"})
    noncontinuous = [item for item in issues if item["evidence"]["reason"] == "同一措施记录不连续"]
    assert {item["record"].row for item in noncontinuous} == {3, 4, 5}


def test_repeated_order_inversions_keep_each_pair_of_problem_locations():
    rows = [record(row=index + 3, measure_id=mid) for index, mid in enumerate(["M2", "M1", "M2", "M1"])]
    issues = run("ordered_records_v3", context(rows), {"field": "measure_id"})
    inversions = [item for item in issues if item["evidence"]["reason"] == "措施顺序倒置"]
    assert {item["record"].row for item in inversions} == {3, 4, 5, 6}
    assert {tuple(location["row"] for location in item["evidence"]["problem_locations"]) for item in inversions} == {(3, 4), (5, 6)}


def test_fully_struck_formula_measure_is_not_effective_content_fallback():
    rows = pair("", "")
    matrix = record("matrix", measure_id="M1", control_measure="核对完整资料。")
    matrix.fields["control_measure"].formula = '=A1'
    matrix.fields["control_measure"].deleted_spans = [{"text": "核对完整资料。", "start": 0, "end": 7}]
    issues = run("roles_same_duty_v3", context(rows, [matrix]), {})
    assert overlaps(issues) == []
    assert any(item["kind"] == "review" for item in issues)


@pytest.mark.parametrize("duty", ["本岗位不应负主体责任", "本岗位不应该承担审核责任", "负主体责任，本岗位不应承担主体责任"])
def test_modal_negation_never_establishes_positive_own_responsibility(duty):
    issues = run("responsibility_phrase_v7", context([record(duty=duty)]), PHRASE)
    assert len(issues) == 1
    assert issues[0]["evidence"]["issue_type"] == "responsibility_phrase_missing"
    if duty.startswith("负主体"):
        assert issues[0]["evidence"]["responsibility_facts"]["self_denials"]


@pytest.mark.parametrize("duty", ["由其他岗位负审核责任", "由建设部承担审核责任", "本岗位不应負审核责任"])
def test_specific_other_actor_or_negation_never_supplies_mapped_type(duty):
    issues = run("value_mapping_v4", context([record(role="审核", duty=duty)]), MAPPING)
    assert len(issues) == 1 and not issues[0]["evidence"]["actual_responsibilities"]


@pytest.mark.parametrize("role,duty", [("审核", "本岗位不承担主体责任，而由供应商负审核责任"),
                                       ("经办", "其他人员负主体责任"), ("审核", "其他单位承担审核责任"),
                                       ("经办", "并非本岗位负主体责任"), ("经办", "本岗位未曾承担主体责任")])
def test_review_examples_reject_nonlocal_or_compound_negative_responsibility(role, duty):
    row = record(role=role, duty=duty)
    phrase = run("responsibility_phrase_v7", context([row]), PHRASE)
    mapped = run("value_mapping_v4", context([row]), MAPPING)
    assert len(phrase) == 1 and len(mapped) == 1
    assert mapped[0]["evidence"]["actual_responsibilities"] == []
    facts = mapped[0]["evidence"]["responsibility_facts"]
    assert not any(not statement["negative"] and not statement["other_actor"] for statement in facts["statements"])
    assert facts["normalized_text"] == duty.replace("，", ",")


@pytest.mark.parametrize("duty", ["本岗位不承担主体责任，而由供应商负审核责任", "其他人员负主体责任", "并非本岗位负主体责任", "本岗位未曾承担主体责任"])
def test_review_examples_do_not_infer_missing_r8_role(duty):
    from risk_audit.checks.confirmed_v180 import _positive_role

    assert _positive_role(record(duty=duty)) == ""


@pytest.mark.parametrize("role,duty", [("审核", "本岗位不承担主体责任，而本岗位负审核责任"),
                                       ("经办", "供应商不承担审核责任，本岗位负主体责任"),
                                       ("审核", "并非本岗位负主体责任；本岗位负审核责任"),
                                       ("经办", "本岗位未曾承担审核责任，但本岗位负主体责任")])
def test_negative_clause_never_negates_independent_positive_local_clause(role, duty):
    row = record(role=role, duty=duty)
    assert run("responsibility_phrase_v7", context([row]), PHRASE) == []
    assert run("value_mapping_v4", context([row]), MAPPING) == []


def test_partial_baseline_unknown_identifier_gets_review_with_template_coordinates():
    rows = [record(row=3, measure_id="M2"), record(row=4, measure_id="M1"), record(row=5, measure_id="X3")]
    ctx = context(rows)
    ctx.baselines[("06", "default")] = {"measure_order": ["M2", "X3"]}
    issues = run("ordered_records_v3", ctx, {"field": "measure_id"})
    assert [(item["record"].row, item["kind"]) for item in issues] == [(4, "review")]
    evidence = issues[0]["evidence"]
    assert all(key in evidence for key in ["reason", "measure_id", "related_sheet", "related_row", "problem_locations"])
    assert evidence["measure_id"] == "M1"


def test_partial_baseline_keeps_known_inversion_and_unknown_review_separate():
    rows = [record(row=3, measure_id="X3"), record(row=4, measure_id="M1"), record(row=5, measure_id="M2")]
    ctx = context(rows)
    ctx.baselines[("06", "default")] = {"measure_order": ["M2", "X3"]}
    issues = run("ordered_records_v3", ctx, {"field": "measure_id"})
    assert {item["record"].row for item in issues if item["kind"] == "violation"} == {3, 5}
    assert [(item["record"].row, item["kind"]) for item in issues if item["kind"] == "review"] == [(4, "review")]


@pytest.mark.parametrize("duty", ["并非只有本岗位负主体责任", "本岗位是否负主体责任", "责任承担方负主体责任", "并非本岗位不负主体责任", "本岗位并非不承担主体责任"])
def test_unresolved_negation_or_actor_scope_requires_review(duty):
    row = record(role="经办", duty=duty)
    assert [item["kind"] for item in run("responsibility_phrase_v7", context([row]), PHRASE)] == ["review"]
    mapped = run("value_mapping_v4", context([row]), MAPPING)
    assert [item["kind"] for item in mapped] == ["review"]
    assert mapped[0]["evidence"]["actual_responsibilities"] == []


def test_baseline_with_no_recognized_rows_does_not_fall_back_to_natural_inversion():
    rows = [record(row=3, measure_id="M2"), record(row=4, measure_id="M1")]
    ctx = context(rows)
    ctx.baselines[("06", "default")] = {"measure_order": ["X3"]}
    issues = run("ordered_records_v3", ctx, {"field": "measure_id"})
    assert [(item["record"].row, item["kind"]) for item in issues] == [(3, "review"), (4, "review")]


@pytest.mark.parametrize("duty", ["其他人员负主体责任而由本岗位负审核责任", "参照“并非本岗位”说明，本岗位负审核责任"])
def test_explicit_local_actor_resets_prior_connection_or_quoted_scope(duty):
    row = record(role="审核", duty=duty)
    assert run("responsibility_phrase_v7", context([row]), PHRASE) == []
    assert run("value_mapping_v4", context([row]), MAPPING) == []


@pytest.mark.parametrize("role,duty,kind", [("审核", "其他相关方对资料完整性负审核责任", "violation"),
                                            ("经办", "其他公司对资料完整性负主体责任", "violation"),
                                            ("经办", "建设部负主体责任", "review"),
                                            ("经办", "建设部对资料完整性负主体责任", "review"),
                                            ("审核", "相关方对资料完整性负审核责任", "violation")])
def test_actor_before_responsibility_object_cannot_default_to_local(role, duty, kind):
    row = record(role=role, duty=duty)
    phrase = run("responsibility_phrase_v7", context([row]), PHRASE)
    mapped = run("value_mapping_v4", context([row]), MAPPING)
    assert [item["kind"] for item in phrase] == [kind]
    assert [item["kind"] for item in mapped] == [kind]
    assert mapped[0]["evidence"]["actual_responsibilities"] == []
    from risk_audit.checks.confirmed_v180 import _positive_role

    assert _positive_role(row) == ""


@pytest.mark.parametrize("duty", ["本部门对资料完整性负审核责任", "本岗位对资料完整性负审核责任", "对资料完整性负审核责任"])
def test_confirmed_local_or_omitted_subject_still_accepts_responsibility_object(duty):
    row = record(role="审核", duty=duty)
    assert run("responsibility_phrase_v7", context([row]), PHRASE) == []
    assert run("value_mapping_v4", context([row]), MAPPING) == []
