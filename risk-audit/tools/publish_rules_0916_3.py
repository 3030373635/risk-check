"""按《风控矩阵0916-3》发布并激活 v1.9.1 规则包。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-audit/src"))

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.util import sha256_file, write_json


DEPARTMENT_KEYWORDS = """
各级单位、管理部门、实施部门、各部门、需求部门、用工部门、归口管理部门、会签部门、实物资产、资产使用、委托、
项目管理部门、项目建设部门、待定、项目承担部门、项目需求部门、各级单位-项目管理部门/建设单位、牵头部门、
牵头管理部门、实物资产管理部门、建设管理部门、使用保管部门、专业管理部门、用工管理部门、食堂经费管理部门、
职工教育经费管理部门、职工体检实施部门、资金发起部门、会议承办部门、接待承办部门、办公用品管理部门、公务车辆管理部门
""".replace("\n", "").split("、")

POSITION_KEYWORDS = """
某某、待定、人员、管理人员、实施人员、需求人员、用工人员、归口管理人员、会签人员、实物资产、资产使用、委托、
项目管理人员、项目建设人员、项目承担人员、项目需求人员、相关人员、采购人员、资产使用保管人员、保管人员、接待人员、
承接人员、出差人员、报账人员、部门负责人、需求审核人员、合同签订人员、合同经办人员、合同管理人员、合同审核人员、
合同结算人员、往来管理人员、使用保管人员、收货人员、需求提报人、负责人、监督人员、领料人员、审批人员、复核人员、
审核人员、柜收人员、签订人员、监控人员、装表人员、勘查人员、办理人员、核算人员、受理人员、收费人员、收取人员、
结算人员、财务人员、技经人员、规划人员、合规人员、计划人员、前期人员、招标人员、需求提报人员、物资上架人员、
履约人员、物资调拨人员、经办人员、匹配人员、项目负责人、出差报销审核人
""".replace("\n", "").split("、")

DRAFT_NAME = "rules-0916-3-1.9.1"
BASE_VERSION = "1.9.0"
TARGET_VERSION = "1.9.1"


def keyword_pattern(values: list[str]) -> str:
    """生成包含匹配正则；values 为规则文档列举的关键词。"""
    unique_values = list(dict.fromkeys(value for value in values if value))
    return "(?:" + "|".join(re.escape(value) for value in unique_values) + ")"


def update_specificity_rule(rule: dict, source_reference: str) -> None:
    """更新部门岗位具体性规则；rule 为第5或第6条配置，source_reference 为来源说明。"""
    rule["revision"] += 1
    rule["source_reference"] = source_reference
    for check in rule["checks"]:
        if check["operator"] != "field_constraints":
            continue
        field = check["params"]["field"]
        if field not in {"department", "position"}:
            continue
        check["operator_version"] = 5
        keywords = DEPARTMENT_KEYWORDS if field == "department" else POSITION_KEYWORDS
        existing_required_values = [value for value in check["params"]["placeholders"] if value in {"无", "-", "/"}]
        check["params"]["placeholders"] = list(dict.fromkeys([*keywords, *existing_required_values]))
        check["params"]["placeholder_patterns"] = [keyword_pattern(keywords)]
        check["params"]["exact_exceptions"] = ["查勘人员"] if field == "position" else []


def update_draft(draft: Path, source: Path) -> None:
    """同步新版规则草稿；draft 为规则目录，source 为0916-3源文档。"""
    source_reference = "风控矩阵0916-3.docx 有效正文（排除修订删除）"
    rules_dir = draft / "rules"
    rules = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in rules_dir.glob("*.json")}

    for rule in rules.values():
        rule["source_reference"] = source_reference
    for filename in ("R05.json", "R06b.json"):
        update_specificity_rule(rules[filename], source_reference)
    for filename, rule in rules.items():
        write_json(rules_dir / filename, rule)

    manifest = json.loads((draft / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(
        version="1.9.1",
        engine_compatibility=">=1.9.1,<2.0.0",
        audit_as_of="2026-09-16",
        title="风控矩阵0916-3更新规则",
        source_document_sha256=sha256_file(source),
    )
    write_json(draft / "manifest.json", manifest)


def build_release(store: RulePackStore, source: Path) -> tuple[Path, Path]:
    """生成并校验新版规则；store 为目标规则仓库，source 为0916-3源文档。"""
    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft, source)
    store.validate(draft)
    release = store.publish(draft.name, TARGET_VERSION)
    return draft, release


def tree_hashes(root: Path) -> dict[str, str]:
    """计算目录逐文件哈希；root 为需要比较的规则草稿或发布包。"""
    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_existing_release(store: RulePackStore, source: Path) -> Path:
    """在临时仓库重建并核对现有产物；store 为正式仓库，source 为规则源文档。"""
    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.1草稿与发布包必须同时存在，不能只保留其中一个")

    with tempfile.TemporaryDirectory(prefix="risk-audit-rules-") as temporary_directory:
        temporary_root = Path(temporary_directory) / "rulepacks"
        temporary_base = temporary_root / "releases" / BASE_VERSION
        temporary_base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(store.root / "releases" / BASE_VERSION, temporary_base)
        temporary_store = RulePackStore(temporary_root, build_registry())
        generated_draft, generated_release = build_release(temporary_store, source)

        # 发布包不可覆盖；只有临时重建结果逐文件一致时才认定可复现。
        comparisons = (
            ("draft", actual_draft, generated_draft),
            ("release", actual_release, generated_release),
        )
        for label, actual, generated in comparisons:
            if tree_hashes(actual) != tree_hashes(generated):
                raise RuntimeError(f"现有{label}与临时重建结果不一致")

    store.validate(actual_release)
    return actual_release


def main() -> None:
    """从1.9.0生成或复验1.9.1；--verify-only 控制是否跳过激活。"""
    parser = argparse.ArgumentParser(description="生成或复验1.9.1规则包")
    parser.add_argument("--verify-only", action="store_true", help="只验证发布产物，不修改当前激活版本")
    args = parser.parse_args()
    source = ROOT / "risk-audit/规则来源/风控矩阵0916-3.docx"
    if not source.exists():
        raise FileNotFoundError(source)
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
    print(json.dumps({"release": str(release), "activated": not args.verify_only, "verified": verified}, ensure_ascii=False))


if __name__ == "__main__":
    main()
