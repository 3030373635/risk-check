"""Constrained header recognition; company qualifiers are never discarded."""
from __future__ import annotations

import re
import unicodedata
from risk_audit.util import norm_text


BRACKET_PAIRS = {")": "(", "）": "（", "]": "[", "】": "【"}
HEADER_REQUIREMENTS = {
    "matrix": {"control_measure", "responsibility", "measure_id"},
    "position_duty": {"department", "position", "duty", "measure_id"},
    "system_rule": {"system_rule_name", "system_rule_content", "measure_id"},
    "incompatible_position": {"incompatible_roles", "position_a", "position_b"},
}


def _without_trailing_group(text: str) -> str | None:
    """删除末尾成对括号区域；text 为保留换行的表头，无法分离时返回 None。"""
    if not text or text[-1] not in BRACKET_PAIRS:
        return None
    stack = []
    for index in range(len(text) - 1, -1, -1):
        char = text[index]
        if char in BRACKET_PAIRS:
            stack.append(BRACKET_PAIRS[char])
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack and index > 0:
                return text[:index].rstrip()
    return None


def _header_candidates(text: str, reverse: dict[str, str]) -> list[str]:
    """生成结构化表头候选；text 为原文，reverse 为字段别名到字段ID的映射。"""
    raw = unicodedata.normalize("NFKC", str(text or "")).strip()
    candidates = []

    def add(value: str) -> None:
        """追加未出现的标准候选；value 为保留业务字段部分的文本。"""
        normalized = norm_text(value)
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    add(raw)
    current = raw
    while (base := _without_trailing_group(current)) is not None:
        add(base)
        current = base
    # 从最右侧边界开始，优先保留较完整的字段名或单位限定。
    boundaries = [index for index, char in enumerate(raw) if char in "\r\n:："]
    for index in reversed(boundaries):
        tail = norm_text(raw[index + 1:])
        # 两个已知字段用换行拼在同一格时属于歧义表头，不能取第一个字段。
        if tail in reverse:
            continue
        current = raw[:index].rstrip()
        add(current)
        while (base := _without_trailing_group(current)) is not None:
            add(base)
            current = base
    return candidates


def unit_names(file):
    """提取有归属依据的单位名称；file 为包含标准全称和确认简称的文件记录。"""
    names = {norm_text(n) for n in getattr(file,'_confirmed_unit_names',[])}
    for item in file.entity_evidence:
        kind, _, name = item.partition(':')
        if kind in {'标准全称', '确认简称'} and name:
            names.add(norm_text(name))
    for name in list(names):
        short = name.removeprefix('国网湖南省电力有限公司').removeprefix('国网湖南')
        names.add(short)
        # 名册的中心名称可附带括号说明和“本部”，表头通常只写中心主体名称。
        center = re.fullmatch(r'(.+?中心)(?:\([^()]+\))?(?:本部)?', short)
        if center:
            names.add(center[1])
        match = re.fullmatch(r'(.+?)(县|市)?供电分公司(本部)?', short)
        if match:
            place = match[1]
            names.update({place+'公司',place+'供电公司','国网'+place+'供电公司'})
            if match[2]:names.update({place+match[2]+'公司',place+match[2]+'供电公司','国网'+place+match[2]+'供电公司'})
            if match[3]:names.add(place+'本部')
        if '有限公司' in short:
            tail = short.split('有限公司',1)[1]
            if tail: names.add(tail)
    identity = file.preservation.get('entity_identity', {})
    if identity.get('entity_registry_status') in {'not_listed', 'missing_code'}:
        # 清单外报送全称只用于本组名称核对，不扩展简称或推定名册身份。
        names.add(norm_text(identity['entity_name']))
    return {n for n in names if len(n)>=3}


def header_owner_names(file):
    # Place-only forms are acceptable in headers, not as body-text prefixes
    # that could accidentally strip part of a real department name.
    names = unit_names(file)
    for name in list(names):
        if name.endswith('公司') and not name.endswith('分公司') and 4 <= len(name) <= 6:
            names.add(name[:-2])
    return {n for n in names if len(n)>=2}


def body_owner_evidence(file, values):
    """Use complete, scoped unit names at responsibility-item boundaries.

    A wrong header does not create a global alias. Explicit inapplicability
    notes are not responsibility items and do not dilute the ownership ratio.

    参数 file 为单位归属记录，values 为业务行号和责任主体有效正文的二元组。
    """
    names = unit_names(file)
    name_pattern = '|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    local = re.compile(r'(?:^|[,、;；])(?:\d+[.、])?(?:' + name_pattern + r')(?=[-—:：])') if names else None
    # 中心与公司同样可以构成外单位主体，出现明确外单位时禁止按占比校正表头。
    units = re.compile(r'(?:^|[,、;；])(?:\d+[.、])?([^,、;；\s—:：-]{2,70}(?:公司|本部|中心))(?=[-—:：])')
    current, foreign, unknown, notes = [], [], [], []
    for row, value in values:
        t = norm_text(value)
        if not t: continue
        if re.fullmatch(r'此条不适用县、支公司|市公司集中管控,县公司不适用|(?:不)?适用', t):
            notes.append(row); continue
        other = [m[1] for m in units.finditer(t) if m[1] not in names]
        if other: foreign.append({'row': row, 'units': other})
        if local and local.search(t): current.append(row)
        else: unknown.append(row)
    total = len(current) + len(unknown)
    confirmed = len(current) >= 2 and len(current) >= .9 * total and not foreign
    return {'confirmed': confirmed, 'current_unit_body_rows': current,
            'foreign_unit_body_rows': foreign, 'unknown_body_rows': unknown,
            'non_responsibility_rows': notes, 'scoped_unit_names': sorted(names)}


def _match_header(value: str, sheet_type: str, reverse: dict[str, str], *, extended: bool = False) -> tuple[str | None, str]:
    """匹配单个表头候选；value 为候选，sheet_type 为表类型，reverse 为别名映射，extended 控制扩展规则。"""
    if sheet_type=='matrix':
        if extended:
            m=re.fullmatch(r'(.+?)[-—:：]?(控制措施|控制载体)',value)
            if m:
                owner=m[1].rstrip('-—:：')
                if owner.endswith(('公司','分公司','本部')):
                    return {'控制措施':'control_measure','控制载体':'carrier'}[m[2]],owner
        if '是否适用' in value:
            # 关键词在表头中的任意位置均构成适用性候选，前缀仅保留作为多列择优信息。
            owner=value.partition('是否适用')[0].rstrip('-—:：').removesuffix('矩阵')
            return 'applicability',owner
        # “原因”和“理由”是同一适用性说明字段，隐藏列也必须按表头正常识别。
        m=re.fullmatch(r'(.*?)(?:修改备注|不适用原因|不适用理由)',value)
        if m and (not m[1] or m[1].endswith(('公司','分公司','本部'))):return 'applicability_reason',m[1]
        if value in {'备注(适用情况)','矩阵适用情况','矩阵变化情况是否适用','是否适用(不适用填“否”)','是否适用(不适用填"否")'}:
            return 'applicability',''
        # 适用性表头允许在中文或英文逗号后追加填写说明，单位限定仍由前缀保留。
        m=re.fullmatch(r'(.*?)(?:是否适用(?:及原因)?|适用情况)(?:[,，].*)?',value)
        if m:
            owner=m[1].rstrip('-—:：').removesuffix('矩阵')
            # “县公司/区县公司”表示适用层级而非具体单位名称，不参与当前主体简称核对。
            if owner in {'县公司', '区县公司'}:
                owner = ''
            if not owner or re.fullmatch(r'[一-鿿]{2,40}',owner):
                return 'applicability',owner
        m=re.fullmatch(r'(.*?)责任主体(?:\((市公司|原集体企业|需根据实际情况调整)\))?',value)
        if m:
            owner=m[1].rstrip('-—:：')
            if not owner or owner.endswith(('公司','分公司','本部','中心')):
                return 'responsibility',owner
        # 括号内明确单位全称与单位前缀等价，必须保留该全称供选列时核对归属。
        m = re.fullmatch(r'责任主体\(([^()]+)\)', value)
        if m and m[1].endswith(('公司', '分公司', '本部', '中心')):
            return 'responsibility', m[1]
        if value in {'公司控制载体','控制载体(原集体企业)','控制载体(市公司)'}:
            return 'carrier',''
    if sheet_type == 'position_duty':
        # 人员姓名表头写法不统一，包含“姓名”即按人员姓名字段读取。
        if '姓名' in value:
            return 'person_names', ''
        if re.fullmatch(r'部门\([^()]{1,30}\)', value):
            return 'department', ''
    return reverse.get(value),''


def header_spec(text, sheet_type, reverse, *, extended=False):
    """识别字段并保留限定单位；text 为表头，sheet_type 为类型，reverse 为别名映射，extended 开启扩展识别。"""
    for value in _header_candidates(text, reverse):
        field, owner = _match_header(value, sheet_type, reverse, extended=extended)
        if field:
            return field, owner
    return None, ''


def fuzzy_header_spec(text: str, reverse: dict[str, str]) -> tuple[str | None, str]:
    """按最长字段别名模糊匹配；text 为表头原文，reverse 为字段别名映射。"""
    value = norm_text(text)
    matches = [(len(alias), alias, field) for alias, field in reverse.items() if alias and alias in value]
    if not matches:
        return None, ''
    longest = max(length for length, _, _ in matches)
    best = {(alias, field) for length, alias, field in matches if length == longest}
    fields = {field for _, field in best}
    if len(fields) != 1:
        return None, ''
    alias, field = next(iter(best))
    prefix = value[:value.find(alias)].rstrip('-—:：')
    owner = prefix if field in {'control_measure', 'carrier', 'responsibility'} and prefix.endswith(('公司', '分公司', '本部', '中心')) else ''
    return field, owner


def header_row_matches(values: list[str], sheet_type: str, reverse: dict[str, str], *, extended: bool = False, allow_fuzzy: bool = False) -> list[tuple[str | None, str]]:
    """匹配同一行表头；values 为各列文本，sheet_type 为表类型，reverse 为别名映射，allow_fuzzy 控制回退。"""
    exact = [header_spec(value, sheet_type, reverse, extended=extended) for value in values]
    if not allow_fuzzy:
        return exact
    combined = [matched if matched[0] else fuzzy_header_spec(value, reverse) for value, matched in zip(values, exact)]
    fields = {field for field, _ in combined if field}
    # 模糊命中只在同一行形成完整核心字段集时生效，零散相似文字仍按精确结果处理。
    return combined if HEADER_REQUIREMENTS.get(sheet_type, set()) <= fields else exact


def normalized_header(text, sheet_type, aliases):
    reverse={norm_text(v):k for k,vs in aliases.get(sheet_type,{}).items() for v in vs}
    return header_spec(text,sheet_type,reverse)[0]
