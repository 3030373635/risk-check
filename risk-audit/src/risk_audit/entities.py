from __future__ import annotations

from pathlib import Path
import re

from openpyxl import load_workbook

from risk_audit.models import Entity
from risk_audit.util import norm_text


ENTITY_NAME_PATTERN = r'.+(?:有限公司|分公司|中心|研究院|设计院|供电所)(?:本部)?'


def reported_entity_name(text):
    """提取明确报送全称；text 为文件名或目录名，不确认其名册身份或单位代码。"""
    for part in reversed(re.split(r'[-—_：:（）()]', text)):
        name = re.sub(r'^\d+[\s.、-]*', '', part.strip())
        if re.fullmatch(ENTITY_NAME_PATTERN, name) and not any(word in name for word in ('矩阵', '三清单', '说明', '申请')):
            return name
    return ''


def material_directory_name(path, root, business_registry=None, business_id=None, business_code=None):
    """取得单位目录名称；path/root 为材料与输入根，其余参数用于排除当前业务目录。"""
    from risk_audit.submission_scope import unit_directory

    directory = unit_directory(path, root, business_registry, business_id, business_code)
    label = re.sub(r'^\d+[\s.、-]*', '', directory.name)
    # 输入根本身是单位目录时也保留全称，普通批次根不能当作单位名称。
    if directory != root or re.fullmatch(ENTITY_NAME_PATTERN, label) or label.endswith('公司'):
        return label
    return '未识别单位（见文件路径）'


def directory_material_entity(path, root, entities, business_registry=None, business_id=None, business_code=None):
    """按单位目录识别；path/root 为材料与输入根，entities 为名册，其余参数用于排除业务目录。"""
    name = material_directory_name(path, root, business_registry, business_id, business_code)
    codes = [code for code, entity in entities.items() if norm_text(name) == norm_text(entity.name)]
    if len(codes) == 1:
        return codes[0], [f'正式单位目录全称:{name}'], False
    if len(codes) > 1:
        return None, [f'正式单位目录全称:{name}'], True
    # 不沿上级目录查找母公司代码，不借用文件名称或简称推定身份。
    evidence = [] if name == '未识别单位（见文件路径）' else [f'报送名称:{name}']
    return None, evidence, False


def identify_material_entity(path, root, entities, aliases, texts, business_registry=None, business_id=None,
                             business_code=None):
    """仅按单位目录识别材料主体，文件名和说明正文不参与归属判断。

    path/root 为材料与输入根，entities 为正式名册；aliases/texts 为扫描器传入的信息；
    business_registry/business_id/business_code 用于排除新版业务目录，
    本规则不使用简称或正文推定主体。返回代码、目录证据及冲突标记。
    """
    return directory_material_entity(path, root, entities, business_registry, business_id, business_code)


def load_entities(path: str | Path) -> tuple[dict[str, Entity], list[Entity]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = wb.active.iter_rows(values_only=True)
        headers = {norm_text(value): index for index, value in enumerate(next(rows, ())) if value is not None}
        required = ["单位名称", "单位代码", "上级单位", "对应主业单位", "是否存续"]
        missing = [x for x in required if norm_text(x) not in headers]
        if missing: raise ValueError(f"主体清单缺少字段: {missing}")
        by_code: dict[str, Entity] = {}; missing_code: list[Entity] = []
        # Streaming once avoids reopening the XLSX row stream for every cell.
        for row, values in enumerate(rows, 2):
            name = values[headers[norm_text("单位名称")]]
            if not name: continue
            raw_code = values[headers[norm_text("单位代码")]]
            if isinstance(raw_code, float) and raw_code.is_integer(): raw_code = int(raw_code)
            code = str(raw_code).strip() if raw_code not in (None, "") else None
            def value(key): return str(values[headers[norm_text(key)]] or "").strip()
            entity = Entity(code, str(name).strip(), value("上级单位"), value("对应主业单位"), value("是否存续"), row)
            if code:
                if code in by_code: raise ValueError(f"主体代码重复: {code}")
                by_code[code] = entity
            else: missing_code.append(entity)
        return by_code, missing_code
    finally:
        wb.close()


def identify_entity(texts: list[str], entities: dict[str, Entity], aliases: dict) -> tuple[str | None, list[str], bool]:
    candidates: dict[str, list[str]] = {}
    # Resolve parent/subsidiary containment inside each evidence item first. A full
    # name in a separate path component or paragraph remains independent evidence.
    for text in texts:
        normalized = norm_text(text)
        full_matches = [
            (code, norm_text(entity.name))
            for code, entity in entities.items()
            if len(norm_text(entity.name)) >= 6 and norm_text(entity.name) in normalized
        ]
        maximal = [(code, name) for code, name in full_matches if not any(name != other and name in other for _, other in full_matches)]
        for code, _ in maximal:
            evidence = f"标准全称:{entities[code].name}"
            if evidence not in candidates.setdefault(code, []): candidates[code].append(evidence)
    if candidates:
        if len(candidates) == 1:
            code = next(iter(candidates)); return code, candidates[code], False
        return None, [x for values in candidates.values() for x in values], True
    for alias, info in aliases.items():
        code = info["entity_code"] if isinstance(info, dict) else info
        if norm_text(alias) and any(norm_text(alias) in norm_text(text) for text in texts):
            candidates.setdefault(code, []).append(f"确认简称:{alias}")
    # Explicit code tokens have the strongest deterministic evidence.
    for code in entities:
        if any(norm_text(code) == norm_text(t) for t in texts):
            candidates.setdefault(code, []).append(f"单位代码:{code}")
    if len(candidates) == 1:
        code = next(iter(candidates)); return code, candidates[code], False
    evidence = [x for values in candidates.values() for x in values]
    return None, evidence, len(candidates) > 1
