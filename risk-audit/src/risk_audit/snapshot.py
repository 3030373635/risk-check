from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from risk_audit import __version__
from risk_audit.models import FileRecord
from risk_audit.readers.excel import COLUMN_VISIBILITY_POLICY
from risk_audit.util import sha256_json, write_json


def build_snapshot(pack: dict[str, Any], files: list[FileRecord], capabilities: list[dict[str, Any]], run_id: str, entity_file: dict[str, str] | None = None, *, include_hidden: bool = False) -> dict[str, Any]:
    """构建可追溯的审核运行快照。

    Args:
        pack: 本次任务固定的已加载规则包。
        files: 本次审核扫描到的输入文件。
        capabilities: 审核引擎已注册的能力版本列表。
        run_id: 审核运行的稳定标识。
        entity_file: 可选的主体名册哈希与路径摘要。
        include_hidden: 是否将隐藏工作表纳入审核。

    Returns:
        包含地区、规则版本、输入摘要和快照哈希的字典。
    """

    frozen_config = {k: v for k, v in pack.items() if not k.startswith("_")}
    snap = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": __version__,
        "region_code": pack["manifest"].get("region_code"),
        "region_name": pack["manifest"].get("region_name"),
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
    """写入快照；path 为目标路径，snapshot 为已构建的快照。"""
    write_json(path, snapshot)
