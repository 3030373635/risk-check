"""按《0928-1》生成、验证并激活 v1.9.20 规则包。"""

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


BASE_VERSION = "1.9.19"
DRAFT_NAME = "rules-0928-1-1.9.20"
TARGET_VERSION = "1.9.20"
SOURCE_NAME = "0928-1.docx"
SOURCE_REFERENCE = f"{SOURCE_NAME} 有效正文（接受新增、排除修订删除）"
PREPROCESSING_OPINION = (
    "【预处理】请补充系统类型，该列填报枚举值：一级部署系统、二级部署系统、"
    "三级部署系统，请根据系统的实际情况填报。"
)


def replace_rule_7_with_preprocessing_notice(rules_dir: Path) -> None:
    """删除第7条并新增系统类型预处理提示配置。

    Args:
        rules_dir: 新规则包的规则目录。
    """

    rule_7_path = rules_dir / "R07.json"
    if not rule_7_path.is_file():
        raise FileNotFoundError(f"待删除的第7条规则不存在: {rule_7_path}")
    rule_7 = json.loads(rule_7_path.read_text(encoding="utf-8"))
    if rule_7.get("display_code") != "R07":
        raise RuntimeError(f"第7条规则身份不符: {rule_7_path}")
    # 0928-1 已删除独立第7条，新发布包不得继续保留该规则入口。
    rule_7_path.unlink()

    notice_rule = {
        "checks": [{
            "check_id": "system_type_preprocessing_notice",
            "location_policy": "row",
            "message": {
                "review": PREPROCESSING_OPINION,
                "violation": PREPROCESSING_OPINION,
            },
            "on_unavailable": "review",
            "operator": "system_type_preprocessing_notice",
            "operator_version": 1,
            "params": {},
        }],
        "display_code": "P01",
        "enabled": True,
        "revision": 1,
        "rule_id": "preprocessing.system_type_notice",
        "schema_version": "1.0",
        "scope": {
            "business_codes": {"exclude": [], "include": ["*"]},
            "entity_codes": {"exclude": [], "include": ["*"]},
            "record_type": "system_rule",
        },
        "source_reference": SOURCE_REFERENCE,
        "title": "系统类型预处理未决提示",
    }
    write_json(rules_dir / "P01.json", notice_rule)


def update_draft(draft: Path, source: Path) -> None:
    """同步 0928-1 规则草稿。

    Args:
        draft: 新规则草稿目录。
        source: 已冻结的规则来源文档。
    """

    rules_dir = draft / "rules"
    replace_rule_7_with_preprocessing_notice(rules_dir)
    for rule_path in rules_dir.glob("*.json"):
        rule = json.loads(rule_path.read_text(encoding="utf-8"))
        rule["source_reference"] = SOURCE_REFERENCE
        write_json(rule_path, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        engine_compatibility=">=1.9.20,<2.0.0",
        audit_as_of="2026-09-28",
        title="0928-1第7条删除及预处理提示更新包",
        source_document_sha256=sha256_file(source),
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore, source: Path) -> tuple[Path, Path]:
    """生成并校验 1.9.20。

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
        raise RuntimeError("1.9.20 草稿与发布包必须同时存在")
    with tempfile.TemporaryDirectory(prefix="risk-audit-rules-0928-1-") as temporary_directory:
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
    """返回已冻结的 0928-1 来源文档。"""

    source = ROOT / "risk-audit/规则来源" / SOURCE_NAME
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def main() -> None:
    """生成或复验 1.9.20；--verify-only 不修改激活版本。"""

    parser = argparse.ArgumentParser(description="生成或复验 1.9.20 规则包")
    parser.add_argument("--verify-only", action="store_true", help="只验证发布产物，不修改激活版本")
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
