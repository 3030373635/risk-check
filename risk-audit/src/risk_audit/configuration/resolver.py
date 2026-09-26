from __future__ import annotations

import copy
from typing import Any


def _set_path(obj: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = obj
    for part in parts[:-1]:
        if part.isdigit(): cur = cur[int(part)]
        else: cur = cur[part]
    last = parts[-1]
    if last.isdigit(): cur[int(last)] = copy.deepcopy(value)
    else: cur[last] = copy.deepcopy(value)


def resolve_rules(pack: dict[str, Any], entity_code: str, business_code: str, variant_id: str) -> list[dict[str, Any]]:
    rules = copy.deepcopy(pack["rules"])
    overlays = sorted(pack.get("overlays", []), key=lambda x: x["priority"])
    for overlay in overlays:
        scope = overlay["scope"]
        if scope.get("entity_code") not in (None, "*", entity_code): continue
        if scope.get("business_code") not in (None, "*", business_code): continue
        if scope.get("variant_id") not in (None, "*", variant_id): continue
        rid = next((r["rule_id"] for r in rules if overlay["path"].startswith(r["rule_id"] + ".")), "")
        path = overlay["path"][len(rid) + 1:] if rid else ""
        for rule in rules:
            if rule["rule_id"] == rid: _set_path(rule, path, overlay["value"])
    return rules


def selector_matches(selector: dict[str, list[str]], value: str) -> bool:
    include = selector["include"]
    return ("*" in include or value in include) and value not in selector.get("exclude", [])
