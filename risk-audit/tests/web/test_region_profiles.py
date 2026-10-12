"""验证地区资源目录的严格解析与故障隔离。"""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from risk_audit.util import sha256_file, sha256_json
from risk_audit_web.region_profiles import RegionProfileError, load_region_catalog


ROOT = Path(__file__).resolve().parents[2]


def _write_release(resources_root: Path, version: str) -> str:
    """复制真实发布规则包；resources_root 为资源根，version 为发布版本。"""
    release = resources_root / "rulepacks" / "releases" / version
    shutil.copytree(ROOT / "rulepacks" / "releases" / version, release)
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    return str(manifest["content_hash"])


def _region_payload(
    region_code: str,
    version: str,
    content_hash: str,
) -> dict[str, object]:
    """生成地区索引条目；参数为地区代码、规则版本和规则内容哈希。"""
    payload: dict[str, object] = {
        "region_code": region_code,
        "region_name": "湖南" if region_code == "hunan" else "重庆",
        "rulepack_version": version,
        "rulepack_content_hash": content_hash,
        "entity_mode": "registry" if region_code == "hunan" else "parent_directory",
        "baseline_root": f"baselines/{region_code}/templates",
        "config_file": f"configs/{region_code}/audit-config.json",
        "enabled": True,
    }
    if payload["entity_mode"] == "registry":
        payload["entity_file"] = f"entities/{region_code}/entities.xlsx"
    return payload


def _write_index(resources_root: Path, regions: list[dict[str, object]]) -> None:
    """写入地区索引；resources_root 为资源根，regions 为有序地区条目。"""
    path = resources_root / "regions" / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": "1.0", "regions": regions}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_region_resources(resources_root: Path, region_code: str) -> None:
    """创建地区业务资源；resources_root 为资源根，region_code 为地区代码。"""
    entity_file = resources_root / "entities" / region_code / "entities.xlsx"
    entity_file.parent.mkdir(parents=True, exist_ok=True)
    entity_file.write_bytes(b"xlsx")
    (resources_root / "baselines" / region_code / "templates").mkdir(parents=True)
    config_file = resources_root / "configs" / region_code / "audit-config.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text('{"disabled_rules":[]}', encoding="utf-8")


@pytest.fixture
def resources_root(tmp_path: Path) -> Path:
    """创建湖南就绪、重庆缺业务资源的目录；tmp_path 为测试临时根。"""
    root = tmp_path / "resources"
    hunan_hash = _write_release(root, "1.9.21")
    chongqing_hash = _write_release(root, "1.9.22")
    _write_region_resources(root, "hunan")
    _write_index(root, [
        _region_payload("hunan", "1.9.21", hunan_hash),
        _region_payload("chongqing", "1.9.22", chongqing_hash),
    ])
    return root


def test_load_region_catalog_allows_parent_directory_without_entity_file(
    resources_root: Path,
) -> None:
    """目录识别地区不需要主体清单；resources_root 为地区资源根。"""
    catalog = load_region_catalog(resources_root)

    assert catalog.require("hunan").available is True
    assert catalog.require("chongqing").available is True
    assert catalog.require("chongqing").entity_mode == "parent_directory"
    assert catalog.require("chongqing").entity_file is None
    assert catalog.require("chongqing").unavailable_reasons == ()


def test_load_region_catalog_requires_registry_entity_file(resources_root: Path) -> None:
    """名册识别地区仍必须具备主体清单；resources_root 为地区资源根。"""
    entity_file = resources_root / "entities/hunan/entities.xlsx"
    entity_file.unlink()

    profile = load_region_catalog(resources_root).require("hunan")

    assert profile.available is False
    assert profile.unavailable_reasons == (f"会计主体清单文件缺失：{entity_file}",)


def test_load_region_catalog_allows_missing_templates_and_config(
    resources_root: Path,
) -> None:
    """模板和审核配置缺失时仍允许地区审核。

    Args:
        resources_root: 已包含地区规则包的测试资源根目录。
    """

    profile = load_region_catalog(resources_root).require("chongqing")

    assert profile.available is True
    assert profile.unavailable_reasons == ()
    assert not profile.baseline_root.exists()
    assert not profile.config_file.exists()


def test_load_region_catalog_exposes_only_public_fields(resources_root: Path) -> None:
    """公开地区信息不得泄露本地路径；resources_root 为地区资源根。"""
    payload = catalog_payload = load_region_catalog(resources_root).require("hunan").to_public_dict()

    assert payload == {
        "region_code": "hunan",
        "region_name": "湖南",
        "rulepack_version": "1.9.21",
        "available": True,
        "unavailable_reasons": [],
    }
    assert "rulepack" not in catalog_payload


def test_load_region_catalog_rejects_duplicate_codes(resources_root: Path) -> None:
    """重复地区代码必须拒绝整个索引；resources_root 为地区资源根。"""
    profile = _region_payload("hunan", "1.9.21", "0" * 64)
    _write_index(resources_root, [profile, profile])

    with pytest.raises(RegionProfileError, match="地区代码重复"):
        load_region_catalog(resources_root)


def test_load_region_catalog_rejects_unknown_fields(resources_root: Path) -> None:
    """未知字段必须拒绝整个索引；resources_root 为地区资源根。"""
    profile = _region_payload("hunan", "1.9.21", "0" * 64)
    profile["unexpected"] = True
    _write_index(resources_root, [profile])

    with pytest.raises(RegionProfileError, match="未知字段"):
        load_region_catalog(resources_root)


def test_load_region_catalog_rejects_path_escape(resources_root: Path) -> None:
    """资源路径越界必须拒绝整个索引；resources_root 为地区资源根。"""
    profile = _region_payload("hunan", "1.9.21", "0" * 64)
    profile["entity_file"] = "../outside.xlsx"
    _write_index(resources_root, [profile])

    with pytest.raises(RegionProfileError, match="越过资源根目录"):
        load_region_catalog(resources_root)


def test_load_region_catalog_isolates_rulepack_hash_mismatch(resources_root: Path) -> None:
    """规则包哈希不一致只禁用对应地区；resources_root 为地区资源根。"""
    index_path = resources_root / "regions" / "index.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["regions"][1]["rulepack_content_hash"] = "0" * 64
    index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    catalog = load_region_catalog(resources_root)

    assert catalog.require("hunan").available is True
    assert catalog.require("chongqing").available is False
    assert any("哈希" in reason for reason in catalog.require("chongqing").unavailable_reasons)


def test_load_region_catalog_rejects_cross_region_rulepack(resources_root: Path) -> None:
    """湖南索引指向重庆发布包时必须禁用湖南地区。"""

    index_path = resources_root / "regions" / "index.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["regions"][0]["rulepack_version"] = "1.9.22"
    payload["regions"][0]["rulepack_content_hash"] = payload["regions"][1][
        "rulepack_content_hash"
    ]
    index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    catalog = load_region_catalog(resources_root)

    assert catalog.require("hunan").available is False
    assert any("地区代码" in reason for reason in catalog.require("hunan").unavailable_reasons)


def test_load_region_catalog_rejects_cross_region_business_resources(
    resources_root: Path,
) -> None:
    """重庆地区不得引用湖南的模板或配置。"""

    index_path = resources_root / "regions" / "index.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["regions"][1].update({
        "baseline_root": "baselines/hunan/templates",
        "config_file": "configs/hunan/audit-config.json",
    })
    index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RegionProfileError, match="必须属于地区 chongqing"):
        load_region_catalog(resources_root)


def test_load_region_catalog_rejects_unknown_entity_mode(resources_root: Path) -> None:
    """未知主体识别方式必须拒绝；resources_root 为地区资源根。"""
    index_path = resources_root / "regions/index.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["regions"][1]["entity_mode"] = "unknown"
    index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RegionProfileError, match="entity_mode"):
        load_region_catalog(resources_root)


def test_load_region_catalog_rejects_unsafe_region_name(resources_root: Path) -> None:
    """地区名称不得用路径分隔符越过托管输出根目录。"""

    index_path = resources_root / "regions" / "index.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["regions"][0]["region_name"] = "../../outside"
    index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RegionProfileError, match="路径分隔符"):
        load_region_catalog(resources_root)
