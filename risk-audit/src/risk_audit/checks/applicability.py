"""Check actual applicability/reason values in combined or split columns."""
from __future__ import annotations

import re
from typing import Any

from risk_audit.util import norm_text


def applicability_values(ctx: Any, p: dict) -> list[dict]:
    out = []; placeholders = {norm_text(x) for x in p["placeholders"]}
    def add(row, kind, category, **evidence):
        out.append({"record": row, "kind": kind, "evidence": {"issue_type": category, **evidence}})
    for row in ctx.records:
        value = row.fields.get("applicability")
        if value is None: continue  # Schema check identifies a missing column once.
        text = norm_text(value.current)
        if value.state == "formula_no_cache":
            add(row, "review", "applicability_cache_missing"); continue
        if not text or text in placeholders:
            add(row, "violation", "applicability_empty"); continue
        negative = re.match(r"^(?:不适用|否)", text)
        if negative:
            inline_reason = text[negative.end():].strip(" ,;:。/，；：-—")
            reason = row.fields.get("applicability_reason")
            separate_reason = norm_text(reason.current) if reason else ""
            if not inline_reason or inline_reason in placeholders:
                if reason and reason.state == "formula_no_cache":
                    add(row, "review", "reason_cache_missing")
                elif not separate_reason or separate_reason in placeholders:
                    add(row, "violation", "not_applicable_reason_empty")
        elif not re.match(r"^(?:适用|是)(?:$|[,:;。])", text):
            add(row, "review", "applicability_unclear", actual_text=value.current)
    return out


def applicability_values_v2(ctx, params):
    from risk_audit.applicability import interpret_record
    from risk_audit.semantic import get_assistant
    placeholders={norm_text(x) for x in params['placeholders']};out=[]
    for row in ctx.records:
        result=interpret_record(row,ctx.resources)
        if result.state=='source_missing':continue
        value=row.fields['applicability']
        evidence={'interpretation':result.to_dict(),'actual_text':value.current,'source_cell':value.coordinate}
        def add(kind,issue,**extra):out.append({'record':row,'kind':kind,'evidence':{**evidence,'issue_type':issue,**extra}})
        if result.state=='source_unavailable':add('review','applicability_cache_missing')
        elif result.state=='empty' or norm_text(value.current) in placeholders:add('violation','applicability_empty')
        elif result.decision=='not_applicable':
            reason=row.fields.get('applicability_reason')
            if not result.reason or norm_text(result.reason) in placeholders:
                if reason and reason.state=='formula_no_cache':add('review','reason_cache_missing')
                elif not reason or not norm_text(reason.current) or norm_text(reason.current) in placeholders:
                    add('violation','not_applicable_reason_empty')
        elif result.decision=='applicable':continue
        else:
            extra={}
            assistant=get_assistant(ctx.resources)
            if result.state=='unknown' and assistant:
                entries=ctx.resources.get('semantic_lexicon',{}).get('prototypes',[])
                extra['semantic_suggestion']=assistant.suggest(value.current,entries,domain='applicability',scope=(row.entity_code,row.business_code))
            add('limitation','applicability_meaning_unresolved',
                unavailable_reason='已读取原文；尚无明确适用性结论，当前业务状态与模板修改说明不能代替是否适用。',**extra)
    return out
