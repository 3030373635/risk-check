"""按《1012 湖南公司版》生成、验证并激活 v1.9.21 规则包。"""

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


BASE_VERSION = "1.9.20"
DRAFT_NAME = "rules-1012-hunan-1.9.21"
TARGET_VERSION = "1.9.21"
SOURCE_NAME = "1012-湖南_模板匹配规则修订版.docx"
SOURCE_REFERENCE = f"{SOURCE_NAME} 湖南公司版有效正文"
TEMPLATE_MATCH_OPINION = "【第3条】未匹配到对应模板，请确认模板对应关系。"
FINANCIAL_OPINION = (
    "【第17条】29.项目可研经济性、财务合规性检查和45.现金收费解缴控制"
    "关键控制点的适用性请再次核实"
)


def update_rule_3(rules_dir: Path) -> None:
    """为第3条增加模板匹配失败提示。

    Args:
        rules_dir: 新规则包的规则目录。
    """

    rule_path = rules_dir / "R03.json"
    rule = json.loads(rule_path.read_text(encoding="utf-8"))
    rule["checks"].append({
        "check_id": "template_match_notice",
        "location_policy": "material",
        "message": {
            "review": TEMPLATE_MATCH_OPINION,
            "violation": TEMPLATE_MATCH_OPINION,
        },
        "on_unavailable": "review",
        "operator": "template_match_notice",
        "operator_version": 1,
        "params": {},
    })
    rule["revision"] += 1
    write_json(rule_path, rule)


def add_rule_17(rules_dir: Path) -> None:
    """新增第二批财务管理适用性复核规则。

    Args:
        rules_dir: 新规则包的规则目录。
    """

    rule = {
        "checks": [{
            "check_id": "financial_management_applicability",
            "location_policy": "row",
            "message": {
                "review": FINANCIAL_OPINION,
                "violation": FINANCIAL_OPINION,
            },
            "on_unavailable": "review",
            "operator": "financial_management_applicability",
            "operator_version": 1,
            "params": {
                "business_id": "financial_management",
                "measure_ids": [[45, 1], [45, 2], [45, 3], [45, 4], [45, 5], [29, 1]],
            },
        }],
        "display_code": "R17",
        "enabled": True,
        "revision": 1,
        "rule_id": "financial_management.key_measure_applicability",
        "schema_version": "1.0",
        "scope": {
            "business_codes": {"exclude": [], "include": ["*"]},
            "entity_codes": {"exclude": [], "include": ["*"]},
            "record_type": "matrix",
        },
        "source_reference": SOURCE_REFERENCE,
        "title": "财务管理关键控制点适用性复核",
    }
    write_json(rules_dir / "R17.json", rule)


def update_draft(draft: Path, source: Path) -> None:
    """同步 1012 湖南公司版规则草稿。

    Args:
        draft: 新规则草稿目录。
        source: 已冻结的规则来源文档。
    """

    rules_dir = draft / "rules"
    update_rule_3(rules_dir)
    add_rule_17(rules_dir)
    for rule_path in rules_dir.glob("*.json"):
        rule = json.loads(rule_path.read_text(encoding="utf-8"))
        rule["source_reference"] = SOURCE_REFERENCE
        write_json(rule_path, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        engine_compatibility=">=1.9.21,<2.0.0",
        audit_as_of="2026-10-12",
        title="1012 湖南公司版模板匹配及财务管理专项规则包",
        source_document_sha256=sha256_file(source),
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore, source: Path) -> tuple[Path, Path]:
    """生成并校验 1.9.21。

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
        raise RuntimeError("1.9.21 草稿与发布包必须同时存在")
    with tempfile.TemporaryDirectory(prefix="risk-audit-rules-1012-hunan-") as temporary_directory:
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
    """返回已冻结的 1012 湖南公司版来源文档。"""

    source = ROOT / "risk-audit/规则来源" / SOURCE_NAME
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def main() -> None:
    """生成或复验 1.9.21；--verify-only 不修改激活版本。"""

    parser = argparse.ArgumentParser(description="生成或复验 1.9.21 规则包")
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
