import pytest

from risk_audit.checks.confirmed_v180 import responsibility_phrase_v8
from risk_audit.checks.registry import CheckContext
from risk_audit.models import FieldValue, Record
from risk_audit.opinion_text import advice, advice_v2


RESPONSIBILITY_ATTRIBUTES = ["准确性", "一致性", "真实性", "完整性", "合规性", "有效性", "及时性"]


def responsibility_opinion(text: str) -> str:
    """生成第10条真实审核意见；text 为岗位职责原文。"""
    field = FieldValue(text, text, "F3")
    row = Record("position_duty", "205H", "06", "default", "x.xlsx", "岗位职责清单", 3,
                 {"duty": field}, "responsibility-opinion")
    context = CheckContext([row], [], [row], {}, {}, {}, [], [])
    issues = responsibility_phrase_v8(context, {"attributes": RESPONSIBILITY_ATTRIBUTES})
    assert len(issues) == 1
    return advice_v2("broad_responsibility_pattern", "violation", issues[0]["evidence"])


def test_responsibility_opinion_explains_missing_quality_with_document_example() -> None:
    """已识别对象和责任类型时，应按0917-6口径指出缺少属性并给出修改句式。"""
    assert responsibility_opinion("对申请资料负有主体责任") == (
        "岗位职责责任句式不完整：已识别“负有主体责任”和责任对象，但未明确责任属性。"
        "请结合实际补充“一致性、准确性、真实性、完整性、有效性、及时性、合规性”等至少一项责任属性。"
        "建议句式：“对……的一致性/准确性/真实性/完整性/有效性/及时性/合规性负主体/templates/审批责任。”"
    )


@pytest.mark.parametrize(
    ("text", "identified", "missing", "suggestion"),
    [
        ("负责审核申请资料", "已识别工作动作", "未识别责任对象、责任属性和肯定承担责任的表述", "建议句式"),
        ("由供应商对资料完整性负主体责任", "已识别“供应商”承担“主体责任”", "未识别本岗位肯定承担责任的表述", "对……的完整性负主体责任"),
        ("对资料完整性不负主体责任", "已识别“主体责任”和责任属性“完整性”", "当前为否定责任表述", "对……的完整性负主体责任"),
        ("对资料完整性负主体责任，本岗位不承担主体责任", "已识别“主体责任”和责任属性“完整性”", "前后存在否定或撤销责任的矛盾表述", "对……的完整性负主体责任"),
    ],
)
def test_responsibility_opinion_names_identified_missing_and_suggested_change(
    text: str,
    identified: str,
    missing: str,
    suggestion: str,
) -> None:
    """所有第10条意见均应说明已识别内容、缺失原因和修改方法；各参数为预期文字。"""
    opinion = responsibility_opinion(text)

    assert identified in opinion
    assert missing in opinion
    assert suggestion in opinion


def test_complex_sentence_remains_a_manual_check_not_a_filling_error():
    text = advice("carrier_subset", "review", {"issue_type": "carrier_parse_unavailable"})
    assert "程序无法判断" in text and "请对照" in text
    assert "未列出" not in text and "请修改" not in text


def test_same_name_does_not_become_confirmed_same_person():
    text = advice("handler_reviewer_overlap", "review", {"unavailable_reason": "经办与审核姓名重合（张三），但无人员ID，可能存在同名"})
    assert "张三" in text and "是否为同一人" in text and "如为同一人" in text


def test_unmatched_carrier_keeps_the_concrete_name_and_action():
    text = advice("carrier_subset", "review", {"issue_type": "carrier_reference_unmatched", "missing_references": ["验收单"]})
    assert "验收单" in text and "控制载体”栏未列出" in text and "名称不一致" in text
