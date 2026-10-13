"""发布重庆第18、19条展示编号修复规则包。"""

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


BASE_VERSION = "1.9.28"
TARGET_VERSION = "1.9.29"
DRAFT_NAME = "chongqing-rule-numbers-1.9.29"
RULE_NUMBER_UPDATES = {
    "R19.json": ("R18.json", "R18", "第18条"),
    "R20.json": ("R19.json", "R19", "第19条"),
}


def update_draft(draft: Path) -> None:
    """在删除旧第16条后重排重庆后续两条规则。

    Args:
        draft: 从重庆 1.9.28 复制出的规则草稿目录。
    """

    rules_directory = draft / "rules"
    updated_rules: list[tuple[str, dict]] = []
    for source_name, (target_name, display_code, clause_number) in RULE_NUMBER_UPDATES.items():
        source_path = rules_directory / source_name
        rule = json.loads(source_path.read_text(encoding="utf-8"))
        rule["display_code"] = display_code
        rule["revision"] += 1
        for check in rule["checks"]:
            # 审查和违规意见必须使用同一个重排后编号。
            check["message"] = {
                "review": f"【{clause_number}】{{opinion}}",
                "violation": f"【{clause_number}】{{opinion}}",
            }
        updated_rules.append((target_name, rule))

    # 先删除两个旧文件，避免 R19 在重排过程中发生命名冲突。
    for source_name in RULE_NUMBER_UPDATES:
        (rules_directory / source_name).unlink()
    for target_name, rule in updated_rules:
        write_json(rules_directory / target_name, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        audit_as_of="2026-10-13",
        title="重庆第18、19条审核意见编号修复规则包",
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore) -> tuple[Path, Path]:
    """生成并校验重庆 1.9.29 草稿与发布包。

    Args:
        store: 规则包仓库。

    Returns:
        新生成的草稿目录和不可变发布目录。
    """

    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft)
    store.validate(draft)
    return draft, store.publish(draft.name, TARGET_VERSION)


def tree_hashes(root: Path) -> dict[str, str]:
    """返回目录内全部文件的 SHA-256 映射。

    Args:
        root: 待计算哈希的规则草稿或发布目录。

    Returns:
        文件相对路径到 SHA-256 的映射。
    """

    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_existing_release(store: RulePackStore) -> Path:
    """临时重建并比对现有重庆 1.9.29 发布产物。

    Args:
        store: 当前规则包仓库。

    Returns:
        已通过确定性比对的发布目录。
    """

    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.29 草稿与发布包必须同时存在")

    with tempfile.TemporaryDirectory(
        prefix="risk-audit-chongqing-rule-numbers-1929-",
    ) as temporary_directory:
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
    """创建或只读复验重庆 1.9.29 规则包。"""

    parser = argparse.ArgumentParser(
        description="生成或复验重庆 1.9.29 审核意见编号修复规则包",
    )
    parser.add_argument("--verify-only", action="store_true", help="只验证已有发布产物")
    args = parser.parse_args()
    store = RulePackStore(ROOT / "risk-audit/rulepacks", build_registry())
    draft_exists = (store.root / "drafts" / DRAFT_NAME).exists()
    release_exists = (store.root / "releases" / TARGET_VERSION).exists()

    if args.verify_only and not (draft_exists and release_exists):
        raise RuntimeError("--verify-only 要求 1.9.29 草稿与发布包已存在")
    if draft_exists and release_exists:
        release = verify_existing_release(store)
        verified = True
    elif draft_exists:
        draft = store.root / "drafts" / DRAFT_NAME
        update_draft(draft)
        store.validate(draft)
        release = store.publish(DRAFT_NAME, TARGET_VERSION)
        verified = False
    elif release_exists:
        raise RuntimeError("1.9.29 发布包存在但草稿缺失，无法执行确定性复验")
    else:
        _, release = build_release(store)
        verified = False

    print(json.dumps({"release": str(release), "verified": verified}, ensure_ascii=False))


if __name__ == "__main__":
    main()
