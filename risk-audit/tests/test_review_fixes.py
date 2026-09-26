from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

from openpyxl import load_workbook

from risk_audit.checks.registry import CheckContext, required_documents, schema_contains, set_subset
from risk_audit.entities import identify_entity
from risk_audit.models import Entity, FieldValue, FileRecord, Record
from risk_audit.readers.excel import parse_workbook
from risk_audit.readers.xls import prepare_conversion_profile
from risk_audit.run_diff import compare_runs
from risk_audit.util import sha256_file


def _file(path: Path, material: str, true_format: str = "xlsx", entity: str | None = "205H", conflict: bool = False) -> FileRecord:
    return FileRecord(path, Path(path.name), "h", true_format, entity, [], conflict, "06", "default", material)


def _context(files=(), records=(), baselines=None, resources=None):
    return CheckContext(list(records), list(files), list(records), {"205H": Entity("205H", "国网湖南信通公司")}, baselines or {}, resources or {}, ["205H"], [{"business_code": "06", "variant_id": "default"}])


def test_real_06_multilevel_header_detects_one_missing_repeated_frequency(project_root, tmp_path, pack):
    source = project_root / "templates/第一批省公司通用版本整合0818/第一批省公司通用版本整合9.4/06 设备（资产）管理/06风控矩阵-设备（资产）管理-省公司（审定）8.11.xlsx"
    baseline_file = _file(source, "matrix")
    baseline_sheet = parse_workbook(baseline_file, source, pack["field_aliases"])[0]
    frequency_paths = [x for x in baseline_sheet.business_header_paths if x["path"][-1]["name"] == "评价频率"]
    assert [(x["coordinate"], x["path"][0]["name"]) for x in frequency_paths] == [("X3", "业务自评价"), ("AA3", "集团监督评价"), ("AE3", "数字化监督场景")]

    changed = tmp_path / source.name
    shutil.copyfile(source, changed)
    wb = load_workbook(changed); wb.active["X3"] = None; wb.save(changed)
    submitted = _file(changed, "matrix")
    submitted.sheets = parse_workbook(submitted, changed, pack["field_aliases"])
    baseline = {("06", "default"): {"fields": sorted(baseline_sheet.columns), "business_header_paths": baseline_sheet.business_header_paths}}
    issues = schema_contains(_context([submitted], baselines=baseline), {"sheet_types": ["matrix"], "required_fields": [], "compare_baseline": True})
    assert any("业务自评价/评价频率（X3）" in x["evidence"]["unavailable_reason"] for x in issues)


def test_r03_split_applicability_tail_stays_review(project_root, pack):
    source = project_root / "outputs/01a09d8b-4204-7c53-979d-e66fe1726a68/信通验证/06 设备（资产）管理-9.11初审/国网湖南信通公司 06风控矩阵-设备（资产）管理.xlsx"
    file = _file(source, "matrix")
    file.sheets = parse_workbook(file, source, pack["field_aliases"])
    ctx = _context([file], resources={"field_aliases": pack["field_aliases"]})
    issues = schema_contains(ctx, {"sheet_types": ["matrix"], "required_fields": [], "check_applicability_tail": True})
    assert any(x["kind"] == "review" and "拆分符合性尚未确认" in x["evidence"]["unavailable_reason"] for x in issues)


def _position(duty: str, carrier: str = "") -> Record:
    fields = {
        "measure_id": FieldValue("M1", "M1", "A3"),
        "carrier": FieldValue(carrier, carrier, "B3"),
        "duty": FieldValue(duty, duty, "C3"),
    }
    return Record("position_duty", "205H", "06", "default", "d.xlsx", "岗位职责清单", 3, fields, "d3")


def _matrix(carriers: str) -> Record:
    fields = {"measure_id": FieldValue("M1", "M1", "A3"), "carrier": FieldValue(carriers, carriers, "B3")}
    return Record("matrix", "205H", "06", "default", "m.xlsx", "矩阵", 3, fields, "m3")


def test_r09_responsibility_enumeration_matches_partial_carrier_set(pack):
    duty = _position("对工程项目明细清单、采购验收记录及验收盘点清单的准确性、完整性负主体责任")
    matrix = _matrix("1.工程项目明细清单；\n2.采购验收记录（采购记录、货物验收单）；\n3.验收盘点清单；\n4.设备台账信息")
    ctx = _context(records=[duty, matrix], resources={"carrier_aliases": {"采购验收记录": "采购验收记录（采购记录、货物验收单）"}})
    ctx.records = [duty]
    assert set_subset(ctx, {"source_field": "carrier", "fallback_explicit_field": "duty", "target_record_type": "matrix", "target_field": "carrier", "alias_resource": "carrier_aliases"}) == []


def test_r09_generic_responsibility_is_not_a_carrier_and_unknown_is_review():
    matrix = _matrix("工程项目明细清单")
    generic = _position("对本岗位工作负责任")
    ctx = _context(records=[generic, matrix]); ctx.records = [generic]
    params = {"source_field": "carrier", "fallback_explicit_field": "duty", "target_record_type": "matrix", "target_field": "carrier"}
    assert set_subset(ctx, params) == []
    unknown = _position("对未经确认资料清单的准确性负主体责任")
    ctx = _context(records=[unknown, matrix]); ctx.records = [unknown]
    issues = set_subset(ctx, params)
    assert len(issues) == 1 and issues[0]["kind"] == "review" and "未经确认资料清单" in issues[0]["evidence"]["unavailable_reason"]


def test_r02_explanation_requires_true_word_format_without_conflict(tmp_path):
    fake = _file(tmp_path / "主体差异说明.docx", "explanation", true_format="xlsx")
    issues = required_documents(_context([fake]), {"material_types": [], "explanation_mode": True})
    assert any(x["evidence"].get("missing_material") == "主体差异说明" for x in issues)
    valid = _file(tmp_path / "主体差异说明.docx", "explanation", true_format="docx")
    assert required_documents(_context([valid]), {"material_types": [], "explanation_mode": True}) == []
    valid.entity_conflict = True
    assert required_documents(_context([valid]), {"material_types": [], "explanation_mode": True})


def test_r01_matrix_or_evidence_keyword_is_accepted(tmp_path):
    matrix = _file(tmp_path / "国网湖南信通公司06取证.xlsx", "matrix")
    assert required_documents(_context([matrix]), {"material_types": [], "naming_mode": True}) == []


def test_entity_containment_is_resolved_per_independent_text():
    entities = {
        "P": Entity("P", "国网湖南省电力有限公司"),
        "C": Entity("C", "国网湖南省电力有限公司信通分公司"),
    }
    assert identify_entity(["国网湖南省电力有限公司信通分公司"], entities, {}) == ("C", ["标准全称:国网湖南省电力有限公司信通分公司"], False)
    code, _, conflict = identify_entity(["国网湖南省电力有限公司信通分公司", "国网湖南省电力有限公司"], entities, {})
    assert code is None and conflict


def test_conversion_profile_disables_active_content_and_link_updates(tmp_path):
    prepare_conversion_profile(tmp_path)
    text = (tmp_path / "user/registrymodifications.xcu").read_text(encoding="utf-8")
    assert "DisableMacrosExecution" in text and "DisableActiveContent" in text
    assert "Office.Calc/Content/Update" in text and '<value>2</value>' in text


def test_run_diff_is_stable_and_reports_all_four_change_classes(tmp_path):
    left = [
        {"finding_key": "a", "message": "old", "severity": "review"},
        {"finding_key": "b", "message": "gone", "severity": "violation"},
        {"finding_key": "c", "message": "same", "severity": "review"},
    ]
    right = [
        {"finding_key": "a", "message": "new", "severity": "violation"},
        {"finding_key": "c", "message": "same", "severity": "review"},
        {"finding_key": "d", "message": "added", "severity": "review"},
    ]
    (tmp_path / "l.json").write_text(json.dumps(left), encoding="utf-8")
    (tmp_path / "r.json").write_text(json.dumps(right), encoding="utf-8")
    result = compare_runs(tmp_path / "l.json", tmp_path / "r.json")
    assert [x["finding_key"] for x in result["added"]] == ["d"]
    assert [x["finding_key"] for x in result["removed"]] == ["b"]
    assert result["wording_changes"][0]["finding_key"] == "a"
    assert result["status_changes"][0]["finding_key"] == "a"
