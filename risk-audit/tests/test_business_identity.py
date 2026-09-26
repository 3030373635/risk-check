"""验证模板业务唯一标识、别名匹配和配置约束。"""
from pathlib import Path

import pytest
from openpyxl import Workbook

from risk_audit.business_identity import (
    BusinessIdentityError,
    identify_business,
    normalize_business_text,
    validate_business_registry,
)
from risk_audit.util import read_json, sha256_file


@pytest.fixture
def business_registry() -> dict:
    """返回包含重复展示编号和电压变体的最小业务配置。"""
    return {
        "schema_version": "2.0",
        "businesses": [
            {
                "business_id": "sales_electricity",
                "business_code": "01",
                "business_name": "营销售电",
                "aliases": ["营销售电"],
                "variants": [{"variant_id": "default", "aliases": [], "template": {
                    "path": "first-01.xlsx", "sha256": "a" * 64,
                }}],
            },
            {
                "business_id": "equity_management",
                "business_code": "01",
                "business_name": "股权（产权）管理",
                "aliases": ["股权（产权）管理", "股权产权管理", "股权管理"],
                "variants": [{"variant_id": "default", "aliases": [], "template": {
                    "path": "second-01.xlsx", "sha256": "b" * 64,
                }}],
            },
            {
                "business_id": "grid_construction",
                "business_code": "03",
                "business_name": "电网基建",
                "aliases": ["电网基建"],
                "variants": [
                    {"variant_id": "35-220kv", "aliases": ["35kv-220kv", "35千伏-220千伏"],
                     "template": {"path": "grid-low.xlsx", "sha256": "c" * 64}},
                    {"variant_id": "500-750kv", "aliases": ["500kv-750kv", "500千伏-750千伏"],
                     "template": {"path": "grid-high.xlsx", "sha256": "d" * 64}},
                ],
            },
            {
                "business_id": "contract_management",
                "business_code": "03",
                "business_name": "合同管理",
                "aliases": ["合同管理"],
                "variants": [{"variant_id": "default", "aliases": [], "template": {
                    "path": "contract.xlsx", "sha256": "e" * 64,
                }}],
            },
            {
                "business_id": "education_training",
                "business_code": "28",
                "business_name": "教育培训",
                "aliases": ["教育培训"],
                "variants": [{"variant_id": "default", "aliases": [], "template": {
                    "path": "education.xlsx", "sha256": "f" * 64,
                }}],
            },
        ],
    }


def test_normalize_business_text_ignores_spacing_parentheses_hyphens_and_case():
    """不同括号、分隔符、空白和大小写不得改变业务匹配文本。"""
    assert normalize_business_text(" 股权（ 产权 ）管理-A ") == normalize_business_text("股权(产权)管理a")


@pytest.mark.parametrize(
    ("relative_path", "business_id", "business_code", "variant_id"),
    [
        ("第一批/01 营销售电/01风控矩阵.xlsx", "sales_electricity", "01", "default"),
        ("第二批/01 股权(产权)管理-省公司版/01三清单.xlsx", "equity_management", "01", "default"),
        ("第二批/28 教育 培训—省公司版/28风控矩阵.xlsx", "education_training", "28", "default"),
        ("第一批/03 电网基建/35KV—220KV/03风控矩阵.xlsx", "grid_construction", "03", "35-220kv"),
        ("第一批/03 电网基建/500千伏-750千伏/03风控矩阵.xlsx", "grid_construction", "03", "500-750kv"),
        ("第二批/03 合同管理/03风控矩阵.xlsx", "contract_management", "03", "default"),
    ],
)
def test_identify_business_uses_name_not_display_code(
    business_registry: dict,
    relative_path: str,
    business_id: str,
    business_code: str,
    variant_id: str,
):
    """relative_path 为报送路径，其余参数为期望身份；重复编号必须按名称隔离。"""
    result = identify_business(Path(relative_path), business_registry)
    assert (result.business_id, result.business_code, result.variant_id) == (
        business_id,
        business_code,
        variant_id,
    )


def test_longest_alias_resolves_containment(business_registry: dict):
    """路径同时命中“股权管理”和完整名称时，必须选择最长别名。"""
    result = identify_business(Path("01 股权（产权）管理/股权管理矩阵.xlsx"), business_registry)
    assert result.business_id == "equity_management"
    assert result.matched_alias == "股权（产权）管理"


def test_equal_length_business_alias_conflict_is_rejected(business_registry: dict):
    """两个业务的最长命中别名同长度时不得猜测。"""
    business_registry["businesses"].append({
        "business_id": "other_training",
        "business_code": "99",
        "business_name": "培训教育",
        "aliases": ["培训教育"],
        "variants": [{"variant_id": "default", "aliases": [], "template": {
            "path": "other.xlsx", "sha256": "1" * 64,
        }}],
    })
    with pytest.raises(BusinessIdentityError, match="业务模板映射冲突.*baseline_registry.json"):
        identify_business(Path("教育培训与培训教育/矩阵.xlsx"), business_registry)


def test_missing_business_and_variant_are_rejected(business_registry: dict):
    """未知业务或电网基建缺少电压信息时必须停止映射。"""
    with pytest.raises(BusinessIdentityError, match="未找到业务模板映射"):
        identify_business(Path("未知业务/01风控矩阵.xlsx"), business_registry)
    with pytest.raises(BusinessIdentityError, match="模板变体无法确定"):
        identify_business(Path("03 电网基建/03风控矩阵.xlsx"), business_registry)


def test_multiple_non_default_variants_are_rejected_even_when_alias_lengths_differ(business_registry: dict):
    """同一业务命中两个非默认变体时不得用最长别名替用户猜测。"""
    grid = next(item for item in business_registry["businesses"] if item["business_id"] == "grid_construction")
    grid["variants"][0]["aliases"].append("35kv")

    with pytest.raises(BusinessIdentityError, match="模板变体映射冲突"):
        identify_business(Path("03 电网基建/35kv与500kv-750kv/矩阵.xlsx"), business_registry)


def test_validate_business_registry_rejects_unstable_or_ambiguous_configuration(business_registry: dict):
    """空身份、纯数字别名、重复变体和重复模板路径都必须被配置校验拒绝。"""
    duplicate = business_registry["businesses"][0].copy()
    duplicate["aliases"] = ["01"]
    duplicate["variants"] = [
        {"variant_id": "default", "aliases": [], "template": {"path": "same.xlsx", "sha256": "a" * 64}},
        {"variant_id": "default", "aliases": [], "template": {"path": "same.xlsx", "sha256": "a" * 64}},
    ]
    invalid = {"schema_version": "2.0", "businesses": [business_registry["businesses"][0], duplicate]}
    errors = validate_business_registry(invalid)
    assert any("business_id 重复" in error for error in errors)
    assert any("不允许纯数字" in error for error in errors)
    assert any("variant_id 重复" in error for error in errors)
    assert any("模板路径重复" in error for error in errors)


def test_confirmed_scanner_carries_unique_business_identity(tmp_path, business_registry: dict):
    """tmp_path 为报送根目录；同为 01 的两个业务必须生成不同业务 ID。"""
    from risk_audit.inventory_v180 import scan_package_v180
    from risk_audit.models import Entity

    unit = tmp_path / "测试主体有限公司"
    paths = [
        unit / "01 营销售电" / "01风控矩阵-测试主体有限公司.xlsx",
        unit / "01 股权（产权）管理" / "01风控矩阵-测试主体有限公司.xlsx",
        unit / "28 教育培训" / "28风控矩阵-测试主体有限公司.xlsx",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        workbook.active.append(["控制措施编号", "控制措施", "责任主体"])
        workbook.save(path)

    files = scan_package_v180(
        tmp_path,
        {"A001": Entity("A001", "测试主体有限公司")},
        {},
        business_registry,
    )

    identities = {
        file.relative_path.parent.name: (file.business_id, file.business_code, file.variant_id)
        for file in files
    }
    assert identities == {
        "01 营销售电": ("sales_electricity", "01", "default"),
        "01 股权（产权）管理": ("equity_management", "01", "default"),
        "28 教育培训": ("education_training", "28", "default"),
    }
    assert all(not any("业务模板" in error or "业务编号" in error for error in file.parse_errors) for file in files)


def test_load_baselines_isolates_duplicate_display_codes(tmp_path):
    """tmp_path 为项目根目录；两个 01 模板必须按业务 ID 同时加载。"""
    from risk_audit.baselines import load_baselines

    templates = []
    for filename, responsibility in [
        ("sales.xlsx", "省公司-营销部-营销专责"),
        ("equity.xlsx", "省公司-财务部-产权专责"),
    ]:
        path = tmp_path / filename
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "风控矩阵"
        sheet.append(["控制措施编号", "控制措施", "控制载体", "责任主体"])
        sheet.append(["M1", "核对资料", "审批单", responsibility])
        workbook.save(path)
        templates.append((path, responsibility))
    registry = {
        "schema_version": "2.0",
        "businesses": [
            {
                "business_id": business_id,
                "business_code": "01",
                "business_name": business_name,
                "aliases": [business_name],
                "variants": [{"variant_id": "default", "aliases": [], "template": {
                    "path": path.name,
                    "sha256": sha256_file(path),
                }}],
            }
            for (business_id, business_name), (path, _) in zip(
                [("sales_electricity", "营销售电"), ("equity_management", "股权产权管理")],
                templates,
            )
        ],
    }
    aliases = read_json(Path(__file__).resolve().parents[1] / "rulepacks/releases/1.9.18/field_aliases.json")

    baselines = load_baselines(tmp_path, registry, aliases, tmp_path / "work")

    assert set(baselines) == {
        ("sales_electricity", "default"),
        ("equity_management", "default"),
    }
    assert baselines[("sales_electricity", "default")]["business_code"] == "01"
    assert baselines[("equity_management", "default")]["business_code"] == "01"
    assert baselines[("sales_electricity", "default")]["responsibilities"][0]["measure_id"] == "M1"
    assert baselines[("equity_management", "default")]["responsibilities"][0]["measure_id"] == "M1"
