"""Resolve the answer column using business rows and scoped ownership evidence."""
from __future__ import annotations
from collections import Counter
import re
from risk_audit.applicability import interpret, interpret_for_entity
from risk_audit.readers.header_semantics import header_owner_names, body_owner_evidence
from risk_audit.util import norm_text


def discover_answer_columns(ws, cache_ws, file, columns, candidates, headers, reasons,
                            header_rows, rows, audit, merged, current_text):
    if 'applicability' in candidates or 'measure_id' not in columns: return
    if not file.entity_code or file.entity_conflict or file.entity_code == 'BASELINE': return
    own = header_owner_names(file)
    version = getattr(file, '_parser_policy', {}).get('version')
    name = next((e.partition(':')[2] for e in file.entity_evidence if e.startswith('标准全称:')), '')
    last = max((c.column for c in ws._cells.values() if c.value not in (None, '')), default=0)
    # Require real measure rows, rather than notes or formatting-only rows.
    if version != 3:
        rows = [r for r in rows if current_text(merged(ws, r, columns['measure_id']))]
    for col in range(columns['measure_id'] + 1, last + 1):
        assigned = {fid for fid, c in columns.items() if c == col}
        if col in audit.values() or (assigned and not (version == 3 and assigned == {'applicability_reason'})): continue
        cells = [merged(ws, r, col) for r in reversed(header_rows)]
        header_cell = next((c for c in cells if current_text(c)), cells[0])
        label = norm_text(current_text(header_cell))
        match = re.fullmatch(r'(.+?)(?:矩阵)?测试情况', label)
        owner = match[1] if match else label if label in own else ''
        unnamed = version == 3 and not label
        change_notes = version == 3 and label in {'矩阵变化内容说明', '矩阵变更内容说明'}
        if not match and label not in own and label not in {'备注', '其他备注', '适用层级', '适用主体'} and not unnamed and not change_notes: continue
        values = []
        unavailable = False
        for row in rows:
            cell = merged(ws, row, col)
            if cell.data_type == 'f':
                cached = merged(cache_ws, row, col).value if cache_ws is not None else None
                if cached is None: unavailable = True; break
                values.append(str(cached))
            else: values.append(current_text(cell))
        if unavailable: continue
        counts = Counter(interpret_for_entity(v, name, getattr(file, '_semantic_lexicon', {}), version).state for v in values)
        # “是/否” under a test/level heading can describe a test outcome. Only
        # explicit 适用/不适用 expressions provide the anchor for this fallback.
        anchors = sum(bool(re.match(r'^(?:不)?适用(?=$|[，,:;。]|因|本|县|无|暂|不)', norm_text(v))) for v in values)
        if version == 3:
            anchors = sum('适用' in norm_text(v) and interpret_for_entity(v, name, getattr(file, '_semantic_lexicon', {}), version).decision is not None for v in values)
        if anchors < 2: continue
        if unnamed:
            # No title means a stronger profile is necessary: every nonblank
            # business value must explicitly say 适用/不适用, not generic 是/否.
            nonempty = [norm_text(v) for v in values if norm_text(v)]
            if any(v not in {'适用', '不适用'} for v in nonempty): continue
        candidates.setdefault('applicability', []).append(col)
        headers[('applicability', col)] = {'header': label, 'coordinate': header_cell.coordinate,
             'owner': owner, 'discovery': 'unnamed_explicit_applicability' if unnamed else 'nonstandard_header_explicit_answers',
             'discovery_profile': {'business_rows': len(rows), 'explicit_answer_anchors': anchors, 'state_counts': dict(counts)}}
        reasons['applicability'] = 'nonstandard_header_explicit_answers'
        if unnamed and col > 1:
            left_cells = [merged(ws, r, col - 1) for r in reversed(header_rows)]
            left = next((c for c in left_cells if current_text(c)), left_cells[0])
            left_label = norm_text(current_text(left))
            note = re.fullmatch(r'(.+?)(?:矩阵)?测试情况', left_label)
            if note and note[1] in own:
                candidates.setdefault('applicability_reason', []).append(col - 1)
                headers[('applicability_reason', col - 1)] = {
                    'header': left_label, 'coordinate': left.coordinate, 'owner': note[1],
                    'paired_answer_column': col, 'discovery': 'adjacent_current_unit_notes'}
                reasons.setdefault('applicability_reason', 'adjacent_current_unit_notes')


def resolve(ws,cache_ws,file,columns,candidates,headers,reasons,rows,policy,lexicon,read_cell,hidden):
    if not policy or file is None or not file.entity_code or file.entity_code=='BASELINE' or file.entity_conflict:return
    fid='applicability';cols=list(dict.fromkeys(candidates.get(fid,[])))
    if not cols:return
    own_names=header_owner_names(file)
    version = policy.get('version')
    full_names=[norm_text(e.partition(':')[2]) for e in file.entity_evidence if e.startswith('标准全称:')]
    scoped=policy.get('scoped_header_aliases',{}).get(file.entity_code,[])
    body_col=columns.get('responsibility')
    body=[read_cell(ws,r,body_col)[0] for r in rows] if body_col else []
    current_body=[r for r,t in zip(rows,body) if any(n in norm_text(t) for n in full_names)]
    body_proof = body_owner_evidence(file, list(zip(rows, body))) if version == 3 else None
    if body_proof: current_body = body_proof['current_unit_body_rows']
    def answer(text):
        return interpret_for_entity(text, full_names[0] if full_names else '', lexicon, version)
    # A local place abbreviation may be interpreted inside a proved subsidiary file.
    local_names=set()
    for n in full_names:
        m=re.search(r'有限公司(.{2,6}?)分公司$',n)
        if m:local_names.update({m[1],m[1]+'公司',m[1]+'分公司'})
    valid=[];profiles={}
    for col in cols:
        header=headers[(fid,col)];owner=norm_text(header.get('owner',''))
        evidence={'entity_code':file.entity_code,'file_identity':list(file.entity_evidence)}
        if not owner or owner in own_names:
            ownership='known'
        elif owner in scoped and full_names:
            ownership='scoped_header_alias';evidence['alias']=owner
        elif current_body and owner in local_names:
            ownership='local_subsidiary_abbreviation';evidence['current_unit_body_rows']=current_body
        elif (version == 3 and body_proof['confirmed'] and len(cols) == 1 and
              not any(owner in norm_text(t) for t in body) and owner.endswith(('公司','分公司','本部'))):
            ownership='template_owner_corrected';evidence.update(body_proof)
        elif (version != 3 and full_names and len(current_body)>=2 and len(current_body)>=0.9*sum(bool(norm_text(t)) for t in body) and len(cols)==1 and
              not any(owner in norm_text(t) for t in body) and owner.endswith(('公司','分公司','本部'))):
            ownership='template_owner_corrected';evidence['current_unit_body_rows']=current_body
            evidence['foreign_owner_body_rows']=[]
        elif '是否适用' in norm_text(header.get('header', '')):
            # 明确字段词优先于未收录的单位简称，保证该列进入后续值分布和多列冲突判断。
            ownership='header_contains_applicability'
        else:
            header['owner_resolution']={'status':'unconfirmed',**evidence};continue
        header['owner_resolution']={'status':ownership,**evidence};valid.append(col)
        values=[];states=[]
        for row in rows:
            text,unavailable=read_cell(ws,row,col,cache_ws)
            values.append(None if unavailable else norm_text(text))
            states.append('source_unavailable' if unavailable else answer(text).state)
        profiles[col]={'values':values,'states':states}
        header['profile']={'business_rows':len(rows),'state_counts':dict(Counter(states))}
    if not valid:
        columns.pop(fid,None);reasons[fid]='owner_unconfirmed';return
    visible=[c for c in valid if not hidden(ws,c)];options=visible or valid
    def coherent(c):
        counts=Counter(profiles[c]['states'])
        return bool(counts['explicit_positive']+counts['explicit_negative']) and all(x in {'empty','explicit_positive','explicit_negative'} for x in counts)
    def compatible(a,b):
        for left,right in zip(profiles[a]['values'],profiles[b]['values']):
            if left==right:continue
            if left is None or right is None:return False
            x,y=answer(left),answer(right)
            if x.decision and y.decision and x.decision!=y.decision:return False
            # Modification notes are auxiliary; unknown prose cannot overrule a definite answer.
            if {x.state,y.state}<={'explicit_positive','explicit_negative','template_adopted','template_modified','empty'}:continue
            if version == 3:
                # A literal modification note supplies no competing decision.
                # An explicit contradictory answer was rejected above.
                note = right if x.decision else left if y.decision else None
                if note is not None and '适用' not in note and re.search(r'修改|调整|新增|删除|增加', note): continue
            return False
        return True
    if len(options)>1:
        def meaning_signature(c):
            result=[]
            for value in profiles[c]['values']:
                meaning=answer(value or '')
                result.append((meaning.decision,meaning.reason) if value is not None and meaning.decision else ('raw',value))
            return tuple(result)
        equal=len({meaning_signature(c) for c in options})==1
        semantic=[c for c in options if coherent(c)]
        owned_answers=[c for c in semantic if headers[(fid,c)].get('owner') and
                       headers[(fid,c)]['owner_resolution']['status'] in {'known','scoped_header_alias','local_subsidiary_abbreviation'}]
        if equal:reasons[fid]='equivalent_business_rows'
        elif len(owned_answers)==1 and all(c==owned_answers[0] or not headers[(fid,c)].get('owner') for c in options) and (version != 3 or all(headers[(fid,c)].get('discovery') != 'unnamed_explicit_applicability' or compatible(owned_answers[0], c) for c in options)):
            options=owned_answers;reasons[fid]='current_unit_answer_values'
        elif len(semantic)==1 and all(compatible(semantic[0],c) for c in options):
            options=semantic;reasons[fid]='answer_values_preferred_to_change_notes'
        else:
            columns.pop(fid,None);reasons[fid]='column_conflict';return
    else:
        status=headers[(fid,options[0])]['owner_resolution']['status']
        reasons[fid]=status if status!='known' else 'visible_answer_column' if visible else 'hidden_field_required'
    columns[fid]=max(options)
    if version == 3 and headers[(fid, columns[fid])].get('discovery') == 'unnamed_explicit_applicability':
        previous = columns[fid] - 1
        paired = headers.get(('applicability_reason', previous), {})
        if paired.get('paired_answer_column') == columns[fid] and paired.get('owner') in own_names:
            columns['applicability_reason'] = previous
            reasons['applicability_reason'] = 'adjacent_current_unit_notes'
        note_header = headers.get((fid, previous), {})
        if (previous in valid and re.search(r'测试情况$', note_header.get('header', ''))
                and note_header.get('owner_resolution', {}).get('status') in {'known', 'scoped_header_alias'}):
            candidates.setdefault('applicability_reason', []).append(previous)
            headers[('applicability_reason', previous)] = dict(note_header)
            columns['applicability_reason'] = previous
            reasons['applicability_reason'] = 'adjacent_current_unit_notes'
    # Pair the reason with the final chosen answer, not the abandoned column.
    adjacent=columns[fid]+1
    reason_cols=candidates.get('applicability_reason',[])
    if adjacent in reason_cols and (not headers[('applicability_reason',adjacent)].get('owner') or
                                    headers[('applicability_reason',adjacent)]['owner'] in own_names):
        columns['applicability_reason']=adjacent;reasons['applicability_reason']='paired_with_applicability'
