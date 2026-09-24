"""V2 duty constraints and same-duty separation, without semantic model calls."""
from __future__ import annotations

from collections import defaultdict
from itertools import product
import re

from risk_audit.util import measure_id_key, norm_text


def issue(row, kind, category, **evidence):
    return {'record': row, 'kind': kind, 'evidence': {'issue_type': category, **evidence}}


def field_constraints(ctx, p):
    out = []
    for row in ctx.records:
        field = row.fields.get(p['field'])
        if field is None or field.state == 'formula_no_cache':
            out.append(issue(row, 'review', 'field_unavailable', field=p['field']))
            continue
        text = norm_text(field.current)
        if not text or text in {norm_text(v) for v in p['placeholders']} or any(re.search(rx, text) for rx in p['placeholder_patterns']):
            out.append(issue(row, 'violation', 'field_not_concrete', field=p['field']))
        if any(s in field.current for s in p['separators']):
            out.append(issue(row, 'violation', 'multiple_positions', field=p['field'], actual_text=field.current))
    return out


def resolved_role(row):
    explicit = norm_text(row.value('role'))
    if explicit:
        return explicit if explicit in {'经办', '审核', '审批'} else ''
    # Only explicit responsibility phrases can supply a missing role.
    matches = {m.group(1) for m in re.finditer(r'(?:负有?|承担)(主体|审核|审批)责任', norm_text(row.value('duty')))}
    return {'主体': '经办', '审核': '审核', '审批': '审批'}.get(next(iter(matches)), '') if len(matches) == 1 else ''


def duty_signature(text):
    # Retain action words, objects and punctuation; remove only role-type differences.
    return re.sub(r'(主体|审核)(?=责任)', '角色', norm_text(text))


def roles_same_duty(ctx, p):
    out = []
    groups = defaultdict(list)
    for row in ctx.records:
        role_field = row.fields.get('role')
        explicit = norm_text(row.value('role'))
        if role_field is not None and role_field.state == 'formula_no_cache':
            out.append(issue(row, 'review', 'explicit_role_unavailable'))
        elif role_field is None or not explicit:
            out.append(issue(row, 'violation', 'explicit_role_missing'))
        elif explicit not in {'经办', '审核', '审批'}:
            out.append(issue(row, 'violation', 'invalid_role', actual_text=row.value('role')))
        role = resolved_role(row)
        if not role:
            continue
        names = [x for x in row.person_keys if x.get('name') and not re.search(r'等|…|\.{3}|待定', x['name']) and x['name'] not in {'/', '-', '无'}]
        if not names:
            out.append(issue(row, 'review', 'person_missing'))
        raw_mid = norm_text(row.value('measure_id'))
        if not row.entity_code or not row.business_code or row.variant_id == 'unknown' or not raw_mid:
            out.append(issue(row, 'review', 'separation_scope_missing'))
            continue
        mid = measure_id_key(raw_mid)
        if role in {'经办', '审核'}:
            groups[(row.entity_code, row.business_code, row.variant_id, mid)].append((row, role, names))

    # Emit only on rows actually involved, with the counterpart location as evidence.
    for entries in groups.values():
        handlers = [x for x in entries if x[1] == '经办']
        reviewers = [x for x in entries if x[1] == '审核']
        for (left, _, lp), (right, _, rp) in product(handlers, reviewers):
            overlap = {}
            for a, b in product(lp, rp):
                ai, bi = a.get('person_id'), b.get('person_id')
                if ai and bi:
                    if norm_text(ai) == norm_text(bi):
                        overlap[(norm_text(ai), True)] = a['name']
                elif norm_text(a['name']) == norm_text(b['name']):
                    overlap[(norm_text(a['name']), False)] = a['name']
            if not overlap:
                continue
            if any(not x.value('duty') or x.fields['duty'].state == 'formula_no_cache' for x in (left, right)):
                for row, other in ((left, right), (right, left)):
                    out.append(issue(row, 'review', 'separation_duty_missing', related_file=other.file_path, related_sheet=other.sheet, related_row=other.row))
                continue
            if duty_signature(left.value('duty')) != duty_signature(right.value('duty')):
                continue
            for reliable in (True, False):
                names = sorted({name for (_, confirmed), name in overlap.items() if confirmed == reliable})
                if not names:
                    continue
                for row, other in ((left, right), (right, left)):
                    out.append(issue(row, 'violation' if reliable else 'review', 'same_duty_person_overlap', conflict_people='、'.join(names),
                                     related_file=other.file_path, related_sheet=other.sheet, related_row=other.row,
                                     positions=[row.value('position'), other.value('position')], duty_signature=duty_signature(row.value('duty'))))
    return out


def responsibility_phrase(ctx, p):
    out = []
    attrs = '|'.join(re.escape(x) for x in p['attributes'])
    pattern = re.compile(r'对[^。！？!?；;]*?(?:' + attrs + r')[^。！？!?；;]*?(?:负有?|承担)[^。！？!?；;]*?责任')
    for row in ctx.records:
        field = row.fields.get('duty')
        if field is None or field.state == 'formula_no_cache':
            out.append(issue(row, 'review', 'responsibility_unavailable'))
        else:
            text = norm_text(field.current)
            matches = list(pattern.finditer(text))
            accepted = any(not re.search(r'不负|不承担|无需|不需要', m.group()) and not text[max(0, m.start()-1):m.start()] == '不' for m in matches)
            if not accepted:
                out.append(issue(row, 'violation', 'responsibility_phrase_missing'))
    return out


def responsibility_phrase_v2(ctx, p):
    """Also accept an explicit object/quality + 负责 predicate, not an action."""
    attrs = '|'.join(re.escape(x) for x in p['attributes'])
    attribute_list = rf'(?:{attrs})(?:(?:[、,，]|及|和|与)(?:{attrs}))*'
    negative = r'(?:不(?:必|用|再|得|应|应当|需要|能|会|愿)?|无需|无须|免于)'
    next_clause = rf'[，,](?:但|并|且|而)?(?:{negative})?对'
    pattern = re.compile(rf'对(?P<object>(?:(?!{next_clause})[^。！？!?；;])*?)' + attribute_list +
                         r'[，,]?(?:直接|全面|共同|分别)?负责(?=[，,。！？!?；;]|$)')
    negatives = re.compile(negative + r'(?:直接|全面|共同|分别)?$')

    def accepted(text):
        text = norm_text(text)
        for match in pattern.finditer(text):
            obj = match.group('object').strip('的、,，与及和')
            # Attribute-only lists are not a responsibility object. Never
            # cross another 对-clause and accidentally consume its negation.
            object_content = re.sub(rf'(?:{attrs})|[的、,，及和与]', '', obj)
            if not object_content or obj.startswith(('照', '比')):
                continue
            if negatives.search(text[:match.start()]):
                continue
            return True
        return False

    return [item for item in responsibility_phrase(ctx, p)
            if item['evidence'].get('issue_type') != 'responsibility_phrase_missing'
            or not accepted(item['record'].value('duty'))]


def responsibility_phrase_v3(ctx, p):
    """Quality adjectives have the same role as their 性 forms in a duty clause."""
    attributes=list(dict.fromkeys([*p['attributes'],*(x[:-1] for x in p['attributes'] if x.endswith('性'))]))
    return responsibility_phrase_v2(ctx,{**p,'attributes':attributes})


def roles_same_duty_v2(ctx, p):
    issues=roles_same_duty(ctx,p)
    for item in issues:
        row=item['record'];category=item['evidence']['issue_type']
        missing='role' if category=='explicit_role_missing' else 'person_names' if category=='person_missing' else None
        if missing and missing not in row.fields:
            item['kind']='review'
            item['evidence'].update(issue_type='separation_field_unavailable',field=missing)
    return issues
