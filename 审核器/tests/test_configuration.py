from __future__ import annotations

import copy
import json
import shutil

import pytest

from risk_audit.configuration.publisher import RulePackStore
from risk_audit.configuration.validator import ConfigError, validate_pack


def rule(pack, rid):
    return next(x for x in pack["rules"] if x["rule_id"] == rid)


def test_release_valid_and_r07_cannot_be_enabled(pack, registry):
    validate_pack(pack, registry)
    rule(pack, "systems.content")["enabled"] = True
    with pytest.raises(ConfigError, match="not implemented"):
        validate_pack(pack, registry)


@pytest.mark.parametrize("mutation,needle", [
    (lambda p: rule(p, "duties.completeness")["checks"][0]["params"].update({"mystery": 1}), "unknown parameter"),
    (lambda p: rule(p, "duties.completeness")["checks"][0]["params"].update({"fields": ["manual_review"]}), "artificial audit field"),
    (lambda p: rule(p, "duties.completeness")["checks"][0]["message"].update({"violation": "{unknown_answer}"}), "unknown template variable"),
    (lambda p: rule(p, "duties.completeness").update({"unknown": True}), "unknown field"),
])
def test_unknown_configuration_rejected(pack, registry, mutation, needle):
    mutation(pack)
    with pytest.raises(ConfigError, match=needle): validate_pack(pack, registry)


def test_overlay_conflict_and_unknown_path_rejected(pack, registry):
    pack["overlays"] = [
        {"priority": 10, "scope": {"entity_code": "205H"}, "path": "duties.separation.enabled", "value": True},
        {"priority": 10, "scope": {"entity_code": "205H"}, "path": "duties.separation.enabled", "value": False},
        {"priority": 20, "scope": {"entity_code": "205H"}, "path": "duties.separation.no_such_path", "value": False},
    ]
    with pytest.raises(ConfigError) as exc: validate_pack(pack, registry)
    assert "conflicting overlay" in str(exc.value)
    assert "target path does not exist" in str(exc.value)


def test_immutable_publish_and_rollback(tmp_path, project_root, registry):
    root = tmp_path / "packs"; (root / "releases").mkdir(parents=True)
    shutil.copytree(project_root / "审核器/rulepacks/releases/1.0.0", root / "releases/1.0.0")
    (root / "active.json").write_text('{"version":"1.0.0"}', encoding="utf-8")
    store = RulePackStore(root, registry)
    draft = store.create_draft("next", "1.0.0")
    data = json.loads((draft / "rules/R10.json").read_text(encoding="utf-8")); data["revision"] = 2
    (draft / "rules/R10.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    target = store.publish("next", "1.0.1")
    assert target.exists()
    with pytest.raises(FileExistsError): store.publish("next", "1.0.1")
    assert store.rollback("1.0.0").name == "1.0.0"
    assert json.loads((root / "active.json").read_text())["version"] == "1.0.0"


def test_new_existing_capability_rule_valid_without_registry_change(pack, registry):
    new = copy.deepcopy(rule(pack, "duties.completeness"))
    new.update(rule_id="extension.required_position", display_code="EX13", title="扩展字段检查")
    new["checks"][0]["check_id"] = "required_position_only"
    new["checks"][0]["params"]["fields"] = ["position"]
    pack["rules"].append(new)
    validate_pack(pack, registry)


def test_schema_contains_v3_accepts_canonical_field_labels(pack, registry):
    """pack 为待验证规则包，registry 为能力表；结构检查 v3 应允许配置通用字段名。"""
    schema_rule = rule(pack, "schema.applicability")
    schema_check = schema_rule["checks"][0]
    schema_check["operator_version"] = 3
    schema_check["params"]["field_labels"] = {
        "applicability": "是否适用/是否适用及原因",
    }

    validate_pack(pack, registry)
