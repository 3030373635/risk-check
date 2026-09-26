from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from risk_audit.checks.registry import CapabilityRegistry
from risk_audit.configuration.loader import load_pack
from risk_audit.configuration.validator import validate_pack
from risk_audit.util import deep_diff, sha256_json, write_json


class RulePackStore:
    def __init__(self, root: str | Path, registry: CapabilityRegistry):
        self.root = Path(root).resolve()
        self.registry = registry
        (self.root / "drafts").mkdir(parents=True, exist_ok=True)
        (self.root / "releases").mkdir(parents=True, exist_ok=True)

    def create_draft(self, name: str, from_version: str | None = None) -> Path:
        target = self.root / "drafts" / name
        if target.exists(): raise FileExistsError(target)
        source = self.root / "releases" / from_version if from_version else self.active_path()
        shutil.copytree(source, target)
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        manifest["status"] = "draft"; manifest.pop("content_hash", None)
        write_json(target / "manifest.json", manifest)
        return target

    def validate(self, path: str | Path) -> dict[str, Any]:
        pack = load_pack(path); validate_pack(pack, self.registry); return pack

    def publish(self, draft_name: str, version: str) -> Path:
        source = self.root / "drafts" / draft_name
        pack = self.validate(source)
        target = self.root / "releases" / version
        if target.exists(): raise FileExistsError(f"immutable release already exists: {target}")
        manifest = dict(pack["manifest"])
        manifest.update({"version": version, "status": "released"})
        hashes = {k: v for k, v in pack["_resource_hashes"].items() if k != "manifest.json"}
        manifest["content_hash"] = sha256_json(hashes)
        shutil.copytree(source, target)
        write_json(target / "manifest.json", manifest)
        self.validate(target)
        return target

    def activate(self, version: str) -> Path:
        target = self.root / "releases" / version
        self.validate(target)
        write_json(self.root / "active.json", {"version": version, "content_hash": load_pack(target)["manifest"].get("content_hash")})
        return target

    def rollback(self, version: str) -> Path:
        return self.activate(version)

    def active_path(self) -> Path:
        active = json.loads((self.root / "active.json").read_text(encoding="utf-8"))
        return self.root / "releases" / active["version"]

    def diff(self, left: str | Path, right: str | Path) -> list[dict[str, Any]]:
        a, b = load_pack(left), load_pack(right)
        for x in (a, b): x.pop("_path", None); x.pop("_resource_hashes", None)
        return deep_diff(a, b)
