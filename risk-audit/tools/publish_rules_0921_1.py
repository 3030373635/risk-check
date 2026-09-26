"""按《0921-1》生成、验证并激活 v1.9.17 规则包。"""

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


BASE_VERSION = "1.9.16"
DRAFT_NAME = "rules-0921-1-1.9.17"
TARGET_VERSION = "1.9.17"
SOURCE_NAME = "0921-1.docx"
SOURCE_REFERENCE = f"{SOURCE_NAME} 有效正文（接受新增、排除修订删除）"
APPLICABILITY_OPINION = "【第16条】请结合业务实际再次核实适用性。"


def add_applicability_rule(rules_dir: Path) -> None:
    """更新矩阵适用性联动规则；rules_dir 为新规则包的规则目录。"""
    rule = {
        "checks": [
            {
                "check_id": "measure_applicability_alignment",
                "location_policy": "row",
                "message": {
                    "review": APPLICABILITY_OPINION,
                    "violation": APPLICABILITY_OPINION,
                },
                "on_unavailable": "review",
                "operator": "measure_applicability_alignment",
                "operator_version": 3,
                "params": {},
            }
        ],
        "display_code": "R16",
        "enabled": True,
        "revision": 5,
        "rule_id": "matrices.measure_applicability",
        "schema_version": "1.0",
        "scope": {
            "business_codes": {"exclude": [], "include": ["*"]},
            "entity_codes": {"exclude": [], "include": ["*"]},
            "record_type": "matrix",
        },
        "source_reference": SOURCE_REFERENCE,
        "title": "岗位清单已有措施与矩阵适用性联动",
    }
    write_json(rules_dir / "R15b.json", rule)


def update_draft(draft: Path, source: Path) -> None:
    """同步0921-1规则草稿；draft 为草稿目录，source 为冻结来源文档。"""
    rules_dir = draft / "rules"
    add_applicability_rule(rules_dir)
    for rule_path in rules_dir.glob("*.json"):
        rule = json.loads(rule_path.read_text(encoding="utf-8"))
        rule["source_reference"] = SOURCE_REFERENCE
        write_json(rule_path, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        engine_compatibility=">=1.9.17,<2.0.0",
        audit_as_of="2026-09-21",
        title="0921-1会计主体隔离与适用性更新包",
        source_document_sha256=sha256_file(source),
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore, source: Path) -> tuple[Path, Path]:
    """生成并校验1.9.17；store 为规则仓库，source 为冻结来源文档。"""
    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft, source)
    store.validate(draft)
    return draft, store.publish(draft.name, TARGET_VERSION)


def tree_hashes(root: Path) -> dict[str, str]:
    """计算目录逐文件哈希；root 为草稿或发布目录。"""
    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_existing_release(store: RulePackStore, source: Path) -> Path:
    """重建并核对现有产物；store 为规则仓库，source 为冻结来源文档。"""
    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.17草稿与发布包必须同时存在")
    with tempfile.TemporaryDirectory(prefix="risk-audit-rules-0921-1-") as temporary_directory:
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
    """取得并冻结规则来源；verify_only 为真时不得从外部复制文档。"""
    destination = ROOT / "risk-audit/规则来源" / SOURCE_NAME
    if destination.exists():
        return destination
    if verify_only:
        raise FileNotFoundError(destination)
    external = ROOT.parent / SOURCE_NAME
    if not external.exists():
        raise FileNotFoundError(external)
    shutil.copy2(external, destination)
    return destination


def main() -> None:
    """生成或复验1.9.17；--verify-only 只校验产物，不修改激活版本。"""
    parser = argparse.ArgumentParser(description="生成或复验1.9.17规则包")
    parser.add_argument("--verify-only", action="store_true", help="只验证发布产物，不修改激活版本")
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
