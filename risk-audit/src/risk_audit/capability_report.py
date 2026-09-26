"""Explain incomplete checks without confusing material defects with software gaps."""
from collections import Counter, defaultdict
import csv
from pathlib import Path

from risk_audit.util import norm_text, sha256_json, write_json


def build_capability_report(files, limitations, findings, scopes):
    records = {(r.file_path, r.sheet, r.row): r for f in files for s in f.sheets for r in s.records}
    sources = {str(f.relative_path): f for f in files}
    front = defaultdict(list)
    for finding in findings:
        f = finding.to_dict() if hasattr(finding, 'to_dict') else finding
        front[(f['file_path'], f['sheet'], f['row'])].append(f)
    details = []
    for limit in limitations:
        ec = limit.get('entity_code')
        if not ec or (limit.get('business_id') or limit.get('business_code'), limit.get('variant_id')) not in {
                (b.get('business_id') or b['business_code'], b['variant_id'])
                for b in scopes.get(ec, {}).get('businesses', [])}:
            continue
        e = limit['evidence']; field = e.get('field', '')
        loc = (limit.get('file_path'), limit.get('sheet'), limit.get('row'))
        row = records.get(loc); value = row.fields.get(field) if row and field else None
        text = value.current if value else ''
        read_status = 'field_unspecified' if not field else 'source_unconfirmed' if value is None else (
            'formula_unavailable' if value.state == 'formula_no_cache' else 'empty' if not norm_text(text) else 'value')
        category = 'source_needs_investigation'
        interpretation = 'not_started'
        if field in {'role', 'person_names', 'measure_id', 'applicability', 'duty'} and value is not None and value.state != 'formula_no_cache':
            missing = norm_text(text) in {'', '/', '／'}
            invalid_role = field == 'role' and norm_text(text) not in {'经办', '审核', '审批'}
            if missing or invalid_role:
                category = 'material_precondition'
                interpretation = 'invalid_role' if invalid_role and not missing else 'missing_value'
            elif field == 'applicability':
                category = 'interpretation_unsupported'; interpretation = 'undecided'
            elif field == 'duty' and e.get('cause_type') == 'parser_incomplete':
                category = 'grammar_unsupported'; interpretation = 'unsupported'
        if e.get('issue_type') in {'responsibility_structure_unresolved', 'responsibility_role_unresolved'}:
            category = 'grammar_unsupported'; interpretation = 'unsupported'
        # Reference failures are material preconditions only when the existing
        # measure-ID rule has independently reported the same row's defect.
        previous = [f for f in front[loc] if f['check_id'] in {
            'measure_exists', 'measure_id_required', 'person_required', 'duty_required', 'handler_reviewer_overlap', 'applicability_values'}]
        if e.get('issue_type') in {'deletion_matrix_missing', 'deletion_measure_missing'} and any(
                f['check_id'] in {'measure_exists', 'measure_id_required'} and f['severity'] == 'violation' for f in previous):
            category = 'material_precondition'; interpretation = 'reference_defect_reported'
        source = sources.get(limit.get('file_path'))
        details.append({
            'source_group_id': sha256_json([ec, *loc, field, category])[:20],
            'check_id': limit['check_id'], 'rule': limit['display_code'], 'category': category,
            'read_status': read_status, 'interpretation_status': interpretation,
            'decision_status': 'unfinished_due_to_material' if category == 'material_precondition' else 'unfinished_due_to_capability_or_source',
            'auto_pass': False, 'entity_code': ec, 'file': loc[0], 'sheet': loc[1], 'row': loc[2],
            'field': field, 'cell': value.coordinate if value else None, 'raw_text': text,
            'source_sha256': source.sha256 if source else None,
            'field_source': e.get('field_source'), 'issue_type': e.get('issue_type'),
            'root_cause_id': e.get('root_cause_id'), 'blocked_by': e.get('blocked_by'),
            'existing_material_findings': [{'finding_key': f['finding_key'], 'check_id': f['check_id'],
                                            'message': f['message']} for f in previous],
        })
    counts = Counter(x['category'] for x in details)
    return {'schema_version': 1, 'scope': '已确认主体应审业务', 'unfinished_checks': len(details),
            'affected_rows': len({(x['file'], x['sheet'], x['row']) for x in details}),
            'source_groups': len({x['source_group_id'] for x in details}),
            'by_category': dict(counts), 'automatic_passes': 0,
            'note': '检查记录、影响行、来源组分别计数；来源待查不等于已经证实的程序错误。',
            'details': details}


def write_capability_report(output, report):
    output = Path(output)
    write_json(output / 'capability_diagnostics.json', report)
    labels = {'material_precondition': '材料前置问题', 'interpretation_unsupported': '适用含义尚未确定',
              'grammar_unsupported': '责任句式尚未解析', 'source_needs_investigation': '字段或矩阵来源待查'}
    with (output / '程序判定能力明细.csv').open('w', encoding='utf-8-sig', newline='') as h:
        writer = csv.writer(h)
        writer.writerow(['来源组', '分类', '规则', '检查项', '单位代码', '文件', '工作表', '行', '字段', '单元格',
                         '读取状态', '解释状态', '结论状态', '原文', '已报材料问题数'])
        for d in report['details']:
            writer.writerow([d['source_group_id'], labels[d['category']], d['rule'], d['check_id'], d['entity_code'],
                             d['file'], d['sheet'], d['row'], d['field'], d['cell'], d['read_status'],
                             d['interpretation_status'], d['decision_status'], d['raw_text'], len(d['existing_material_findings'])])
    return {k: v for k, v in report.items() if k != 'details'}
