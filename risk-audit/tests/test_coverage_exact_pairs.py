"""Coverage and concrete naming are independent, including on the real 06 sample."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from risk_audit.checks.coverage import responsibility_coverage
from risk_audit.checks.duty_details import field_constraints
from risk_audit.configuration.loader import load_pack
from risk_audit.models import FieldValue, FileRecord, Record
from risk_audit.readers.excel import parse_workbook


ROOT = Path(__file__).resolve().parents[2]
PARAMS = {"generic_responsibilities": ["资产使用保管部门", "各级单位", "各部门"],
          "confirmed_mappings": {}, "confirmed_aliases": {}, "unresolved_kind": "review"}


def record(kind="matrix", row=3, **values):
    fields = {"measure_id": "M1", "applicability": "是",
              "responsibility": "国网湖南信通公司-资产使用保管部门-主任",
              "department": "资产使用保管部门", "position": "主任", **values}
    return Record(kind, "205H", "06", "default", kind + ".xlsx", kind, row,
                  {k: FieldValue(v, v, f"A{row}") for k, v in fields.items()}, kind + str(row))


def context(matrix, duties):
    return SimpleNamespace(records=[matrix], all_records=[matrix, *duties])


@pytest.mark.parametrize("prefix", ["国网湖南信通公司-", "各级单位-", ""])
def test_separate_columns_match_without_repeated_unit_name(prefix):
    m = record(responsibility=prefix + "资产使用保管部门-主任")
    assert responsibility_coverage(context(m, [record("position_duty")]), PARAMS) == []


def test_exact_pair_after_layout_whitespace_normalization():
    m = record(responsibility="国网湖南信通公司—资产使用保管部门—主 任")
    d = record("position_duty", department="资产使用\n保管部门")
    assert responsibility_coverage(context(m, [d]), PARAMS) == []


@pytest.mark.parametrize("field,value", [("entity_code", "20GA"), ("business_code", "07"),
                                        ("variant_id", "other"), ("measure_id", "M2")])
def test_matching_generic_pair_elsewhere_does_not_satisfy_current_measure(field, value):
    m = record(); wrong = record("position_duty")
    if field == "measure_id": wrong.fields[field] = FieldValue(value, value, "G3")
    else: setattr(wrong, field, value)
    related = record("position_duty", row=4, department="财务资产部", position="核算专责")
    issues = responsibility_coverage(context(m, [wrong, related]), PARAMS)
    assert [x["evidence"]["issue_type"] for x in issues] == ["coverage_mapping_unconfirmed"]


def test_department_and_position_must_occur_together_on_one_duty_row():
    duties = [record("position_duty", position="设备专责"),
              record("position_duty", row=4, department="财务资产部")]
    assert responsibility_coverage(context(record(), duties), PARAMS)


def test_only_unmatched_items_remain_in_the_review_opinion():
    matched = "国网湖南信通公司-资产使用保管部门-主任"
    unresolved = "国网湖南信通公司-需求提报部门-设备专责"
    m = record(responsibility=matched + "、" + unresolved)
    issues = responsibility_coverage(context(m, [record("position_duty")]), PARAMS)
    assert len(issues) == 1
    assert issues[0]["evidence"]["responsibility_items"] == [unresolved]


def test_confirmed_mapping_still_requires_all_pairs():
    m = record()
    params = {**PARAMS, "confirmed_mappings": {m.value("responsibility"): [
        {"department": "资产使用保管部门", "position": "主任"},
        {"department": "技术发展部", "position": "主任"}]}}
    issues = responsibility_coverage(context(m, [record("position_duty")]), params)
    assert [x["evidence"]["issue_type"] for x in issues] == ["specific_responsibility_missing"]


def test_real_sample_preserves_missing_pair_and_department_specificity_checks():
    pack = load_pack(ROOT / "risk-audit/rulepacks/releases/1.2.1")
    folder = ROOT / "templates/国网湖南信通公司第一批风控矩阵应用落地资料-9.11初审"
    records = []
    for path in sorted(folder.rglob("*.xlsx")):
        if path.name.startswith("~$") or "06" not in path.name: continue
        material = "three_lists" if "三清单" in path.name else "matrix"
        f = FileRecord(path, path.relative_to(folder), "test", "xlsx", "205H", [], False,
                       "06", "default", material)
        records.extend(r for s in parse_workbook(f, path, pack["field_aliases"]) for r in s.records)
    matrices = {r.row: r for r in records if r.record_type == "matrix"}
    duties = [r for r in records if r.record_type == "position_duty"]
    params = next(c["params"] for rule in pack["rules"] for c in rule["checks"]
                  if c["check_id"] == "matrix_duty_coverage")
    assert responsibility_coverage(context(matrices[4], duties), params) == []
    issues = responsibility_coverage(context(matrices[14], duties), params)
    assert len(issues) == 1
    assert issues[0]["evidence"]["missing_responsibilities"] == [
        "国网湖南信通公司-项目管理中心-物资采购服务专责"]
    detail_params = next(c["params"] for rule in pack["rules"] for c in rule["checks"]
                         if c["check_id"] == "department_specific")
    department_issues = field_constraints(SimpleNamespace(records=duties), detail_params)
    assert {5, 6} <= {x["record"].row for x in department_issues
                      if x["evidence"]["issue_type"] == "field_not_concrete"}
