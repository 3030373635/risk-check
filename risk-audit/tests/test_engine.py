from __future__ import annotations

import copy

from risk_audit.engine import run_engine
from risk_audit.models import FieldValue, Record


def rec(row, business="06", role="", names=(), measure="M01", department="部门", position="岗位", duty="对事项负有主体责任"):
    fields = {
        "measure_id": FieldValue(measure, measure, f"G{row}"), "role": FieldValue(role, role, f"E{row}"),
        "person_names": FieldValue("、".join(names), "、".join(names), f"C{row}"), "department": FieldValue(department, department, f"A{row}"),
        "position": FieldValue(position, position, f"B{row}"), "duty": FieldValue(duty, duty, f"F{row}"), "duty_id": FieldValue("1", "1", f"D{row}"),
    }
    r = Record("position_duty", "205H", business, "default", "x.xlsx", "岗位职责清单", row, fields, f"r{row}-{business}")
    r.person_keys = [{"name": n, "person_id": None, "identity_reliable": False} for n in names]
    return r


def minimal_files(records):
    from risk_audit.models import FileRecord, ParsedSheet
    from pathlib import Path
    s = ParsedSheet("岗位职责清单", "position_duty", [2], {}, {}, 9, 3, records)
    return [FileRecord(Path("x.xlsx"), Path("x.xlsx"), "h", "xlsx", "205H", [], False, records[0].business_code, "default", "three_lists", [s])]


def only_rules(pack, ids):
    p = copy.deepcopy(pack); p["rules"] = [r for r in p["rules"] if r["rule_id"] in ids]; return p


def test_r11_off_does_not_break_r08(pack, registry):
    rows = [rec(3, role="经办", names=["张三"]), rec(4, role="审核", names=["张三"])]
    p = only_rules(pack, {"duties.separation", "duties.role_mapping"})
    next(x for x in p["rules"] if x["rule_id"] == "duties.role_mapping")["enabled"] = False
    findings, statuses = run_engine(p, registry, minimal_files(rows), {"205H": type("E", (), {"name": "信通"})()}, {})
    assert any(x.rule_id == "duties.separation" and x.severity == "review" for x in findings)
    assert any(x.rule_id == "duties.role_mapping" and x.status == "disabled" for x in statuses)


def test_r08_business_10_excluded_by_configuration(pack, registry):
    rows = [rec(3, "10", "经办", ["张三"]), rec(4, "10", "审核", ["张三"])]
    findings, _ = run_engine(only_rules(pack, {"duties.separation"}), registry, minimal_files(rows), {"205H": type("E", (), {"name": "信通"})()}, {})
    assert findings == []


def test_cross_measure_and_entity_are_hard_isolated(pack, registry):
    rows = [rec(3, role="经办", names=["张三"], measure="M01"), rec(4, role="审核", names=["张三"], measure="M02")]
    findings, _ = run_engine(only_rules(pack, {"duties.separation"}), registry, minimal_files(rows), {"205H": type("E", (), {"name": "信通"})()}, {})
    assert findings == []


def test_scope_overlay_actually_changes_execution(pack, registry):
    rows = [rec(3, role="经办", names=["张三"]), rec(4, role="审核", names=["张三"])]
    p = only_rules(pack, {"duties.separation"})
    p["overlays"] = [{"priority": 1, "scope": {"business_code": "06"}, "path": "duties.separation.enabled", "value": False}]
    findings, _ = run_engine(p, registry, minimal_files(rows), {"205H": type("E", (), {"name": "信通"})()}, {})
    assert findings == []

