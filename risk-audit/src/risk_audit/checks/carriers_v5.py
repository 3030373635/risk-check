"""Versioned grammar repairs plus non-decisive local semantic candidates."""
from __future__ import annotations
import re
from risk_audit.checks.carriers_v4 import parse_carrier_references_v4, carrier_aliases_v4, DOCUMENT_END
from risk_audit.util import norm_text


def parse_carrier_references_v5(text,*,allowed,aliases=None,terminology=(),assistant=None):
    parsed=parse_carrier_references_v4(text,allowed=allowed,aliases=aliases,terminology=terminology)
    local_aliases=carrier_aliases_v4(allowed,aliases)
    remaining=[];refs=set(parsed['references']);source=norm_text(text)
    for fragment in parsed['unavailable_fragments']:
        value=norm_text(fragment)
        contract=re.fullmatch(r'(?:确保)?使用((?:[一-鿿]{1,16})?合同)约定(?:的)?账户(?:进行)?支付',value)
        if contract and not re.search(r'不|未|无|禁止|不得',contract[1]):
            reference=local_aliases.get(contract[1],contract[1])
            refs.add(reference);parsed['evidence'].append({'kind':'contract_account_attribute','text':fragment,'reference':reference});continue
        if re.fullmatch(r'(?:组织)?出险报案(?:与|及|和)后续配合',value):
            parsed['evidence'].append({'kind':'complete_action_without_document','text':fragment});continue
        # Bounded attributes of an explicitly named document earlier in this sentence.
        attribute=re.fullmatch(r'人员、时间、订单的合理性(?:与|和|及)唯一性',value)
        preceding=source.partition('对'+value)[0]
        named=[n for n in refs if n.endswith('订单') and n in preceding]
        if attribute and len(named)==1:
            parsed['evidence'].append({'kind':'explicit_order_antecedent_attributes','text':fragment,'reference':named[0]});continue
        remaining.append(fragment)
    parsed.update(references=sorted(refs),unresolved=sorted(refs-set(allowed)),unavailable_fragments=remaining)
    parsed['status']='unavailable' if remaining else 'unresolved' if parsed['unresolved'] else 'matched'
    if assistant:
        entries=[{'text':n,'concept':'current_measure_carrier'} for n in sorted(allowed)]
        for query in list(dict.fromkeys([*parsed['unresolved'],*remaining]))[:3]:
            if not query or len(query)>180:continue
            result=assistant.suggest(query,entries,domain='carrier',scope=tuple(sorted(allowed)))
            parsed['evidence'].append({'kind':'semantic_candidates_only','text':query,'retrieval':result})
    return parsed


def set_subset_v5(ctx,params):
    from risk_audit.semantic import get_assistant
    from risk_audit.checks.quality_checks import set_subset_v4
    assistant=get_assistant(ctx.resources)
    def parser(text,**kwargs):return parse_carrier_references_v5(text,assistant=assistant,**kwargs)
    return set_subset_v4(ctx,params,reference_parser=parser)
