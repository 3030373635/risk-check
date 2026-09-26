"""Prefer a proven deleted reference over a missing-carrier question about it."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from typing import Callable

from risk_audit.checks.deleted_content import literal_text
from risk_audit.models import CheckStatus, Finding
from risk_audit.util import norm_text


def reconcile_deleted_carriers(findings: list[Finding], statuses: list[CheckStatus], render: Callable[[Finding], str]) -> list[Finding]:
    def location(f):
        return (f.entity_code, f.business_code, f.variant_id, f.file_path, f.sheet, f.row)

    deletions = defaultdict(list)
    for f in findings:
        if f.severity == "violation" and f.evidence.get("final_text_wins") and f.evidence.get("issue_type") == "deleted_matrix_text_reappeared":
            deletions[location(f)].append(f)
    removed = Counter()
    result = []
    for f in findings:
        if f.check_id != "carrier_subset" or f.evidence.get("issue_type") != "carrier_reference_unmatched":
            result.append(f); continue
        candidates = [d for d in deletions[location(f)] if f.evidence.get('source_kind') in {None, 'duty_text'}
                      and literal_text(d.evidence["duty_text"]) == literal_text(f.evidence.get("original_sentence", ""))]
        missing = f.evidence.get("missing_references", [])
        covered = {}
        for reference in missing:
            # Exact object equality only. A deleted single character must not
            # suppress an unrelated document name that happens to contain it.
            match = next((d for d in candidates if any(norm_text(text) == reference for text in d.evidence["supersedes_carrier_references"])), None)
            if match is not None: covered[reference] = match
        if not covered:
            result.append(f); continue
        original = {"finding_key": f.finding_key, "rule_id": f.rule_id, "check_id": f.check_id, "message": f.message, "evidence": deepcopy(f.evidence)}
        for d in candidates:
            refs = [ref for ref, owner in covered.items() if owner is d]
            if refs:
                d.evidence.setdefault("superseded_carrier_checks", []).append({**original, "references": refs})
        remaining = [ref for ref in missing if ref not in covered]
        if remaining:
            f.evidence["missing_references"] = remaining
            f.evidence["unavailable_reason"] = "明确引用载体未在当前矩阵载体集合中找到：" + "、".join(remaining)
            f.evidence["handled_by_deletion_check"] = list(covered)
            f.message = render(f)
            result.append(f)
        else:
            removed[(f.rule_id, f.check_id)] += 1
    for status in statuses:
        if status.status in {"executed", "partial"}:
            status.findings -= removed[(status.rule_id, status.check_id)]
    return result
