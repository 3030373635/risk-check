"""Concrete unit names in the matrix responsibility field, independent of coverage."""
from __future__ import annotations

import re
from typing import Any

from risk_audit.util import norm_text


def responsibility_items(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"[、,，;；\n]+", text) if x.strip()]


def responsibility_parts(text: str) -> list[str]:
    return [norm_text(x) for x in re.split(r"[-—－]", text) if norm_text(x)]


def generic_unit_segments(item: str, names: set[str]) -> list[str]:
    parts = responsibility_parts(item)
    # Unit–department–position; earlier segments may name parent units.
    # For incomplete entries, recognize an explicit generic unit at the start.
    units = parts[:-2] if len(parts) >= 3 else parts[:1]
    return list(dict.fromkeys(x for x in units if x in names))


def responsibility_unit_specific(ctx: Any, p: dict) -> list[dict]:
    names = {norm_text(x) for x in p['generic_unit_names']}
    out = []
    for row in ctx.records:
        if re.match(r"^(?:不适用|否)", norm_text(row.value('applicability'))):
            continue
        field = row.fields.get(p['field'])
        if field is None or field.state == 'formula_no_cache':
            out.append({'record': row, 'kind': 'review', 'evidence': {
                'issue_type': 'responsibility_unit_unavailable', 'field': p['field'],
                'unavailable_reason': '未能读取矩阵责任主体，暂时无法检查责任单位名称。',
            }})
            continue
        matches = []
        for item in responsibility_items(field.current):
            units = generic_unit_segments(item, names)
            if units:
                matches.append({'responsibility': item, 'unit_names': units, 'source_cell': field.coordinate})
        if matches:
            out.append({'record': row, 'kind': 'violation', 'evidence': {
                'issue_type': 'responsibility_unit_not_concrete', 'field': p['field'],
                'generic_unit_names': list(dict.fromkeys(name for m in matches for name in m['unit_names'])),
                'matches': matches, 'source_cell': field.coordinate,
            }})
    return out
