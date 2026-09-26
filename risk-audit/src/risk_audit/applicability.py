"""One interpretation shared by column selection, R03 and R05.

Business inactivity and template adoption do not establish applicability.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import re
from risk_audit.util import norm_text


@dataclass(frozen=True)
class Applicability:
    state:str
    decision:str|None=None
    reason:str=''
    basis:str=''

    def to_dict(self):return asdict(self)


def interpret(text,lexicon=None):
    t=norm_text(text).strip('。;')
    if not t:return Applicability('empty')
    positive=re.match(r'^(?:适用|是)(?=$|[,:;。])',t)
    negative=re.match(r'^(?:不适用|否)(?=$|[,:;。]|因|本|县|无|暂|不)',t)
    # An explicit contradictory assertion anywhere in the same answer is not resolved by its first word.
    if (positive and re.search(r'不适用',t)) or (negative and re.search(r'(?:但|仍|也|同时|实际)(?:为|是)?适用',t)):
        return Applicability('conflict',basis='同一回答存在相反适用性信息')
    if re.search(r'是否|待(?:确认|核实|定)|不确定|并非|不是不|不一定|可能|[?？]|(?:如|若).*(?:适用|执行)|但.*(?:开展|适用|执行)',t):
        return Applicability('unknown',basis='含疑问、条件、转折或复杂否定，不能据关键词判定')
    if negative:return Applicability('explicit_negative','not_applicable',t[negative.end():].strip(' ,;:。/，；：-—'),'明确否定')
    if positive:return Applicability('explicit_positive','applicable',basis='明确肯定')
    for item in (lexicon or {}).get('applicability',[]):
        if re.fullmatch(item['pattern'],t):
            reason=t if item.get('reason_from_text') else ''
            if item.get('decision')=='not_applicable':
                parts=re.split(r'[,:;]',t,maxsplit=1)
                reason=parts[1] if len(parts)>1 else ''
            return Applicability(item['state'],item.get('decision'),reason,item['id'])
    return Applicability('unknown',basis='已有原文，规则尚未归类')


def interpret_record(record,resources):
    value=record.fields.get('applicability')
    if value is None:return Applicability('source_missing',basis='尚未选定适用情况列')
    if value.state=='formula_no_cache':return Applicability('source_unavailable',basis='公式无缓存')
    name = resources.get('_applicability_entities', {}).get(record.entity_code, '')
    version = resources.get('parser_policy',{}).get('version')
    basic = interpret_for_entity(value.current, name, resources.get('semantic_lexicon',{}), version)
    if basic.decision or version not in {2, 3}: return basic
    county = bool(re.fullmatch(r'国网湖南省电力有限公司.{2,6}县供电分公司', name))
    city = bool(re.fullmatch(r'国网湖南省电力有限公司.{2,6}供电分公司本部', name))
    t = norm_text(value.current).strip('。;')
    if county and t == '此条不适用县、支公司':
        return Applicability('explicit_negative','not_applicable',t,'会计主体清单确认本单位为县供电分公司；原文明示县公司不适用')
    header = resources.get('_applicability_headers', {}).get((record.file_path, record.sheet), '')
    if header in {'适用层级', '适用主体'}:
        if (county or city) and t == '地市及区县':
            return Applicability('explicit_positive','applicable',basis='适用主体栏明确包含本单位的会计主体层级')
        if city and t == '地市本部、变电检修公司、输电检修公司、吉首支公司':
            return Applicability('explicit_positive','applicable',basis='适用层级栏明确包含地市本部；主体清单确认本单位为地市本部')
    return basic


def interpret_for_entity(text, name, lexicon=None, version=None):
    basic = interpret(text, lexicon)
    if version != 3 or basic.decision: return basic
    if (re.fullmatch(r'国网湖南省电力有限公司.{2,6}县供电分公司', norm_text(name))
            and norm_text(text).strip('。;') == '市公司集中管控,县公司不适用'):
        return Applicability('explicit_negative', 'not_applicable', '市公司集中管控',
                             '主体清单确认县供电分公司；原文明示县公司不适用及原因')
    return basic


def attach_context(resources, files, entities):
    """Use roster identities, never a guessed level from a filename."""
    resources['_applicability_entities'] = {k:norm_text(v.name) for k,v in entities.items()}
    headers = {}
    for file in files:
        for choice in file.preservation.get('column_selections', []):
            if choice['field'] != 'applicability' or not choice.get('selected_column'): continue
            selected = next((c for c in choice['candidates'] if c['column'] == choice['selected_column']), {})
            headers[(str(file.relative_path), choice['sheet'])] = norm_text(selected.get('header',''))
    resources['_applicability_headers'] = headers
