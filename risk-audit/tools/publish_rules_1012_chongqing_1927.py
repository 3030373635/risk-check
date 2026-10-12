"""根据重庆审核确认稿的修订内容发布 v1.9.27 规则包。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-audit"))
sys.path.insert(0, str(ROOT / "risk-audit/src"))

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.util import sha256_file, write_json
from tools.publish_rules_1012_chongqing_1926 import tree_hashes


BASE_VERSION = "1.9.26"
TARGET_VERSION = "1.9.27"
DRAFT_NAME = "rules-1012-chongqing-1.9.27"
SOURCE_NAME = "风控矩阵及三清单审核规则 审核确认稿（重庆公司版）.docx"
SOURCE_REFERENCE = f"{SOURCE_NAME} 有效正文"


def update_schema_rule(draft: Path) -> None:
    """按新稿删除第 3 条中的适用性字段专项检查。

    Args:
        draft: 从重庆 1.9.26 复制出的规则草稿目录。
    """

    rules_directory = draft / "rules"
    schema_rule_path = rules_directory / "R03.json"
    schema_rule = json.loads(schema_rule_path.read_text(encoding="utf-8"))
    schema_params = schema_rule["checks"][0]["params"]

    # 新稿已删除适用性必填和末列位置要求，仅保留模板字段结构比较。
    schema_params.pop("accept_split_applicability", None)
    schema_params.pop("check_applicability_tail", None)
    schema_params["required_fields"] = [
        field_id
        for field_id in schema_params["required_fields"]
        if field_id != "applicability"
    ]
    schema_params["field_labels"].pop("applicability", None)
    schema_rule["revision"] = 11
    schema_rule["title"] = "基准字段及模板匹配"
    write_json(schema_rule_path, schema_rule)

    # R03b 专门检查空值、占位符和不适用原因，新稿已整体删除该要求。
    applicability_rule_path = rules_directory / "R03b.json"
    if applicability_rule_path.exists():
        applicability_rule_path.unlink()


def update_draft(draft: Path, source: Path) -> None:
    """将重庆审核确认稿的修订内容写入规则草稿。

    Args:
        draft: 从重庆 1.9.26 复制出的规则草稿目录。
        source: 已冻结的重庆审核确认稿 Word 文档。
    """

    update_schema_rule(draft)
    for rule_path in (draft / "rules").glob("*.json"):
        rule = json.loads(rule_path.read_text(encoding="utf-8"))
        rule["source_reference"] = SOURCE_REFERENCE
        write_json(rule_path, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        region_code="chongqing",
        region_name="重庆",
        engine_compatibility=">=1.9.27,<2.0.0",
        audit_as_of="2026-10-12",
        title="重庆适用性审核口径修订规则包",
        source_document_sha256=sha256_file(source),
    )
    write_json(manifest_path, manifest)


def build_release(
    store: RulePackStore,
    source: Path,
) -> tuple[Path, Path]:
    """生成并校验重庆 1.9.27 草稿与不可变发布包。

    Args:
        store: 规则包仓库。
        source: 已冻结的重庆审核确认稿 Word 文档。

    Returns:
        生成的草稿目录和发布目录。
    """

    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft, source)
    store.validate(draft)
    return draft, store.publish(draft.name, TARGET_VERSION)


def verify_existing_release(
    store: RulePackStore,
    source: Path,
) -> Path:
    """临时重建并比对现有重庆 1.9.27 发布产物。

    Args:
        store: 当前规则包仓库。
        source: 已冻结的重庆审核确认稿 Word 文档。

    Returns:
        已通过确定性比对的发布目录。
    """

    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.27 草稿与发布包必须同时存在")

    with tempfile.TemporaryDirectory(
        prefix="risk-audit-rules-1012-chongqing-1927-",
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
    """返回仓库内冻结的重庆审核确认稿 Word 文档。"""

    source = ROOT / "risk-audit/规则来源" / SOURCE_NAME
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def main() -> None:
    """生成或只读复验重庆 1.9.27 规则包。"""

    parser = argparse.ArgumentParser(description="生成或复验重庆 1.9.27 规则包")
    parser.add_argument("--verify-only", action="store_true", help="只验证已有发布产物")
    args = parser.parse_args()
    source = source_document()
    store = RulePackStore(ROOT / "risk-audit/rulepacks", build_registry())
    draft_exists = (store.root / "drafts" / DRAFT_NAME).exists()
    release_exists = (store.root / "releases" / TARGET_VERSION).exists()

    if args.verify_only and not (draft_exists and release_exists):
        raise RuntimeError("--verify-only 要求 1.9.27 草稿与发布包已存在")
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
        raise RuntimeError("1.9.27 发布包存在但草稿缺失，无法执行确定性复验")
    else:
        _, release = build_release(store, source)
        verified = False

    print(json.dumps({"release": str(release), "verified": verified}, ensure_ascii=False))


if __name__ == "__main__":
    main()
