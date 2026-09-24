from __future__ import annotations

from risk_audit.entities import identify_entity, load_entities
from risk_audit.validation.manual import extract_manual_labels


def test_entity_list_keeps_missing_code_and_signal_xingtong_separate(project_root, pack):
    entities, missing = load_entities(project_root / "审核/会计主体清单20260907.xlsx")
    assert len(entities) == 506 and len(missing) == 1 and missing[0].source_row == 508
    code, _, conflict = identify_entity(["国网湖南信通公司"], entities, pack["entity_aliases"])
    assert (code, conflict) == ("205H", False)
    code, _, conflict = identify_entity(["国网湖南信通公司", "湖南星通"], entities, pack["entity_aliases"])
    assert code is None and conflict


def test_manual_extraction_expected_scopes(project_root, tmp_path):
    root = project_root / "审核/国网湖南信通公司第一批风控矩阵应用落地资料-9.11初审/国网湖南信通公司第一批风控矩阵应用落地资料-9.11初审"
    report = extract_manual_labels(root, tmp_path / "labels.json")
    assert report["counts"]["manual_nonempty_cells"] == 272
    assert report["counts"]["scope_excluded"] == 15
    role = sum(x.get("rule_id") == "duties.role_mapping" for x in report["atoms"])
    phrase = sum(x.get("rule_id") == "duties.responsibility_phrase" for x in report["atoms"])
    assert (role, phrase) == (8, 8)
    assert "总体准确率" in report["warning"]

