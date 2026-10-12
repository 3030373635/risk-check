"""根据《1012-重庆 同步模板匹配规则版》发布 v1.9.22 规则包。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from docx import Document


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-audit/src"))

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.util import measure_id_key, sha256_file, write_json


BASE_VERSION = "1.9.20"
DRAFT_NAME = "rules-1012-chongqing-1.9.22"
TARGET_VERSION = "1.9.22"
SOURCE_NAME = "1012-重庆_同步模板匹配规则版.docx"
SOURCE_REFERENCE = f"{SOURCE_NAME} 有效正文"
SYSTEM_CONTROL_OPINION = "需核实系统规则清单中建设状态与矩阵中控制方式的一致性"
SYSTEM_NAME_OPINION = "需核实系统规则清单中系统名称与矩阵中控制系统的一致性"
MISSING_KEY_POINT_OPINION = "需核实适用的矩阵或关键控制点是否完整"
EXTRA_KEY_POINT_OPINION = "需核实该矩阵或关键控制点是否适用"
TEMPLATE_MATCH_OPINION = "【第3条】未匹配到对应模板，请确认模板对应关系。"


BUSINESS_MAPPING = {
    "营销售电": "sales_electricity",
    "交易与购电": "power_purchase_trading",
    "35千伏-750千伏电网基建": "grid_construction",
    "迁改工程管理": "relocation_project_management",
    "配网工程管理": "distribution_grid_project_management",
    "设备（资产）管理": "asset_management",
    "物资（服务）采购与实施": "procurement_implementation",
    "数字化与研发投入": "digitalization_rd_investment",
    "职工福利保障与薪酬管理": "employee_welfare_compensation",
    "员工报账": "employee_reimbursement",
    "财务管理": "financial_management",
    "合同管理": "contract_management",
    "特高压建设": "ultra_high_voltage_construction",
    "数科平台业务": "digital_platform_business",
    "物业后勤业务": "property_logistics",
    "检修运维成本项目全流程管理": "maintenance_cost_project_management",
    "违章罚款收入管理": "violation_fine_income_management",
}
EXPECTED_BUSINESS_NAMES = tuple(BUSINESS_MAPPING)


def extract_key_points(source: Path, business_mapping: dict[str, str]) -> dict[str, list[list[int]]]:
    """从来源文档中严格提取业务关键控制点编号。

    Args:
        source: 已冻结的重庆规则 Word 文档。
        business_mapping: 文档业务名到稳定业务标识的显式映射。
    """

    document = Document(source)
    if len(document.tables) != 1:
        raise RuntimeError(f"重庆规则文档必须且只能包含 1 张规则表，实际为 {len(document.tables)} 张")
    table = document.tables[0]
    if len(table.rows) < 3 or len(table.columns) != 2:
        raise RuntimeError("重庆规则表结构必须为两列且包含标题、表头和数据")

    points: dict[str, list[list[int]]] = defaultdict(list)
    seen: dict[str, set[int]] = defaultdict(set)
    unknown_businesses: set[str] = set()
    for row_number, row in enumerate(table.rows[2:], start=3):
        business_name = row.cells[0].text.strip()
        point_text = row.cells[1].text.strip()
        business_id = business_mapping.get(business_name)
        if business_id is None:
            unknown_businesses.add(business_name or f"第{row_number}行空业务")
            continue
        key = measure_id_key(point_text)
        if len(key) != 1 or not isinstance(key[0], int):
            raise RuntimeError(f"第{row_number}行关键控制点编号无法唯一解析：{point_text}")
        point = key[0]
        if point in seen[business_id]:
            raise RuntimeError(f"第{row_number}行关键控制点编号重复：{business_name} {point}")
        seen[business_id].add(point)
        points[business_id].append([point])

    if unknown_businesses:
        raise RuntimeError(f"来源文档包含未知业务：{'|'.join(sorted(unknown_businesses))}")
    present_names = {name for name, business_id in business_mapping.items() if business_id in points}
    missing_names = sorted(set(business_mapping) - present_names)
    if missing_names:
        raise RuntimeError(f"来源文档缺少业务：{'|'.join(missing_names)}")
    return {business_id: values for business_id, values in points.items()}


def business_aliases() -> dict[str, list[str]]:
    """返回重庆关键控制点规则的明确业务别名。"""

    aliases: dict[str, list[str]] = defaultdict(list)
    for business_name, business_id in BUSINESS_MAPPING.items():
        aliases[business_id].append(business_name)
    # 基线模板使用「物业后勤」，来源文档使用「物业后勤业务」。
    aliases["property_logistics"].append("物业后勤")
    return dict(aliases)


def _rule(
    *,
    display_code: str,
    rule_id: str,
    title: str,
    check_id: str,
    operator: str,
    params: dict[str, object],
    violation_message: str,
    review_message: str | None = None,
) -> dict[str, object]:
    """构造一条重庆报送包级规则。

    Args:
        display_code: 展示编号。
        rule_id: 稳定规则标识。
        title: 规则标题。
        check_id: 规则包内唯一检查标识。
        operator: 已注册审核能力名称。
        params: 冻结的检查参数。
        violation_message: 确定问题的审核意见。
        review_message: 前提不可用时的复核意见。
    """

    return {
        "checks": [{
            "check_id": check_id,
            "location_policy": "material",
            "message": {
                "review": review_message or violation_message,
                "violation": violation_message,
            },
            "on_unavailable": "review",
            "operator": operator,
            "operator_version": 1,
            "params": params,
        }],
        "display_code": display_code,
        "enabled": True,
        "revision": 1,
        "rule_id": rule_id,
        "schema_version": "1.0",
        "scope": {
            "business_codes": {"exclude": [], "include": ["*"]},
            "entity_codes": {"exclude": [], "include": ["*"]},
            "record_type": "package",
        },
        "source_reference": SOURCE_REFERENCE,
        "title": title,
    }


def remove_hunan_specific_resources(draft: Path) -> None:
    """从公共基线副本中移除只能用于湖南的冻结识别数据。

    Args:
        draft: 从 1.9.20 复制的重庆草稿目录。
    """

    write_json(draft / "entity_aliases.json", {})
    write_json(draft / "input_overrides.json", [])
    write_json(draft / "terminology.json", {
        "schema_version": "1.0",
        "terms": {"carrier": [], "department": [], "position": []},
        "metadata": {"purpose": "重庆未提供地区专用术语库"},
    })
    # R14 的适用范围和以下识别资源均由湖南样本冻结，不得进入重庆发布包。
    for relative_path in (
        "rules/R14.json",
        "parser_policy.json",
        "semantic_lexicon.json",
        "vocabulary_provenance.json",
        "vocabulary_sources.json",
    ):
        path = draft / relative_path
        if path.exists():
            path.unlink()


def update_draft(draft: Path, source: Path) -> None:
    """将重庆来源文档的已确认规则写入新草稿。

    Args:
        draft: 从 1.9.20 复制的规则草稿目录。
        source: 已冻结的重庆规则文档。
    """

    key_points = extract_key_points(source, BUSINESS_MAPPING)
    remove_hunan_specific_resources(draft)
    rules_dir = draft / "rules"
    rule_3_path = rules_dir / "R03.json"
    rule_3 = json.loads(rule_3_path.read_text(encoding="utf-8"))
    rule_3["checks"].append({
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
    rule_3["revision"] += 1
    write_json(rule_3_path, rule_3)
    system_rule = _rule(
        display_code="R16",
        rule_id="chongqing.system_control_consistency",
        title="系统控制清单与矩阵一致性",
        check_id="system_control_consistency",
        operator="system_control_consistency",
        params={},
        violation_message="{opinion}",
        review_message="{opinion}",
    )
    branch_rule = _rule(
        display_code="R17",
        rule_id="chongqing.power_supply_branch_key_points",
        title="供电分公司适用关键控制点审核",
        check_id="power_supply_branch_key_points",
        operator="power_supply_branch_key_points",
        params={"expected_key_points": key_points, "business_aliases": business_aliases()},
        violation_message="{opinion}",
        review_message="{opinion}",
    )
    write_json(rules_dir / "R16.json", system_rule)
    write_json(rules_dir / "R17.json", branch_rule)
    for rule_path in rules_dir.glob("*.json"):
        rule = json.loads(rule_path.read_text(encoding="utf-8"))
        rule["source_reference"] = SOURCE_REFERENCE
        write_json(rule_path, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        region_code="chongqing",
        region_name="重庆",
        engine_compatibility=">=1.9.22,<2.0.0",
        audit_as_of="2026-10-12",
        title="1012 重庆同步模板匹配专项规则包",
        source_document_sha256=sha256_file(source),
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore, source: Path) -> tuple[Path, Path]:
    """生成并校验 1.9.22 草稿与发布包。

    Args:
        store: 规则包仓库。
        source: 已冻结的重庆规则文档。
    """

    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft, source)
    store.validate(draft)
    return draft, store.publish(draft.name, TARGET_VERSION)


def tree_hashes(root: Path) -> dict[str, str]:
    """返回目录内每个文件的 SHA-256。

    Args:
        root: 草稿或发布包目录。
    """

    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_existing_release(store: RulePackStore, source: Path) -> Path:
    """在临时仓库重建并比对现有 1.9.22 发布产物。

    Args:
        store: 当前规则包仓库。
        source: 已冻结的重庆规则文档。
    """

    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.22 草稿与发布包必须同时存在")
    with tempfile.TemporaryDirectory(prefix="risk-audit-rules-1012-chongqing-") as temporary_directory:
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
    """返回已冻结的重庆规则来源文档。"""

    source = ROOT / "risk-audit/规则来源" / SOURCE_NAME
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def main() -> None:
    """生成或复验 1.9.22；--verify-only 不修改全局激活指针。"""

    parser = argparse.ArgumentParser(description="生成或复验 1.9.22 重庆规则包")
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
