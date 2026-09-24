"""0917-1 更新规则的行为验收。"""

from __future__ import annotations

import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

from risk_audit.checks.registry import CheckContext
from risk_audit.configuration.loader import load_pack
from risk_audit.util import sha256_file
from test_confirmed_v180 import context, record


ROOT = Path(__file__).resolve().parents[1]

DEPARTMENT_0917 = {
    "field": "department",
    "placeholders": ["管理部门", "实施部门"],
    "placeholder_patterns": ["管理部门|实施部门"],
    "separators": [],
    "exact_exceptions": [],
    "prefix_exceptions": ["归口管理部门（党委", "其他福利实施部门（党委组织"],
    "contains_exceptions": [],
}

POSITION_0917 = {
    "field": "position",
    "placeholders": ["人员", "管理人员", "负责人"],
    "placeholder_patterns": ["人员|管理人员|负责人"],
    "separators": ["、", "，", ",", "；", ";"],
    "exact_exceptions": ["查勘人员"],
    "prefix_exceptions": [],
    "contains_exceptions": ["查勘人员", "专责", "负责人-", "班长", "值长"],
}

DUPLICATE_VALUES = {
    "measure_id": "M1",
    "duty": "对资料真实性负主体责任",
    "department": "财务部",
    "position": "核算专责",
    "person_names": "张三",
    "role": "经办",
}


def run(name: str, rows: list, params: dict) -> list[dict]:
    """调用0917能力；name 为函数名，rows 为真实材料行，params 为规则参数。"""
    module = import_module("risk_audit.checks.confirmed_v180")
    assert hasattr(module, name), f"{name} 尚未实现"
    return getattr(module, name)(context(rows), params)


@pytest.mark.parametrize(
    "department",
    ["归口管理部门（党委办公室）", "其他福利实施部门（党委组织部）"],
)
def test_0917_department_prefixes_are_exempted(department: str) -> None:
    """department 为确认的名称前缀；后续具体部门文字不得触发泛称意见。"""
    assert run("field_constraints_v6", [record(department=department)], DEPARTMENT_0917) == []


def test_0917_department_exception_requires_prefix_position() -> None:
    """例外文字出现在名称中部时仍应提示，避免包含匹配扩大部门放行范围。"""
    issues = run(
        "field_constraints_v6",
        [record(department="外包归口管理部门（党委办公室）")],
        DEPARTMENT_0917,
    )

    assert [item["evidence"]["issue_type"] for item in issues] == ["field_not_concrete"]


@pytest.mark.parametrize(
    "position",
    [
        "现场查勘人员",
        "采购人员专责",
        "部门负责人-综合管理人员",
        "运行班长（管理人员）",
        "通信值长人员",
    ],
)
def test_0917_position_keywords_are_contains_exceptions(position: str) -> None:
    """position 含确认关键词时不再因同时包含泛称而提示岗位不具体。"""
    assert run("field_constraints_v6", [record(position=position)], POSITION_0917) == []


def test_0917_position_exception_keeps_split_position_warning() -> None:
    """包含岗位例外只放行具体性检查，多个岗位写在同一行仍须拆分。"""
    issues = run(
        "field_constraints_v6",
        [record(position="采购人员专责、审核专责")],
        POSITION_0917,
    )

    assert [item["evidence"]["issue_type"] for item in issues] == ["multiple_positions"]


def duplicate_rows() -> list:
    """构造六字段完全相同的两条岗位职责；调用方可修改文件或工作表边界。"""
    return [record(row=row_number, **DUPLICATE_VALUES) for row_number in (3, 4)]


def test_0917_duplicate_rows_remain_detected_in_same_sheet() -> None:
    """同一文件同一工作表中的完整六字段重复仍应逐行提示。"""
    issues = run("duplicate_duties_v2", duplicate_rows(), {})

    assert {item["record"].row for item in issues} == {3, 4}


@pytest.mark.parametrize("boundary", ["sheet", "file"])
def test_0917_duplicate_rows_do_not_cross_sheet(boundary: str) -> None:
    """boundary 指定不同工作表或文件；两条相同行不得跨该边界判重。"""
    rows = duplicate_rows()
    if boundary == "sheet":
        rows[1].sheet = "岗位职责清单二"
    else:
        rows[1].file_path = "另一份三清单.xlsx"

    assert run("duplicate_duties_v2", rows, {}) == []


def test_active_0917_rulepack_executes_new_boundaries() -> None:
    """激活规则包必须继承0917第5条和第15条新版能力。"""
    active = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))
    assert active["version"] == "1.9.19"
    pack = load_pack(ROOT / "rulepacks/releases" / active["version"])

    rule_5 = next(rule for rule in pack["rules"] if rule["rule_id"] == "duties.completeness")
    checks = {check["check_id"]: check for check in rule_5["checks"]}
    department_check = checks["department_specific"]
    position_check = checks["position_specific"]
    assert department_check["operator_version"] == 6
    assert position_check["operator_version"] == 6
    assert run(
        "field_constraints_v6",
        [record(department="归口管理部门（党委办公室）")],
        department_check["params"],
    ) == []
    assert run(
        "field_constraints_v6",
        [record(position="采购人员专责")],
        position_check["params"],
    ) == []

    duplicate_rule = next(rule for rule in pack["rules"] if rule["rule_id"] == "duties.duplicate_rows")
    assert duplicate_rule["checks"][0]["operator_version"] == 2
    assert pack["manifest"]["source_document_sha256"] == sha256_file(ROOT / "规则来源/0923-2.docx")


def test_0917_publish_script_verifies_existing_release() -> None:
    """1.9.7规则包必须可重建验证，且只读验证不改变激活版本。"""
    active_version = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))["version"]
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_0917.py"), "--verify-only"],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["verified"] is True
    assert payload["activated"] is False
    assert json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))["version"] == active_version
