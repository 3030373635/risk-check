"""按《0923-2》生成、验证并激活 v1.9.18 规则包。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "审核器/src"))

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.util import sha256_file, write_json


BASE_VERSION = "1.9.17"
DRAFT_NAME = "rules-0923-2-1.9.18"
TARGET_VERSION = "1.9.18"
SOURCE_NAME = "0923-2.docx"
SOURCE_REFERENCE = f"{SOURCE_NAME} 有效正文（接受新增、排除修订删除）"
SYSTEM_TYPE_OPINION = (
    "请补充系统类型，该列填报枚举值：一级部署系统、二级部署系统、"
    "三级部署系统，请根据系统的实际情况填报。"
)


def replace_system_type_rule(rules_dir: Path) -> None:
    """替换系统类型表头提示规则。

    Args:
        rules_dir: 新规则包的规则目录。
    """

    path = rules_dir / "R07.json"
    previous = json.loads(path.read_text(encoding="utf-8"))
    rule = {
        "checks": [
            {
                "check_id": "system_type_completion",
                "location_policy": "row",
                "message": {
                    "review": SYSTEM_TYPE_OPINION,
                    "violation": SYSTEM_TYPE_OPINION,
                },
                "on_unavailable": "review",
                "operator": "system_type_completion",
                "operator_version": 1,
                "params": {},
            }
        ],
        "display_code": "R07",
        "enabled": True,
        "revision": previous["revision"] + 1,
        "rule_id": "systems.type_completion",
        "schema_version": "1.0",
        "scope": {
            "business_codes": {"exclude": [], "include": ["*"]},
            "entity_codes": {"exclude": [], "include": ["*"]},
            "record_type": "system_rule",
        },
        "source_reference": SOURCE_REFERENCE,
        "title": "系统控制规则清单系统类型补全",
    }
    write_json(path, rule)


def update_draft(draft: Path, source: Path) -> None:
    """同步 0923-2 规则草稿。

    Args:
        draft: 新规则草稿目录。
        source: 已冻结的规则来源文档。
    """

    rules_dir = draft / "rules"
    replace_system_type_rule(rules_dir)
    for rule_path in rules_dir.glob("*.json"):
        rule = json.loads(rule_path.read_text(encoding="utf-8"))
        rule["source_reference"] = SOURCE_REFERENCE
        write_json(rule_path, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        engine_compatibility=">=1.9.18,<2.0.0",
        audit_as_of="2026-09-23",
        title="0923-2系统类型预处理更新包",
        source_document_sha256=sha256_file(source),
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore, source: Path) -> tuple[Path, Path]:
    """生成并校验 1.9.18。

    Args:
        store: 规则包仓库。
        source: 已冻结的规则来源文档。
    """

    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft, source)
    store.validate(draft)
    return draft, store.publish(draft.name, TARGET_VERSION)


def tree_hashes(root: Path) -> dict[str, str]:
    """计算目录逐文件哈希。

    Args:
        root: 草稿或发布目录。
    """

    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_existing_release(store: RulePackStore, source: Path) -> Path:
    """重建并核对现有发布产物。

    Args:
        store: 规则包仓库。
        source: 已冻结的规则来源文档。
    """

    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.18 草稿与发布包必须同时存在")
    with tempfile.TemporaryDirectory(prefix="risk-audit-rules-0923-2-") as temporary_directory:
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
    """取得已冻结规则来源。

    Args:
        verify_only: 为真时禁止从外部复制来源文档。
    """

    destination = ROOT / "审核器/规则来源" / SOURCE_NAME
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
    """生成或复验 1.9.18；--verify-only 不修改激活版本。"""

    parser = argparse.ArgumentParser(description="生成或复验 1.9.18 规则包")
    parser.add_argument("--verify-only", action="store_true", help="只验证发布产物，不修改激活版本")
    args = parser.parse_args()
    source = source_document(args.verify_only)
    store = RulePackStore(ROOT / "审核器/rulepacks", build_registry())
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
