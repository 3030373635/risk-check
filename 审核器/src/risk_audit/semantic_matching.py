"""Structured, abstaining retrieval. These results never authorize an audit pass."""
from __future__ import annotations

import re
from risk_audit.util import measure_id_key, norm_text

_SPLIT = re.compile(r'[-—－]')
_MULTI = re.compile(r'[、,;；/\n]')
_GRADES = {c: str(i) for i, c in enumerate('一二三四五六七八九十', 1)}
_FUNCTIONS = ('资金', '预算', '核算', '税务', '薪酬', '培训', '车辆', '物资', '纪检', '法律', '科技', '设备')
_DEPARTMENT_FUNCTIONS = {
    '财务': r'财务|财会|会计', '审计': r'审计', '组织人事': r'组织|人事|人力资源',
    '党建': r'党建|党群', '纪检': r'纪检|监察', '营销': r'营销',
    '物资': r'物资|采购', '建设': r'建设|基建', '科技': r'科技|研发',
}


def parse_responsibility(text, unit_names, generic_units=()):
    parts = [p for p in _SPLIT.split(norm_text(text)) if p]
    names = {norm_text(n) for n in unit_names}
    generic = {norm_text(n) for n in generic_units}
    if len(parts) == 2:
        if parts[0] in names or parts[0] in generic or parts[0].endswith(('公司', '单位', '本部')):
            return None, '责任主体未明确分出部门和岗位'
        owner, dept, position, ownership = '', *parts, 'record_scope'
    elif len(parts) == 3:
        owner, dept, position = parts
        if owner in names:
            ownership = 'confirmed_current_entity'
        elif owner in generic:
            ownership = 'generic_owner_not_confirmed'
        else:
            return None, '责任单位名称未与本主体对应，不能忽略单位前缀推荐岗位'
    else:
        return None, '责任主体结构不明确，未按整句相似度推荐岗位'
    if not dept or not position or _MULTI.search(dept + position):
        return None, '部门或岗位含并列、缺项，需先完成结构识别'
    return {'owner': owner, 'department': dept, 'position': position, 'ownership': ownership}, ''


def position_conflicts(query, candidate):
    a, b = norm_text(query), norm_text(candidate)
    reasons = []
    def grade_number(g):
        if g.isdigit():return str(int(g))
        if g in _GRADES:return _GRADES[g]
        if re.fullmatch(r'[一二三四五六七八九]?十[一二三四五六七八九]?',g):
            tens,ones=g.split('十')
            return str(int(_GRADES.get(tens,'1'))*10+int(_GRADES.get(ones,'0')))
        return g
    grades = lambda t: {grade_number(g) for g in re.findall(r'([一二三四五六七八九十百零〇两]+|\d+)级职员', t)}
    ga, gb = grades(a), grades(b)
    if ga and gb and ga != gb:
        reasons.append('职员等级不同')
    leaders = lambda t: bool(re.search(r'经理|主任|书记|处长|科长|部长|总监|总工', t))
    if leaders(a) and leaders(b) and ('副' in a) != ('副' in b):
        reasons.append('正职与副职不同')
    roles = lambda t: set(re.findall(r'经办|审核|审批|复核', t))
    if roles(a) and roles(b) and roles(a) != roles(b):
        reasons.append('经办审核审批等角色不同')
    negative = lambda t: bool(re.search(r'不(?:负|承担|负责|参与)|无需|无须|免于', t))
    if negative(a) != negative(b):
        reasons.append('肯定与否定不同')
    fa, fb = {x for x in _FUNCTIONS if x in a}, {x for x in _FUNCTIONS if x in b}
    if fa and fb and fa.isdisjoint(fb):
        reasons.append('岗位专业方向不同')
    return reasons


def department_conflicts(query, candidate):
    """A shared generic job title cannot bridge clearly different functions."""
    functions=lambda text:{key for key,pattern in _DEPARTMENT_FUNCTIONS.items() if re.search(pattern,norm_text(text))}
    a,b=functions(query),functions(candidate)
    return ['部门明确职能不同'] if a and b and a.isdisjoint(b) else []


def responsibility_candidates(service, text, entries, *, scope, unit_names, generic_units=()):
    result = {'domain': 'responsibility', 'scope': list(scope), 'text': text,
              'candidates': [], 'decision': 'candidate_only', 'retrieval_version': 2,
              'excluded_candidates': [], 'input_candidate_count': len(entries)}
    if not service.config.get('enabled'):
        return {**result, 'status': 'disabled'}
    if 'responsibility' not in service.config.get('allowed_domains', []):
        return {**result, 'status': 'excluded', 'reason': '该领域不在本期语义辅助范围'}
    if len(scope) != 4 or not all(scope) or scope[2] == 'unknown':
        return {**result, 'status': 'abstained', 'reason': '主体、业务、矩阵类型或措施编号未确认'}
    query, reason = parse_responsibility(text, unit_names, generic_units)
    if not query:
        return {**result, 'status': 'abstained', 'reason': reason}
    result['query_fields'] = query
    compatible = []
    for entry in entries:
        reasons = []
        if list(entry.get('scope', [])) != list(scope):
            reasons.append('候选主体业务矩阵类型或措施编号不同')
        dept, pos = norm_text(entry.get('department', '')), norm_text(entry.get('position', ''))
        if not dept or not pos or _MULTI.search(dept + pos):
            reasons.append('候选部门岗位结构不完整或含并列岗位')
        # Department values can repeat the current unit prefix, but never a foreign one.
        if _SPLIT.search(dept):
            parsed, error = parse_responsibility(dept + '-' + pos, unit_names, generic_units)
            if error or parsed['ownership'] != 'confirmed_current_entity':
                reasons.append('候选部门中的单位前缀未确认')
            else:
                dept = parsed['department']
        reasons.extend(position_conflicts(query['position'], pos))
        reasons.extend(department_conflicts(query['department'], dept))
        if reasons:
            result['excluded_candidates'].append({**entry, 'reasons': reasons})
        else:
            compatible.append({**entry, 'comparison_department': dept, 'comparison_position': pos})
    if not compatible:
        return {**result, 'status': 'abstained', 'reason': '同编号记录中没有通过结构和关键差异检查的候选'}
    from risk_audit.semantic import LocalEncoder, SemanticUnavailable
    import sqlite3
    try:
        if service.error:
            raise SemanticUnavailable(service.error)
        if service.encoder is None:
            service.encoder = LocalEncoder(service.runtime['model_dir'], service.runtime['cache_path'],
                                           threads=service.config['threads'],
                                           expected_manifest=service.config['model_manifest_sha256'])
        # Keep oversize candidate fields from blocking every other candidate.
        max_tokens = service.encoder.manifest['max_tokens']
        if any(len(service.encoder.tokenizer.encode(query[k]).ids) > max_tokens for k in ('department', 'position')):
            raise SemanticUnavailable('责任主体字段超过模型长度上限，未截断后判定')
        eligible = []
        for entry in compatible:
            if any(len(service.encoder.tokenizer.encode(entry[k]).ids) > max_tokens
                   for k in ('comparison_department', 'comparison_position')):
                result['excluded_candidates'].append({**entry, 'reasons': ['候选字段超过模型长度上限，未截断']})
            else:
                eligible.append(entry)
        if not eligible:
            return {**result, 'status': 'abstained', 'reason': '候选字段均超过模型长度上限'}
        values = service.encoder.encode([query['department'], query['position'],
            *[e[k] for e in eligible for k in ('comparison_department', 'comparison_position')]])
        policy = service.config['retrieval']
        ranked = []
        for i, entry in enumerate(eligible):
            ds, ps = float(values[2 + i * 2] @ values[0]), float(values[3 + i * 2] @ values[1])
            exact_dept = query['department'] == entry['comparison_department']
            exact_pos = query['position'] == entry['comparison_position']
            ds, ps = (1.0 if exact_dept else ds), (1.0 if exact_pos else ps)
            item = {**entry, 'department_score': round(ds, 6), 'position_score': round(ps, 6),
                    'score': round(0.3 * ds + 0.7 * ps, 6), 'department_exact': exact_dept,
                    'position_exact': exact_pos, 'automatic_equivalence': False}
            if ps < policy['min_position_score'] or ds < policy['min_department_score']:
                result['excluded_candidates'].append({**item, 'reasons': ['部门或岗位相似度不足，仅为候选筛选阈值']})
            else:
                ranked.append(item)
        # Exact position identity is stronger retrieval evidence than sentence similarity.
        ranked.sort(key=lambda e: (-int(e['position_exact']), -e['score'], e['text']))
        result['candidates'] = ranked[:service.config['top_k']]
        result['eligible_candidate_count'] = len(ranked)
        result['status'] = 'ranked' if ranked else 'abstained'
        if not ranked:
            result['reason'] = '没有达到字段候选筛选条件的记录'
        result['ambiguous'] = len(ranked) > 1 and ranked[0]['position_exact'] == ranked[1]['position_exact'] and (
            ranked[0]['score'] - ranked[1]['score'] < policy['ambiguity_margin'])
        return result
    except (SemanticUnavailable, OSError, ValueError, sqlite3.Error) as exc:
        if service.encoder is None:
            service.error = str(exc)
        return {**result, 'status': 'unavailable', 'reason': str(exc)}


def filter_applicability(result, policy):
    from risk_audit.applicability import interpret
    interpretation = interpret(result['text'])
    ranked = result['candidates']
    result['retrieval_version'] = 2
    result['excluded_candidates'] = []
    # Unknown status notes and protected logic remain unknown even with a high score.
    protected = re.search(r'新增|是否|待(?:确认|核实|定)|不确定|不一定|并非|不是不|可能|[?？]|若|如果|但|然而|不过|除非', norm_text(result['text']))
    reason = '原文未明确适用结论，不能按最相近类别推定' if protected or interpretation.state == 'conflict' else ''
    if not reason and (not ranked or ranked[0]['score'] < policy['min_applicability_score']):
        reason = '未达到适用表达候选筛选条件'
    alternatives = [x for x in ranked[1:] if x['concept'] != ranked[0]['concept']] if ranked else []
    if not reason and alternatives and ranked[0]['score'] - alternatives[0]['score'] < policy['ambiguity_margin']:
        reason = '不同含义类别得分接近，无法推荐单一类别'
    if reason:
        result['excluded_candidates'] = [{**e, 'reasons': [reason]} for e in ranked]
        result.update(candidates=[], status='abstained', reason=reason)


def responsibility_coverage_v6(ctx, params):
    from collections import defaultdict
    from risk_audit.checks.quality_checks import responsibility_coverage_v5
    from risk_audit.semantic import get_assistant
    from risk_audit.readers.header_semantics import unit_names
    issues = responsibility_coverage_v5(ctx, params, include_semantic=False)
    assistant = get_assistant(ctx.resources)
    if assistant is None:
        return issues
    names, hashes, related = defaultdict(set), {}, defaultdict(dict)
    for f in ctx.files:
        hashes[str(f.relative_path)] = f.sha256
        if f.entity_code and not f.entity_conflict:
            names[f.entity_code].update(unit_names(f))
    for code, entity in ctx.entities.items():
        names[code].add(norm_text(entity.name))
    for row in ctx.all_records:
        if row.record_type != 'position_duty' or not row.value('department') or not row.value('position'):
            continue
        scope = (row.entity_code, row.business_code, row.variant_id, measure_id_key(row.value('measure_id')))
        pair = (row.value('department'), row.value('position'))
        entry = related[scope].setdefault(pair, {'text': '-'.join(pair), 'department': pair[0], 'position': pair[1],
            'concept': 'same_measure_department_position', 'scope': list(scope), 'sources': []})
        entry['sources'].append({'file': row.file_path, 'sha256': hashes.get(row.file_path),
            'sheet': row.sheet, 'row': row.row, 'department_cell': row.fields['department'].coordinate,
            'position_cell': row.fields['position'].coordinate, 'measure_id': row.value('measure_id')})
    for issue in issues:
        e, row = issue['evidence'], issue['record']
        if e.get('issue_type') not in {'coverage_mapping_unconfirmed', 'specific_responsibility_missing'}:
            continue
        scope = (row.entity_code, row.business_code, row.variant_id, measure_id_key(row.value('measure_id')))
        entries = sorted(related[scope].values(), key=lambda x: x['text'])
        e['semantic_candidates'] = [assistant.suggest_responsibility(item, entries, scope=scope,
            unit_names=names[row.entity_code], generic_units=params['generic_unit_names'])
            for item in (e.get('responsibility_items') or e.get('missing_responsibilities') or [])]
    return issues
