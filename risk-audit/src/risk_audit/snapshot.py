from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from risk_audit import __version__
from risk_audit.models import FileRecord
from risk_audit.readers.excel import COLUMN_VISIBILITY_POLICY
from risk_audit.util import sha256_json, write_json


def build_snapshot(pack: dict[str, Any], files: list[FileRecord], capabilities: list[dict[str, Any]], run_id: str, entity_file: dict[str, str] | None = None, *, include_hidden: bool = False) -> dict[str, Any]:
    frozen_config = {k: v for k, v in pack.items() if not k.startswith("_")}
    snap = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": __version__,
        "rulepack_version": pack["manifest"].get("version"),
        "rulepack_content_hash": pack["manifest"].get("content_hash") or sha256_json(pack["_resource_hashes"]),
        "audit_as_of": pack["manifest"].get("audit_as_of"),
        "capabilities": capabilities,
        "inputs": [{"path": str(f.relative_path), "sha256": f.sha256, "format": f.true_format} for f in files],
        "resource_hashes": pack["_resource_hashes"],
        "effective_configuration": frozen_config,
        "runtime_rule_selection": pack.get("_runtime_rule_selection", {
            "disabled_defaults": [],
            "enabled_overrides": [],
            "effective_states": {},
        }),
        "entity_registry": entity_file,
        "sheet_visibility_policy": "include_hidden" if include_hidden else "visible_only",
        "column_visibility_policy": COLUMN_VISIBILITY_POLICY,
    }
    snap["snapshot_hash"] = sha256_json(snap)
    return snap


def save_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    write_json(path, snapshot)
