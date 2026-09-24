"""Versioned checks for source attribution and consolidated ordering evidence."""
from __future__ import annotations
from collections import defaultdict
import re

from risk_audit.checks.carriers import split_carrier_list
from risk_audit.checks.carriers_v4 import parse_carrier_references_v4, carrier_aliases_v4
from risk_audit.checks.dual_carriers import set_subset_v2
from risk_audit.util import measure_id_key, norm_text, natural_key


def responsibility_phrase_v4(ctx, params):
    from risk_audit.checks.duty_details import responsibility_phrase_v3
    attributes = list(dict.fromkeys([*params['attributes'], *(x[:-1] for x in params['attributes'] if x.endswith('性'))]))
    qualities = '|'.join(re.escape(x) for x in sorted(attributes, key=len, reverse=True))
    pattern = re.compile(rf'(?P<object>[^。!?！？;]{{2,180}}?)(?:{qualities})[^。!?！？;]{{0,80}}?(?:具有|负有?|承担|有)(?:主体|审核|审批|复核)?责任')
    negative = re.compile(r'不(?:负|承担|具有|对)|没(?:有|有义务)|无需|无须|免于|不需要')
    out = []
    candidates = responsibility_phrase_v3(ctx, params)
    existing = {i['record'].record_id for i in candidates}
    for row in ctx.records:
        if row.record_id not in existing and re.search(r'(?:无需|无须|免于|不需要)对', norm_text(row.value('duty'))):
            candidates.append({'record':row,'kind':'violation','evidence':{'issue_type':'responsibility_phrase_missing'}})
    for issue in candidates:
        if issue['evidence'].get('issue_type') != 'responsibility_phrase_missing':
            out.append(issue); continue
        text = norm_text(issue['record'].value('duty'))
        accepted = False
        for match in pattern.finditer(text):
            obj = re.sub(rf'(?:{qualities})|[对的、,及和与]', '', match['object'])
            if len(obj) >= 2 and not negative.search(match[0]):
                accepted = True; break
        if not accepted: out.append(issue)
    return out


def responsibility_coverage_v4(ctx, params):
    from risk_audit.checks.coverage import responsibility_coverage_v3
    issues = responsibility_coverage_v3(ctx, params)
    index = defaultdict(list)
    for r in ctx.all_records:
        if r.record_type == 'position_duty':
            index[(r.entity_code, r.business_code, r.variant_id, measure_id_key(r.value('measure_id')))].append(r)
    for issue in issues:
        e = issue['evidence']; r = issue['record']
        if e.get('issue_type') != 'coverage_mapping_unconfirmed': continue
        related = index[(r.entity_code, r.business_code, r.variant_id, measure_id_key(r.value('measure_id')))]
        details = e.setdefault('mapping_details', [])
        existing = {x['responsibility'] for x in details}
        for item in e.get('responsibility_items', []):
            if item in existing: continue
            parts = [x for x in re.split(r'[-—－]', norm_text(item)) if x]
            if len(parts) < 3: continue
            dept, position = parts[-2:]
            same_dept = [x for x in related if norm_text(x.value('department')) == dept]
            same_position = [x for x in related if norm_text(x.value('position')) == position]
            matches = same_dept or same_position
            details.append({'responsibility': item, 'matrix_department': dept, 'matrix_position': position,
                            'duty_positions': sorted({x.value('position') for x in same_dept}),
                            'department_candidates': sorted({x.value('department') for x in same_position}) if not same_dept else [],
                            'duty_rows': [{'file': x.file_path, 'sheet': x.sheet, 'row': x.row,
                                           'department': x.value('department'), 'position': x.value('position')} for x in matches]})
    return issues


def responsibility_coverage_v5(ctx, params, *, include_semantic=True):
    from dataclasses import replace
    from risk_audit.applicability import interpret_record
    interpretations={r.record_id:interpret_record(r,ctx.resources) for r in ctx.records}
    originals={r.record_id:r for r in ctx.records};rows=[]
    for row in ctx.records:
        result=interpretations[row.record_id];field=row.fields.get('applicability')
        if field:
            canonical={'applicable':'适用','not_applicable':'不适用'}.get(result.decision,'')
            rows.append(replace(row,fields={**row.fields,'applicability':replace(field,current=canonical)}))
        else:rows.append(row)
    issues=responsibility_coverage_v4(replace(ctx,records=rows),params)
    from risk_audit.semantic import get_assistant
    assistant=get_assistant(ctx.resources) if include_semantic else None
    related=defaultdict(list); numbered=defaultdict(list)
    def numbered_key(row):
        """返回数字措施范围键；row 为矩阵或岗位记录。"""
        return row.entity_code, row.business_code, row.variant_id, measure_id_key(row.value('measure_id'))
    for r in ctx.all_records:
        if r.record_type=='position_duty':
            related[(r.entity_code,r.business_code,r.variant_id,measure_id_key(r.value('measure_id')))].append(r)
            key=numbered_key(r)
            if key:numbered[key].append(r)
    for issue in issues:
        rid=issue['record'].record_id;issue['record']=originals[rid]
        issue['evidence']['applicability_interpretation']=interpretations[rid].to_dict()
        e=issue['evidence'];row=originals[rid]
        if e.get('issue_type')=='duty_record_missing':
            candidates=numbered.get(numbered_key(row),[])
            if candidates:
                issue['kind']='review';e['issue_type']='duty_measure_name_conflict'
                e['automatic_equivalence']=False
                e['candidate_measure_ids']=sorted({r.value('measure_id') for r in candidates})
                e['candidate_rows']=[{'file':r.file_path,'sheet':r.sheet,'row':r.row,
                    'cell':r.fields['measure_id'].coordinate,'measure_id':r.value('measure_id')} for r in candidates]
                e['matching_basis']='同主体、业务、变体、业务前缀、环节序号及措施序号；名称不一致，未建立跨表关联'
        if assistant and e.get('issue_type') in {'coverage_mapping_unconfirmed','specific_responsibility_missing'}:
            scope=(row.entity_code,row.business_code,row.variant_id,measure_id_key(row.value('measure_id')))
            entries=[{'text':name,'concept':'same_measure_department_position'} for name in sorted({
                r.value('department')+'-'+r.value('position') for r in related[scope]
                if r.value('department') and r.value('position')})]
            if entries:
                e['semantic_candidates']=[assistant.suggest(item,entries,domain='responsibility',scope=scope)
                                          for item in (e.get('responsibility_items') or e.get('missing_responsibilities') or []) if len(item)<=160]
    return issues


def set_subset_v4(ctx, params, *, reference_parser=parse_carrier_references_v4):
    issues = set_subset_v2(ctx, params, reference_parser=reference_parser, alias_builder=carrier_aliases_v4)
    matrices = defaultdict(list)
    for r in ctx.all_records:
        if r.record_type == 'matrix':
            matrices[(r.entity_code, r.business_code, r.variant_id, measure_id_key(r.value('measure_id')))].append(r)
    out = []
    for issue in issues:
        e = issue['evidence']; r = issue['record']
        if e.get('issue_type') != 'carrier_reference_unmatched':
            out.append(issue); continue
        current = matrices[(r.entity_code, r.business_code, r.variant_id, measure_id_key(r.value('measure_id')))]
        baseline = ctx.baselines.get((r.business_id or r.business_code, r.variant_id), {})
        base_rows = [b for b in baseline.get('measures', []) if measure_id_key(b['measure_id']) == measure_id_key(r.value('measure_id'))]
        inherited = []; still_missing = list(e['missing_references'])
        # No approval from a control sentence. This remains an unfinished
        # template check, with frozen baseline evidence, not an allowed alias.
        if len(base_rows) == 1 and current:
            b = base_rows[0]
            for name in still_missing:
                if (name in b['control_measure'] and name not in b['carrier']
                    and all(name in x.value('control_measure') and name not in x.value('carrier')
                            and split_carrier_list(x.value('carrier')) == split_carrier_list(b['carrier']) for x in current)):
                    inherited.append(name)
        if inherited:
            relation = {**base_rows[0], 'path': baseline['path'], 'sha256': baseline['sha256'],
                        'relation': 'same_measure_same_carrier_list_and_shared_control_reference'}
            out.append({**issue, 'kind': 'limitation', 'evidence': {**e,
                'issue_type': 'carrier_template_inconsistency', 'missing_references': inherited,
                'publication_channel': 'internal', 'cause_type': 'baseline_inconsistency',
                'baseline_relation': relation,
                'current_matrix': [{'file': x.file_path, 'sheet': x.sheet, 'row': x.row,
                                    'carrier': x.value('carrier'), 'control_measure': x.value('control_measure')} for x in current],
                'unavailable_reason': '同编号省公司基准及当前矩阵的措施文字均提到'+ '、'.join(inherited) +'，但相同载体列表均未列出；需集中确认模板口径，未判通过。'}})
            still_missing = [x for x in still_missing if x not in inherited]
        if still_missing:
            out.append({**issue, 'evidence': {**e, 'missing_references': still_missing,
                'matrix_sources': [{'file': x.file_path, 'sheet': x.sheet, 'row': x.row, 'carrier': x.value('carrier')} for x in current]}})
    return out


def ordered_records_v2(ctx, params):
    from risk_audit.checks.registry import ordered_records
    findings = ordered_records(ctx, params)
    groups = defaultdict(list); all_rows = defaultdict(list)
    for i in findings: groups[(i['record'].file_path, i['record'].sheet)].append(i)
    for r in ctx.records: all_rows[(r.file_path, r.sheet)].append(r)
    out = []
    for key, issues in groups.items():
        rows = sorted(all_rows[key], key=lambda r: r.row)
        representative = min((i['record'] for i in issues), key=lambda r: r.row)
        baseline = ctx.baselines.get((representative.business_id or representative.business_code, representative.variant_id), {})
        rank = {norm_text(mid): index for index, mid in enumerate(baseline.get('measure_order', []))}
        measures = {norm_text(r.value(params['field'])) for r in rows if r.value(params['field'])}
        unknown = sorted(measures - rank.keys(), key=natural_key) if rank else []
        order = sorted(measures & rank.keys(), key=rank.get) if rank else sorted(measures, key=natural_key)
        out.append({'record': representative, 'kind': 'violation', 'evidence': {
            'issue_type': 'sheet_measure_order', 'reason': '整表措施排序', 'issue_count': len(issues),
            'affected_locations': [{'file': i['record'].file_path, 'sheet': i['record'].sheet, 'row': i['record'].row,
                                    **i['evidence']} for i in issues],
            'expected_measure_order': order, 'unranked_measures': unknown,
            'ordering_basis': {'path': baseline.get('path'), 'sha256': baseline.get('sha256')} if rank else {'method': 'natural_measure_order'},
            'all_measure_rows': [{'row': r.row, 'measure_id': r.value(params['field'])} for r in rows]}})
    return out
