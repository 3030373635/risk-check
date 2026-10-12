"""发布支持重庆独立三清单文件的 v1.9.23 规则包。"""

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


BASE_VERSION = "1.9.22"
TARGET_VERSION = "1.9.23"
DRAFT_NAME = "chongqing-split-three-lists-1.9.23"
MATERIAL_NAMING_KEYWORDS = {
    "matrix": ["风控矩阵", "矩阵", "取证"],
    "three_lists": ["三清单", "系统规则", "岗位内控", "不相容"],
}


def update_draft(draft: Path) -> None:
    """扩展重庆材料命名匹配并升级规则包元数据。

    Args:
        draft: 从重庆 1.9.22 复制的规则草稿目录。
    """

    rule_path = draft / "rules/R01.json"
    rule = json.loads(rule_path.read_text(encoding="utf-8"))
    naming_check = next(
        check
        for check in rule["checks"]
        if check["check_id"] == "file_naming"
    )
    naming_check["params"]["naming_keywords"] = MATERIAL_NAMING_KEYWORDS
    rule["revision"] += 1
    write_json(rule_path, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        engine_compatibility=">=1.9.23,<2.0.0",
        audit_as_of="2026-10-12",
        title="1012 重庆模板匹配及分拆三清单兼容规则包",
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore) -> tuple[Path, Path]:
    """生成并校验 1.9.23 草稿与发布包。

    Args:
        store: 规则包仓库。
    """

    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft)
    store.validate(draft)
    return draft, store.publish(draft.name, TARGET_VERSION)


def tree_hashes(root: Path) -> dict[str, str]:
    """返回目录内每个文件的 SHA-256。

    Args:
        root: 待计算的规则包目录。
    """

    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_existing_release(store: RulePackStore) -> Path:
    """重建并比对现有 1.9.23 发布产物。

    Args:
        store: 当前规则包仓库。
    """

    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.23 草稿与发布包必须同时存在")
    with tempfile.TemporaryDirectory(prefix="risk-audit-chongqing-split-lists-") as temporary_directory:
        temporary_root = Path(temporary_directory) / "rulepacks"
        temporary_base = temporary_root / "releases" / BASE_VERSION
        temporary_base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(store.root / "releases" / BASE_VERSION, temporary_base)
        temporary_store = RulePackStore(temporary_root, build_registry())
        generated_draft, generated_release = build_release(temporary_store)
        for label, actual, generated in (
            ("草稿", actual_draft, generated_draft),
            ("发布包", actual_release, generated_release),
        ):
            if tree_hashes(actual) != tree_hashes(generated):
                raise RuntimeError(f"现有{label}与临时重建结果不一致")
    store.validate(actual_release)
    return actual_release


def main() -> None:
    """创建或复验 1.9.23 重庆规则包；无参数。"""

    parser = argparse.ArgumentParser(description="生成或复验 1.9.23 重庆分拆三清单规则包")
    parser.add_argument("--verify-only", action="store_true", help="只复验已有发布产物")
    args = parser.parse_args()
    store = RulePackStore(ROOT / "risk-audit/rulepacks", build_registry())
    draft_exists = (store.root / "drafts" / DRAFT_NAME).exists()
    release_exists = (store.root / "releases" / TARGET_VERSION).exists()
    if args.verify_only and not (draft_exists and release_exists):
        raise RuntimeError("--verify-only 要求 1.9.23 草稿与发布包已存在")
    if draft_exists or release_exists:
        release = verify_existing_release(store)
        verified = True
    else:
        _, release = build_release(store)
        verified = False
    print(json.dumps({"release": str(release), "verified": verified}, ensure_ascii=False))


if __name__ == "__main__":
    main()
