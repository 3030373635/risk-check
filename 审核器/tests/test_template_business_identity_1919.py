"""验证 1.9.19 多批次模板业务目录和不可变发布内容。"""
import copy
from collections import Counter
from pathlib import Path

import pytest

from risk_audit.business_identity import identify_business, validate_business_registry
from risk_audit.checks.registry import build_registry
from risk_audit.configuration.loader import load_pack
from risk_audit.configuration.validator import ConfigError, validate_pack
from risk_audit.util import read_json, sha256_file


ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "审核器/rulepacks/releases/1.9.19"


def test_1919_registers_all_templates_with_unique_business_ids_and_real_hashes():
    """规则包必须登记第一批 11 份和第二批 8 份模板，并保留重复展示编号。"""
    registry = read_json(RELEASE / "baseline_registry.json")
    businesses = registry["businesses"]
    templates = [variant["template"] for business in businesses for variant in business["variants"]]
    duplicate_codes = {code for code, count in Counter(b["business_code"] for b in businesses).items() if count > 1}

    assert registry["schema_version"] == "2.0"
    assert len(businesses) == 18
    assert len({business["business_id"] for business in businesses}) == 18
    assert len(templates) == 19
    assert duplicate_codes == {"01", "02", "03", "08"}
    assert {business["business_code"] for business in businesses} >= {"28", "29", "30", "31"}
    assert validate_business_registry(registry) == []
    for template in templates:
        path = ROOT / template["path"]
        assert path.is_file()
        assert sha256_file(path) == template["sha256"]


def test_every_template_path_selects_its_declared_business_and_variant():
    """每条固定模板路径都必须反向匹配到自身业务，防止别名配置串线。"""
    registry = read_json(RELEASE / "baseline_registry.json")
    for business in registry["businesses"]:
        for variant in business["variants"]:
            identity = identify_business(Path(variant["template"]["path"]), registry)
            assert (identity.business_id, identity.variant_id) == (
                business["business_id"],
                variant["variant_id"],
            )


def test_1919_release_manifest_and_active_pointer_are_consistent():
    """发布包内容哈希和 active 指针必须由正式发布机制生成。"""
    pack = load_pack(RELEASE)
    active = read_json(ROOT / "审核器/rulepacks/active.json")
    assert pack["manifest"]["version"] == "1.9.19"
    assert pack["manifest"]["status"] == "released"
    assert active == {"version": "1.9.19", "content_hash": pack["manifest"]["content_hash"]}


def test_rulepack_rejects_unknown_baseline_registry_schema():
    """非 2.0 且非旧 entries 结构不得绕过模板业务目录校验。"""
    pack = copy.deepcopy(load_pack(RELEASE))
    pack["baseline_registry"]["schema_version"] = "2"

    with pytest.raises(ConfigError, match="baseline_registry.schema_version"):
        validate_pack(pack, build_registry())
