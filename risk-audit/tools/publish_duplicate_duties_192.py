"""发布并激活支持职责等价判重及重复位置展示的 v1.9.2 规则包。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-audit/src"))

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.util import write_json


BASE_VERSION = "1.9.1"
DRAFT_NAME = "duplicate-duties-1.9.2"
TARGET_VERSION = "1.9.2"


def update_draft(draft: Path) -> None:
    """更新第15条及版本信息；draft 为从1.9.1复制出的规则草稿目录。"""
    rule_path = draft / "rules/R15.json"
    rule = json.loads(rule_path.read_text(encoding="utf-8"))
    rule["revision"] += 1
    for check in rule["checks"]:
        if check["check_id"] != "duplicate_duties":
            continue
        # 动态意见由展示层根据重复证据列出文件、工作表和全部对应行号。
        check["message"] = {kind: "【第15条】{advice_v2}" for kind in ("violation", "review")}
    write_json(rule_path, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        engine_compatibility=">=1.9.2,<2.0.0",
        title="风控矩阵第15条职责等价判重规则",
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore) -> Path:
    """生成并校验新规则包；store 为目标规则仓库。"""
    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft)
    store.validate(draft)
    return store.publish(draft.name, TARGET_VERSION)


def main() -> None:
    """发布或校验1.9.2并设为默认规则包；无参数。"""
    store = RulePackStore(ROOT / "risk-audit/rulepacks", build_registry())
    release = store.root / "releases" / TARGET_VERSION
    if release.exists():
        store.validate(release)
    else:
        release = build_release(store)
    store.activate(TARGET_VERSION)
    print(json.dumps({"release": str(release), "activated": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
