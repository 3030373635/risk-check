from __future__ import annotations

import pytest

from risk_audit.checks.carriers import parse_carrier_references, split_carrier_list
from risk_audit.checks.registry import CheckContext, set_subset
from risk_audit.models import Entity, FieldValue, Record


PARAMS = {
    "source_field": "carrier",
    "fallback_explicit_field": "duty",
    "target_record_type": "matrix",
    "target_field": "carrier",
    "alias_resource": "carrier_aliases",
}


def record(record_type: str, *, duty: str = "", carriers: str = "", entity: str = "205H", row: int = 3) -> Record:
    fields = {
        "measure_id": FieldValue("M1", "M1", f"A{row}"),
        "carrier": FieldValue(carriers, carriers, f"B{row}"),
        "duty": FieldValue(duty, duty, f"C{row}"),
    }
    return Record(record_type, entity, "06", "default", f"{record_type}.xlsx", record_type, row, fields, f"{record_type}-{row}")


def run(duty: Record, matrices: list[Record], resources: dict | None = None):
    all_records = [duty, *matrices]
    ctx = CheckContext(
        [duty], [], all_records, {"205H": Entity("205H", "信通公司")}, {}, resources or {}, ["205H"],
        [{"business_code": "06", "variant_id": "default"}],
    )
    return set_subset(ctx, PARAMS)


def test_compound_terms_and_quality_words_are_never_split_characterwise():
    allowed = {"验收与移交记录", "及时性评价表", "涉及事项清单"}
    parsed = parse_carrier_references(
        "对验收与移交记录、及时性评价表及涉及事项清单的准确性、完整性负主体责任。",
        allowed=allowed,
    )
    assert parsed["status"] == "matched"
    assert set(parsed["references"]) == allowed


def test_described_responsibility_attribute_is_not_a_third_carrier():
    allowed = {"工程项目明细清单", "验收盘点清单"}
    parsed = parse_carrier_references(
        "对工程项目明细清单及验收盘点清单的设备与现场信息一致性负审核责任。", allowed=allowed
    )
    assert parsed["status"] == "matched"
    assert set(parsed["references"]) == allowed


def test_behaviour_only_duties_are_diagnosed_not_applicable():
    for sentence in ("对本岗位工作负责任。", "保证账卡物相符。", "涉及财务管理的部分。", "确保某事项。"):
        parsed = parse_carrier_references(sentence, allowed={"审批单"})
        assert parsed["status"] == "not_applicable"
        assert parsed["references"] == []


def test_action_quality_is_an_attribute_of_the_carrier():
    for sentence in ("对协议签订及时性负责任。", "负责协议签订及时性。"):
        parsed = parse_carrier_references(sentence, allowed={"协议"})
        assert parsed["status"] == "matched"
        assert parsed["references"] == ["协议"]


def test_quotes_do_not_hide_an_unquoted_reference_and_unknown_quote_is_real():
    parsed = parse_carrier_references(
        "对《申请单》及验收记录的准确性负责任。", allowed={"申请单", "验收记录"}
    )
    assert parsed["status"] == "matched"
    assert set(parsed["references"]) == {"申请单", "验收记录"}
    unknown = parse_carrier_references("对《未知申请单》的准确性负责任。", allowed={"申请单"})
    assert unknown["status"] == "unresolved"
    assert unknown["references"] == ["未知申请单"]


def test_short_name_does_not_match_a_longer_unconfirmed_name():
    parsed = parse_carrier_references("对审批单附件清单的完整性负责任。", allowed={"审批单"})
    assert "审批单" not in parsed["references"]
    assert "审批单附件清单" in parsed["references"]


def test_terminology_recognizes_but_never_expands_current_measure_allowance():
    duty = record("position_duty", duty="对其他措施申请单的准确性负责任。")
    matrix = record("matrix", carriers="当前措施验收记录")
    issues = run(duty, [matrix], {"terminology": {"schema_version": "1.0", "terms": {"carrier": ["其他措施申请单"], "department": [], "position": []}, "metadata": {}}})
    assert issues[0]["evidence"]["issue_type"] == "carrier_reference_unmatched"
    assert issues[0]["evidence"]["missing_references"] == ["其他措施申请单"]


def test_partial_subset_is_sufficient_and_numbering_is_safely_removed():
    duty = record("position_duty", duty="对验收与移交记录的准确性负责任。")
    matrix = record("matrix", carriers="1.验收与移交记录；\n2.审批单（正式版）。")
    assert split_carrier_list(matrix.value("carrier")) == {"验收与移交记录", "审批单(正式版)"}
    assert run(duty, [matrix]) == []


def test_duplicate_matrix_rows_do_not_overwrite_or_union_conflicts():
    duty = record("position_duty", duty="对申请单的准确性负责任。")
    same = [record("matrix", carriers="申请单", row=3), record("matrix", carriers="申请单", row=4)]
    assert run(duty, same) == []
    conflict = [record("matrix", carriers="申请单", row=3), record("matrix", carriers="验收记录", row=4)]
    issues = run(duty, conflict)
    assert len(issues) == 1
    assert issues[0]["evidence"]["issue_type"] == "matrix_ambiguous"


def test_unknown_document_and_unparseable_object_have_distinct_issue_types():
    matrix = record("matrix", carriers="申请单")
    unknown = run(record("position_duty", duty="对未知申请单的准确性负责任。"), [matrix])
    assert unknown[0]["evidence"]["issue_type"] == "carrier_reference_unmatched"
    unavailable = run(record("position_duty", duty="对专项数据的准确性负责任。"), [matrix])
    assert unavailable[0]["evidence"]["issue_type"] == "carrier_parse_unavailable"
    assert unavailable[0]["evidence"]["original_sentence"] == "对专项数据的准确性负责任。"


def test_partial_parse_keeps_an_explicit_unmatched_reference():
    matrix = record("matrix", carriers="申请单")
    issues = run(record("position_duty", duty="对《未知申请单》及专项数据的准确性负责任。"), [matrix])
    assert {i["evidence"]["issue_type"] for i in issues} == {"carrier_parse_unavailable", "carrier_reference_unmatched"}


def test_quoted_matrix_names_and_fullwidth_numbering_match_duty_names():
    assert run(record("position_duty", duty="对《验收单》的准确性负责任。"), [record("matrix", carriers="1．《验收单》；2．申请单")]) == []


def test_action_ending_in_document_noun_is_not_fabricated_as_a_document():
    parsed = parse_carrier_references("负责按时向发电客户发布上月电费账单。", allowed={"客户账单"})
    assert parsed["status"] == "unavailable"
    assert parsed["references"] == []


@pytest.mark.parametrize("sentence", [
    "确保账卡一致。", "保证账卡相符。", "确保账卡物一致。", "保证账、卡、物相符。",
    "确保账实一致。", "确保账账相符。", "保证账表一致。", "对保持账卡一致负主体责任。",
])
def test_reconciliation_objectives_do_not_become_carrier_references(sentence):
    parsed = parse_carrier_references(sentence, allowed={"资产卡片", "设备台账"})
    assert parsed["status"] == "not_applicable"
    assert parsed["references"] == [] and parsed["unavailable_fragments"] == []


def test_real_06_duty_33_reconciliation_sentence_has_no_carrier_question():
    sentence = ("会同实物管理部门和使用保管单位开展固定资产清查盘点，组织开展资产盘活工作。"
                "根据清查盘点结果，同步调整资产卡片价值信息，确保账卡一致。")
    matrix = record("matrix", carriers="1.现场设备实物信息；2.资产卡片信息；3.专业系统设备台账信息。")
    assert run(record("position_duty", duty=sentence), [matrix]) == []


def test_control_objective_does_not_hide_actual_unmatched_carriers():
    sentence = "对《资产卡片》、设备台账及未知审批单的准确性负主体责任。确保账卡一致。"
    matrix = record("matrix", carriers="资产卡片；设备台账")
    resources = {"terminology": {"terms": {"carrier": ["未知审批单"]}}}
    issues = run(record("position_duty", duty=sentence), [matrix], resources)
    assert [x["evidence"]["issue_type"] for x in issues] == ["carrier_reference_unmatched"]
    assert issues[0]["evidence"]["missing_references"] == ["未知审批单"]


def test_document_named_after_control_objective_is_still_checked():
    parsed = parse_carrier_references("对账卡一致核对表的准确性负主体责任。确保账卡一致。", allowed={"资产卡片"})
    assert parsed["status"] == "unresolved"
    assert parsed["unresolved"] == ["账卡一致核对表"]


def test_control_objective_does_not_hide_other_unparsed_object():
    issues = run(record("position_duty", duty="确保账卡一致。对专项数据的准确性负主体责任。"),
                 [record("matrix", carriers="资产卡片")])
    assert [x["evidence"]["issue_type"] for x in issues] == ["carrier_parse_unavailable"]
    assert issues[0]["evidence"]["parse_evidence"] == [
        {"kind": "unparsed_responsibility_object", "text": "专项数据"}]
