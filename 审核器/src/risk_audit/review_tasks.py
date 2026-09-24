"""Group work by its cause without deleting row findings or approving a group."""
from __future__ import annotations
from collections import Counter,defaultdict
from pathlib import Path
import csv
from risk_audit.util import measure_id_key,norm_text,sha256_json,write_json

LABELS={'department':'部门','position':'岗位名称','person_names':'人员姓名','role':'角色','duty':'岗位职责',
        'measure_id':'控制措施编号','applicability':'本单位适用情况','responsibility':'责任主体','carrier':'控制载体','duty_id':'岗位职责编号'}


def build_review_tasks(files,findings,scopes):
    """生成集中复核事项；files/findings/scopes 分别为材料、意见和本次应审范围。"""
    values=[f.to_dict() if hasattr(f,'to_dict') else f for f in findings]
    records={(r.file_path,r.sheet,r.row):r for f in files for s in f.sheets for r in s.records}
    matrices=defaultdict(list)
    for r in records.values():
        if r.record_type=='matrix':matrices[(r.entity_code,r.business_id or r.business_code,r.variant_id,measure_id_key(r.value('measure_id')))].append(r)
    choices={(str(f.relative_path),c['sheet'],c['field']):c for f in files for c in f.preservation.get('column_selections',[])}
    owner_notices={(str(f.relative_path),n['sheet']):n for f in files for n in f.preservation.get('sheet_owner_notices',[])}
    groups={}
    def scope_of(f):
        if not f['entity_code'] or not f['business_code'] or f['variant_id']=='unknown':return '归属待确认'
        allowed={(x.get('business_id') or x['business_code'],x['variant_id']) for x in scopes.get(f['entity_code'],{}).get('businesses',[])}
        return '应审范围' if (f.get('business_id') or f['business_code'],f['variant_id']) in allowed else '范围外参考'
    def reading(file,sheet,field):
        key=(file,sheet,field);choice=choices.get(key,{})
        reason=choice.get('reason')
        if reason=='owner_unconfirmed':text=f'“{LABELS.get(field,field)}”表头中的单位尚未与本文件主体对应，请集中确认该列归属。'
        elif reason=='column_conflict':text=f'本表有多列“{LABELS.get(field,field)}”，内容不同，暂不能确定使用哪列。请确认有效列后重审。'
        else:text=f'本表“{LABELS.get(field,field)}”列未能识别，请先确认表头或列位置，再重审相关内容。'
        return ('读取处理',key,text,{'field':field,'selection':choice})
    for f in values:
        e=f['evidence'];category=e.get('issue_type','');record=records.get((f['file_path'],f['sheet'],f['row']))
        route=scope_of(f);scope=(f['entity_code'],f.get('business_id') or f['business_code'],f['variant_id'])
        dependency=None
        if record:
            field=e.get('field')
            if field and field not in record.fields:dependency=reading(record.file_path,record.sheet,field)
            elif f['check_id']=='explicit_role_responsibility' and 'role' not in record.fields:dependency=reading(record.file_path,record.sheet,'role')
            elif category=='person_missing' and 'person_names' not in record.fields:dependency=reading(record.file_path,record.sheet,'person_names')
            elif category=='explicit_role_missing' and 'role' not in record.fields:dependency=reading(record.file_path,record.sheet,'role')
            elif category=='coverage_applicability_unknown' and 'applicability' not in record.fields:dependency=reading(record.file_path,record.sheet,'applicability')
            elif category=='responsibility_unit_unavailable' and 'responsibility' not in record.fields:dependency=reading(record.file_path,record.sheet,'responsibility')
            elif category=='carrier_target_unavailable':
                targets=matrices.get((*scope,measure_id_key(record.value('measure_id'))),[])
                missing=[r for r in targets if 'carrier' not in r.fields]
                if missing:dependency=reading(missing[0].file_path,missing[0].sheet,'carrier')
        if route=='归属待确认':
            unit=Path(f['file_path']).parts[0] if f['file_path'] else f['entity_code']
            kind,key,title,details='归属确认',(unit,),f'请先确认“{unit}”的主体及业务归属，再核对其行级意见。',{}
        elif (f['file_path'],f['sheet']) in owner_notices:
            notice=owner_notices[(f['file_path'],f['sheet'])]
            kind,key,title,details='表名归属确认',(f['file_path'],f['sheet']),notice['message'],notice
        elif dependency:
            kind,key,title,details=dependency
        elif record and category in {'coverage_mapping_unconfirmed','coverage_position_ambiguous'}:
            items=e.get('responsibility_items') or e.get('ambiguous_positions',[])
            kind='岗位对应确认';key=(*scope,items)
            title='请集中确认矩阵称谓与本单位实际部门、岗位的对应关系；确认后仍须逐条措施核对是否覆盖。'
            details={'responsibility_items':items,'approval_reusable':False}
        elif record and category in {'coverage_applicability_unknown','applicability_unclear','applicability_empty','not_applicable_reason_empty'}:
            kind='适用性填写核对';key=(*scope,record.file_path,record.sheet,category,norm_text(record.value('applicability')))
            title='请明确这些措施的本单位适用情况及必要原因，再核对岗位清单；相同填法集中列示，各措施仍须分别确认。'
            details={'current_value':record.value('applicability'),'all_rows_need_resolution':True}
        elif record and category=='carrier_reference_unmatched':
            current=matrices.get((*scope,measure_id_key(record.value('measure_id'))),[])
            state=sorted(r.value('carrier') for r in current)
            names=sorted(e.get('missing_references',[]))
            kind='载体引用核实';key=(*scope,measure_id_key(record.value('measure_id')),names,state,e.get('source_kind'))
            title='本措施引用的'+'、'.join('“'+n+'”' for n in names)+'未在当前矩阵载体中找到，请集中确认名称或引用关系。'
            details={'measure_id':record.value('measure_id'),'references':names,'matrix_carriers':state}
        elif category=='carrier_parse_unavailable':
            kind='解析规则补充';key=(f['business_code'],norm_text(e.get('original_sentence','')))
            title='相同职责句式尚未解析完整，请集中补充解析规则后逐行重新核对各单位矩阵。'
            details={'sentence':e.get('original_sentence',''),'approval_reusable':False}
        elif category=='same_duty_person_overlap':
            pair=sorted([(f['file_path'],f['sheet'],f['row']),(e['related_file'],e['related_sheet'],e['related_row'])])
            kind='业务核实';key=(*scope,'person_pair',pair,e.get('conflict_people',''))
            title=f['message'];details={}
        elif record and (category=='person_missing' or f['check_id']=='person_required'):
            kind='填写核对';key=(*scope,'person',record.file_path,record.sheet,record.row)
            title='请补全本行实际执行人员，再核对经办与审核的人员分工。';details={}
        elif record and f['check_id'] in {'broad_responsibility_pattern','explicit_role_responsibility'}:
            kind='责任表述核对';key=(*scope,'responsibility',norm_text(record.value('role')),norm_text(record.value('duty')))
            title='请核对本职责的责任表述及其与角色的对应关系；同一处修改涉及的规则集中列示。';details={}
        else:
            kind='业务核实' if f['severity']=='review' else '规则判定问题'
            current=matrices.get((*scope,measure_id_key(record.value('measure_id'))),[]) if record else []
            matrix_state=sorted((r.value('carrier'),r.value('control_measure'),r.value('responsibility')) for r in current)
            key=(*scope,f['check_id'],category,f['message'],measure_id_key(record.value('measure_id')) if record else '',matrix_state)
            title=f['message'];details={}
        identity=sha256_json([route,kind,key]);g=groups.setdefault(identity,{'task_id':identity[:16],'scope':route,'kind':kind,'message':title,'details':details,'findings':[],'locations':[],'rules':[],'severities':[]})
        g['findings'].append(f['finding_key'])
        location={'file':f['file_path'],'sheet':f['sheet'],'row':f['row'],'entity_code':f['entity_code'],
                  'business_id':f.get('business_id') or f['business_code'],
                  'business_code':f['business_code'],'variant_id':f['variant_id']}
        if location not in g['locations']:g['locations'].append(location)
        for affected in e.get('affected_locations', []):
            extra={**location,'file':affected['file'],'sheet':affected['sheet'],'row':affected['row']}
            if extra not in g['locations']:g['locations'].append(extra)
        if f['display_code'] not in g['rules']:g['rules'].append(f['display_code'])
        if f['severity'] not in g['severities']:g['severities'].append(f['severity'])
    tasks=list(groups.values())
    for g in tasks:
        g['finding_count']=len(g['findings']);g['affected_rows']=len(g['locations'])
        g['status']='待处理';g['resolution_applies_automatically']=False
    tasks.sort(key=lambda x:(x['scope']!='应审范围',x['kind'] not in {'读取处理','解析规则补充'},-x['finding_count'],x['task_id']))
    return tasks


def write_review_tasks(output,tasks):
    """写出复核事项；output 为报告目录，tasks 为 build_review_tasks 的归并结果。"""
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    write_json(output/'review_tasks.json',tasks)
    with (output/'复核事项.csv').open('w',encoding='utf-8-sig',newline='') as h:
        w=csv.writer(h);w.writerow(['事项编号','范围','类型','处理事项','关联规则','影响行数','原意见数','示例位置'])
        for t in tasks:
            first=t['locations'][0] if t['locations'] else {}
            w.writerow([t['task_id'],t['scope'],t['kind'],t['message'],'、'.join(t['rules']),t['affected_rows'],t['finding_count'],f"{first.get('file','')}#{first.get('sheet','')}!{first.get('row','')}"])
    in_scope=[t for t in tasks if t['scope']=='应审范围']
    summary={'tasks':len(tasks),'in_scope_tasks':len(in_scope),'in_scope_review_tasks':sum('review' in t['severities'] for t in in_scope),
             'in_scope_by_kind':dict(Counter(t['kind'] for t in in_scope)),
             'input_findings':sum(t['finding_count'] for t in tasks),'review_task_file':str(output/'复核事项.csv')}
    lines=['# 集中复核事项','',f"应审范围内共 {len(in_scope)} 个处理事项；其中含待核实意见的 {summary['in_scope_review_tasks']} 个。",'',
           '本表汇总外发意见。程序未完成的检查见内部处理事项及limitations.json，不能视为通过。整表问题保留全部影响位置；事项归并不是已确认缺陷统计。','',
           '| 类型 | 事项数 |','|---|---:|']
    lines.extend(f'| {k} | {v} |' for k,v in summary['in_scope_by_kind'].items())
    lines.extend(['','详见同目录的“复核事项.csv”；原意见与全部行位置在 review_tasks.json 保留。'])
    (output/'集中复核事项.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    write_json(output/'review_tasks_summary.json',summary)
    return summary
