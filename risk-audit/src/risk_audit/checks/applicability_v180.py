"""确认稿第14条：按完整责任主体组确认适用范围，并复用于责任覆盖。"""
from __future__ import annotations

from dataclasses import replace
import re
import unicodedata
from typing import Any

from risk_audit.applicability import interpret
from risk_audit.checks.coverage_v170 import responsibility_coverage_v7
from risk_audit.util import norm_text


def _configuration(resources: dict, params: dict) -> dict:
    """获取共享适用范围配置。

    参数 resources 为规则资源，params 为本次能力参数；显式 restrictions 优先。
    """
    if 'restrictions' in params:
        return params
    return resources.get('responsibility_applicability', {})


def _matches(text: str, patterns: list[str]) -> bool:
    """完整匹配任一正则。

    参数 text 为规范化的单个责任主体或名册全名，patterns 为完整匹配正则列表。
    """
    return any(re.fullmatch(pattern, text) is not None for pattern in patterns)


def _validate_configuration(config: dict) -> None:
    """防止错误配置被解释为明确排除。

    参数 config 为完整适用范围配置；缺失范围、错误参数类型或非法正则会抛出 ValueError。
    """
    rules = config.get('restrictions', [])
    if not isinstance(rules, list):
        raise ValueError('restrictions 必须为列表')
    identifiers = set()
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get('id'), str) or not rule['id']:
            raise ValueError('每个限定范围必须具有有效 id')
        if rule['id'] in identifiers:
            raise ValueError('限定范围 id 必须唯一')
        identifiers.add(rule['id'])
        # entity_patterns 缺失与明确空列表不是同一含义；只有后者能表示全主体排除。
        for key in ('patterns', 'entity_patterns'):
            patterns = rule.get(key)
            if not isinstance(patterns, list) or any(not isinstance(pattern, str) or not pattern for pattern in patterns):
                raise ValueError(f'{key} 必须明确提供完整正则列表')
            for pattern in patterns:
                re.compile(pattern)
        required = rule.get('required_patterns', [])
        if not isinstance(required, list) or any(not isinstance(pattern, str) or not pattern for pattern in required):
            raise ValueError('required_patterns 必须为完整正则列表')
        for pattern in required:
            re.compile(pattern)
        if not rule['patterns']:
            raise ValueError('责任主体 patterns 不得为空')
    known = config.get('known_entity_patterns', [])
    if not isinstance(known, list) or any(not isinstance(pattern, str) or not pattern for pattern in known):
        raise ValueError('known_entity_patterns 必须为完整正则列表')
    for pattern in known:
        re.compile(pattern)


def _owner(item: str, names: dict[str, str], aliases: dict) -> str | None:
    """确认单个责任条目的名册主体。

    参数 item 为完整责任条目，names 为代码到名册全名的映射，aliases 为已确认简称资源。
    仅接受可唯一对应名册真实代码的完整单位前缀，不推断未知简称。
    """
    # 单位全名及其明确部门、岗位后缀可以确认主体，不能截取全名中的省公司部分。
    owners = {code for code, name in names.items() if name and re.fullmatch(
        re.escape(name) + r'(?:[-/][^-/,;、]+){0,2}', item)}
    for alias, info in aliases.items():
        if isinstance(info, dict):
            if info.get('confirmed') is not True:
                continue
            code = info.get('entity_code')
        else:
            code = info
        name = norm_text(alias)
        if name and isinstance(code, str) and code in names and names[code] and re.fullmatch(
                re.escape(name) + r'(?:[-/][^-/,;、]+){0,2}', item):
            owners.add(code)
    # 规范化后同一简称若对应不同单位，保留未知而不是任取第一个匹配项。
    return next(iter(owners)) if len(owners) == 1 else None


def _context_resources(ctx: Any) -> dict:
    """以实际上下文名册建立本次资源副本。

    参数 ctx 为审核上下文；其 entities 是主体归属的权威来源，不修改原资源。
    """
    names = {code: norm_text(entity.name) for code, entity in ctx.entities.items()}
    return {**ctx.resources, '_applicability_entities': names}


def _restriction_item_matches(item: str, owner: str | None, names: dict[str, str], rule: dict) -> bool:
    """确认责任条目属于限定规则且不与实际名册身份冲突。

    参数 item 为完整责任条目，owner 为已确认名册主体代码，names 为代码到全名的映射，
    rule 为含 patterns 和 entity_patterns 的单项限定配置。
    """
    if not _matches(item, rule['patterns']):
        return False
    # 已确认独立会计主体的全名或简称优先于泛称部门正则，不能被“国网*部”吞作省本级部门。
    if owner is not None and rule['entity_patterns']:
        return _matches(names[owner], rule['entity_patterns'])
    return True


def responsibility_applicability(row: Any, resources: dict, params: dict) -> tuple[bool | None, dict]:
    """返回本单位是否属于完整责任组限定范围及可复用依据。

    参数 row 为矩阵记录；resources 为规则资源及 _applicability_entities 名册全名映射；
    params 为包含 restrictions 和可选 known_entity_patterns 的配置。
    每个 restriction 含 id、patterns（完整责任条目正则）和 entity_patterns（名册全名正则），
    可选 required_patterns 要求每个必需正则至少完整匹配一个条目，用于必须并列出现的组合主体。
    返回 True 表示本单位属于限定范围，或普通完整组不排除本单位；是否具有强适用结论
    须检查依据的 restriction_ids，空列表时保留材料本身的适用性；False 表示已确认排除；
    None 表示来源、句式、主体归属或配置冲突尚未确认，调用方必须保留检查限制。
    """
    value = row.fields.get('responsibility')
    evidence = {'field': 'responsibility', 'responsibility_text': value.current if value else '',
                'source_cell': value.coordinate if value else '', 'responsibility_items': [],
                'restriction_ids': [], 'entity_name': '', 'unavailable_reason': ''}
    if value is None or value.state == 'formula_no_cache':
        evidence['unavailable_reason'] = '责任主体栏目尚未读到有效值，无法确认整组适用范围。'
        return None, evidence

    # 必须先按原文换行分组，norm_text 会移除换行；斜杠属于单位/部门/岗位而非组分隔号。
    text = unicodedata.normalize('NFKC', value.current)
    items = [norm_text(item) for item in re.split(r'[;；,，、\r\n]+', text) if norm_text(item)]
    evidence['responsibility_items'] = items
    if not items or any(re.search(r'负责(?!人)|承担|适用|待(?:确认|核实|定)|未知|其他|可能|包括|仅|等', item)
                            or item.endswith(('-', '/', '—')) for item in items):
        evidence['unavailable_reason'] = '责任主体整组为空、含未确认描述或句式不完整，不能据局部片段限定适用主体。'
        return None, evidence

    config = _configuration(resources, params)
    names = {code: norm_text(name) for code, name in resources.get('_applicability_entities', {}).items()}
    name = names.get(row.entity_code, '')
    evidence['entity_name'] = name
    try:
        _validate_configuration(config)
        restrictions = config.get('restrictions', [])
        if not restrictions:
            evidence['unavailable_reason'] = '责任主体适用范围配置尚未提供。'
            return None, evidence
        owners = [_owner(item, names, resources.get('entity_aliases', {})) for item in items]
        # “仅出现”必须覆盖每个责任条目，整组出现任何其他主体就不能命中该限定规则。
        matched = [rule for rule in restrictions if rule.get('patterns')
                   and all(_restriction_item_matches(item, owner, names, rule) for item, owner in zip(items, owners))
                   and all(any(re.fullmatch(pattern, item) is not None for item in items)
                           for pattern in rule.get('required_patterns', []))]
        evidence['restriction_ids'] = [rule['id'] for rule in matched]
        if matched:
            # 北京电力交易中心等明确空适用范围不依赖本单位类别即可判定排除。
            if all(not rule.get('entity_patterns') for rule in matched):
                evidence['matching_basis'] = '完整责任组仅出现配置中明确对所有主体不适用的责任主体。'
                return False, evidence
            if not name:
                evidence['unavailable_reason'] = '本单位尚无主体清单确认的完整单位名称。'
                return None, evidence
            known_patterns = [pattern for rule in restrictions for pattern in rule.get('entity_patterns', [])]
            known_patterns.extend(config.get('known_entity_patterns', []))
            if not _matches(name, known_patterns):
                evidence['unavailable_reason'] = '主体清单已读取，但本单位所属适用类别尚未确认。'
                return None, evidence
            decisions = {_matches(name, rule.get('entity_patterns', [])) for rule in matched}
            if len(decisions) != 1:
                evidence['unavailable_reason'] = '完整责任组命中互相冲突的限定配置，需核实适用范围。'
                return None, evidence
            evidence['matching_basis'] = '整组责任条目均完整匹配限定规则，并按主体清单全名确认本单位类别。'
            return next(iter(decisions)), evidence

        # 未命中限定规则时，只认可每个条目均已确认、且整组实际包含本单位的混合组。
        known_items = [(owner, any(_restriction_item_matches(item, owner, names, rule)
                       for rule in restrictions)) for item, owner in zip(items, owners)]
        if name and all(owner is not None or known for owner, known in known_items):
            if any(owner == row.entity_code for owner, _ in known_items):
                evidence['matching_basis'] = '整组均为已确认责任主体且明确包含本单位，不适用单一主体限定。'
                return True, evidence
        evidence['unavailable_reason'] = '责任主体整组尚无完整确认的限定范围或本单位责任，不能据单个片段自动通过或排除。'
        return None, evidence
    except (KeyError, TypeError, ValueError, re.error) as exc:
        evidence['unavailable_reason'] = f'责任主体适用范围配置无法完整解释：{exc}'
        return None, evidence


def _unresolved(row: Any, evidence: dict) -> dict:
    """构造待核实的检查限制。

    参数 row 为原始矩阵记录，evidence 为来源、整组判断及未确认原因。
    """
    return {'record': row, 'kind': 'limitation', 'evidence': {
        **evidence, 'issue_type': 'responsibility_applicability_unresolved',
        'cause_type': 'interpretation_incomplete', 'automatic_equivalence': False}}


def responsibility_applicability_check(ctx: Any, params: dict) -> list[dict]:
    """检查第14条责任主体范围与材料明确适用答案的冲突。

    参数 ctx 为包含矩阵记录、主体清单和资源的审核上下文，params 为适用范围配置。
    仅明确是/适用、否/不适用与已确认六类限定范围冲突时形成 violation，未确认责任组形成 limitation。
    普通完整本地或混合责任组不强制适用，沿用材料原有答案及其他适用性规则。
    """
    resources = _context_resources(ctx)
    issues = []
    for row in ctx.records:
        decision, evidence = responsibility_applicability(row, resources, params)
        value = row.fields.get('applicability')
        evidence.update(applicability_text=value.current if value else '',
                        applicability_cell=value.coordinate if value else '',
                        expected_applicability=decision)
        if decision is None:
            issues.append(_unresolved(row, evidence))
            continue
        if not evidence['restriction_ids']:
            # 完整普通或混合责任组仅说明不排除本单位，不能新增六类限定条件之外的适用强制。
            continue
        answer = interpret(value.current) if value and value.state != 'formula_no_cache' else None
        # R14 不使用模板采用说明或模糊语义推定；原文必须有无矛盾的明确适用答案。
        if answer is None or answer.state not in {'explicit_positive', 'explicit_negative'}:
            evidence['unavailable_reason'] = '材料尚未提供已确认的明确适用答案，未将责任范围推定为审核通过。'
            issues.append(_unresolved(row, evidence))
            continue
        actual = answer.decision == 'applicable'
        if actual != decision:
            issues.append({'record': row, 'kind': 'violation', 'evidence': {
                **evidence, 'issue_type': 'responsibility_applicability_conflict',
                'actual_applicability': actual}})
    return issues


def responsibility_coverage_v8(ctx: Any, params: dict) -> list[dict]:
    """把第14条同一责任范围结论用于第5条岗位责任覆盖。

    参数 ctx 为审核上下文，params 为原责任覆盖参数，及可选 responsibility_applicability 配置。
    明确排除的措施跳过覆盖；未知整组留下检查限制；已确认适用的限定措施进入 v7 覆盖。
    """
    resources = _context_resources(ctx)
    config = params.get('responsibility_applicability', {})
    rows = []
    unresolved = []
    originals = {row.record_id: row for row in ctx.records}
    for row in ctx.records:
        decision, evidence = responsibility_applicability(row, resources, config)
        if decision is False:
            continue
        if decision is None:
            unresolved.append(_unresolved(row, evidence))
        if decision is True and evidence['restriction_ids']:
            value = row.fields.get('applicability')
            if value is not None:
                # 错误填写“否”不能豁免已确认本单位适用的责任覆盖；R14 单独输出填写冲突。
                row = replace(row, fields={**row.fields, 'applicability': replace(value, current='适用', state='value')})
        rows.append(row)
    coverage_params = {key: value for key, value in params.items() if key != 'responsibility_applicability'}
    issues = responsibility_coverage_v7(replace(ctx, records=rows, resources=resources), coverage_params)
    for issue in issues:
        # 归还原始记录，避免虚拟适用答案进入材料写回及最终依据。
        issue['record'] = originals[issue['record'].record_id]
    return issues + unresolved
