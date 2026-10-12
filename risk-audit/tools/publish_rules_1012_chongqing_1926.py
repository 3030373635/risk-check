"""根据《风控矩阵1012-重庆》发布重庆 v1.9.26 规则包。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-audit"))
sys.path.insert(0, str(ROOT / "risk-audit/src"))

from risk_audit.business_identity import validate_business_registry
from risk_audit.checks.registry import build_registry
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.util import sha256_file, write_json
from tools.publish_rules_1012_chongqing import BUSINESS_MAPPING, extract_key_points


BASE_VERSION = "1.9.25"
TARGET_VERSION = "1.9.26"
DRAFT_NAME = "rules-1012-chongqing-1.9.26"
SOURCE_NAME = "风控矩阵1012-重庆.docx"
SOURCE_REFERENCE = f"{SOURCE_NAME} 有效正文"
TEMPLATE_ROOT = ROOT / "risk-audit/baselines/chongqing/templates"

# 每项依次为批次目录、业务编号、目录业务名、稳定业务标识和补充别名。
BUSINESS_DEFINITIONS = (
    ("1.第一批重庆公司风控矩阵", "01", "营销售电", "sales_electricity", ["营销与售电"]),
    ("1.第一批重庆公司风控矩阵", "02", "交易与购电", "power_purchase_trading", ["交易购电", "营销购电"]),
    ("1.第一批重庆公司风控矩阵", "03", "35千伏-750千伏电网基建", "grid_construction", ["35Kv-750Kv电网基建项目", "电网基建"]),
    ("1.第一批重庆公司风控矩阵", "04", "迁改工程管理", "relocation_project_management", ["迁改工程"]),
    ("1.第一批重庆公司风控矩阵", "05", "配网工程管理", "distribution_grid_project_management", ["配网工程"]),
    ("1.第一批重庆公司风控矩阵", "06", "设备（资产）管理", "asset_management", ["设备资产管理", "设备管理"]),
    ("1.第一批重庆公司风控矩阵", "07", "物资（服务）采购与实施", "procurement_implementation", ["物资服务采购与实施", "物资采购与实施"]),
    ("1.第一批重庆公司风控矩阵", "08", "数字化与研发投入", "digitalization_rd_investment", ["数字化研发投入", "数字化与研发", "科研投入"]),
    ("1.第一批重庆公司风控矩阵", "09", "职工福利保障与薪酬管理", "employee_welfare_compensation", ["职工福利与薪酬管理"]),
    ("1.第一批重庆公司风控矩阵", "10", "员工报账", "employee_reimbursement", ["员工报销"]),
    ("2.第二批重庆公司风控矩阵", "01", "股权（产权）管理", "equity_management", ["股权产权管理", "股权管理", "产权管理"]),
    ("2.第二批重庆公司风控矩阵", "02", "财务管理", "financial_management", []),
    ("2.第二批重庆公司风控矩阵", "03", "合同管理", "contract_management", []),
    ("2.第二批重庆公司风控矩阵", "04", "特高压建设", "ultra_high_voltage_construction", ["特高压管理"]),
    ("2.第二批重庆公司风控矩阵", "05", "抽蓄（水电）项目建设与运营", "pumped_storage_hydropower", ["抽蓄水电项目建设与运营"]),
    ("2.第二批重庆公司风控矩阵", "21", "软件开发与实施服务", "software_development_implementation", ["软件开发实施服务"]),
    ("2.第二批重庆公司风控矩阵", "23", "合同能源管理项目", "contract_energy_management", ["合同能源管理"]),
    ("2.第二批重庆公司风控矩阵", "24", "数科平台业务", "digital_platform_business", ["数字科技平台业务"]),
    ("2.第二批重庆公司风控矩阵", "25", "充电设施建设及运营管理", "charging_facility_operation", ["充电设施建设运营管理"]),
    ("2.第二批重庆公司风控矩阵", "26", "监理业务", "supervision_business", ["工程监理"]),
    ("2.第二批重庆公司风控矩阵", "27", "招标代理", "tendering_agency", ["招标代理业务"]),
    ("2.第二批重庆公司风控矩阵", "28", "教育培训", "education_training", []),
    ("2.第二批重庆公司风控矩阵", "29", "施工建设", "construction", []),
    ("2.第二批重庆公司风控矩阵", "30", "物业后勤业务", "property_logistics", ["物业后勤"]),
    ("2.第二批重庆公司风控矩阵", "31", "供电服务", "power_supply_service", []),
    ("2.第二批重庆公司风控矩阵", "32", "售电业务", "electricity_sales_business", []),
    ("2.第二批重庆公司风控矩阵", "33", "检修运维成本项目全流程管理", "maintenance_cost_project_management", ["检修运维成本项目管理"]),
    ("2.第二批重庆公司风控矩阵", "34", "违章罚款收入管理", "violation_fine_income_management", ["违章罚款收入"]),
    ("2.第二批重庆公司风控矩阵", "37", "设计业务", "design_business", ["工程设计"]),
)


def _unique(values: list[str]) -> list[str]:
    """按原顺序去重字符串。

    Args:
        values: 需要去重的字符串列表。
    """

    return list(dict.fromkeys(value for value in values if value))


def build_baseline_registry(template_root: Path) -> dict[str, Any]:
    """从重庆模板目录构造带冻结哈希的业务注册表。

    Args:
        template_root: 重庆模板根目录，注册表路径均相对此目录保存。
    """

    businesses: list[dict[str, Any]] = []
    registered_paths: set[str] = set()
    for batch_name, code, name, business_id, extra_aliases in BUSINESS_DEFINITIONS:
        business_directory = template_root / batch_name / f"{code} {name}"
        candidates = sorted(
            path for path in business_directory.iterdir()
            if path.is_file() and path.suffix.lower() in {".xls", ".xlsx"}
        )
        if len(candidates) != 1:
            raise RuntimeError(f"重庆业务目录必须且只能包含一个模板：{business_directory}")
        template = candidates[0]
        relative_path = template.relative_to(template_root).as_posix()
        registered_paths.add(relative_path)
        businesses.append({
            "business_id": business_id,
            "business_code": code,
            "business_name": name,
            "aliases": _unique([name, *extra_aliases]),
            "variants": [{
                "variant_id": "default",
                "aliases": [],
                "template": {
                    "path": relative_path,
                    "sha256": sha256_file(template),
                },
            }],
        })

    # 未登记模板会使规则 20 悄然漏检，因此发布前必须做全集比对。
    actual_paths = {
        path.relative_to(template_root).as_posix()
        for path in template_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".xls", ".xlsx"}
    }
    if registered_paths != actual_paths:
        missing = sorted(actual_paths - registered_paths)
        extra = sorted(registered_paths - actual_paths)
        raise RuntimeError(f"重庆模板登记不完整，未登记={missing}，多登记={extra}")

    registry = {"schema_version": "2.0", "businesses": businesses}
    errors = validate_business_registry(registry)
    if errors:
        raise RuntimeError("重庆模板业务配置无效：" + "；".join(errors))
    return registry


def business_aliases(registry: dict[str, Any]) -> dict[str, list[str]]:
    """生成专项规则共用的业务别名映射。

    Args:
        registry: 重庆基准模板注册表。
    """

    return {
        business["business_id"]: business["aliases"]
        for business in registry["businesses"]
        if business["business_id"] in set(BUSINESS_MAPPING.values())
    }


def build_rule(
    *,
    display_code: str,
    rule_id: str,
    title: str,
    check_id: str,
    operator: str,
) -> dict[str, Any]:
    """构造一条重庆包级专项规则。

    Args:
        display_code: 展示规则编号。
        rule_id: 稳定规则标识。
        title: 规则标题。
        check_id: 规则包内检查标识。
        operator: 已注册的审核能力名称。
    """

    message = f"【第{display_code.removeprefix('R')}条】{{opinion}}"
    return {
        "checks": [{
            "check_id": check_id,
            "location_policy": "row",
            "message": {"review": message, "violation": message},
            "on_unavailable": "review",
            "operator": operator,
            "operator_version": 1,
            "params": {},
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


def update_draft(draft: Path, source: Path, template_root: Path) -> None:
    """将重庆新规则和实际模板注册表写入草稿。

    Args:
        draft: 从 1.9.25 复制的规则草稿目录。
        source: 已冻结的新重庆规则 Word 文档。
        template_root: 重庆模板根目录。
    """

    registry = build_baseline_registry(template_root)
    key_points = extract_key_points(source, BUSINESS_MAPPING)
    write_json(draft / "baseline_registry.json", registry)

    rules_directory = draft / "rules"
    branch_rule_path = rules_directory / "R17.json"
    branch_rule = json.loads(branch_rule_path.read_text(encoding="utf-8"))
    branch_rule["checks"][0]["params"] = {
        "expected_key_points": key_points,
        "business_aliases": business_aliases(registry),
    }
    # 1.9.25 中该规则修订号为 2；重复执行草稿恢复时仍固定为本次修订号。
    branch_rule["revision"] = 3
    write_json(branch_rule_path, branch_rule)

    write_json(rules_directory / "R19.json", build_rule(
        display_code="R19",
        rule_id="chongqing.responsibility_alignment",
        title="责任主体与岗位内控责任清单一致性审核",
        check_id="responsibility_alignment",
        operator="chongqing_responsibility_alignment",
    ))
    write_json(rules_directory / "R20.json", build_rule(
        display_code="R20",
        rule_id="chongqing.control_measure_completeness",
        title="控制措施完整性审核",
        check_id="control_measure_completeness",
        operator="chongqing_control_measure_completeness",
    ))

    for rule_path in rules_directory.glob("*.json"):
        rule = json.loads(rule_path.read_text(encoding="utf-8"))
        rule["source_reference"] = SOURCE_REFERENCE
        write_json(rule_path, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        region_code="chongqing",
        region_name="重庆",
        engine_compatibility=">=1.9.26,<2.0.0",
        audit_as_of="2026-10-12",
        title="重庆责任主体一致性与控制措施完整性规则包",
        source_document_sha256=sha256_file(source),
    )
    write_json(manifest_path, manifest)


def build_release(
    store: RulePackStore,
    source: Path,
    template_root: Path,
) -> tuple[Path, Path]:
    """生成并校验 1.9.26 草稿与发布包。

    Args:
        store: 规则包仓库。
        source: 已冻结的新重庆规则 Word 文档。
        template_root: 重庆模板根目录。
    """

    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft, source, template_root)
    store.validate(draft)
    return draft, store.publish(draft.name, TARGET_VERSION)


def tree_hashes(root: Path) -> dict[str, str]:
    """计算目录内所有文件哈希。

    Args:
        root: 待计算哈希的目录。
    """

    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_existing_release(
    store: RulePackStore,
    source: Path,
    template_root: Path,
) -> Path:
    """临时重建并比对现有 1.9.26 发布产物。

    Args:
        store: 当前规则包仓库。
        source: 已冻结的新重庆规则 Word 文档。
        template_root: 重庆模板根目录。
    """

    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.26 草稿与发布包必须同时存在")
    with tempfile.TemporaryDirectory(
        prefix="risk-audit-rules-1012-chongqing-1926-",
    ) as temporary_directory:
        temporary_root = Path(temporary_directory) / "rulepacks"
        temporary_base = temporary_root / "releases" / BASE_VERSION
        temporary_base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(store.root / "releases" / BASE_VERSION, temporary_base)
        temporary_store = RulePackStore(temporary_root, build_registry())
        generated_draft, generated_release = build_release(
            temporary_store,
            source,
            template_root,
        )
        for label, actual, generated in (
            ("草稿", actual_draft, generated_draft),
            ("发布包", actual_release, generated_release),
        ):
            if tree_hashes(actual) != tree_hashes(generated):
                raise RuntimeError(f"现有{label}与临时重建结果不一致")
    store.validate(actual_release)
    return actual_release


def source_document() -> Path:
    """返回仓库内冻结的新重庆规则来源文档。"""

    source = ROOT / "risk-audit/规则来源" / SOURCE_NAME
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def main() -> None:
    """生成或只读复验 1.9.26 重庆规则包。"""

    parser = argparse.ArgumentParser(description="生成或复验 1.9.26 重庆规则包")
    parser.add_argument("--verify-only", action="store_true", help="只验证已有发布产物")
    args = parser.parse_args()
    source = source_document()
    store = RulePackStore(ROOT / "risk-audit/rulepacks", build_registry())
    draft_exists = (store.root / "drafts" / DRAFT_NAME).exists()
    release_exists = (store.root / "releases" / TARGET_VERSION).exists()
    if args.verify_only and not (draft_exists and release_exists):
        raise RuntimeError("--verify-only 要求 1.9.26 草稿与发布包已存在")
    if draft_exists and release_exists:
        release = verify_existing_release(store, source, TEMPLATE_ROOT)
        verified = True
    elif draft_exists:
        # 首次构建中断时允许从唯一命名的草稿继续，不删除任何已有文件。
        draft = store.root / "drafts" / DRAFT_NAME
        update_draft(draft, source, TEMPLATE_ROOT)
        store.validate(draft)
        release = store.publish(DRAFT_NAME, TARGET_VERSION)
        verified = False
    elif release_exists:
        raise RuntimeError("1.9.26 发布包存在但草稿缺失，无法执行确定性复验")
    else:
        _, release = build_release(store, source, TEMPLATE_ROOT)
        verified = False
    print(json.dumps({"release": str(release), "verified": verified}, ensure_ascii=False))


if __name__ == "__main__":
    main()
