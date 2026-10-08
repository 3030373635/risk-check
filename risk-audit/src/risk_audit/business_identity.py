"""模板业务配置校验与报送路径业务身份识别。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_SEPARATORS = re.compile(r"[\s()（）\[\]【】{}｛｝<>《》·•・,，、:：;；._—–－-]+")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BUSINESS_CODE_PREFIX = re.compile(r"^(\d{1,2})(?:\D|$)")


class BusinessIdentityError(ValueError):
    """表示报送路径无法安全映射到唯一业务或模板变体。"""


@dataclass(frozen=True)
class BusinessIdentity:
    """保存报送材料匹配后的稳定业务身份与展示信息。"""

    business_id: str
    business_code: str
    business_name: str
    variant_id: str
    matched_alias: str
    matched_variant_alias: str = ""


def normalize_business_text(value: str) -> str:
    """规范化业务匹配文本；value 为路径或别名，返回删除常见分隔符后的比较值。"""
    normalized = unicodedata.normalize("NFKC", str(value)).lower()
    return _SEPARATORS.sub("", normalized)


def validate_business_registry(registry: dict[str, Any]) -> list[str]:
    """校验模板业务配置；registry 为 baseline_registry 内容，返回全部错误信息。"""
    errors: list[str] = []
    if not isinstance(registry, dict) or registry.get("schema_version") != "2.0":
        return ['baseline_registry.schema_version 必须为 "2.0"']
    businesses = registry.get("businesses")
    if not isinstance(businesses, list) or not businesses:
        return ["baseline_registry.businesses 必须为非空数组"]

    business_ids: set[str] = set()
    template_paths: set[str] = set()
    for business_index, business in enumerate(businesses):
        where = f"baseline_registry.businesses[{business_index}]"
        if not isinstance(business, dict):
            errors.append(f"{where} 必须为对象")
            continue
        business_id = business.get("business_id")
        if not isinstance(business_id, str) or not _IDENTIFIER.fullmatch(business_id):
            errors.append(f"{where}.business_id 必须为小写稳定标识")
        elif business_id in business_ids:
            errors.append(f"{where}.business_id 重复: {business_id}")
        else:
            business_ids.add(business_id)
        for field in ("business_code", "business_name"):
            if not isinstance(business.get(field), str) or not business[field].strip():
                errors.append(f"{where}.{field} 必须为非空字符串")
        aliases = business.get("aliases")
        if not isinstance(aliases, list) or not aliases:
            errors.append(f"{where}.aliases 必须为非空数组")
        else:
            for alias_index, alias in enumerate(aliases):
                alias_where = f"{where}.aliases[{alias_index}]"
                if not isinstance(alias, str) or not normalize_business_text(alias):
                    errors.append(f"{alias_where} 必须为非空别名")
                elif normalize_business_text(alias).isdigit():
                    errors.append(f"{alias_where} 不允许纯数字别名")

        variants = business.get("variants")
        if not isinstance(variants, list) or not variants:
            errors.append(f"{where}.variants 必须为非空数组")
            continue
        variant_ids: set[str] = set()
        for variant_index, variant in enumerate(variants):
            variant_where = f"{where}.variants[{variant_index}]"
            if not isinstance(variant, dict):
                errors.append(f"{variant_where} 必须为对象")
                continue
            variant_id = variant.get("variant_id")
            if not isinstance(variant_id, str) or not variant_id.strip():
                errors.append(f"{variant_where}.variant_id 必须为非空字符串")
            elif variant_id in variant_ids:
                errors.append(f"{variant_where}.variant_id 重复: {variant_id}")
            else:
                variant_ids.add(variant_id)
            variant_aliases = variant.get("aliases")
            if not isinstance(variant_aliases, list) or any(
                not isinstance(alias, str) or not normalize_business_text(alias) for alias in variant_aliases
            ):
                errors.append(f"{variant_where}.aliases 必须为字符串数组")
            template = variant.get("template")
            if not isinstance(template, dict):
                errors.append(f"{variant_where}.template 必须为对象")
                continue
            path = template.get("path")
            digest = template.get("sha256")
            if not isinstance(path, str) or not path.strip():
                errors.append(f"{variant_where}.template.path 必须为非空字符串")
            elif path in template_paths:
                errors.append(f"{variant_where}.模板路径重复: {path}")
            else:
                template_paths.add(path)
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                errors.append(f"{variant_where}.template.sha256 必须为 64 位小写十六进制")
    return errors


def _select_longest_match(
    matches: list[tuple[int, str, dict[str, Any]]],
    relative_path: Path,
    conflict_label: str,
) -> tuple[int, str, dict[str, Any]]:
    """选择唯一最长别名；matches 为长度、原别名和配置项，relative_path 用于错误定位。"""
    longest = max(item[0] for item in matches)
    winners = [item for item in matches if item[0] == longest]
    identities = {id(item[2]) for item in winners}
    if len(identities) != 1:
        candidates = "、".join(
            f"{item[2].get('business_id', item[2].get('variant_id', ''))}（命中“{item[1]}”）"
            for item in winners
        )
        raise BusinessIdentityError(
            f"{conflict_label}：{relative_path}；候选：{candidates}；"
            "请检查 baseline_registry.json 的 aliases 配置"
        )
    return winners[0]


def identify_business(
    relative_path: Path,
    registry: dict[str, Any],
    input_root_name: str = "",
) -> BusinessIdentity:
    """使用文件直接父目录识别唯一业务。

    Args:
        relative_path: 材料文件在输入根目录下的相对路径。
        registry: schema_version 为 2.0 的模板业务配置。
        input_root_name: 输入根目录名称，仅在材料直接位于根目录时用于业务识别。
    """

    # 单业务包可被直接选为输入目录，此时相对路径没有父目录，必须使用输入根目录名。
    business_directory = relative_path.parent.name or input_root_name
    directory_text = normalize_business_text(business_directory)
    business_matches: list[tuple[int, str, dict[str, Any]]] = []
    for business in registry.get("businesses", []):
        for alias in business.get("aliases", []):
            normalized_alias = normalize_business_text(alias)
            if normalized_alias and normalized_alias in directory_text:
                business_matches.append((len(normalized_alias), alias, business))

    if business_matches:
        _, matched_alias, business = _select_longest_match(
            business_matches,
            relative_path,
            "业务模板映射冲突",
        )
    else:
        # 目录简称未配置为别名时，仅在编号唯一时允许回退。
        code_match = _BUSINESS_CODE_PREFIX.match(business_directory)
        business_code = code_match.group(1).zfill(2) if code_match else ""
        code_matches = [
            item
            for item in registry.get("businesses", [])
            if str(item.get("business_code", "")).zfill(2) == business_code
        ] if business_code else []
        if len(code_matches) > 1:
            candidates = "、".join(str(item.get("business_name", "")) for item in code_matches)
            raise BusinessIdentityError(
                f"业务编号映射冲突：{relative_path}；父目录={business_directory}；"
                f"编号={business_code}；候选：{candidates}"
            )
        if not code_matches:
            raise BusinessIdentityError(
                f"未找到业务模板映射：{relative_path}；父目录={business_directory}；"
                "请使用可识别的业务名称或唯一业务编号"
            )
        business = code_matches[0]
        matched_alias = ""

    variants = business.get("variants", [])
    # 业务由父目录确定；电压等模板变体仍可由整条路径补充判断。
    # 根目录参与业务识别时也必须参与变体识别，避免丢失电压范围等关键信息。
    identity_path = Path(input_root_name) / relative_path if not relative_path.parent.name else relative_path
    path_text = normalize_business_text(str(identity_path))
    variant_matches: list[tuple[int, str, dict[str, Any]]] = []
    for variant in variants:
        if variant.get("variant_id") == "default":
            continue
        for alias in variant.get("aliases", []):
            normalized_alias = normalize_business_text(alias)
            if normalized_alias and normalized_alias in path_text:
                variant_matches.append((len(normalized_alias), alias, variant))
    if variant_matches:
        # 变体不能采用“最长命中”消除跨变体冲突；同时出现两个电压范围必须交由报送方确认。
        matched_variants = {id(item[2]) for item in variant_matches}
        if len(matched_variants) > 1:
            candidates = "、".join(
                f"{item[2].get('variant_id', '')}（命中“{item[1]}”）"
                for item in variant_matches
            )
            raise BusinessIdentityError(
                f"模板变体映射冲突：{relative_path}；业务={business.get('business_id')}；"
                f"候选：{candidates}；请检查 baseline_registry.json 的 variants[].aliases"
            )
        _, matched_variant_alias, variant = _select_longest_match(
            variant_matches,
            relative_path,
            "模板变体映射冲突",
        )
    else:
        defaults = [variant for variant in variants if variant.get("variant_id") == "default"]
        if len(defaults) != 1:
            candidates = "、".join(str(variant.get("variant_id", "")) for variant in variants)
            raise BusinessIdentityError(
                f"模板变体无法确定：{relative_path}；业务={business.get('business_id')}；"
                f"候选变体={candidates or '无'}；请检查 baseline_registry.json 的 variants[].aliases"
            )
        variant = defaults[0]
        matched_variant_alias = ""
    return BusinessIdentity(
        business_id=business["business_id"],
        business_code=business["business_code"],
        business_name=business["business_name"],
        variant_id=variant["variant_id"],
        matched_alias=matched_alias,
        matched_variant_alias=matched_variant_alias,
    )
