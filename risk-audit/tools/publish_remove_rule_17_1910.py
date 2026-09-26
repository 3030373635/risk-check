"""删除第17条规则，生成、验证并激活 v1.9.10 规则包。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-audit/src"))

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.util import sha256_file, write_json


BASE_VERSION = "1.9.9"
DRAFT_NAME = "remove-rule-17-1.9.10"
TARGET_VERSION = "1.9.10"
SOURCE_NAME = "0917-4.docx"
REMOVED_RULE_ID = "matrices.responsibility_department"
REMOVED_RULE_FILE = "R15c.json"


def remove_rule_17(rules_dir: Path) -> None:
    """删除第17条配置；rules_dir 为新规则包草稿的规则目录。"""
    rule_path = rules_dir / REMOVED_RULE_FILE
    if not rule_path.is_file():
        raise FileNotFoundError(f"待删除的第17条配置不存在: {rule_path}")
    rule = json.loads(rule_path.read_text(encoding="utf-8"))
    if rule.get("rule_id") != REMOVED_RULE_ID or rule.get("display_code") != "R17":
        raise RuntimeError(f"第17条配置身份不符: {rule_path}")
    # 来源文档已删除整条规则，新发布包不再保留可执行配置。
    rule_path.unlink()


def update_draft(draft: Path, source: Path) -> None:
    """同步第17条删除草稿；draft 为草稿目录，source 为冻结来源文档。"""
    remove_rule_17(draft / "rules")
    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        engine_compatibility=">=1.9.10,<2.0.0",
        audit_as_of="2026-09-17",
        title="0917第17条删除规则包",
        source_document_sha256=sha256_file(source),
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore, source: Path) -> tuple[Path, Path]:
    """生成并校验1.9.10；store 为规则仓库，source 为来源文档。"""
    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft, source)
    store.validate(draft)
    return draft, store.publish(draft.name, TARGET_VERSION)


def tree_hashes(root: Path) -> dict[str, str]:
    """计算目录逐文件哈希；root 为规则草稿或发布目录。"""
    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_existing_release(store: RulePackStore, source: Path) -> Path:
    """重建并核对现有产物；store 为规则仓库，source 为来源文档。"""
    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.10草稿与发布包必须同时存在")
    with tempfile.TemporaryDirectory(prefix="risk-audit-remove-rule-17-") as temporary_directory:
        temporary_root = Path(temporary_directory) / "rulepacks"
        temporary_base = temporary_root / "releases" / BASE_VERSION
        temporary_base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(store.root / "releases" / BASE_VERSION, temporary_base)
        temporary_store = RulePackStore(temporary_root, build_registry())
        generated_draft, generated_release = build_release(temporary_store, source)
        for label, actual, generated in (
            ("draft", actual_draft, generated_draft),
            ("release", actual_release, generated_release),
        ):
            if tree_hashes(actual) != tree_hashes(generated):
                raise RuntimeError(f"现有{label}与临时重建结果不一致")
    store.validate(actual_release)
    return actual_release


def source_document() -> Path:
    """返回已冻结的0917-4来源文档；无参数。"""
    source = ROOT / "risk-audit/规则来源" / SOURCE_NAME
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def main() -> None:
    """生成或复验1.9.10；--verify-only 只验证产物且不修改激活版本。"""
    parser = argparse.ArgumentParser(description="生成或复验1.9.10规则包")
    parser.add_argument("--verify-only", action="store_true", help="只验证发布产物，不修改当前激活版本")
    args = parser.parse_args()
    source = source_document()
    store = RulePackStore(ROOT / "risk-audit/rulepacks", build_registry())
    draft_exists = (store.root / "drafts" / DRAFT_NAME).exists()
    release_exists = (store.root / "releases" / TARGET_VERSION).exists()
    if draft_exists or release_exists:
        release = verify_existing_release(store, source)
        verified = True
    else:
        _, release = build_release(store, source)
        verified = False
    if not args.verify_only:
        store.activate(TARGET_VERSION)
    print(json.dumps({
        "release": str(release),
        "activated": not args.verify_only,
        "verified": verified,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
