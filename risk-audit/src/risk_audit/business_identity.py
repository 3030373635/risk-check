"""模板业务配置校验与报送路径业务身份识别。"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_SEPARATORS = re.compile(r"[\s()（）\[\]【】{}｛｝<>《》·•・,，、:：;；._—–－-]+")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BUSINESS_CODE_PREFIX = re.compile(r"^(\d{1,2})(?:\D|$)")
_TABLE_HEADER_MARKERS = (
    "控制措施编号",
    "岗位职责编号",
    "岗位职责",
    "不相容岗位",
    "系统名称",
)


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


def workbook_business_evidence(path: Path, file_format: str) -> list[str]:
    """读取工作表名和表头区文字。

    Args:
        path: 待检查的 Excel 材料路径。
        file_format: 根据文件签名识别的 xlsx 或 xls 格式。
    """

    evidence: list[str] = []
    if file_format == "xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for worksheet in workbook.worksheets:
                evidence.append(worksheet.title)
                # 只采集业务表头之前的标题区，防止正文提及其他业务造成伪冲突。
                for row in worksheet.iter_rows(min_row=1, max_row=12, min_col=1, max_col=12, values_only=True):
                    texts = [str(value) for value in row if value not in (None, "")]
                    evidence.extend(texts)
                    if any(marker in normalize_business_text(text) for marker in _TABLE_HEADER_MARKERS for text in texts):
                        break
        finally:
            workbook.close()
    elif file_format == "xls":
        import xlrd

        workbook = xlrd.open_workbook(path, on_demand=True)
        try:
            for worksheet in workbook.sheets():
                evidence.append(worksheet.name)
                for row in range(min(worksheet.nrows, 12)):
                    texts = []
                    for column in range(min(worksheet.ncols, 12)):
                        value = worksheet.cell_value(row, column)
                        if value not in (None, ""):
                            texts.append(str(value))
                    evidence.extend(texts)
                    if any(marker in normalize_business_text(text) for marker in _TABLE_HEADER_MARKERS for text in texts):
                        break
        finally:
            workbook.release_resources()
    return list(dict.fromkeys(evidence))


def _is_table_header_row(texts: list[str]) -> bool:
    """判断一行是否已进入正文字段表头。

    Args:
        texts: 当前行中的非空单元格文本。
    """

    # 标题可能是“不相容岗位清单”；只有多列表头才截止，避免丢掉单元格标题。
    return len(texts) >= 2 and any(
        marker in normalize_business_text(text)
        for marker in _TABLE_HEADER_MARKERS
        for text in texts
    )


def workbook_material_evidence(path: Path, file_format: str) -> list[str]:
    """读取可用于材料类型判断的工作表名和正文前标题。

    Args:
        path: 待检查的 Excel 材料路径。
        file_format: 根据文件签名识别的 xlsx 或 xls 格式。
    """

    evidence: list[str] = []
    if file_format == "xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for worksheet in workbook.worksheets:
                evidence.append(worksheet.title)
                for row in worksheet.iter_rows(min_row=1, max_row=12, min_col=1, max_col=12, values_only=True):
                    texts = [str(value) for value in row if value not in (None, "")]
                    # 正文字段名不能作为整个工作簿的材料类型证据。
                    if _is_table_header_row(texts):
                        break
                    evidence.extend(texts)
        finally:
            workbook.close()
    elif file_format == "xls":
        import xlrd

        workbook = xlrd.open_workbook(path, on_demand=True)
        try:
            for worksheet in workbook.sheets():
                evidence.append(worksheet.name)
                for row in range(min(worksheet.nrows, 12)):
                    texts = []
                    for column in range(min(worksheet.ncols, 12)):
                        value = worksheet.cell_value(row, column)
                        if value not in (None, ""):
                            texts.append(str(value))
                    if _is_table_header_row(texts):
                        break
                    evidence.extend(texts)
        finally:
            workbook.release_resources()
    return list(dict.fromkeys(evidence))


def unmatched_business_id(relative_path: Path) -> str:
    """为未匹配材料生成稳定的业务分组标识。

    Args:
        relative_path: 材料在报送根目录下的相对路径。
    """

    group_text = normalize_business_text(str(relative_path.parent)) or relative_path.parent.as_posix()
    digest = hashlib.sha256(group_text.encode("utf-8")).hexdigest()[:16]
    return f"unmatched_{digest}"


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
    workbook_evidence: list[str] | None = None,
) -> BusinessIdentity:
    """综合目录、文件名和工作簿证据识别唯一业务。

    Args:
        relative_path: 材料文件在输入根目录下的相对路径。
        registry: schema_version 为 2.0 的模板业务配置。
        input_root_name: 输入根目录名称，仅在材料直接位于根目录时用于业务识别。
        workbook_evidence: 工作表名和表内标题等工作簿证据。
    """

    # 单业务包可被直接选为输入目录，此时相对路径没有父目录，必须使用输入根目录名。
    business_directory = relative_path.parent.name or input_root_name
    evidence_values = [business_directory, relative_path.stem, *(workbook_evidence or [])]
    business_matches: list[tuple[int, str, dict[str, Any]]] = []
    for evidence in evidence_values:
        normalized_evidence = normalize_business_text(evidence)
        if not normalized_evidence:
            continue
        for business_item in registry.get("businesses", []):
            for alias in business_item.get("aliases", []):
                normalized_alias = normalize_business_text(alias)
                if normalized_alias and normalized_alias in normalized_evidence:
                    business_matches.append((len(normalized_alias), alias, business_item))

    if business_matches:
        matched_businesses = {item[2]["business_id"] for item in business_matches}
        if len(matched_businesses) != 1:
            candidates = "、".join(
                sorted({
                    f"{item[2]['business_name']}（命中“{item[1]}”）"
                    for item in business_matches
                })
            )
            raise BusinessIdentityError(
                f"业务模板映射冲突：{relative_path}；候选：{candidates}；"
                "请检查 baseline_registry.json 的 aliases 配置"
            )
        _, matched_alias, business = max(business_matches, key=lambda item: item[0])
    else:
        # 文字证据均未命中时，仅在目录或文件名编号唯一时允许回退。
        code_values = []
        for value in (business_directory, relative_path.stem):
            code_match = _BUSINESS_CODE_PREFIX.match(value)
            if code_match:
                code_values.append(code_match.group(1).zfill(2))
        if len(set(code_values)) > 1:
            raise BusinessIdentityError(
                f"业务编号映射冲突：{relative_path}；编号：{'、'.join(sorted(set(code_values)))}"
            )
        business_code = code_values[0] if code_values else ""
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
    path_text = normalize_business_text(" ".join([str(identity_path), *(workbook_evidence or [])]))
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
