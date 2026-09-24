"""Conservative carrier grammar; unresolved grammar stays an internal limit."""
from __future__ import annotations
import re

from risk_audit.checks.carriers_v3 import (parse_carrier_references_v3, local_carrier_aliases,
                                         find_references, DOCUMENT_CUE, CONTROL_ACTION)
from risk_audit.checks.carriers import QUALITY_NAMES, QUOTED
from risk_audit.util import norm_text

DOCUMENT_END = re.compile(r'(?:申请单|审批单|通知书|申请书|说明书|证明材料|制度|办法|规定|发票|收据|合同|协议|报告|凭证|清单|台账|记录|明细表|汇总表|单据|附件|单|表)$')
NON_DOCUMENT_OBJECT = re.compile(
    r'^(?:追责|程序|标准|用工|使用范围|预算管控|超发|滥发|(?:超发|滥发|兑现)?职工薪酬|'
    r'企业负责人薪酬兑现|服务内容|付款方式|关键信息|信息|内容|金额|税率|条款|'
    r'数量|规格|型号|品名|价格|时间|地点|事由|用途|真实性|合理性|准确性|一致性|完整性|及时性)$')


def carrier_aliases_v4(allowed, aliases=None):
    allowed = {norm_text(x) for x in allowed}
    result = local_carrier_aliases(allowed, aliases or {})
    options = {}
    def candidate(name, target):
        if name and name != target and name not in allowed:
            options.setdefault(name, set()).add(target)
    for label in allowed:
        if label == '发票':
            for name in ('增值税发票', '增值税专用发票', '增值税普通发票'): candidate(name, label)
        if label == '合同文本': candidate('合同',label)
        if label == '合同': candidate('合同文本',label)
        if label.endswith('报销审批单') and label != '报销审批单': candidate('报销审批单', label)
        # Both sides must explicitly name a document. No guessed restoration
        # of elided qualifiers, and no split within e.g. “合同及协议管理办法”.
        parts = re.split(r'及|和|与', label)
        if len(parts) > 1 and all(DOCUMENT_END.search(part) for part in parts):
            for part in parts: candidate(part, label)
        shared = re.fullmatch(r'(.*?)初设/施设(报告)', label)
        if shared:
            for stage in ('初设','施设'): candidate(shared[1]+stage+shared[2], label)
        normalized = re.sub(r'^(?:相关的|相应的)', '', label)
        if '或' in normalized:
            normalized = normalized.removesuffix('等')
            alternatives = normalized.split('或')
            if all(DOCUMENT_END.search(x) for x in alternatives):
                for part in alternatives: candidate(part, label)
                candidate(normalized, label)
    for name, targets in options.items():
        if len(targets) == 1:
            result.setdefault(name, next(iter(targets)))
        else:
            result.pop(name, None)  # An abbreviation cannot pick among two carriers.
    invoice_targets = {label for label in allowed if label == '发票' or '发票' in label.split('或')} | options.get('发票',set())
    if len(invoice_targets) == 1:
        for name in ('增值税发票', '增值税专用发票', '增值税普通发票'):
            if name not in allowed: result.setdefault(name, next(iter(invoice_targets)))
    return result


def _non_document_remainder(text):
    text = norm_text(text).strip('的中内上、,;及和与')
    if not text: return True
    parts = re.split(r'[、,;]|及|和|与|的', text)
    if all(not p or NON_DOCUMENT_OBJECT.fullmatch(p) for p in parts): return True
    return bool(CONTROL_ACTION.search(text) and not DOCUMENT_CUE.search(text))


def _references_in_fragment(text, known, aliases):
    """Allow document data predicates, never a prefix of a longer title."""
    found, spans = find_references(text, known, aliases)
    for term in sorted(known, key=lambda x: (-len(x), x)):
        start = 0
        while (start := text.find(term, start)) >= 0:
            end = start + len(term); left, right = text[:start], text[end:]
            before = not left or left[-1] in '对、,;及和与' or left.endswith(('以及','确认','校验','核对','提交','检查','复核'))
            after = not right or right.startswith(('条款','税率','约定','通过','信息','内容','签订','履行','中','上','内','的','以及','及','和','与'))
            if before and after and not any(start < b and end > a for a,b in spans):
                found.add(aliases.get(term,term)); spans.append((start,end))
            start = end
    return found,spans


def parse_carrier_references_v4(text, *, allowed, aliases=None, terminology=()):
    allowed = {norm_text(x) for x in allowed}
    aliases = carrier_aliases_v4(allowed, aliases)
    # A vocabulary entry is recognition evidence, not permission. Reject
    # action fragments from the vocabulary before looking for document names.
    vocabulary = {norm_text(x) for x in terminology if DOCUMENT_END.search(norm_text(x))}
    normalized = norm_text(text)
    source = normalized.replace('对于', '就').replace('对照', '比照')
    antecedents = [n for n in allowed if len(n)>2 and n.endswith('合同') and
                   re.search(re.escape(n)+r'(?:的)?(?:签订|审核|审批|履行)(?:以及|及|和|与|、该)合同(?=的|准确|完整|真实)',source)]
    restated = len(antecedents)==1 and '合同' not in allowed
    if restated: aliases['合同']=antecedents[0]
    parsed = parse_carrier_references_v3(source, allowed=allowed, aliases=aliases, terminology=vocabulary)
    known = allowed | set(aliases) | set(aliases.values()) | vocabulary
    refs = set(parsed['references']); unresolved = []; evidence = list(parsed['evidence'])
    # Only an explicit adjacent restatement in this sentence can bind a bare
    # generic term to its already named carrier. Mere uniqueness in a matrix
    # does not prove that an unqualified “合同” means a particular contract.
    if restated:
        evidence.append({'kind':'explicit_adjacent_restatement','text':'合同','reference':antecedents[0]})
    containers = set()
    for name in sorted(known,key=lambda x:(-len(x),x)) if '库的' in source else ():
        for m in re.finditer(r'([一-鿿]{1,20}库)的'+re.escape(name)+r'(?=的|中|上|内|准确|完整|一致|$)', source):
            containers.add(m[1].removeprefix('对'))
            refs.add(aliases.get(name,name))
            evidence.append({'kind':'container_document_reference','text':m[0],'reference':name})
    for item in parsed['evidence']:
        if item['kind'] != 'control_action' or not re.search(r'制度|办法|条例', item['text']): continue
        policy = re.fullmatch(r'(.{3,80}?(?:制度|办法|条例))(?:的)?执行',item['text'])
        if policy and not re.search(r'按照|依据|根据|[、,;及和与]',policy[1]):
            refs.add(aliases.get(policy[1],policy[1]))
            evidence.append({'kind':'complete_policy_reference','text':policy[1]})
        else:
            unresolved.append(item['text'])
    explicit = {norm_text(m[1]) for m in QUOTED.finditer(source)}
    for item in parsed['evidence']:
        if item['kind'] != 'unlisted_document_reference': continue
        name = norm_text(item['text'])
        if name not in explicit and re.search(r'^(?:使用|按照|依据|根据|所管辖|每月)|(?:起草|生成|进行|开展|按照|依据)', name):
            refs.discard(aliases.get(name,name)); unresolved.append(name)
            evidence.append({'kind':'unparsed_action_not_document_name','text':name})
    for fragment in parsed['unavailable_fragments']:
        found, spans = _references_in_fragment(fragment, known, aliases)
        refs.update(found)
        chars = list(fragment)
        for start, end in spans: chars[start:end] = ' ' * (end - start)
        remainder = ''.join(chars)
        if norm_text(remainder) in containers: remainder=''
        if found:
            remainder = re.sub(r'等(?:相关)?(?:资料|材料|单据|信息)(?:中|内|上)?', '', remainder)
        remainder = re.sub(r'(?:(?:进行|开展|组织|核对|审核|确认|初审|注明|涉及))+$', '', remainder)
        # “服务内容、付款方式等合同关键信息”: the named carrier is 合同;
        # the preceding enumerated attributes do not introduce new documents.
        attribute = re.fullmatch(r'(.+?)等(.+?)(?:的)?(?:关键)?(?:信息|内容|条款)', norm_text(remainder))
        if attribute and _non_document_remainder(attribute[1]):
            name = attribute[2]
            if name in known or DOCUMENT_END.search(name) and not re.search(r'[、,;及和与]|确认|按照|使用', name):
                refs.add(aliases.get(name, name)); remainder = ''
                evidence.append({'kind': 'document_attributes', 'text': fragment, 'reference': name})
        # A complete unquoted document followed only by a locative particle
        # is a concrete reference, not a sentence requiring interpretation.
        locative = re.fullmatch(r'(.{2,80}?)(?:中|上|内)(?:的)?', norm_text(remainder))
        if locative and DOCUMENT_END.search(locative[1]) and not re.search(r'[、,;及和与]|确认|提交|按照',locative[1]):
            refs.add(aliases.get(locative[1],locative[1])); remainder=''
            evidence.append({'kind':'locative_document_reference','text':locative[1]})
        if _non_document_remainder(remainder):
            evidence.append({'kind': 'non_document_object_or_attribute', 'text': fragment})
        else:
            unresolved.append(fragment)
    parsed.update(references=sorted(refs), unresolved=sorted(refs-allowed), unavailable_fragments=unresolved,
                  original_sentence=str(text or ''), evidence=evidence)
    parsed['status'] = 'unavailable' if unresolved else 'unresolved' if refs-allowed else 'matched' if refs else 'not_applicable'
    return parsed
