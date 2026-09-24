"""发布并激活只执行岗位清单实际排序的 v1.9.3 规则包。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "审核器/src"))

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.util import write_json


BASE_VERSION = "1.9.2"
DRAFT_NAME = "remove-order-findings-1.9.3"
TARGET_VERSION = "1.9.3"
ORDER_RULE_FILES = ("R12.json", "R12b.json", "R12c.json")
DISABLED_REASON = "0916有效新增内容要求对岗位清单实际排序，已删除原第12条排序检查及对外意见"


def update_draft(draft: Path) -> None:
    """停用排序检查规则；draft 为从 1.9.2 复制出的规则草稿目录。"""
    for filename in ORDER_RULE_FILES:
        rule_path = draft / "rules" / filename
        rule = json.loads(rule_path.read_text(encoding="utf-8"))
        rule["revision"] += 1
        rule["enabled"] = False
        rule["disabled_reason"] = DISABLED_REASON
        write_json(rule_path, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        engine_compatibility=">=1.9.3,<2.0.0",
        title="风控矩阵岗位清单实际排序规则",
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore) -> Path:
    """生成并校验 1.9.3 规则包；store 为目标规则仓库。"""
    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft)
    store.validate(draft)
    return store.publish(draft.name, TARGET_VERSION)


def main() -> None:
    """发布或校验 1.9.3 并设为默认规则包；无参数。"""
    store = RulePackStore(ROOT / "审核器/rulepacks", build_registry())
    release = store.root / "releases" / TARGET_VERSION
    if release.exists():
        store.validate(release)
    else:
        release = build_release(store)
    store.activate(TARGET_VERSION)
    print(json.dumps({"release": str(release), "activated": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
