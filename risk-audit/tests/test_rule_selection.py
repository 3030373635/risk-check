"""验证运行时规则启停配置。"""
from __future__ import annotations

import copy
import json

import pytest

from risk_audit import configuration
from risk_audit.cli import parser
from risk_audit.configuration import ConfigError


def test_disabled_display_code_controls_all_subrules(tmp_path, pack):
    """tmp_path 为临时目录，pack 为规则包；R03 应一次关闭全部同编号子规则。"""
    load_audit_config = getattr(configuration, "load_audit_config", None)
    apply_rule_selection = getattr(configuration, "apply_rule_selection", None)
    assert callable(load_audit_config) and callable(apply_rule_selection)
    config_path = tmp_path / "audit-config.json"
    config_path.write_text(json.dumps({"disabled_rules": ["R03"]}), encoding="utf-8")
    runtime_pack = copy.deepcopy(pack)
    original_states = {
        rule["rule_id"]: rule["enabled"]
        for rule in runtime_pack["rules"]
        if rule["display_code"] != "R03"
    }

    settings = load_audit_config(config_path)
    selection = apply_rule_selection(runtime_pack, settings, enabled_rules=[])

    r03_rules = [rule for rule in runtime_pack["rules"] if rule["display_code"] == "R03"]
    assert len(r03_rules) == 2
    assert all(rule["enabled"] is False for rule in r03_rules)
    assert selection["effective_states"]["R03"] is False
    assert {
        rule["rule_id"]: rule["enabled"]
        for rule in runtime_pack["rules"]
        if rule["display_code"] != "R03"
    } == original_states


def test_command_line_enable_overrides_default_disabled_rule(tmp_path, pack):
    """tmp_path 为临时目录，pack 为规则包；本次启用应覆盖配置文件的默认停用。"""
    load_audit_config = getattr(configuration, "load_audit_config", None)
    apply_rule_selection = getattr(configuration, "apply_rule_selection", None)
    assert callable(load_audit_config) and callable(apply_rule_selection)
    config_path = tmp_path / "audit-config.json"
    config_path.write_text(json.dumps({"disabled_rules": ["R03"]}), encoding="utf-8")
    runtime_pack = copy.deepcopy(pack)

    settings = load_audit_config(config_path)
    selection = apply_rule_selection(runtime_pack, settings, enabled_rules=["R03"])

    assert all(
        rule["enabled"] is True
        for rule in runtime_pack["rules"]
        if rule["display_code"] == "R03"
    )
    assert selection["enabled_overrides"] == ["R03"]
    assert selection["effective_states"]["R03"] is True


def test_unknown_rule_code_is_rejected(pack):
    """pack 为规则包；不存在的展示编号必须报错，不得静默忽略。"""
    apply_rule_selection = getattr(configuration, "apply_rule_selection", None)
    assert callable(apply_rule_selection)

    with pytest.raises(ConfigError, match="unknown rule display code R99"):
        apply_rule_selection(copy.deepcopy(pack), {"disabled_rules": ["R99"]}, enabled_rules=[])


def test_cli_accepts_config_and_enable_rule_options():
    """命令行解析器应接收配置路径和可重复的本次启用编号。"""
    args = parser().parse_args([
        "trial",
        "--input", "input",
        "--config", "custom-audit-config.json",
        "--enable-rule", "R03",
        "--enable-rule", "R07",
    ])

    assert args.config == "custom-audit-config.json"
    assert args.enable_rules == ["R03", "R07"]
