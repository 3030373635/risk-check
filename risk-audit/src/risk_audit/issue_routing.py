"""Publication policy: an incomplete check is never a unit defect."""
from __future__ import annotations

from collections import Counter
import csv
from pathlib import Path

from risk_audit.util import norm_text, sha256_json, write_json


TECHNICAL = {
    'responsibility_structure_unresolved': ('parser_incomplete', 'duty'),
    'responsibility_role_unresolved': ('parser_incomplete', 'duty'),
    'applicability_meaning_unresolved': ('interpretation_incomplete', 'applicability'),
    'carrier_parse_unavailable': ('parser_incomplete', 'duty'),
    'carrier_source_unavailable': ('field_source_unavailable', 'duty'),
    'carrier_target_unavailable': ('field_source_unavailable', 'carrier'),
    'carrier_scope_unconfirmed': ('scope_unconfirmed', ''),
    'carrier_measure_id_missing': ('dependency_incomplete', 'measure_id'),
    'matrix_ambiguous': ('matrix_source_unconfirmed', 'measure_id'),
    'coverage_scope_unconfirmed': ('scope_unconfirmed', ''),
    'coverage_applicability_unknown': ('dependency_incomplete', 'applicability'),
    'coverage_measure_id_missing': ('dependency_incomplete', 'measure_id'),
    'separation_scope_missing': ('dependency_incomplete', 'measure_id'),
    'separation_field_unavailable': ('field_source_unavailable', ''),
    'explicit_role_unavailable': ('field_source_unavailable', 'role'),
    'field_unavailable': ('field_source_unavailable', ''),
    'responsibility_unit_unavailable': ('field_source_unavailable', 'responsibility'),
    'schema_field_mapping': ('field_source_unavailable', ''),
    'applicability_cache_missing': ('field_source_unavailable', 'applicability'),
    'reason_cache_missing': ('field_source_unavailable', 'applicability_reason'),
    'deletion_matrix_missing': ('matrix_source_unconfirmed', 'measure_id'),
    'deletion_source_unavailable': ('field_source_unavailable', ''),
    'deletion_scope_unknown': ('scope_unconfirmed', ''),
    'deletion_measure_missing': ('dependency_incomplete', 'measure_id'),
    'person_missing': ('dependency_incomplete', 'person_names'),
    'responsibility_unavailable': ('field_source_unavailable', 'duty'),
    'carrier_template_inconsistency': ('baseline_inconsistency', 'carrier'),
}
DEPENDENCIES = {'applicability': 'applicability_values', 'role': 'handler_reviewer_overlap',
                'person_names': 'person_required', 'measure_id': 'measure_id_required', 'duty': 'duty_required'}


def route_issue(issue, check, policy):
    """区分材料问题与程序限制；issue 为检查结果，check 为条款配置，policy 为发布口径。"""
    if not policy or policy.get('version') not in {1, 2}:
        return issue
    e = dict(issue['evidence']); category = e.get('issue_type', '')
    record = issue.get('record')
    # 确认稿要求明确缺列向单位提示；句式未支持等程序限制仍保留内部记录。
    if policy.get('version') == 2 and issue['kind'] == 'review':
        if category == 'schema_field_mapping' and e.get('missing_fields'):
            # 基准必需字段未识别是需单位核实的整表事项，不能仅留内部限制。
            e.update(publication_channel='unit', cause_type='material_problem')
            return {**issue, 'evidence': e, 'location_policy': 'material'}
        field = e.get('field', '')
        if category == 'separation_field_unavailable':
            field = field or 'role'
        if record and field and field not in record.fields and category in {'field_unavailable', 'separation_field_unavailable', 'responsibility_unavailable'}:
            e.update(publication_channel='unit', cause_type='material_problem')
            # 岗位清单的第8、10条意见属于原工作表明细，不得通过资料级兜底转写到风控矩阵。
            location_policy = 'row' if category in {'separation_field_unavailable', 'responsibility_unavailable'} else 'material'
            return {**issue, 'evidence': e, 'location_policy': location_policy}
    if issue['kind'] == 'violation':
        e.update(publication_channel='unit', cause_type='material_problem')
        return {**issue,'evidence':e}
    record = issue.get('record'); field = e.get('source_field') or e.get('field', '')
    route = TECHNICAL.get(category)
    if check.get('operator') == 'reference_exists' and issue['kind'] == 'review':
        route = ('matrix_source_unconfirmed', 'measure_id')
    if check['check_id'] == 'explicit_role_responsibility' and record:
        role = record.fields.get('role')
        if role is None or role.state == 'formula_no_cache' or norm_text(role.current) not in {'经办', '审核', '审批'}:
            route = ('dependency_incomplete', 'role')
    if field and record:
        source = record.fields.get(field)
        if source is None or source.state == 'formula_no_cache':
            route = route or ('field_source_unavailable', field)
    if not route:
        if issue['kind']=='review': e.update(publication_channel='unit',cause_type='business_difference')
        return {**issue,'evidence':e}
    cause, default_field = route
    field = field or default_field
    if category == 'coverage_applicability_unknown' and record and 'applicability' not in record.fields:
        cause = 'field_source_unavailable'
    e.update(publication_channel='internal', cause_type=cause, field=field,
             blocked_by=DEPENDENCIES.get(field, 'source_resolution') if cause == 'dependency_incomplete' else 'source_resolution')
    return {**issue, 'kind': 'limitation', 'evidence': e}


def build_internal_diagnostics(limitations, files, scopes):
    selections = {(str(f.relative_path), x['sheet'], x['field']): x
                  for f in files for x in f.preservation.get('column_selections', [])}
    groups = {}
    for detail in limitations:
        e = detail['evidence']; cause = e.get('cause_type', 'check_incomplete')
        ec, business_id, bc, variant = (detail.get(x, '') for x in ('entity_code', 'business_id', 'business_code', 'variant_id'))
        scope = '应审范围' if ec and (business_id or bc, variant) in {
            (b.get('business_id') or b['business_code'], b['variant_id'])
            for b in scopes.get(ec, {}).get('businesses', [])
        } else '范围外或归属待确认'
        field = e.get('field', '')
        source = (detail.get('file_path', ''), detail.get('sheet', ''), field)
        if cause == 'parser_incomplete':
            fragments = e.get('unavailable_fragments') or [e.get('responsibility_facts', {}).get('normalized_text') or e.get('unavailable_reason', '').partition('解析：')[2] or e.get('original_sentence', '')]
            key = (cause, bc, sorted({norm_text(x) for x in fragments}))
        elif cause == 'baseline_inconsistency':
            b = e['baseline_relation']
            key = (cause, bc, variant, b['sha256'], b['measure_id'], e.get('missing_references', []))
        elif cause == 'dependency_incomplete':
            key = (cause, ec, *source, e.get('blocked_by'))
        else:
            key = (cause, ec, *source, e.get('issue_type') if not field else '')
        root_id = sha256_json([scope, key])[:20]
        e['root_cause_id'] = root_id
        if source in selections: e['field_source'] = selections[source]
        group = groups.setdefault(root_id, {'root_cause_id': root_id, 'scope': scope, 'cause_type': cause,
                                           'status': '未完成，修复前置问题后重审', 'auto_pass': False,
                                           'blocked_by': [], 'affected_locations': [], 'checks': [], 'details': []})
        location = {k: detail.get(k, '') for k in ('entity_code', 'business_id', 'business_code', 'variant_id', 'file_path', 'sheet', 'row')}
        if location not in group['affected_locations']: group['affected_locations'].append(location)
        if e.get('blocked_by') and e['blocked_by'] not in group['blocked_by']: group['blocked_by'].append(e['blocked_by'])
        if detail['check_id'] not in group['checks']: group['checks'].append(detail['check_id'])
        group['details'].append(detail)
    return sorted(groups.values(), key=lambda g: (g['scope'] != '应审范围', g['cause_type'], -len(g['details']), g['root_cause_id']))


def write_internal_diagnostics(output, groups):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'internal_diagnostics.json', groups)
    labels = {'parser_incomplete': '解析规则待完善', 'field_source_unavailable': '字段来源待处理',
              'dependency_incomplete': '等待前置字段补齐', 'matrix_source_unconfirmed': '矩阵来源待确认',
              'baseline_inconsistency': '省公司模板口径待确认', 'scope_unconfirmed': '主体范围待确认'}
    with (output / '内部处理事项.csv').open('w', encoding='utf-8-sig', newline='') as h:
        w = csv.writer(h); w.writerow(['根因编号', '范围', '类型', '未完成检查数', '影响行数', '前置事项', '示例原因', '状态'])
        for g in groups:
            w.writerow([g['root_cause_id'], g['scope'], labels.get(g['cause_type'], g['cause_type']), len(g['details']),
                        len(g['affected_locations']), '、'.join(g['blocked_by']), g['details'][0]['evidence'].get('unavailable_reason', ''), g['status']])
    current = [g for g in groups if g['scope'] == '应审范围']
    return {'groups': len(groups), 'in_scope_groups': len(current),
            'in_scope_unfinished_checks': sum(len(g['details']) for g in current),
            'in_scope_by_cause': dict(Counter(g['cause_type'] for g in current)),
            'report': str(output / '内部处理事项.csv')}
