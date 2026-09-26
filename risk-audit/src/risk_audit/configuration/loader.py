from __future__ import annotations

from pathlib import Path
from typing import Any

from risk_audit.util import read_json, sha256_file


RESOURCE_FILES = (
    "manifest.json", "field_catalog.json", "field_aliases.json", "entity_aliases.json",
    "submission_scope.json", "baseline_registry.json", "roles_and_positions.json",
    "carrier_aliases.json", "overlays.json", "terminology.json", "input_overrides.json", "result_policy.json",
    "parser_policy.json", "semantic_config.json", "semantic_lexicon.json",
    "responsibility_applicability.json",
)


def load_pack(path: str | Path) -> dict[str, Any]:
    """加载规则包并计算资源文件哈希。

    参数 path 为规则包目录，可传入字符串或 Path 对象。
    """
    base = Path(path).resolve()
    if not base.is_dir():
        raise FileNotFoundError(f"rule pack does not exist: {base}")
    pack: dict[str, Any] = {"_path": str(base), "rules": []}
    for name in RESOURCE_FILES:
        p = base / name
        if p.exists():
            pack[name.removesuffix(".json")] = read_json(p)
    rules_dir = base / "rules"
    if not rules_dir.is_dir():
        raise FileNotFoundError(f"missing rules directory: {rules_dir}")
    for p in sorted(rules_dir.glob("*.json")):
        rule = read_json(p)
        rule["_source"] = str(p)
        pack["rules"].append(rule)
    pack["_resource_hashes"] = {
        # 哈希键统一使用正斜杠，避免 Windows 分隔符改变发布包内容哈希。
        p.relative_to(base).as_posix(): sha256_file(p)
        for p in sorted(base.rglob("*.json"))
    }
    return pack
