"""Versioned responsibility facts: actors, later denials and narrow grammar fixes.

Version 1 facts/operators remain unchanged for historical rule packs.
"""
from copy import deepcopy
from functools import lru_cache
import re

from risk_audit.responsibility import parse_responsibility, ROLE, DELEGATED, QUOTE, PREDICATE
from risk_audit.util import norm_text

ACTOR = re.compile(r'(?:^|[,，])(?:由)?(?P<actor>本岗位|本人|本部门|供应商|承包商|其他部门|其他岗位|他人|第三方)(?=对|负责|应|需|承担|负|不)')
LOCAL = {'本岗位', '本人', '本部门'}
DENIAL = re.compile(r'(?:本岗位|本人|本部门)(?:并)?(?:不|无需|无须|不必|不需要)(?:再)?(?:承担|负有|负|履行)(?P<object>上述|前述|该项|该|这些|任何|全部|主体|审核|审批)?责任')
NOMINAL_REQUIREMENT = re.compile(r'(?:管理|技术|合同|制度|规范|标准)要求(?:的)?落实(?:情况|效果)')


@lru_cache(maxsize=8192)
def parse_v2(text, attributes=()):
    source = norm_text(text)
    analysis = list(source); rewrites = []
    # Parallel objects/actions can share one final liability predicate. Keep
    # independent liability clauses and sentence boundaries separate.
    for sentence in re.finditer(r'[^。!?！？]+', source):
        predicates = list(PREDICATE.finditer(sentence[0]))
        broken = re.search(r'负进行(?:复核|审核)|(?:主体|审核|审批)在责任|付(?:全面|主体|审核|审批)责任', sentence[0])
        complete_roles = sum(len(ROLE.findall(p[0])) for p in predicates) == len(ROLE.findall(sentence[0]))
        actor_switch = re.search(r'[;；](?:本岗位|本人|本部门)|由(?:供应商|承包商|其他部门|他人)|(?:供应商|承包商|其他部门|他人)(?:负责|承担|保证|处理)', sentence[0])
        if sentence[0].startswith('对') and len(predicates) == 1 and complete_roles and not broken and not actor_switch:
            for offset, char in enumerate(sentence[0][:predicates[0].start()]):
                if char in ';；':
                    analysis[sentence.start() + offset] = ','
                    rewrites.append({'kind': 'parallel_clause', 'start': sentence.start() + offset})
    # A complete 对...的属性 fragment immediately followed by a lone liability
    # predicate is an accidental stop, not a missing responsibility assertion.
    for match in re.finditer(r'对[^。;；!?！？]{2,100}。(?=(?:负有?|承担|具有)(?:主体|审核|审批)责任)', source):
        analysis[match.end() - 1] = ','
        rewrites.append({'kind': 'adjacent_object_fragment', 'start': match.end() - 1})
    # 规范管理/规范会议组织 are actions, not an explicit 规范性 attribute.
    for match in re.finditer(r'规范(?=管理|自付单据|会议组织|会议报销|做好报销)', source):
        analysis[match.start():match.end()] = '··'
        rewrites.append({'kind': 'action_not_quality', 'start': match.start(), 'text': match[0]})
    facts = deepcopy(parse_responsibility(''.join(analysis), attributes))
    facts['parser_version'] = 2
    normalized = facts['normalized_text'] = source
    facts['syntax_evidence'] = rewrites
    quotes = [(m.start(), m.end()) for m in QUOTE.finditer(normalized)]
    quoted = lambda pos: any(a <= pos < b for a, b in quotes)
    denials = [{'start': m.start(), 'end': m.end(), 'text': m[0],
                'role': m['object'] + '责任' if m['object'] in {'主体', '审核', '审批'} else None}
               for m in DENIAL.finditer(normalized) if not quoted(m.start())]
    facts['self_denials'] = denials
    for s in facts['statements']:
        s['text'] = source[s['start']:s['end']]
        prefix = normalized[s['start']:s['predicate_start']]
        masked = NOMINAL_REQUIREMENT.sub(lambda m: '·' * len(m[0]), prefix)
        s['delegated'] = bool(DELEGATED.search(masked + s['predicate']))
        actor_matches = [m for m in ACTOR.finditer(prefix) if not quoted(s['start'] + m.start())]
        actor = actor_matches[-1]['actor'] if actor_matches else ''
        s['actor'] = actor or '本行默认承担者'
        s['other_actor'] = bool(actor and actor not in LOCAL)
        s['actor_basis'] = actor_matches[-1][0] if actor_matches else '未出现明确的其他承担者'
        s['denied_later'] = [d for d in denials if d['start'] >= s['end']
                             and (not d['role'] or d['role'] in s['roles'])]
        # Explicit quality can follow the liability predicate: 负审核责任，
        # 确保合同合规. A separate sentence or a mere action does not supply it.
        suffix = re.split(r'[。;；!?！？]', source[s['end']:], maxsplit=1)[0]
        assurance = re.match(r'^[,，](?:并)?(?:确保|保证|保障)(.+)$', suffix)
        if assurance and attributes:
            terms = set(attributes) | {x[:-1] for x in attributes if x.endswith('性')}
            terms -= {'规范', '匹配'}
            quality_re = re.compile('|'.join(re.escape(x) for x in sorted(terms, key=len, reverse=True)))
            qualities = quality_re.findall(assurance[1])
            # A negative outcome in a parallel object (收益不流失) does not
            # negate 条款合规. Negation within a quality-bearing fragment is
            # still unresolved; conditional/delegated scope remains global.
            fragments = re.split(r'[、,，]', assurance[1])
            uncertain = re.search(r'是否|可能|待核实|待确认|由.*(?:部门|供应商)', assurance[1]) or any(
                quality_re.search(fragment) and re.search(r'不|未|无需|无法', fragment)
                for fragment in fragments)
            if qualities and not uncertain:
                s['qualities'].extend(qualities)
                s['postposed_quality'] = {'text': suffix, 'start': s['end']}
            elif qualities:
                s['quality_link_unresolved'] = True
        # 匹配 can name the operation being held accountable, while 准确性 is
        # its attribute. Do not delete both as if they were two attributes.
        if (not s['object_present'] and re.fullmatch(r'(?:本岗位)?对匹配(?:的)?准确性', prefix)
                and any(x in attributes for x in ('准确性',))):
            s['object_present'] = True
            s['object_basis'] = {'text': '匹配', 'kind': 'operation_object',
                                 'start': s['start'] + prefix.index('匹配')}
            s['qualities'] = [x for x in s['qualities'] if x != '匹配']
    facts['malformed_spans'] = []
    for match in re.finditer(r'(?:负有?|承担)(?:主体|审核|审批)在责任|付(?:全面|主体|审核|审批)责任|负进行(?:复核|审核)', normalized):
        if not quoted(match.start()):
            facts['malformed_spans'].append({'text': match[0], 'start': match.start(), 'end': match.end()})
    if not facts['statements'] and facts['role_mentions']:
        # A terminal bare role after an action is not an assertion. Unknown
        # connectors (承担未知句式...) remain a program limitation.
        for match in re.finditer(r'[^。;；]*?(?:主体|审核|审批)责任(?=[。;；]|$)', normalized):
            if (not quoted(match.end() - 3) and not re.search(r'承担|负|具有|有.*责任|履行|落实.*责任|责任[:：]', match[0])):
                facts['malformed_spans'].append({'text': match[0], 'start': match.start(), 'end': match.end()})
    return facts


def own_statements(facts):
    return [s for s in facts['statements'] if not any(s.get(k) for k in
            ('negative', 'quoted', 'delegated', 'other_actor', 'denied_later'))]


def contradiction(facts):
    return any(s['denied_later'] and not any(s.get(k) for k in
               ('negative', 'quoted', 'delegated', 'other_actor')) for s in facts['statements'])


def only_other_actor(facts):
    return any(s['other_actor'] and not s['quoted'] for s in facts['statements']) and not own_statements(facts)


def responsibility_phrase_v6(ctx, params):
    out = []
    for row in ctx.records:
        field = row.fields.get('duty')
        if field is None or field.state == 'formula_no_cache':
            out.append({'record': row, 'kind': 'review', 'evidence': {'issue_type': 'responsibility_unavailable', 'field': 'duty'}})
            continue
        if not row.value('duty'): continue
        facts = parse_v2(row.value('duty'), tuple(params['attributes']))
        positive = own_statements(facts)
        kind = 'violation'
        if contradiction(facts):
            category = 'responsibility_self_contradiction'
        elif any(s['qualities'] and s['object_present'] for s in positive):
            continue
        elif only_other_actor(facts):
            category = 'responsibility_other_actor'
        elif facts['malformed_spans']:
            category = 'responsibility_expression_malformed'
        elif any(s.get('quality_link_unresolved') for s in positive):
            category, kind = 'responsibility_structure_unresolved', 'review'
        elif positive and not any(s['qualities'] for s in positive) and not (
                set(facts['role_mentions']) - {r for s in facts['statements'] for r in s['roles']}):
            category = 'responsibility_quality_missing'
        elif (not facts['statements'] and not facts['role_mentions'] and
              not re.search(r'(?:负有?|承担|担负|具有|履行|落实)[^。;；]{0,35}责任', facts['normalized_text'])):
            category = 'responsibility_assertion_missing'
        elif facts['quality_terms'] and (positive or facts['role_mentions'] or '责任' in facts['normalized_text']):
            if facts['statements'] and all(s['negative'] for s in facts['statements']):
                category = 'responsibility_assertion_missing'
            else:
                category, kind = 'responsibility_structure_unresolved', 'review'
        else:
            category = 'responsibility_assertion_missing'
        out.append({'record': row, 'kind': kind, 'evidence': {'issue_type': category, 'field': 'duty',
                    'responsibility_facts': facts,
                    'interpretation_status': 'unsupported' if kind == 'review' else 'decided',
                    'unavailable_reason': '责任对象、属性及承担责任的关联句式尚未解析完整' if kind == 'review' else ''}})
    return out


def value_mapping_v3(ctx, params):
    out = []
    mapped_types = {t for terms in params['mapping'].values() for t in terms}
    for row in ctx.records:
        role = norm_text(row.value(params['source_field']))
        field = row.fields.get(params['text_field'])
        expected = params['mapping'].get(role)
        if expected is None:
            out.append({'record': row, 'kind': 'review', 'evidence': {'field': 'role', 'unavailable_reason': '角色未明确，未从岗位或动作词推断'}})
            continue
        if field is None or field.state == 'formula_no_cache':
            out.append({'record': row, 'kind': 'review', 'evidence': {'issue_type': 'responsibility_unavailable', 'field': params['text_field']}})
            continue
        if not row.value(params['text_field']): continue
        facts = parse_v2(row.value(params['text_field']))
        actual = sorted({r for s in own_statements(facts) for r in s['roles'] if r in mapped_types})
        represented = {r for s in facts['statements'] for r in s['roles']}
        unsupported = set(facts['role_mentions']) - represented
        evidence = {'role': role, 'expected': '/'.join(expected), 'actual_responsibilities': actual,
                    'responsibility_facts': facts}
        kind = 'violation'
        if contradiction(facts):
            category = 'responsibility_self_contradiction'
        elif only_other_actor(facts):
            category = 'responsibility_other_actor'
        elif facts['malformed_spans'] and not actual:
            category = 'responsibility_expression_malformed'
        elif unsupported or (any(s['delegated'] for s in facts['statements']) and not actual):
            kind, category = 'review', 'responsibility_role_unresolved'
            evidence['unavailable_reason'] = '责任类型存在未支持的句式，尚未确定本岗位实际承担的责任'
        elif len(actual) > 1:
            kind, category = 'review', 'responsibility_multiple_types'
        elif actual and any(x in expected for x in actual):
            continue
        else:
            category = 'responsibility_role_conflict' if actual else 'responsibility_role_missing'
        out.append({'record': row, 'kind': kind, 'evidence': {**evidence, 'issue_type': category,
                    'interpretation_status': 'unsupported' if category == 'responsibility_role_unresolved' else 'decided'}})
    return out
