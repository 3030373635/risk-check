"""按《0917-4》生成、验证并激活 v1.9.9 规则包。"""

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


BASE_VERSION = "1.9.8"
DRAFT_NAME = "rules-0917-4-1.9.9"
TARGET_VERSION = "1.9.9"
SOURCE_NAME = "0917-4.docx"
SOURCE_REFERENCE = f"{SOURCE_NAME} 有效正文（接受新增、排除修订删除）"


def update_rule_10(rules_dir: Path) -> None:
    """加入规范性责任属性；rules_dir 为草稿中的规则配置目录。"""
    rule_path = rules_dir / "R10.json"
    rule = json.loads(rule_path.read_text(encoding="utf-8"))
    check = next(
        item for item in rule["checks"]
        if item["check_id"] == "broad_responsibility_pattern"
    )
    attributes = check["params"]["attributes"]
    # 规范性是0917-4新增的有效责任属性，发布脚本保持幂等以支持重建验证。
    if "规范性" not in attributes:
        attributes.append("规范性")
    rule["revision"] += 1
    write_json(rule_path, rule)


def update_draft(draft: Path, source: Path) -> None:
    """同步0917-4规则草稿；draft 为草稿目录，source 为冻结来源文档。"""
    rules_dir = draft / "rules"
    update_rule_10(rules_dir)
    for rule_path in rules_dir.glob("*.json"):
        rule = json.loads(rule_path.read_text(encoding="utf-8"))
        rule["source_reference"] = SOURCE_REFERENCE
        write_json(rule_path, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        engine_compatibility=">=1.9.9,<2.0.0",
        audit_as_of="2026-09-17",
        title="0917第10条规范性责任属性规则",
        source_document_sha256=sha256_file(source),
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore, source: Path) -> tuple[Path, Path]:
    """生成并校验1.9.9；store 为规则仓库，source 为0917-4来源文档。"""
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
    """重建并核对现有产物；store 为规则仓库，source 为0917-4来源文档。"""
    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.9草稿与发布包必须同时存在")
    with tempfile.TemporaryDirectory(prefix="risk-audit-rules-0917-4-") as temporary_directory:
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


def source_document(verify_only: bool) -> Path:
    """取得并冻结规则来源；verify_only 控制只读验证时不得复制外部文件。"""
    destination = ROOT / "risk-audit/规则来源" / SOURCE_NAME
    if destination.exists():
        return destination
    if verify_only:
        raise FileNotFoundError(destination)
    external = ROOT.parent / SOURCE_NAME
    if not external.exists():
        raise FileNotFoundError(external)
    # 首次发布冻结用户提供的最终文档，后续复验只使用仓库内副本。
    shutil.copy2(external, destination)
    return destination


def main() -> None:
    """生成或复验1.9.9；--verify-only 只验证产物且不修改当前激活版本。"""
    parser = argparse.ArgumentParser(description="生成或复验1.9.9规则包")
    parser.add_argument("--verify-only", action="store_true", help="只验证发布产物，不修改当前激活版本")
    args = parser.parse_args()
    source = source_document(args.verify_only)
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
