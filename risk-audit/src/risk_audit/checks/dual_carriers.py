"""Check the carrier column and duty text as separate, attributable sources."""
from dataclasses import replace

from risk_audit.models import FieldValue
from risk_audit.util import norm_text
from risk_audit.checks.carriers import parse_carrier_references_v2


def set_subset_v2(ctx, params, *, reference_parser=parse_carrier_references_v2, alias_builder=None):
    # Reuse the frozen v1 parser and matrix prerequisites. A second source must
    # not widen the allowed carrier set or bypass any scope/ambiguity check.
    from risk_audit.checks.registry import set_subset

    column = params['source_field']
    body = params.get('fallback_explicit_field')
    originals = {r.record_id: r for r in ctx.records}

    def readable_text(r, field):
        value = r.fields.get(field) if field else None
        return value is not None and value.state != 'formula_no_cache' and bool(norm_text(value.current))

    def without_column(r):
        field = r.fields.get(column)
        return replace(r, fields={**r.fields, column: FieldValue('', '', field.coordinate if field else '', state='empty')})

    primary_records = [without_column(r) if r.fields.get(column) and r.fields[column].state == 'formula_no_cache' else r for r in ctx.records]
    primary = set_subset(replace(ctx, records=primary_records), params, reference_parser=reference_parser, alias_builder=alias_builder)
    prerequisites = {'carrier_scope_unconfirmed', 'carrier_measure_id_missing', 'carrier_target_unavailable', 'matrix_ambiguous'}
    blocked = {i['record'].record_id for i in primary if i['evidence'].get('issue_type') in prerequisites}
    result = []

    def append(item, source_field):
        original = originals[item['record'].record_id]
        field = original.fields.get(source_field)
        evidence = {**item['evidence'], 'source_field': source_field,
                    'source_kind': 'carrier_field' if source_field == column else 'duty_text',
                    'source_cell': field.coordinate if field else ''}
        result.append({**item, 'record': original, 'evidence': evidence})

    for item in primary:
        r = originals[item['record'].record_id]
        if r.record_id in blocked:
            result.append({**item, 'record': r})
        else:
            append(item, column if readable_text(r, column) else body)

    # The old capability reads the column OR the body. Force only the second
    # read on temporary records, while all persisted records remain unchanged.
    secondary_records = [without_column(r) for r in ctx.records
                         if r.record_id not in blocked and readable_text(r, column) and body != column and readable_text(r, body)]
    if secondary_records:
        for item in set_subset(replace(ctx, records=secondary_records), params, reference_parser=reference_parser, alias_builder=alias_builder):
            append(item, body)

    for r in ctx.records:
        if r.record_id in blocked:
            continue
        unreadable = []
        field = r.fields.get(column)
        if field is not None and field.state == 'formula_no_cache':
            unreadable.append(column)
        if body and body != column and not readable_text(r, body):
            unreadable.append(body)
        for source_field in unreadable:
            append({'record': r, 'kind': 'review', 'evidence': {
                'issue_type': 'carrier_source_unavailable',
                'unavailable_reason': '本来源栏目缺失、未填写或公式没有可读取的计算结果，不能视为引用核对通过',
            }}, source_field)
    return result


def set_subset_v3(ctx, params):
    from risk_audit.checks.carriers_v3 import parse_carrier_references_v3, local_carrier_aliases
    return set_subset_v2(ctx, params, reference_parser=parse_carrier_references_v3, alias_builder=local_carrier_aliases)
