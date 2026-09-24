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
        str(p.relative_to(base)): sha256_file(p)
        for p in sorted(base.rglob("*.json"))
    }
    return pack
