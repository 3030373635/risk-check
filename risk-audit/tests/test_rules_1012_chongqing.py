"""验证 1012 重庆同步模板匹配规则。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from docx import Document

from risk_audit.checks.registry import CheckContext, build_registry
from risk_audit.models import Entity, FieldValue, FileRecord, Record
from risk_audit.system_type_preprocessing import classify_system_owner
from tools.publish_rules_1012_chongqing import (
    BASE_VERSION,
    BUSINESS_MAPPING,
    EXPECTED_BUSINESS_NAMES,
    SOURCE_NAME,
    extract_key_points,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "规则来源" / SOURCE_NAME
RELEASE = ROOT / "rulepacks/releases/1.9.22"


def make_record(record_type: str, row: int, business_id: str = "financial_management", **values: str) -> Record:
    """构造重庆专项规则测试记录。

    Args:
        record_type: 审核记录类型。
        row: 原始工作表行号。
        business_id: 稳定业务标识。
        values: 按逻辑字段名给出的字段值。
    """

    fields = {name: FieldValue(value, value, f"A{row}") for name, value in values.items()}
    return Record(
        record_type,
        "CQ001",
        "02",
        "default",
        "财务管理.xlsx",
        record_type,
        row,
        fields,
        f"{record_type}-{row}",
        business_id=business_id,
    )


def make_context(
    records: list[Record],
    *,
    entity_name: str = "国网重庆市电力公司某供电分公司",
    files: list[FileRecord] | None = None,
    business_id: str = "financial_management",
) -> CheckContext:
    """构造包含重庆主体与全部记录的审核上下文。

    Args:
        records: 当前审核单元内的全部记录。
        entity_name: 当前主体名称。
        files: 可选的原始材料文件。
        business_id: 当前审核单元的业务标识。
    """

    return CheckContext(
        [],
        files or [],
        records,
        {"CQ001": Entity("CQ001", entity_name)},
        {},
        {},
        ["CQ001"],
        [{"business_id": business_id, "business_code": "02", "variant_id": "default"}],
    )


def test_system_control_consistency_reports_missing_and_mismatched_rows() -> None:
    """系统控制清单缺失、建设状态或系统名称不一致时应分别提示。"""

    capability = build_registry().get("system_control_consistency", 1)
    assert capability is not None
    matrix_missing = make_record(
        "matrix", 3, measure_id="1.1", control_mode="系统已建", control_system="ERP",
    )
    matrix_mismatch = make_record(
        "matrix", 4, measure_id="2.1", control_mode="系统已建", control_system="ERP",
    )
    system_mismatch = make_record(
        "system_rule", 8, measure_id="2.1", build_status="系统待建", system_name="PMS",
    )

    issues = capability.runner(make_context([matrix_missing, matrix_mismatch, system_mismatch]), {})

    assert {issue["evidence"]["issue_type"] for issue in issues} == {
        "system_rule_missing",
        "build_status_mismatch",
        "system_name_mismatch",
    }


def test_system_control_consistency_accepts_matching_rows() -> None:
    """建设状态与系统名称均一致时不应报错。"""

    capability = build_registry().get("system_control_consistency", 1)
    matrix = make_record(
        "matrix", 3, measure_id="1.1", control_mode="系统已建", control_system="ERP、PMS",
    )
    system = make_record(
        "system_rule", 8, measure_id="1.1", build_status="系统已建", system_name="PMS,ERP",
    )

    assert capability.runner(make_context([matrix, system]), {}) == []


def test_system_control_consistency_combines_system_names_across_rows() -> None:
    """同一措施的多行系统名称应合并后与矩阵比较。"""

    capability = build_registry().get("system_control_consistency", 1)
    matrix = make_record(
        "matrix", 3, measure_id="1.1", control_mode="系统已建", control_system="ERP、PMS",
    )
    system_erp = make_record(
        "system_rule", 8, measure_id="1.1", build_status="系统已建", system_name="ERP",
    )
    system_pms = make_record(
        "system_rule", 9, measure_id="1.1", build_status="系统已建", system_name="PMS",
    )

    assert capability.runner(make_context([matrix, system_erp, system_pms]), {}) == []


def test_branch_key_points_reports_missing_and_extra_points() -> None:
    """供电分公司的关键控制点缺失或多报时应分别提示。"""

    capability = build_registry().get("power_supply_branch_key_points", 1)
    assert capability is not None
    actual = [
        make_record("matrix", 3, measure_id="29.1"),
        make_record("matrix", 4, measure_id="99.1"),
    ]
    params = {
        "expected_key_points": {"financial_management": [[29], [45]]},
        "business_aliases": {"financial_management": ["财务管理"]},
    }

    issues = capability.runner(make_context(actual), params)

    assert {issue["evidence"]["issue_type"] for issue in issues} == {
        "missing_key_points",
        "extra_key_point",
    }
    missing = next(issue for issue in issues if issue["evidence"]["issue_type"] == "missing_key_points")
    assert missing["evidence"]["missing_key_points"] == [[45]]


def test_branch_key_points_only_applies_to_power_supply_branch() -> None:
    """非供电分公司不执行该专项模板规则。"""

    capability = build_registry().get("power_supply_branch_key_points", 1)
    params = {
        "expected_key_points": {"financial_management": [[29]]},
        "business_aliases": {"financial_management": ["财务管理"]},
    }

    issues = capability.runner(
        make_context([], entity_name="国网重庆市电力公司本部"),
        params,
    )

    assert issues == []


def test_branch_key_points_recognizes_unique_alias_for_unmatched_file() -> None:
    """未匹配模板的文件只有一个业务别名命中时应使用该业务。"""

    capability = build_registry().get("power_supply_branch_key_points", 1)
    record = make_record("matrix", 3, business_id="unmatched_1234", measure_id="29.1")
    record.file_path = "财务管理风控矩阵.xlsx"
    params = {
        "expected_key_points": {"financial_management": [[29]]},
        "business_aliases": {"financial_management": ["财务管理"]},
    }

    context = make_context([record], business_id="unmatched_1234")
    context.resources.update({
        "_entity_business_ids": ["unmatched_1234"],
        "_entity_business_files": {"unmatched_1234": ["财务管理风控矩阵.xlsx"]},
        "_current_business_id": "unmatched_1234",
    })

    assert capability.runner(context, params) == []


def test_branch_key_points_normalizes_unmatched_source_business() -> None:
    """来源新业务未进入基准时应通过文件别名归一，不得同时报缺失和多报。"""

    capability = build_registry().get("power_supply_branch_key_points", 1)
    record = make_record("matrix", 3, business_id="unmatched_abcd", measure_id="5.1")
    record.file_path = "特高压建设风控矩阵.xlsx"
    context = make_context([record], business_id="unmatched_abcd")
    context.resources.update({
        "_entity_business_ids": ["unmatched_abcd"],
        "_entity_business_files": {"unmatched_abcd": ["特高压建设风控矩阵.xlsx"]},
        "_current_business_id": "unmatched_abcd",
    })
    params = {
        "expected_key_points": {"ultra_high_voltage_construction": [[5]]},
        "business_aliases": {"ultra_high_voltage_construction": ["特高压建设"]},
    }

    assert capability.runner(context, params) == []


def test_branch_key_points_expands_multiple_businesses_in_one_unmatched_unit() -> None:
    """同一 unmatched_* 单元中的多份来源业务文件应逐路径归一。"""

    capability = build_registry().get("power_supply_branch_key_points", 1)
    ultra_high_voltage = make_record(
        "matrix", 3, business_id="unmatched_same", measure_id="5.1",
    )
    ultra_high_voltage.file_path = "特高压建设风控矩阵.xlsx"
    digital_platform = make_record(
        "matrix", 4, business_id="unmatched_same", measure_id="7.1",
    )
    digital_platform.file_path = "数科平台业务风控矩阵.xlsx"
    context = make_context(
        [ultra_high_voltage, digital_platform],
        business_id="unmatched_same",
    )
    context.resources.update({
        "_entity_business_ids": ["unmatched_same"],
        "_entity_business_files": {
            "unmatched_same": [
                "特高压建设风控矩阵.xlsx",
                "数科平台业务风控矩阵.xlsx",
            ],
        },
        "_current_business_id": "unmatched_same",
    })
    params = {
        "expected_key_points": {
            "ultra_high_voltage_construction": [[5]],
            "digital_platform_business": [[7]],
        },
        "business_aliases": {
            "ultra_high_voltage_construction": ["特高压建设"],
            "digital_platform_business": ["数科平台业务"],
        },
    }

    assert capability.runner(context, params) == []


def test_branch_key_points_reports_all_businesses_for_entity_without_business_files() -> None:
    """供电分公司只有说明材料时应报告全部来源业务缺失。"""

    capability = build_registry().get("power_supply_branch_key_points", 1)
    context = make_context([], business_id="__entity_completeness__")
    context.resources.update({
        "_entity_business_ids": [],
        "_entity_business_files": {},
        "_current_business_id": "__entity_completeness__",
        "_entity_completeness_pass": True,
    })
    params = {
        "expected_key_points": {
            "financial_management": [[29]],
            "marketing_management": [[1], [2]],
        },
        "business_aliases": {
            "financial_management": ["财务管理"],
            "marketing_management": ["营销管理"],
        },
    }

    issues = capability.runner(context, params)

    assert {issue["evidence"]["business_id"] for issue in issues} == {
        "financial_management",
        "marketing_management",
    }


def test_branch_key_points_only_checks_current_business_unit() -> None:
    """单业务审核单元不应重复报其他业务的缺失。"""

    capability = build_registry().get("power_supply_branch_key_points", 1)
    params = {
        "expected_key_points": {
            "financial_management": [[29]],
            "marketing_management": [[1]],
        },
        "business_aliases": {
            "financial_management": ["财务管理"],
            "marketing_management": ["营销管理"],
        },
    }

    issues = capability.runner(make_context([]), params)

    assert len(issues) == 1
    assert issues[0]["evidence"]["business_id"] == "financial_management"


def test_branch_key_points_reports_wholly_missing_business_once_per_entity() -> None:
    """供电分公司整项未报业务时应在主体首个单元提示完整性。"""

    capability = build_registry().get("power_supply_branch_key_points", 1)
    actual = [make_record("matrix", 3, measure_id="29.1")]
    context = make_context(actual)
    context.resources.update({
        "_entity_business_ids": ["financial_management"],
        "_current_business_id": "financial_management",
        "_entity_completeness_pass": True,
    })
    params = {
        "expected_key_points": {
            "financial_management": [[29]],
            "marketing_management": [[1], [2]],
        },
        "business_aliases": {
            "financial_management": ["财务管理"],
            "marketing_management": ["营销管理"],
        },
    }

    issues = capability.runner(context, params)

    assert len(issues) == 1
    assert issues[0]["evidence"]["business_id"] == "marketing_management"
    assert issues[0]["evidence"]["missing_key_points"] == [[1], [2]]


def test_branch_key_points_reports_extra_business_matrix() -> None:
    """供电分公司提交不在来源 17 项中的整张矩阵时应提示适用性。"""

    capability = build_registry().get("power_supply_branch_key_points", 1)
    record = make_record("matrix", 3, business_id="education_training", measure_id="1.1")
    context = make_context([record], business_id="education_training")
    context.resources.update({
        "_entity_business_ids": ["education_training"],
        "_current_business_id": "education_training",
        "_entity_completeness_pass": True,
    })
    params = {
        "expected_key_points": {"financial_management": [[29]]},
        "business_aliases": {"financial_management": ["财务管理"]},
    }

    issues = capability.runner(context, params)

    assert {issue["evidence"]["issue_type"] for issue in issues} == {
        "extra_business",
        "missing_key_points",
    }
    extra = next(issue for issue in issues if issue["evidence"]["issue_type"] == "extra_business")
    assert extra["record"] is record


def test_branch_key_points_does_not_repeat_entity_missing_on_second_variant() -> None:
    """同一业务的第二个变体不得重复输出主体级整项缺报。"""

    capability = build_registry().get("power_supply_branch_key_points", 1)
    record = make_record("matrix", 3, business_id="grid_construction", measure_id="1.1")
    context = make_context([record], business_id="grid_construction")
    context.resources.update({
        "_entity_business_ids": ["grid_construction"],
        "_entity_business_files": {"grid_construction": ["电网基建风控矩阵.xlsx"]},
        "_current_business_id": "grid_construction",
        "_entity_completeness_pass": False,
    })
    params = {
        "expected_key_points": {
            "grid_construction": [[1]],
            "marketing_management": [[2]],
        },
        "business_aliases": {
            "grid_construction": ["电网基建"],
            "marketing_management": ["营销管理"],
        },
    }

    assert capability.runner(context, params) == []


def _modified_document(tmp_path: Path) -> Path:
    """复制来源文档以便构造发布失败样例。

    Args:
        tmp_path: pytest 临时目录。
    """

    target = tmp_path / SOURCE_NAME
    shutil.copyfile(SOURCE, target)
    return target


def test_source_extracts_all_confirmed_businesses_and_points() -> None:
    """来源文档必须解析出 17 个已确认业务和 368 个关键控制点。"""

    points = extract_key_points(SOURCE, BUSINESS_MAPPING)

    assert set(points) == set(BUSINESS_MAPPING.values())
    assert sum(len(items) for items in points.values()) == 368


def test_source_rejects_unknown_business(tmp_path: Path) -> None:
    """来源文档出现未确认业务时必须终止发布。"""

    source = _modified_document(tmp_path)
    document = Document(source)
    cells = document.tables[0].add_row().cells
    cells[0].text = "未知业务"
    cells[1].text = "1.测试"
    document.save(source)

    with pytest.raises(RuntimeError, match="未知业务"):
        extract_key_points(source, BUSINESS_MAPPING)


def test_source_rejects_missing_business(tmp_path: Path) -> None:
    """来源文档缺少任一已确认业务时必须终止发布。"""

    source = _modified_document(tmp_path)
    document = Document(source)
    table = document.tables[0]
    for row in list(table.rows)[2:]:
        if row.cells[0].text.strip() == EXPECTED_BUSINESS_NAMES[0]:
            table._tbl.remove(row._tr)
    document.save(source)

    with pytest.raises(RuntimeError, match="缺少业务"):
        extract_key_points(source, BUSINESS_MAPPING)


def test_source_rejects_multiple_tables(tmp_path: Path) -> None:
    """来源文档出现多张规则表时必须终止发布。"""

    source = _modified_document(tmp_path)
    document = Document(source)
    document.add_table(rows=1, cols=2)
    document.save(source)

    with pytest.raises(RuntimeError, match="1 张"):
        extract_key_points(source, BUSINESS_MAPPING)


def test_source_rejects_illegal_key_point_number(tmp_path: Path) -> None:
    """关键控制点编号无法唯一解析时必须终止发布。"""

    source = _modified_document(tmp_path)
    document = Document(source)
    document.tables[0].rows[2].cells[1].text = "没有编号"
    document.save(source)

    with pytest.raises(RuntimeError, match="关键控制点编号"):
        extract_key_points(source, BUSINESS_MAPPING)


def test_chongqing_system_owner_is_second_level() -> None:
    """重庆市公司系统规则管理主体应归为二级部署系统。"""

    assert classify_system_owner("国网重庆市电力有限公司") == "二级部署系统"
    assert classify_system_owner("国网重庆市电力公司") == "二级部署系统"


def test_exact_chongqing_city_company_is_second_level() -> None:
    """省级别名“重庆市公司”不应被“市公司”关键词排除。"""

    assert classify_system_owner("重庆市公司") == "二级部署系统"
    assert classify_system_owner("国网重庆市电力公司某供电分公司") is None


def test_release_is_chongqing_specific_and_excludes_hunan_rule_17() -> None:
    """1.9.22 应标识重庆地区，且不得继承湖南财务管理第 17 条。"""

    manifest = json.loads((RELEASE / "manifest.json").read_text(encoding="utf-8"))
    rules = [json.loads(path.read_text(encoding="utf-8")) for path in (RELEASE / "rules").glob("*.json")]

    assert BASE_VERSION == "1.9.20"
    assert manifest["version"] == "1.9.22"
    assert manifest["region_code"] == "chongqing"
    assert manifest["region_name"] == "重庆"
    assert {rule["rule_id"] for rule in rules}.issuperset({
        "chongqing.system_control_consistency",
        "chongqing.power_supply_branch_key_points",
    })
    assert "financial_management.key_measure_applicability" not in {rule["rule_id"] for rule in rules}


def test_release_contains_no_hunan_specific_resources() -> None:
    """重庆发布包不得包含湖南主体、地名或冻结样本引用。"""

    markers = ("国网湖南", "湖南省电力", "湘西", "星通公司")
    leaks = {
        str(path.relative_to(RELEASE)): marker
        for path in RELEASE.rglob("*.json")
        for marker in markers
        if marker in path.read_text(encoding="utf-8")
    }

    assert leaks == {}


def test_repository_region_index_pins_both_release_hashes() -> None:
    """仓库地区索引必须与湖南、重庆发布清单完全一致。"""

    index = json.loads((ROOT / "regions/index.json").read_text(encoding="utf-8"))
    profiles = {item["region_code"]: item for item in index["regions"]}
    for region_code, version in (("hunan", "1.9.21"), ("chongqing", "1.9.22")):
        manifest = json.loads(
            (ROOT / f"rulepacks/releases/{version}/manifest.json").read_text(encoding="utf-8")
        )
        assert profiles[region_code]["rulepack_version"] == version
        assert profiles[region_code]["rulepack_content_hash"] == manifest["content_hash"]


def test_verify_only_is_deterministic_and_does_not_activate_release() -> None:
    """重庆发布包必须可重复构建，验证时不得改动全局激活指针。"""

    active_path = ROOT / "rulepacks/active.json"
    before = active_path.read_bytes()
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_1012_chongqing.py"), "--verify-only"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verified"] is True
    assert active_path.read_bytes() == before
