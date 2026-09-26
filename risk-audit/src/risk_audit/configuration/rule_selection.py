from __future__ import annotations

from pathlib import Path
from typing import Any

from risk_audit.configuration.validator import ConfigError
from risk_audit.util import read_json


AUDIT_CONFIG_KEYS = {"disabled_rules"}


def _normalize_rule_codes(values: Any, where: str) -> list[str]:
    """校验并标准化规则展示编号。

    values 为待校验的编号数组，where 为错误信息中的配置位置；
    返回去除首尾空白并统一为大写的编号列表。
    """
    if not isinstance(values, list):
        raise ConfigError([f"{where}: string array required"])
    normalized = [value.strip().upper() for value in values if isinstance(value, str)]
    if len(normalized) != len(values) or any(not value for value in normalized):
        raise ConfigError([f"{where}: non-empty strings required"])
    if len(normalized) != len(set(normalized)):
        raise ConfigError([f"{where}: duplicate rule display code"])
    return normalized


def load_audit_config(path: str | Path) -> dict[str, list[str]]:
    """读取并校验审核运行配置。

    path 为 JSON 配置文件路径；返回已标准化的默认停用规则配置。
    """
    config_path = Path(path).resolve()
    config = read_json(config_path)
    if not isinstance(config, dict):
        raise ConfigError(["audit config: object required"])
    unknown_keys = sorted(set(config) - AUDIT_CONFIG_KEYS)
    if unknown_keys:
        raise ConfigError([f"audit config: unknown field {key!r}" for key in unknown_keys])
    return {
        "disabled_rules": _normalize_rule_codes(
            config.get("disabled_rules", []),
            "audit config.disabled_rules",
        )
    }


def apply_rule_selection(
    pack: dict[str, Any],
    settings: dict[str, list[str]],
    *,
    enabled_rules: list[str] | None = None,
) -> dict[str, Any]:
    """将默认停用和本次启用选择应用到规则包。

    pack 为已加载的规则包，settings 为审核运行配置，enabled_rules
    为命令行中仅对本次运行启用的展示编号；返回用于运行快照的选择记录。
    """
    disabled_codes = _normalize_rule_codes(
        settings.get("disabled_rules", []),
        "audit config.disabled_rules",
    )
    enabled_codes = _normalize_rule_codes(
        enabled_rules or [],
        "command line.enable_rule",
    )
    known_codes = {rule["display_code"].upper() for rule in pack.get("rules", [])}
    requested_codes = set(disabled_codes) | set(enabled_codes)
    unknown_codes = sorted(requested_codes - known_codes)
    if unknown_codes:
        raise ConfigError([
            f"unknown rule display code {code}"
            for code in unknown_codes
        ])

    # 先应用默认停用，再应用本次启用，确保命令行可以临时覆盖配置文件。
    for rule in pack.get("rules", []):
        display_code = rule["display_code"].upper()
        if display_code in disabled_codes:
            rule["enabled"] = False
        if display_code in enabled_codes:
            rule["enabled"] = True

    effective_states = {
        code: all(
            rule["enabled"]
            for rule in pack["rules"]
            if rule["display_code"].upper() == code
        )
        for code in sorted(requested_codes)
    }
    return {
        "disabled_defaults": disabled_codes,
        "enabled_overrides": enabled_codes,
        "effective_states": effective_states,
    }
