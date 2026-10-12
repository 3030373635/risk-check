"""地区规则包与业务资源的严格配置加载。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.loader import load_pack
from risk_audit.configuration.validator import ConfigError, validate_pack
from risk_audit.util import sha256_file, sha256_json


_INDEX_FIELDS = {"schema_version", "regions"}
_REGION_REQUIRED_FIELDS = {
    "region_code",
    "region_name",
    "rulepack_version",
    "rulepack_content_hash",
    "entity_mode",
    "baseline_root",
    "config_file",
    "enabled",
}
_REGION_OPTIONAL_FIELDS = {"entity_file"}
_ENTITY_MODES = {"registry", "parent_directory"}
_REGION_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class RegionProfileError(ValueError):
    """表示地区索引结构、字段或资源路径不安全。"""


@dataclass(frozen=True)
class RegionProfile:
    """保存一个地区已经解析的固定规则包与业务资源。"""

    region_code: str
    region_name: str
    rulepack_version: str
    rulepack_content_hash: str
    rulepack: Path
    entity_mode: str
    entity_file: Path | None
    baseline_root: Path
    config_file: Path
    enabled: bool
    unavailable_reasons: tuple[str, ...]

    @property
    def available(self) -> bool:
        """返回该地区是否允许创建任务；无参数。"""
        return self.enabled and not self.unavailable_reasons

    def to_public_dict(self) -> dict[str, object]:
        """返回不含本地资源路径的公开地区信息；无参数。"""
        reasons = list(self.unavailable_reasons)
        if not self.enabled:
            reasons.insert(0, "地区已禁用")
        return {
            "region_code": self.region_code,
            "region_name": self.region_name,
            "rulepack_version": self.rulepack_version,
            "available": self.available,
            "unavailable_reasons": reasons,
        }


@dataclass(frozen=True)
class RegionCatalog:
    """保存按稳定地区代码索引的地区配置。"""

    profiles: dict[str, RegionProfile]

    def require(self, region_code: str) -> RegionProfile:
        """返回指定地区；region_code 为稳定地区代码，未知代码抛出配置错误。"""
        try:
            return self.profiles[region_code]
        except KeyError as error:
            raise RegionProfileError(f"未知地区代码：{region_code}") from error


def _load_index(index_path: Path) -> dict[str, Any]:
    """读取地区索引；index_path 为索引文件，返回已校验的根对象。"""
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RegionProfileError(f"地区索引不存在：{index_path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise RegionProfileError(f"地区索引无法读取：{index_path}；{error}") from error
    if not isinstance(payload, dict):
        raise RegionProfileError("地区索引必须是 JSON 对象")
    unknown_fields = set(payload) - _INDEX_FIELDS
    if unknown_fields:
        raise RegionProfileError(f"地区索引存在未知字段：{sorted(unknown_fields)}")
    if set(payload) != _INDEX_FIELDS:
        raise RegionProfileError("地区索引缺少 schema_version 或 regions")
    if payload["schema_version"] != "1.0":
        raise RegionProfileError(f"地区索引版本不支持：{payload['schema_version']!r}")
    if not isinstance(payload["regions"], list):
        raise RegionProfileError("地区索引 regions 必须是数组")
    return payload


def _required_text(profile: dict[str, Any], field: str, position: int) -> str:
    """读取必填文本；profile 为地区条目，field 为字段名，position 为条目序号。"""
    value = profile.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RegionProfileError(f"地区条目 {position} 的 {field} 必须是非空文本")
    return value.strip()


def _safe_resource_path(resources_root: Path, raw_path: str, field: str) -> Path:
    """解析安全资源路径；参数为资源根、POSIX 相对路径和字段名，返回绝对路径。"""
    pure_path = PurePosixPath(raw_path)
    if (
        not raw_path
        or "\\" in raw_path
        or pure_path.is_absolute()
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        raise RegionProfileError(f"{field} 越过资源根目录或不是合法 POSIX 相对路径：{raw_path}")
    root = resources_root.resolve(strict=False)
    candidate = (root / Path(*pure_path.parts)).resolve(strict=False)
    # resolve 后仍须位于资源根，阻止 .. 和已存在符号链接逃逸。
    if candidate == root or root not in candidate.parents:
        raise RegionProfileError(f"{field} 越过资源根目录：{raw_path}")
    return candidate


def _rulepack_hash(rulepack: Path) -> str:
    """计算发布包内容摘要；rulepack 为发布目录，返回 JSON 资源哈希。"""
    manifest_path = rulepack / "manifest.json"
    resource_hashes = {
        path.relative_to(rulepack).as_posix(): sha256_file(path)
        for path in sorted(rulepack.rglob("*.json"), key=lambda item: item.as_posix())
        if path != manifest_path
    }
    return sha256_json(resource_hashes)


def verify_rulepack_release(
    rulepack: Path,
    version: str,
    expected_hash: str,
    region_code: str,
    region_name: str,
) -> list[str]:
    """检查规则发布包。

    Args:
        rulepack: 待校验的发布目录。
        version: 地区索引或任务固定的版本。
        expected_hash: 地区索引或任务固定的内容哈希。
        region_code: 地区索引或任务固定的地区代码。
        region_name: 地区索引或任务固定的地区名称。
    """
    if not rulepack.is_dir():
        return [f"规则包目录缺失：{rulepack}"]
    manifest_path = rulepack / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"规则包清单无法读取：{manifest_path}；{error}"]
    if not isinstance(manifest, dict):
        return [f"规则包清单必须是 JSON 对象：{manifest_path}"]

    reasons: list[str] = []
    if manifest.get("version") != version:
        reasons.append(f"规则包版本不一致：期望 {version}")
    if manifest.get("status") != "released":
        reasons.append("规则包不是已发布状态")
    if manifest.get("region_code") != region_code:
        reasons.append(f"规则包地区代码不一致：期望 {region_code}")
    if manifest.get("region_name") != region_name:
        reasons.append(f"规则包地区名称不一致：期望 {region_name}")
    manifest_hash = manifest.get("content_hash")
    if manifest_hash != expected_hash:
        reasons.append("规则包清单哈希与地区索引不一致")
    try:
        actual_hash = _rulepack_hash(rulepack)
    except OSError as error:
        reasons.append(f"规则包资源无法读取：{error}")
    else:
        if actual_hash != expected_hash:
            reasons.append("规则包内容哈希与地区索引不一致")
    try:
        # 哈希一致仍不代表规则结构和能力引用可执行。
        validate_pack(load_pack(rulepack), build_registry())
    except (ConfigError, OSError, TypeError, ValueError) as error:
        reasons.append(f"规则包结构或能力无效：{error}")
    return reasons


def _inspect_region_resource(path: Path, label: str, *, directory: bool = False) -> str | None:
    """检查地区业务资源；path 为目标，label 为名称，directory 指定是否应为目录。"""
    exists = path.is_dir() if directory else path.is_file()
    if exists:
        return None
    expected_type = "目录" if directory else "文件"
    return f"{label}{expected_type}缺失：{path}"


def _parse_region(resources_root: Path, raw_profile: object, position: int) -> RegionProfile:
    """解析一个地区条目；参数为资源根、原始条目和序号，返回固定地区配置。"""
    if not isinstance(raw_profile, dict):
        raise RegionProfileError(f"地区条目 {position} 必须是 JSON 对象")
    allowed_fields = _REGION_REQUIRED_FIELDS | _REGION_OPTIONAL_FIELDS
    unknown_fields = set(raw_profile) - allowed_fields
    if unknown_fields:
        raise RegionProfileError(f"地区条目 {position} 存在未知字段：{sorted(unknown_fields)}")
    missing = sorted(_REGION_REQUIRED_FIELDS - set(raw_profile))
    if missing:
        raise RegionProfileError(f"地区条目 {position} 缺少字段：{missing}")

    region_code = _required_text(raw_profile, "region_code", position)
    if not _REGION_CODE_PATTERN.fullmatch(region_code):
        raise RegionProfileError(f"地区代码格式无效：{region_code}")
    region_name = _required_text(raw_profile, "region_name", position)
    if region_name in {".", ".."} or "/" in region_name or "\\" in region_name:
        raise RegionProfileError(f"地区名称不得包含路径分隔符：{region_name}")
    version = _required_text(raw_profile, "rulepack_version", position)
    if version in {".", ".."} or "/" in version or "\\" in version:
        raise RegionProfileError(f"规则包版本路径无效：{version}")
    content_hash = _required_text(raw_profile, "rulepack_content_hash", position)
    entity_mode = _required_text(raw_profile, "entity_mode", position)
    if entity_mode not in _ENTITY_MODES:
        raise RegionProfileError(
            f"地区条目 {position} 的 entity_mode 不支持：{entity_mode}"
        )
    enabled = raw_profile.get("enabled")
    if not isinstance(enabled, bool):
        raise RegionProfileError(f"地区条目 {position} 的 enabled 必须是布尔值")

    # 规则包路径只由安全版本号生成，客户端和索引不能注入任意目录。
    rulepack = _safe_resource_path(
        resources_root,
        f"rulepacks/releases/{version}",
        "rulepack_version",
    )
    if entity_mode == "registry":
        entity_file = _safe_resource_path(
            resources_root,
            _required_text(raw_profile, "entity_file", position),
            "entity_file",
        )
    else:
        if "entity_file" in raw_profile:
            raise RegionProfileError(
                f"地区条目 {position} 使用 parent_directory 时不得配置 entity_file"
            )
        entity_file = None
    baseline_root = _safe_resource_path(
        resources_root,
        _required_text(raw_profile, "baseline_root", position),
        "baseline_root",
    )
    config_file = _safe_resource_path(
        resources_root,
        _required_text(raw_profile, "config_file", position),
        "config_file",
    )
    expected_roots = {
        "baseline_root": resources_root / "baselines" / region_code,
        "config_file": resources_root / "configs" / region_code,
    }
    region_resources = [
        ("baseline_root", baseline_root),
        ("config_file", config_file),
    ]
    if entity_file is not None:
        expected_roots["entity_file"] = resources_root / "entities" / region_code
        region_resources.append(("entity_file", entity_file))
    for field, path in region_resources:
        expected_root = expected_roots[field].resolve(strict=False)
        if path != expected_root and expected_root not in path.parents:
            raise RegionProfileError(f"{field} 必须属于地区 {region_code} 的独立资源目录")

    reasons = verify_rulepack_release(
        rulepack,
        version,
        content_hash,
        region_code,
        region_name,
    )
    if entity_mode == "registry" and entity_file is not None:
        # 名册模式依赖主体清单；目录模式不读取该资源。
        entity_reason = _inspect_region_resource(entity_file, "会计主体清单")
        if entity_reason:
            reasons.append(entity_reason)
    # 模板和运行配置均支持降级：无模板时执行其他规则，无配置时使用默认规则选择。
    return RegionProfile(
        region_code=region_code,
        region_name=region_name,
        rulepack_version=version,
        rulepack_content_hash=content_hash,
        rulepack=rulepack,
        entity_mode=entity_mode,
        entity_file=entity_file,
        baseline_root=baseline_root,
        config_file=config_file,
        enabled=enabled,
        unavailable_reasons=tuple(reasons),
    )


def load_region_catalog(resources_root: Path) -> RegionCatalog:
    """加载地区目录；resources_root 为 runtime/resources 根，返回严格解析的目录。"""
    resolved_root = resources_root.resolve(strict=False)
    payload = _load_index(resolved_root / "regions" / "index.json")
    profiles: dict[str, RegionProfile] = {}
    for position, raw_profile in enumerate(payload["regions"], start=1):
        profile = _parse_region(resolved_root, raw_profile, position)
        if profile.region_code in profiles:
            raise RegionProfileError(f"地区代码重复：{profile.region_code}")
        profiles[profile.region_code] = profile
    return RegionCatalog(profiles=profiles)
