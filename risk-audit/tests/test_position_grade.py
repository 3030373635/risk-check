"""Explicit parenthetical staff grades may identify a unique scoped position."""
from pathlib import Path

import pytest

from risk_audit.checks.coverage import responsibility_coverage
from risk_audit.configuration.loader import load_pack
from risk_audit.models import FieldValue, FileRecord
from risk_audit.opinion_text import advice_v2
from risk_audit.readers.excel import parse_workbook
from test_coverage_exact_pairs import record, context, PARAMS


def matrix():
    return record(responsibility="国网湖南信通公司-综合管理部-四级职员")


def duty(position="薪酬绩效及体改管理专责（四级职员）", **kw):
    return record("position_duty", department="综合管理部", position=position, **kw)


@pytest.mark.parametrize("title", ["薪酬专责（四级职员）", "薪酬绩效及体改管理专责(四级职员)",
                                 "薪酬\n专责（四级\n职员）", "薪酬专责（4级职员）"])
def test_explicit_grade_in_a_unique_position_is_covered(title):
    assert responsibility_coverage(context(matrix(), [duty(title)]), PARAMS) == []


@pytest.mark.parametrize("title", ["薪酬专责（五级职员）", "薪酬专责（原四级职员）", "薪酬专责（四级职员及以上）",
                                 "薪酬专责（四级职员）（兼任）", "薪酬专责（四级职员培训）", "薪酬专责、培训专责（四级职员）"])
def test_different_or_qualified_grade_is_not_silently_matched(title):
    assert responsibility_coverage(context(matrix(), [duty(title)]), PARAMS)


@pytest.mark.parametrize("field,value", [("entity_code", "20GA"), ("business_code", "07"),
                                        ("variant_id", "other"), ("measure_id", "M2"),
                                        ("department", "财务资产部")])
def test_grade_match_never_crosses_scope_or_department(field, value):
    d = duty()
    if field in {"measure_id", "department"}: d.fields[field] = FieldValue(value, value, "A3")
    else: setattr(d, field, value)
    assert responsibility_coverage(context(matrix(), [d]), PARAMS)


def test_two_distinct_positions_with_same_grade_require_specific_mapping():
    ds = [duty("薪酬专责（四级职员）"), duty("培训专责（四级职员）", row=4)]
    issues = responsibility_coverage(context(matrix(), ds), PARAMS)
    assert [x["evidence"]["issue_type"] for x in issues] == ["coverage_position_ambiguous"]
    message = advice_v2("matrix_duty_coverage", "review", issues[0]["evidence"])
    assert "多个岗位" in message and "薪酬专责" in message and "培训专责" in message
    assert "尚未" not in message


def test_multiple_rows_of_the_same_position_are_not_ambiguous():
    assert responsibility_coverage(context(matrix(), [duty(), duty(row=4)]), PARAMS) == []


def test_confirmed_mapping_has_priority_over_grade_inference():
    params = {**PARAMS, "confirmed_mappings": {matrix().value("responsibility"): [
        {"department": "综合管理部", "position": "薪酬绩效及体改管理专责（四级职员）"},
        {"department": "综合管理部", "position": "培训专责（四级职员）"}]}}
    issues = responsibility_coverage(context(matrix(), [duty()]), params)
    assert [x["evidence"]["issue_type"] for x in issues] == ["specific_responsibility_missing"]


def test_qualifying_text_in_duties_does_not_supply_a_missing_position_grade():
    d = duty("薪酬专责", duty="由四级职员办理")
    assert responsibility_coverage(context(matrix(), [d]), PARAMS)


def test_real_asset_title_change_keeps_only_the_unresolved_logistics_position():
    root = Path(__file__).resolve().parents[2]
    pack = load_pack(root / "risk-audit/rulepacks/releases/1.2.1")
    folder = root / "templates/国网湖南信通公司第一批风控矩阵应用落地资料-9.11初审"
    records = []
    for path in sorted(folder.rglob("*.xlsx")):
        if path.name.startswith("~$") or "06" not in path.name: continue
        material = "three_lists" if "三清单" in path.name else "matrix"
        f = FileRecord(path, path.relative_to(folder), "test", "xlsx", "205H", [], False,
                       "06", "default", material)
        records.extend(r for s in parse_workbook(f, path, pack["field_aliases"]) for r in s.records)
    ms = {r.row: r for r in records if r.record_type == "matrix"}
    ds = [r for r in records if r.record_type == "position_duty"]
    params = next(c["params"] for rule in pack["rules"] for c in rule["checks"]
                  if c["check_id"] == "matrix_duty_coverage")
    for rn in (23, 24, 25):
        issues = responsibility_coverage(context(ms[rn], ds), params)
        assert len(issues) == 1
        assert issues[0]["evidence"]["responsibility_items"] == [
            "国网湖南信通公司-综合管理部-后勤及车辆管理专责"]
