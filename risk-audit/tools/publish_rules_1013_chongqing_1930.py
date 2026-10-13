"""根据新版《风控矩阵-1013-重庆》发布重庆 v1.9.30 规则包。"""

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


BASE_VERSION = "1.9.29"
TARGET_VERSION = "1.9.30"
DRAFT_NAME = "rules-1013-chongqing-1.9.30"
SOURCE_NAME = "风控矩阵-1013-重庆.docx"
SOURCE_REFERENCE = f"{SOURCE_NAME} 有效正文"


def person_name_single_rule() -> dict:
    """构造重庆第20条人员姓名规范性规则。

    Returns:
        仅允许单个人员姓名、不允许顿号和中英文逗号的规则配置。
    """

    message = "【第20条】每行岗位名称中只能有一个对应人员，若有多人，则应分多行填写。"
    return {
        "checks": [{
            "check_id": "person_name_single",
            "location_policy": "row",
            "message": {
                "review": message,
                "violation": message,
            },
            "on_unavailable": "skip",
            "operator": "text_pattern",
            "operator_version": 1,
            "params": {
                "field": "person_names",
                # 顿号、中文逗号和英文逗号均表示同一行填写了多个人员。
                "patterns": ["^[^、，,]+$"],
            },
        }],
        "display_code": "R20",
        "enabled": True,
        "revision": 1,
        "rule_id": "chongqing.person_name_single",
        "schema_version": "1.0",
        "scope": {
            "business_codes": {
                "exclude": [],
                "include": ["*"],
            },
            "entity_codes": {
                "exclude": [],
                "include": ["*"],
            },
            "record_type": "position_duty",
        },
        "source_reference": SOURCE_REFERENCE,
        "title": "岗位内控责任清单人员姓名规范性审核",
    }


def update_draft(draft: Path, source: Path) -> None:
    """把新版来源和第20条写入重庆规则草稿。

    Args:
        draft: 从重庆 1.9.29 复制出的规则草稿目录。
        source: 已冻结的新版重庆 Word 规则来源。
    """

    rules_directory = draft / "rules"
    write_json(rules_directory / "R20.json", person_name_single_rule())
    for rule_path in rules_directory.glob("*.json"):
        rule = json.loads(rule_path.read_text(encoding="utf-8"))
        rule["source_reference"] = SOURCE_REFERENCE
        write_json(rule_path, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        audit_as_of="2026-10-14",
        title="重庆岗位内控责任清单人员姓名规范性规则包",
        source_document_sha256=sha256_file(source),
    )
    write_json(manifest_path, manifest)


def build_release(
    store: RulePackStore,
    source: Path,
) -> tuple[Path, Path]:
    """生成并校验重庆 1.9.30 草稿与发布包。

    Args:
        store: 规则包仓库。
        source: 已冻结的新版重庆 Word 规则来源。

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


def verify_existing_release(
    store: RulePackStore,
    source: Path,
) -> Path:
    """临时重建并比对现有重庆 1.9.30 发布产物。

    Args:
        store: 当前规则包仓库。
        source: 已冻结的新版重庆 Word 规则来源。

    Returns:
        已通过确定性比对的发布目录。
    """

    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.30 草稿与发布包必须同时存在")

    with tempfile.TemporaryDirectory(
        prefix="risk-audit-rules-1013-chongqing-1930-",
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
    """返回仓库内冻结的新版重庆规则来源文档。

    Returns:
        新版 Word 规则来源路径。
    """

    source = ROOT / "risk-audit/规则来源" / SOURCE_NAME
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def main() -> None:
    """生成或只读复验重庆 1.9.30 规则包。"""

    parser = argparse.ArgumentParser(description="生成或复验重庆 1.9.30 规则包")
    parser.add_argument("--verify-only", action="store_true", help="只验证已有发布产物")
    args = parser.parse_args()
    source = source_document()
    store = RulePackStore(ROOT / "risk-audit/rulepacks", build_registry())
    draft_exists = (store.root / "drafts" / DRAFT_NAME).exists()
    release_exists = (store.root / "releases" / TARGET_VERSION).exists()

    if args.verify_only and not (draft_exists and release_exists):
        raise RuntimeError("--verify-only 要求 1.9.30 草稿与发布包已存在")
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
        raise RuntimeError("1.9.30 发布包存在但草稿缺失，无法执行确定性复验")
    else:
        _, release = build_release(store, source)
        verified = False

    print(json.dumps({"release": str(release), "verified": verified}, ensure_ascii=False))


if __name__ == "__main__":
    main()
