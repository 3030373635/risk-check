from __future__ import annotations

from pathlib import Path
from typing import Any

from risk_audit.util import read_json


def _findings_path(value: str | Path) -> Path:
    path = Path(value)
    return path / "findings.json" if path.is_dir() else path


def compare_runs(left: str | Path, right: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Compare two deterministic finding sets by their stable finding_key."""
    left_path, right_path = _findings_path(left), _findings_path(right)
    before = {item["finding_key"]: item for item in read_json(left_path)}
    after = {item["finding_key"]: item for item in read_json(right_path)}
    added = [after[key] for key in sorted(after.keys() - before.keys())]
    removed = [before[key] for key in sorted(before.keys() - after.keys())]
    wording_changes = []
    status_changes = []
    for key in sorted(before.keys() & after.keys()):
        if before[key].get("message") != after[key].get("message"):
            wording_changes.append({"finding_key": key, "before": before[key].get("message"), "after": after[key].get("message")})
        if before[key].get("severity") != after[key].get("severity"):
            status_changes.append({"finding_key": key, "before": before[key].get("severity"), "after": after[key].get("severity")})
    return {"added": added, "removed": removed, "wording_changes": wording_changes, "status_changes": status_changes}
