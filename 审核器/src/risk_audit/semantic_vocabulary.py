"""Export grouped, source-linked suggestions without self-approving aliases."""
from collections import defaultdict
from pathlib import Path
import csv
from risk_audit.util import norm_text,sha256_json,write_json


def write_semantic_vocabulary(run_dir,files,diagnostics,findings,limitations):
    run_dir=Path(run_dir);event_sources=defaultdict(list)
    hashes={str(f.relative_path):f.sha256 for f in files}
    records={(r.file_path,r.sheet,r.row):r for f in files for s in f.sheets for r in s.records}
    # Fragments also need exact originating rows, not just their nearest whole sentence.
    for finding in [*findings,*limitations]:
        f=finding.to_dict() if hasattr(finding,'to_dict') else finding
        evidence=f.get('evidence',{})
        record=records.get((f.get('file_path'),f.get('sheet'),f.get('row')))
        def collect(value):
            if isinstance(value,dict):
                if {'domain','text','scope','candidates'} <= value.keys():
                    key=sha256_json([value['domain'],value['text'],value['scope']])
                    field=evidence.get('source_field','responsibility' if value['domain']=='responsibility' else 'applicability')
                    cell=record.fields.get(field) if record else None
                    event_sources[key].append({'file':f.get('file_path'),'sha256':hashes.get(f.get('file_path')),
                        'sheet':f.get('sheet'),'row':f.get('row'),
                        'field':field,'cell':evidence.get('source_cell') or (cell.coordinate if cell else ''),
                        'measure_id':record.value('measure_id') if record else '',
                        'entity_code':f.get('entity_code'),'business_code':f.get('business_code'),'variant_id':f.get('variant_id')})
                for child in value.values():collect(child)
            elif isinstance(value,list):
                for child in value:collect(child)
        collect(evidence)
    groups={}
    for event in diagnostics.get('events',[]):
        located=event_sources.get(sha256_json([event['domain'],event['text'],event['scope']]),[])
        located=list({sha256_json(x):x for x in located}.values())
        for candidate in event.get('candidates',[]):
            if norm_text(event['text'])==norm_text(candidate['text']):continue
            key=sha256_json([event['domain'],event['text'],candidate['text'],event['scope']])[:20]
            groups[key]={'id':key,'field_type':event['domain'],'source_text':event['text'],
                         'candidate_text':candidate['text'],'score':candidate['score'],'candidate_context':event['scope'],
                         'scope':sorted({' / '.join(str(x.get(k,'')) for k in ('entity_code','business_code','variant_id','measure_id')) for x in located}),
                         'relation':'待验证；不预先认定等价、简称或包含','status':'candidate_only',
                         'sources':located,'automatic_equivalence':False,
                         'constraints':['同主体及业务范围','同控制措施编号的岗位候选','否定与角色等关键差异必须单独核对'],
                         'counterexamples_required':True}
    entries=list(groups.values());write_json(run_dir/'semantic_vocabulary_candidates.json',entries)
    with (run_dir/'语义词库候选.csv').open('w',encoding='utf-8-sig',newline='') as out:
        writer=csv.writer(out);writer.writerow(['类别','原文','候选写法','相似度（非正确概率）','适用范围','是否已认定等价','来源位置'])
        for e in entries:
            writer.writerow([e['field_type'],e['source_text'],e['candidate_text'],e['score'],' / '.join(e['scope']),'否',
                             '；'.join(f"{x['file']}#{x['sheet']}:{x.get('cell') or x['row']}" for x in e['sources'])])
    summary={'candidates':len(entries),'with_source_positions':sum(bool(e['sources']) for e in entries),'activated_aliases':0}
    if any(e.get('retrieval_version')==2 for e in diagnostics.get('events',[])):
        summary['relation_groups']=write_relation_groups(run_dir,diagnostics,event_sources)
    return summary


def write_relation_groups(run_dir,diagnostics,event_sources):
    """Group reviewable relations, retaining each measure's own target evidence."""
    from collections import Counter
    groups={};excluded=Counter();abstained=Counter()
    for event in diagnostics.get('events',[]):
        if event.get('retrieval_version')!=2:continue
        if event.get('status')=='abstained':abstained[event.get('reason','')]+=1
        for rejected in event.get('excluded_candidates',[]):
            excluded.update(rejected.get('reasons',[]))
        source_key=sha256_json([event['domain'],event['text'],event['scope']])
        origins=list({sha256_json(x):x for x in event_sources.get(source_key,[])}.values())
        query=event.get('query_fields',{})
        query_pair=[query.get('department',''),query.get('position','')]
        for candidate in event.get('candidates',[]):
            target_pair=[candidate.get('comparison_department',candidate.get('department','')),
                         candidate.get('comparison_position',candidate.get('position',''))]
            identity=[event['domain'],event['scope'][:3],query_pair if any(query_pair) else event['text'],
                      target_pair if any(target_pair) else candidate['text']]
            key=sha256_json(identity)[:20]
            g=groups.setdefault(key,{'id':key,'domain':event['domain'],'scope':event['scope'][:3],
                'source_fields':query_pair,'target_fields':target_pair,'source_texts':[],
                'candidate_texts':[],'instances':[],'ambiguity_present':False,
                'generic_owner_present':False,'score_min':candidate['score'],'score_max':candidate['score'],
                'status':'candidate_only','automatic_equivalence':False})
            g['source_texts']=sorted(set(g['source_texts'])|{event['text']})
            g['candidate_texts']=sorted(set(g['candidate_texts'])|{candidate['text']})
            g['score_min']=min(g['score_min'],candidate['score']);g['score_max']=max(g['score_max'],candidate['score'])
            g['ambiguity_present'] |= bool(event.get('ambiguous'))
            g['generic_owner_present'] |= query.get('ownership')=='generic_owner_not_confirmed'
            g['instances'].append({'scope':event['scope'],'source_text':event['text'],
                'sources':origins,'targets':candidate.get('sources',[]),
                'candidate_text':candidate['text'],'score':candidate['score'],
                'department_score':candidate.get('department_score'),'position_score':candidate.get('position_score')})
    for g in groups.values():
        g['instances']=list({sha256_json(x):x for x in g['instances']}.values())
        g['measure_count']=len({tuple(i['scope']) for i in g['instances']})
        g['source_row_count']=len({(s['file'],s['sheet'],s['row']) for i in g['instances'] for s in i['sources']})
        g['requires_confirmation']='先核对代表关系；即使确认名称对应，各措施仍须使用该措施自身的岗位记录，不跨措施补齐。'
    ordered=sorted(groups.values(),key=lambda g:(-g['source_row_count'],g['id']))
    write_json(run_dir/'semantic_relation_groups.json',ordered)
    with (run_dir/'语义关系集中核对.csv').open('w',encoding='utf-8-sig',newline='') as out:
        writer=csv.writer(out);writer.writerow(['关系编号','主体/业务/矩阵类型','矩阵部门','矩阵岗位','清单部门','清单岗位',
            '涉及措施数','涉及矩阵行数','存在多个接近候选','存在泛称单位','判断状态','代表矩阵位置','代表清单位置'])
        for g in ordered:
            instance=g['instances'][0]
            locate=lambda xs:'；'.join(f"{x['file']}#{x['sheet']}:{x.get('cell') or x.get('position_cell') or x['row']}" for x in xs[:2])
            writer.writerow([g['id'],'/'.join(map(str,g['scope'])),*g['source_fields'],*g['target_fields'],
                g['measure_count'],g['source_row_count'],'是' if g['ambiguity_present'] else '否',
                '是' if g['generic_owner_present'] else '否','候选，尚未认定等价',locate(instance['sources']),locate(instance['targets'])])
    summary={'groups':len(ordered),'relation_instances':sum(len(g['instances']) for g in ordered),
        'excluded_candidate_reasons':dict(excluded),'abstained_query_reasons':dict(abstained),
        'automatic_approvals':0,'audit_opinions_removed_by_grouping':0}
    write_json(run_dir/'semantic_relation_summary.json',summary)
    lines=['# 语义关系集中核对说明','',
        f"本次将{summary['relation_instances']}条有来源的候选关系归为{len(ordered)}组。分组只减少重复查看关系，不改变任何审核结论。",'',
        '先查看语义关系集中核对.csv中的高频关系，完整同编号原文、矩阵来源与清单来源见semantic_relation_groups.json。',
        '被排除的候选和无法推荐的原因保留在semantic_diagnostics.json，不另行作为单位整改事项。',
        '这些表供程序规则及词库维护使用，不要求单位逐行复核模型候选。名称关系未验证前不得自动转为通用别名。','',
        '## 候选排除原因','',*[f'- {reason}：{n}次' for reason,n in excluded.most_common()],
        '', '## 未推荐原因','',*[f'- {reason}：{n}条查询' for reason,n in abstained.most_common()]]
    (run_dir/'语义辅助集中说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return summary
