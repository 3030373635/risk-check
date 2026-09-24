"""Exact reappearance of struck matrix text in effective position duties."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from risk_audit.util import measure_id_key, norm_text


def literal_text(value: str) -> str:
    # Layout spaces/newlines are ignored; wording, order and punctuation remain.
    return re.sub(r"\s+", "", str(value or ""))


def numbered_items(text: str) -> list[dict[str, Any]]:
    """Literal numbered entries with offsets in the original cell, not NLP tokens."""
    marker = re.compile(r"(?:^|(?<=[\n；;。]))[ \t]*(?:[0-9０-９]+[.．、]|[（(][0-9０-９]+[)）])[ \t]*")
    depths = []; depth = 0
    for char in text:
        depths.append(depth)
        if char in "（(《【[": depth += 1
        elif char in "）)》】]": depth = max(0, depth - 1)
    markers = [m for m in marker.finditer(text) if depths[m.start()] == 0]
    out = []
    for i, match in enumerate(markers):
        start = match.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        while start < end and text[start].isspace(): start += 1
        while end > start and (text[end - 1].isspace() or text[end - 1] in "。；;"): end -= 1
        if start < end:
            out.append({"text": text[start:end], "start": start, "end": end})
    return out


def _retained(candidate: dict[str, Any], source: Any, fields: list[str]) -> bool:
    current = [literal_text(source.value(f)) for f in fields]
    if any(candidate["text"] in text for text in current):
        return True
    # Renumbering a fully replaced list does not make retained items forbidden.
    # This applies only to a complete numbered block, never arbitrary prose.
    raw = candidate["deleted_text"]
    if candidate["extraction"] == "contiguous_span" and re.match(r"\s*(?:[0-9０-９]+[.．、]|[（(][0-9０-９]+[)）])", raw):
        items = numbered_items(raw)
        return bool(items) and all(any(literal_text(item["text"]) in text for text in current) for item in items)
    return False


def deleted_text_reappears(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    return _deleted_text_reappears(ctx, p, final_text_wins=False)


def deleted_text_reappears_v2(ctx: Any, p: dict[str, Any]) -> list[dict[str, Any]]:
    return _deleted_text_reappears(ctx, p, final_text_wins=True)


def _deleted_text_reappears(ctx: Any, p: dict[str, Any], *, final_text_wins: bool) -> list[dict[str, Any]]:
    by_group = defaultdict(list)
    for source in ctx.all_records:
        if source.record_type in {"matrix", "deleted_matrix"}:
            by_group[(source.entity_code, source.business_code, source.variant_id)].append(source)
    out = []; reported = set()
    def review(row, category, reason, *, row_level=False):
        key = (row.file_path, row.sheet, category, row.row if row_level else None)
        if key not in reported:
            reported.add(key)
            out.append({"record": row, "kind": "review", "evidence": {"issue_type": category, "unavailable_reason": reason}})
    for row in ctx.records:
        if not row.entity_code or not row.business_code or row.variant_id == "unknown":
            review(row, "deletion_scope_unknown", "单位、业务或模板类型尚未确认，暂时无法核对矩阵中的删除内容。")
            continue
        sources = by_group.get((row.entity_code, row.business_code, row.variant_id), [])
        if p["match_scope"] == "measure":
            raw_mid = norm_text(row.value("measure_id"))
            if not raw_mid:
                review(row, "deletion_measure_missing", "本行未填写可读取的控制措施编号，暂时无法对应矩阵检查删除内容，请补充编号后再核对。", row_level=True)
                continue
            mid = measure_id_key(raw_mid)
            def source_id(source):
                field = source.fields.get("measure_id")
                value = field.current or (str(field.raw) if field.raw is not None else "") if field else ""
                return measure_id_key(value)
            sources = [s for s in sources if mid and source_id(s) == mid]
        if not sources:
            review(row, "deletion_matrix_missing", "未找到可供比对的矩阵记录，暂时无法检查删除内容，请核实矩阵是否齐全、归属及编号是否正确。")
            continue
        candidates = []; seen = set()
        incomplete = False
        for source in sources:
            for field_id in p["source_fields"]:
                field = source.fields.get(field_id)
                if field is None or field.deleted_unavailable:
                    incomplete = True; continue
                spans = [{**span, "extraction": "contiguous_span"} for span in field.deleted_spans]
                if final_text_wins:
                    # Read list boundaries from the complete original cell. A partially
                    # struck entry must never turn into a newly invented short keyword.
                    for item in numbered_items(str(field.raw or "")):
                        parent = next((s for s in field.deleted_spans if s["start"] <= item["start"] and item["end"] <= s["end"]), None)
                        if parent:
                            spans.append({**item, "extraction": "numbered_item", "parent_span": dict(parent)})
                for span in spans:
                    text = literal_text(span["text"])
                    if not text: continue
                    key = (source.file_path, source.sheet, field.coordinate, span["start"], span["end"])
                    if key in seen: continue
                    seen.add(key)
                    candidates.append({"text": text, "deleted_text": span["text"], "source_file": source.file_path,
                        "source_sheet": source.sheet, "source_cell": field.coordinate, "source_field": field_id,
                        "source_row": source.row, "source_measure_id": source.value("measure_id"),
                        "source_start": span["start"], "source_end": span["end"],
                        **({"extraction": span["extraction"], "parent_span": span.get("parent_span")} if final_text_wins else {})})
        if incomplete:
            review(row, "deletion_source_unavailable", "矩阵的控制措施或控制载体栏目未识别完整，或划删除线的公式没有计算结果，请补齐可读取的材料后再核对。")
        if not candidates: continue
        target = row.fields.get(p["target_field"])
        if target is None or target.state == "formula_no_cache":
            out.append({"record": row, "kind": "review", "evidence": {"issue_type": "deletion_duty_unavailable", "unavailable_reason": "本行岗位职责没有可读取的内容，暂时无法检查是否沿用矩阵中已删除的文字。"}})
            continue
        # A formula whose displayed value is itself struck has already been removed.
        duty = "" if target.formula and target.deleted_spans else target.current
        target_text = literal_text(duty)
        hits = []
        retained = []; conflicts = []
        for candidate in candidates:
            start = target_text.find(candidate["text"])
            if start >= 0:
                if final_text_wins:
                    current_sources = [s for s in sources if s.record_type == "matrix"]
                    kept = [s for s in current_sources if _retained(candidate, s, p["source_fields"])]
                    if kept:
                        locations = [{"file": s.file_path, "sheet": s.sheet, "row": s.row} for s in kept]
                        if len(kept) != len(current_sources):
                            conflicts.append({**candidate, "retained_at": locations})
                        else:
                            retained.append({**candidate, "retained_at": locations})
                        continue
                hits.append({**candidate, "target_cell": target.coordinate, "target_start_normalized": start,
                             "target_end_normalized": start + len(candidate["text"])})
        if conflicts:
            out.append({"record": row, "kind": "review", "evidence": {
                "issue_type": "deletion_current_conflict", "conflicting_texts": list(dict.fromkeys(x["deleted_text"] for x in conflicts)),
                "matches": conflicts, "unavailable_reason": "矩阵中同一措施出现多次，对部分删除文字是否保留的填写不一致，请确认最终采用内容后再核对岗位职责。",
            }})
        if hits:
            if final_text_wins:
                # A whole list and its constituent items may both hit. Keep complete
                # evidence, but avoid repeating contained text in the opinion.
                display_hits = [h for h in hits if not any(h["text"] != other["text"] and h["text"] in other["text"] for other in hits)]
            else:
                display_hits = hits
            out.append({"record": row, "kind": "violation", "evidence": {
                "issue_type": "deleted_matrix_text_reappeared", "deleted_texts": list(dict.fromkeys(x["deleted_text"] for x in display_hits)),
                "matches": hits, "duty_text": duty, "target_cell": target.coordinate,
                "match_scope": p["match_scope"], "normalization": "whitespace_only",
                **({"final_text_wins": True, "retained_matches": retained,
                    "supersedes_carrier_references": list(dict.fromkeys(x["deleted_text"] for x in hits))} if final_text_wins else {}),
            }})
    return out
