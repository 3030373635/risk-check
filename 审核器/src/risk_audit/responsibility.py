"""Deterministic responsibility clauses, with auditable spans in normalized text.

These facts do not infer responsibility from a job title or a semantic score.
Unsupported clauses remain distinguishable from an explicit missing attribute.
"""
from __future__ import annotations

from functools import lru_cache
import re

from risk_audit.util import norm_text

MODIFIER = r'(?:直接|全面|共同|分别|相应|主要|全部|首要|一定|初审|最终|[一二三四五]级)*'
PREDICATE = re.compile(rf'(?:负有?|承担|担负|具有|有|履行|落实){MODIFIER}(?:(?:主体|审核|审批|复核){MODIFIER})?责任|(?:负有?|承担|担负)(?P<duty_object>[^。;；!?！？责任]{{1,120}}的)(?:主体|审核|审批|复核)责任|负责(?![一-鿿])')
ROLE = re.compile(rf'(主体|审核|审批|复核){MODIFIER}责任')
NEGATIVE = re.compile(r'(?:不|未|没|无需|无须|不必|不得|不能|不需要|免于|没有)(?:再|直接)?$')
DELEGATED = re.compile(r'(?:督促|监督|指导|检查|要求|协助)[^,，。;；]{0,40}(?:承担|履行|落实)')
QUOTE = re.compile(r'“[^”]*”|「[^」]*」|"[^"\n]*"')


@lru_cache(maxsize=8192)
def parse_responsibility(text: str, attributes: tuple[str, ...] = ()) -> dict:
    text = norm_text(text)
    attrs = set(attributes) | {x[:-1] for x in attributes if x.endswith('性')}
    # A narrow spelling synonym, not an inference from an action such as 验真.
    if '完整性' in attrs: attrs.update({'完备性', '完备', '齐全'})
    quality = re.compile('|'.join(re.escape(x) for x in sorted(attrs, key=lambda x: (-len(x), x)))) if attrs else None
    def quality_hits(value):
        nouns=[(m.start(),m.end()) for m in re.finditer(r'技术规范(?:书)?|规范性文件',value)]
        return [m for m in quality.finditer(value)
                if not any(a <= m.start() and m.end() <= b for a,b in nouns)
                and not (m[0]=='规范' and m.start()==0)] if quality else []
    quotes = [(m.start(), m.end()) for m in QUOTE.finditer(text)]
    statements = []
    for match in PREDICATE.finditer(text):
        # A quoted requirement is not an assertion by this row's performer.
        quoted = any(a <= match.start() < b for a, b in quotes)
        left = max((text.rfind(c, 0, match.start()) for c in '。!?！？;；'), default=-1) + 1
        previous = [s['end'] for s in statements if left <= s['start'] < match.start()]
        left = max([left, *previous])
        prefix = text[left:match.start()]
        negative = bool(NEGATIVE.search(prefix) or re.search(r'(?:无需|无须|不必|不需要|免于|不)对[^。;；]*$', prefix))
        delegated = bool(DELEGATED.search(prefix + match[0]))
        body = prefix + (match.group('duty_object') or '')
        linked = quality_hits(body)
        invoice_authenticity = bool('真实性' in attrs and re.search(r'发票验真|(?:核验|验证|查验)发票真伪', body))
        # Parallel objects can share the final predicate. 核对 / 比对 are verbs,
        # not a new object boundary. A preceding predicate or sentence stops it.
        obj = body
        if quality: obj = quality.sub('', obj)
        obj = re.sub(r'[的对、,，及和与\s]', '', obj)
        roles = [m[1] + '责任' for m in ROLE.finditer(match[0])]
        statements.append({'start': left, 'end': match.end(), 'predicate_start': match.start(),
                           'text': text[left:match.end()], 'predicate': match[0],
                           'roles': roles, 'qualities': [m[0] for m in linked] + (['真实性（发票验真）'] if invoice_authenticity else []),
                           'object_present': len(obj) >= 2, 'negative': negative,
                           'quoted': quoted, 'delegated': delegated})
    return {'parser_version': 1, 'normalized_text': text, 'statements': statements,
            'quality_terms': [m[0] for m in quality_hits(text)],
            'role_mentions': [m[1] + '责任' for m in ROLE.finditer(text)]}


def asserted(facts):
    return [s for s in facts['statements'] if not (s['negative'] or s['quoted'] or s['delegated'])]


def responsibility_phrase_v5(ctx, params):
    out = []
    for row in ctx.records:
        field = row.fields.get('duty')
        if field is None or field.state == 'formula_no_cache':
            out.append({'record': row, 'kind': 'review', 'evidence': {'issue_type': 'responsibility_unavailable', 'field': 'duty'}})
            continue
        text = row.value('duty')
        if not text: continue  # duty_required owns the empty field.
        facts = parse_responsibility(text, tuple(params['attributes']))
        positive = asserted(facts)
        if any(s['qualities'] and s['object_present'] for s in positive): continue
        if positive and not facts['quality_terms']:
            category, kind = 'responsibility_quality_missing', 'violation'
        elif facts['quality_terms'] and (positive or facts['role_mentions'] or '责任' in norm_text(text)):
            if facts['statements'] and all(s['negative'] for s in facts['statements']):
                category, kind = 'responsibility_assertion_missing', 'violation'
            else:
                category, kind = 'responsibility_structure_unresolved', 'review'
        else:
            category, kind = 'responsibility_assertion_missing', 'violation'
        out.append({'record': row, 'kind': kind, 'evidence': {'issue_type': category,
                    'field': 'duty', 'responsibility_facts': facts,
                    'unavailable_reason': '责任对象、属性及承担责任的关联句式尚未解析完整' if kind == 'review' else ''}})
    return out


def value_mapping_v2(ctx, params):
    out = []
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
        facts = parse_responsibility(row.value(params['text_field']))
        mapped_types = {t for terms in params['mapping'].values() for t in terms}
        actual = sorted({r for s in asserted(facts) for r in s['roles'] if r in mapped_types})
        represented = {r for s in facts['statements'] for r in s['roles']}
        unsupported = set(facts['role_mentions']) - represented
        ambiguous = any(s['delegated'] for s in facts['statements']) or bool(
            re.search(r'(?:主体|审核|审批)[^。,;责任]{1,3}责任', facts['normalized_text'])
            and not actual)
        evidence = {'role': role, 'expected': '/'.join(expected), 'actual_responsibilities': actual,
                    'responsibility_facts': facts}
        if unsupported or ambiguous:
            kind, category = 'review', 'responsibility_role_unresolved'
            evidence['unavailable_reason'] = '责任类型存在引用或他人承担等句式，尚未确定本岗位实际承担的责任'
        elif len(actual) > 1:
            kind, category = 'review', 'responsibility_multiple_types'
        elif actual and any(x in expected for x in actual):
            continue
        else:
            kind = 'violation'
            category = 'responsibility_role_conflict' if actual else 'responsibility_role_missing'
        out.append({'record': row, 'kind': kind, 'evidence': {**evidence, 'issue_type': category}})
    return out
