"""Versioned carrier parsing with explicit basis/action classification."""
from __future__ import annotations
import re
from risk_audit.checks.carriers import parse_carrier_references_v2, QUOTED, _known_references, _is_term_boundary
from risk_audit.util import norm_text

VERBS=('审核','复核','核对','提交','报送','编制','填写','检查','对照','确认','查阅','开具','签订','保存','取得','提供')
DOCUMENT_CUE=re.compile(r'报告|协议|合同|凭证|档案|方案|文件|资料|台账|清单|记录|单据|证明|通知书|申请书|发票|收据|订单|账簿|账册|(?:单|表)(?=$|[的中内上、,，;；及和与]|准确|一致|完整|真实)')
CONTROL_ACTION=re.compile(r'选择|初审|复核|确认|核对|校验|填报|报送|检查|监督|追究|落实|控制|执行|办理|组织|开展|整改|报销|支付|发放|归集|分配|列支|管理|保管|签订|填写|编制|提交')


def exact_document_sequence(text,known,aliases):
    """Only accept one complete segmentation into two or more known names."""
    text=norm_text(text)
    names=sorted({n for n in known if len(n)>=3},key=lambda n:(-len(n),n))
    solutions=[]
    def visit(offset,parts):
        if len(solutions)>1:return
        while offset<len(text) and text[offset] in '、,，;；':offset+=1
        if offset==len(text):
            if len(parts)>=2:solutions.append(parts)
            return
        for name in names:
            if text.startswith(name,offset):visit(offset+len(name),parts+[aliases.get(name,name)])
    if len(text)<=240:visit(0,[])
    return solutions[0] if len(solutions)==1 else None


def local_carrier_aliases(allowed, aliases):
    """Use only names explicitly defined in the current measure's carrier list."""
    result={norm_text(k):norm_text(v) for k,v in (aliases or {}).items()}
    options={}
    for label in allowed:
        label=norm_text(label)
        m=re.fullmatch(r'([^()]+)\([^()]+\)',label)
        if m:options.setdefault(m[1],set()).add(label)
        for part in label.split('或') if '或' in label else []:
            if part and re.search(r'(?:合同|协议|发票|收据|报告|清单|凭证|单|表)$',part):options.setdefault(part,set()).add(label)
    for name,targets in options.items():
        if len(targets)==1 and name not in allowed:result.setdefault(name,next(iter(targets)))
    return result


def find_references(text, known, aliases):
    found=set();spans=[]
    for term in sorted(known,key=lambda x:(-len(x),x)):
        if not term:continue
        start=0
        while (start:=text.find(term,start))>=0:
            end=start+len(term);left=text[:start];right=text[end:]
            before=not left or left[-1] in '对、,，;；。:：()《》' or left.endswith((*VERBS,*(v+'的' for v in VERBS)))
            after=not right or right[0] in '的中内上、,，;；。:：()《》' or right.startswith(('准确','一致','完整','真实','金额','信息','条款','填报','编制','审批','审核','物品','品名','数量','规格','型号','时间','人员','地点','事由','用途','税率','摘要','价格','内容','编号'))
            if (_is_term_boundary(text,start,end) or before and after) and not any(start<b and end>a for a,b in spans):
                found.add(aliases.get(term,term));spans.append((start,end))
            start=end
    return found,spans


def basis_text(text,allowed,aliases):
    parts=[];offset=0;evidence=[]
    for m in QUOTED.finditer(text):
        name=norm_text(m[1]);canonical=aliases.get(name,name)
        before=text[:m.start()];after=text[m.end():]
        is_basis=bool(re.search(r'(?:按照|依据|根据|依照)$',before) and re.match(r'(?:的)?(?:规定|要求|执行|开展)',after))
        if is_basis and canonical not in allowed:
            parts.extend([text[offset:m.start()],'制度依据']);offset=m.end()
            evidence.append({'kind':'policy_basis','text':name})
    parts.append(text[offset:])
    return ''.join(parts),evidence


def parse_carrier_references_v3(text, *, allowed, aliases=None, terminology=()):
    allowed={norm_text(x) for x in allowed};aliases=local_carrier_aliases(allowed,aliases)
    normalized=norm_text(text);masked,basis=basis_text(normalized,allowed,aliases)
    parsed=parse_carrier_references_v2(masked,allowed=allowed,aliases=aliases,terminology=terminology)
    known=allowed|set(aliases)|set(aliases.values())|{norm_text(x) for x in terminology}
    found,_=find_references(masked,known,aliases)
    refs=set(parsed['references'])|found
    unresolved=[]
    evidence=parsed['evidence']+basis+[{'kind':'action_bounded_reference','text':x} for x in sorted(found)]
    for item in parsed['evidence']:
        if item['kind']!='unlisted_document_reference':continue
        sequence=exact_document_sequence(item['text'],known,aliases)
        if sequence:
            refs.discard(aliases.get(norm_text(item['text']),norm_text(item['text'])))
            refs.update(sequence)
            evidence.append({'kind':'exact_document_sequence','text':item['text']})
    for fragment in parsed['unavailable_fragments']:
        sequence=exact_document_sequence(fragment,known,aliases)
        if sequence:
            refs.update(sequence);evidence.append({'kind':'exact_document_sequence','text':fragment});continue
        found,spans=find_references(fragment,known,aliases);refs.update(found)
        chars=list(fragment)
        for start,end in spans:chars[start:end]=' '* (end-start)
        remainder=''.join(chars)
        # Unknown document-shaped objects stay unresolved, even in an action
        # sentence. Recognizing a verb alone cannot approve an unknown report.
        if CONTROL_ACTION.search(remainder) and not DOCUMENT_CUE.search(remainder):
            evidence.append({'kind':'control_action','text':fragment})
        else:unresolved.append(fragment)
    parsed.update(references=sorted(refs),unresolved=sorted(refs-allowed),unavailable_fragments=unresolved,
                  original_sentence=str(text or ''),evidence=evidence)
    parsed['status']='unavailable' if unresolved else 'unresolved' if refs-allowed else 'matched' if refs else 'not_applicable'
    return parsed
