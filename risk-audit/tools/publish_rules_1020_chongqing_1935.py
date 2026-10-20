"""根据重庆 1020 审核确认稿发布第14条更新后的 1.9.35 规则包。"""

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


BASE_VERSION = "1.9.34"
TARGET_VERSION = "1.9.35"
DRAFT_NAME = "rules-1020-chongqing-1.9.35"
SOURCE_NAME = "风控矩阵1020-重庆.docx"
SOURCE_REFERENCE = f"{SOURCE_NAME} 接受修订后的有效正文"


def update_draft(draft: Path, source: Path) -> None:
    """更新第14条系统控制一致性规则及新版来源信息。

    Args:
        draft: 从重庆 1.9.34 复制出的规则草稿目录。
        source: 仓库内冻结的 1020 重庆规则来源文档。
    """

    rule_path = draft / "rules/R14.json"
    rule = json.loads(rule_path.read_text(encoding="utf-8"))
    rule["rule_id"] = "chongqing.system_control_consistency_1020"
    rule["revision"] = 2
    rule["checks"][0]["check_id"] = "system_control_consistency_1020"
    write_json(rule_path, rule)

    # 1020 文档是完整确认稿，全部规则统一记录本次冻结来源。
    for current_rule_path in (draft / "rules").glob("*.json"):
        current_rule = json.loads(current_rule_path.read_text(encoding="utf-8"))
        current_rule["source_reference"] = SOURCE_REFERENCE
        write_json(current_rule_path, current_rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        audit_as_of="2026-10-20",
        title="重庆风控矩阵及三清单 1020 审核确认稿规则包",
        source_document_sha256=sha256_file(source),
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore, source: Path) -> tuple[Path, Path]:
    """生成并校验重庆 1.9.35 草稿与发布包。

    Args:
        store: 规则包仓库。
        source: 已冻结的 1020 重庆规则来源文档。

    Returns:
        新生成的草稿目录和不可变发布目录。
    """

    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft, source)
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


def verify_existing_release(store: RulePackStore, source: Path) -> Path:
    """临时重建并比对现有重庆 1.9.35 发布产物。

    Args:
        store: 当前规则包仓库。
        source: 已冻结的 1020 重庆规则来源文档。

    Returns:
        已通过确定性比对的发布目录。
    """

    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.35 草稿与发布包必须同时存在")

    with tempfile.TemporaryDirectory(
        prefix="risk-audit-rules-1020-chongqing-1935-",
    ) as temporary_directory:
        temporary_root = Path(temporary_directory) / "rulepacks"
        temporary_base = temporary_root / "releases" / BASE_VERSION
        temporary_base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(store.root / "releases" / BASE_VERSION, temporary_base)
        temporary_store = RulePackStore(temporary_root, build_registry())
        generated_draft, generated_release = build_release(temporary_store, source)
        for label, actual, generated in (
            ("草稿", actual_draft, generated_draft),
            ("发布包", actual_release, generated_release),
        ):
            if tree_hashes(actual) != tree_hashes(generated):
                raise RuntimeError(f"现有{label}与临时重建结果不一致")

    store.validate(actual_release)
    return actual_release


def source_document() -> Path:
    """返回仓库内冻结的 1020 重庆规则来源文档。

    Returns:
        1020 重庆 Word 规则来源路径。
    """

    source = ROOT / "risk-audit/规则来源" / SOURCE_NAME
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def main() -> None:
    """生成或只读复验重庆 1.9.35 规则包。"""

    parser = argparse.ArgumentParser(description="生成或复验重庆 1.9.35 规则包")
    parser.add_argument("--verify-only", action="store_true", help="只验证已有发布产物")
    args = parser.parse_args()
    source = source_document()
    store = RulePackStore(ROOT / "risk-audit/rulepacks", build_registry())
    draft_exists = (store.root / "drafts" / DRAFT_NAME).exists()
    release_exists = (store.root / "releases" / TARGET_VERSION).exists()

    if args.verify_only and not (draft_exists and release_exists):
        raise RuntimeError("--verify-only 要求 1.9.35 草稿与发布包已存在")
    if draft_exists and release_exists:
        release = verify_existing_release(store, source)
        verified = True
    elif draft_exists:
        draft = store.root / "drafts" / DRAFT_NAME
        update_draft(draft, source)
        store.validate(draft)
        release = store.publish(DRAFT_NAME, TARGET_VERSION)
        verified = False
    elif release_exists:
        raise RuntimeError("1.9.35 发布包存在但草稿缺失，无法执行确定性复验")
    else:
        _, release = build_release(store, source)
        verified = False

    print(json.dumps({"release": str(release), "verified": verified}, ensure_ascii=False))


if __name__ == "__main__":
    main()
