"""Separate an unread responsibility column from a genuinely empty cell."""
from risk_audit.semantic_matching import responsibility_coverage_v6


def responsibility_coverage_v7(ctx, params):
    issues = responsibility_coverage_v6(ctx, params)
    for issue in issues:
        if issue['evidence'].get('issue_type') != 'coverage_mapping_unconfirmed':
            continue
        row = issue['record']; value = row.fields.get('responsibility')
        if value is None or value.state == 'formula_no_cache':
            issue['kind'] = 'review'
            issue['evidence'].update(issue_type='responsibility_unit_unavailable', field='responsibility',
                                     unavailable_reason='责任主体栏目尚未读到有效值，未将读取问题认定为单位漏填。')
        elif not value.current.strip():
            issue['kind'] = 'violation'
            issue['evidence'].update(issue_type='responsibility_empty', field='responsibility',
                                     source_cell=value.coordinate, unavailable_reason='')
    return issues
