"""验证 1016 重庆确认稿新增和调整后的专项审核能力。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from risk_audit.checks.registry import CheckContext, build_registry
from risk_audit.configuration.loader import load_pack
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.models import Entity, FieldValue, Record
from risk_audit.opinion_text import advice_v2
from risk_audit.pipeline_versions import (
    CONFIRMED_RULE_VERSIONS,
    PREPROCESSING_RULE_VERSIONS,
    SYSTEM_TYPE_PREPROCESSING_RULE_VERSIONS,
)
from risk_audit.util import sha256_file
from tools.publish_rules_1016_chongqing_1934 import tree_hashes, update_draft


ENTITY_CODE = "CQ001"
BUSINESS_CODE = "02"
BUSINESS_ID = "financial_management"
ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "rulepacks/releases/1.9.34"
SOURCE = ROOT / "规则来源/风控矩阵1016-重庆.docx"


def make_record(record_type: str, row: int, **values: str) -> Record:
    """构造 1016 重庆规则测试记录。

    Args:
        record_type: 矩阵或三清单记录类型。
        row: 原始工作表行号。
        values: 按逻辑字段名给出的字段值。

    Returns:
        带固定主体和业务标识的审核记录。
    """

    fields = {
        name: FieldValue(value, value, f"A{row}")
        for name, value in values.items()
    }
    record = Record(
        record_type,
        ENTITY_CODE,
        BUSINESS_CODE,
        "default",
        "02财务管理.xlsx",
        record_type,
        row,
        fields,
        f"{record_type}-{row}",
        business_id=BUSINESS_ID,
    )
    names = values.get("person_names", "")
    record.person_keys = [
        {"name": name, "person_id": name, "identity_reliable": True}
        for name in names.replace("，", "、").split("、")
        if name
    ]
    return record


def make_context(records: list[Record]) -> CheckContext:
    """构造包含一个重庆主体和一个业务的规则上下文。

    Args:
        records: 参与跨表核对的全部记录。

    Returns:
        可直接传给能力运行器的上下文。
    """

    return CheckContext(
        records,
        [],
        records,
        {ENTITY_CODE: Entity(ENTITY_CODE, "国网重庆市电力公司")},
        {},
        {},
        [ENTITY_CODE],
        [{
            "business_id": BUSINESS_ID,
            "business_code": BUSINESS_CODE,
            "variant_id": "default",
            "required": True,
        }],
    )


def run_capability(
    name: str,
    records: list[Record],
    params: dict | None = None,
    version: int = 2,
) -> list[dict]:
    """执行 1016 重庆专项能力。

    Args:
        name: 能力注册名称。
        records: 参与审核的记录。
        params: 新确认稿对应的可选能力参数。
        version: 需要执行的能力版本。

    Returns:
        能力产生的原始结构化问题。
    """

    capability = build_registry().get(name, version)
    assert capability is not None
    return capability.runner(make_context(records), params or {})


def test_duty_separation_does_not_infer_role_from_responsibility() -> None:
    """角色为空时只能提示补充角色，不得再根据职责内容参与分离判断。"""

    records = [
        make_record(
            "position_duty",
            2,
            measure_id="财务管理-12-控制措施05",
            department="财务部",
            person_names="张三",
            role="",
            duty="对资料完整性负主体责任",
        ),
        make_record(
            "position_duty",
            3,
            measure_id="财务管理-12-控制措施05",
            department="财务部",
            person_names="张三",
            role="审核",
            duty="对资料完整性负审核责任",
        ),
    ]

    issues = run_capability(
        "chongqing_duty_separation",
        records,
        {
            "infer_role_from_duty": False,
            "person_placeholders": ["等", "…", "...", "外包", "附件", "不详", "待定"],
        },
    )

    assert {item["evidence"]["issue_type"] for item in issues} == {"explicit_role_missing"}


def test_duty_separation_does_not_treat_person_placeholder_as_name() -> None:
    """外包、附件和不详属于人员占位值，不得形成同人冲突。"""

    records = [
        make_record(
            "position_duty",
            row,
            measure_id="财务管理-12-控制措施05",
            department="财务部",
            person_names="外包",
            role=role,
            duty=duty,
        )
        for row, role, duty in (
            (2, "经办", "对资料完整性负主体责任"),
            (3, "审核", "对资料完整性负审核责任"),
        )
    ]

    issues = run_capability(
        "chongqing_duty_separation",
        records,
        {
            "infer_role_from_duty": False,
            "person_placeholders": ["等", "…", "...", "外包", "附件", "不详", "待定"],
        },
    )

    assert {item["evidence"]["issue_type"] for item in issues} == {"person_missing"}


def test_responsibility_phrase_reports_two_missing_parts_separately() -> None:
    """责任属性和责任类型都缺失时，应分别给出两条确认稿意见。"""

    record = make_record("position_duty", 2, duty="负责复核付款资料")

    issues = run_capability(
        "chongqing_responsibility_phrase",
        [record],
        {"split_opinions": True},
    )

    assert {item["evidence"]["opinion"] for item in issues} == {
        "对……的一致性/准确性/真实性/完整性/有效性/及时性/合规性……",
        "……负主体/审核/审批/监督责任。",
    }


def test_system_control_does_not_compare_system_names() -> None:
    """1016 确认稿删除系统名称一致性后，仅保留建设状态等核对。"""

    matrix = make_record(
        "matrix",
        2,
        measure_id="财务管理-12-控制措施05",
        control_mode="系统已建",
        control_system="财务管控系统",
    )
    system = make_record(
        "system_rule",
        3,
        measure_id="财务管理-12-控制措施05",
        build_status="系统已建",
        system_name="资金管理系统",
    )

    issues = run_capability(
        "chongqing_system_control_consistency",
        [matrix, system],
        {"compare_system_names": False, "validate_build_status": True},
    )

    assert issues == []


def test_system_control_uses_new_missing_matrix_opinion() -> None:
    """系统清单中的措施在矩阵缺失时，应使用 1016 确认稿的新意见。"""

    system = make_record(
        "system_rule",
        3,
        measure_id="财务管理-12-控制措施05",
        build_status="系统待建",
    )

    issues = run_capability(
        "chongqing_system_control_consistency",
        [system],
        {"matrix_missing_opinion": "需核实矩阵中是否有该条措施或控制方式是否正确"},
    )

    assert [item["evidence"]["opinion"] for item in issues] == [
        "需核实矩阵中是否有该条措施或控制方式是否正确"
    ]


def test_system_control_reports_blank_build_status() -> None:
    """矩阵为系统控制时，清单建设状态空白也必须提示核实。"""

    matrix = make_record(
        "matrix",
        2,
        measure_id="财务管理-12-控制措施05",
        control_mode="系统已建",
    )
    system = make_record(
        "system_rule",
        3,
        measure_id="财务管理-12-控制措施05",
        build_status="",
    )

    issues = run_capability(
        "chongqing_system_control_consistency",
        [matrix, system],
        {"compare_system_names": False, "validate_build_status": True},
    )

    assert [item["evidence"]["opinion"] for item in issues] == ["需核实建设状态是否正确"]


def test_incompatible_alignment_checks_matrix_and_list_both_directions() -> None:
    """不相容岗位标记和清单编号必须双向对应。"""

    matrix_missing_list = make_record(
        "matrix",
        2,
        measure_id="财务管理-12-控制措施05",
        incompatible_control="√",
    )
    matrix_not_marked = make_record(
        "matrix",
        3,
        measure_id="财务管理-18-控制措施02",
        incompatible_control="",
    )
    incompatible = make_record(
        "incompatible_position",
        4,
        measure_id="财务管理-18-控制措施02",
        position_a="财务部-经办岗",
        position_b="财务部-审核岗",
    )

    issues = run_capability(
        "chongqing_incompatible_position_alignment",
        [matrix_missing_list, matrix_not_marked, incompatible],
        version=1,
    )

    assert {(item["record"].record_id, item["evidence"]["opinion"]) for item in issues} == {
        (matrix_missing_list.record_id, "请补充不相容岗位清单"),
        (matrix_not_marked.record_id, "请核实是否存在不相容岗位"),
    }


def test_incompatible_alignment_checks_position_content() -> None:
    """岗位 A、B 应采用部门-岗位格式，且不得跨部门或岗位名称相同。"""

    cross_department = make_record(
        "incompatible_position",
        2,
        measure_id="财务管理-12-控制措施05",
        position_a="财务部-经办岗",
        position_b="审计部-审核岗",
    )
    same_position = make_record(
        "incompatible_position",
        3,
        measure_id="财务管理-18-控制措施02",
        position_a="财务部-审核岗",
        position_b="财务部-审核岗",
    )
    invalid_format = make_record(
        "incompatible_position",
        4,
        measure_id="财务管理-20-控制措施01",
        position_a="财务部经办岗",
        position_b="财务部-审核岗",
    )
    matrices = [
        make_record(
            "matrix",
            row + 10,
            measure_id=record.value("measure_id"),
            incompatible_control="√",
        )
        for row, record in enumerate([cross_department, same_position, invalid_format])
    ]

    issues = run_capability(
        "chongqing_incompatible_position_alignment",
        [*matrices, cross_department, same_position, invalid_format],
        version=1,
    )

    assert {(item["record"].record_id, item["evidence"]["opinion"]) for item in issues} == {
        (
            cross_department.record_id,
            "跨部门岗位天然不相容无需填写，请核实矩阵不相容岗位标记是否准确",
        ),
        (same_position.record_id, "岗位名称相同，请确认是否为不相容岗位"),
        (invalid_format.record_id, "不相容岗位请按‘部门-岗位’的形式填写"),
    }


def test_incompatible_alignment_includes_position_c() -> None:
    """同一措施存在岗位 C 时，部门一致性和岗位去重也必须覆盖岗位 C。"""

    incompatible = make_record(
        "incompatible_position",
        2,
        measure_id="财务管理-12-控制措施05",
        position_a="财务部-经办岗",
        position_b="财务部-审核岗",
        position_c="审计部-审核岗",
    )
    matrix = make_record(
        "matrix",
        3,
        measure_id="财务管理-12-控制措施05",
        incompatible_control="√",
    )

    issues = run_capability(
        "chongqing_incompatible_position_alignment",
        [matrix, incompatible],
        version=1,
    )

    assert {item["evidence"]["opinion"] for item in issues} == {
        "跨部门岗位天然不相容无需填写，请核实矩阵不相容岗位标记是否准确",
        "岗位名称相同，请确认是否为不相容岗位",
    }


def test_incompatible_alignment_aggregates_rows_for_same_measure() -> None:
    """同一措施拆成多行填写时，部门一致性也应跨行核对。"""

    first = make_record(
        "incompatible_position",
        2,
        measure_id="财务管理-12-控制措施05",
        position_a="财务部-经办岗",
        position_b="财务部-审核岗",
    )
    second = make_record(
        "incompatible_position",
        3,
        measure_id="财务管理-12-控制措施05",
        position_a="审计部-监督岗",
        position_b="审计部-审批岗",
    )
    matrix = make_record(
        "matrix",
        4,
        measure_id="财务管理-12-控制措施05",
        incompatible_control="√",
    )

    issues = run_capability(
        "chongqing_incompatible_position_alignment",
        [matrix, first, second],
        version=1,
    )

    assert {
        item["record"].record_id
        for item in issues
        if item["evidence"]["issue_type"] == "incompatible_cross_department"
    } == {first.record_id, second.record_id}


def test_incompatible_alignment_reports_unmatched_or_missing_measure_id() -> None:
    """不相容清单明细缺编号或在矩阵中无对应措施时，不得静默跳过。"""

    missing_id = make_record(
        "incompatible_position",
        2,
        measure_id="待补充",
        position_a="财务部-经办岗",
        position_b="财务部-审核岗",
    )
    unmatched = make_record(
        "incompatible_position",
        3,
        measure_id="财务管理-12-控制措施05",
        position_a="财务部-经办岗",
        position_b="财务部-审核岗",
    )

    issues = run_capability(
        "chongqing_incompatible_position_alignment",
        [missing_id, unmatched],
        version=1,
    )

    assert {(item["record"].record_id, item["evidence"]["opinion"]) for item in issues} == {
        (missing_id.record_id, "请补充控制措施编号"),
        (unmatched.record_id, "矩阵中没有该条控制措施，请核实是否需要保留不相容岗位清单记录"),
    }


def test_incompatible_missing_id_still_checks_position_format() -> None:
    """缺少有效编号不能阻断同一清单行的岗位格式检查。"""

    record = make_record(
        "incompatible_position",
        2,
        measure_id="待补充",
        position_a="财务部经办岗",
        position_b="审计部-审核岗",
    )

    issues = run_capability(
        "chongqing_incompatible_position_alignment",
        [record],
        version=1,
    )

    assert {item["evidence"]["opinion"] for item in issues} == {
        "请补充控制措施编号",
        "不相容岗位请按‘部门-岗位’的形式填写",
    }


def test_incompatible_empty_positions_only_write_matrix_opinion() -> None:
    """岗位 A、B 为空或占位值时，确认稿指定的补充意见只应写在矩阵行。"""

    matrix = make_record(
        "matrix",
        2,
        measure_id="财务管理-12-控制措施05",
        incompatible_control="√",
    )
    blank_incompatible = make_record(
        "incompatible_position",
        3,
        measure_id="财务管理-12-控制措施05",
        position_a="",
        position_b="",
    )
    placeholder_incompatible = make_record(
        "incompatible_position",
        4,
        measure_id="财务管理-12-控制措施05",
        position_a="无",
        position_b="不涉及",
    )

    issues = run_capability(
        "chongqing_incompatible_position_alignment",
        [matrix, blank_incompatible, placeholder_incompatible],
        version=1,
    )

    assert [(item["record"].record_id, item["evidence"]["opinion"]) for item in issues] == [
        (matrix.record_id, "请补充不相容岗位清单")
    ]


def test_incompatible_existing_measure_requires_matrix_mark_even_without_positions() -> None:
    """清单已填写措施编号时，即使岗位为空也必须反向核对矩阵标记。"""

    matrix = make_record(
        "matrix",
        2,
        measure_id="财务管理-12-控制措施05",
        incompatible_control="",
    )
    incompatible = make_record(
        "incompatible_position",
        3,
        measure_id="财务管理-12-控制措施05",
        position_a="无",
        position_b="不涉及",
    )

    issues = run_capability(
        "chongqing_incompatible_position_alignment",
        [matrix, incompatible],
        version=1,
    )

    assert [(item["record"].record_id, item["evidence"]["opinion"]) for item in issues] == [
        (matrix.record_id, "请核实是否存在不相容岗位")
    ]


def test_matrix_schema_uses_exact_difference_reason() -> None:
    """字段差异意见应直接告诉审核人员具体缺少或变化的字段。"""

    reason = "工作表“风控矩阵”与冻结基准相比缺少或改变业务表头：控制目标（H2）"

    assert advice_v2(
        "matrix_schema_1016",
        "review",
        {"unavailable_reason": reason, "missing_fields": ["控制目标（H2）"]},
    ) == reason


def test_release_matches_1016_word_rule_order_and_configuration() -> None:
    """1.9.34 应按 1016 确认稿发布 14 条规则及对应参数。"""

    pack = load_pack(RELEASE)
    numbered = [
        (rule["display_code"], rule["title"])
        for rule in pack["rules"]
        if rule["display_code"].startswith("R")
    ]

    assert [code for code, _ in numbered] == [f"R{number:02d}" for number in range(1, 15)]
    assert [title for _, title in numbered] == [
        "资料提交完整性",
        "基准字段及适用性填写情况",
        "矩阵中责任主体规范性审核",
        "岗位清单中部门、岗位名称和人员姓名",
        "岗位清单中人员姓名规范性审核",
        "岗位清单中一人多岗审核",
        "岗位清单中角色与责任类型对应",
        "岗位清单中经办与审核人员分离",
        "岗位清单中岗位职责基本责任表述",
        "岗位清单中重复项筛查",
        "岗位清单与矩阵一致性审核",
        "矩阵中责任主体与岗位清单一致性审核",
        "不相容岗位清单审核",
        "系统控制规则清单控制方式匹配性审核",
    ]
    rules = {rule["display_code"]: rule for rule in pack["rules"]}
    assert [check["check_id"] for check in rules["R02"]["checks"]] == ["matrix_schema_1016"]
    assert "ignore_fields" not in rules["R02"]["checks"][0]["params"]
    assert rules["R05"]["checks"][0]["params"]["patterns"] == [r"^[^\s、，,]{1,4}$"]
    assert rules["R05"]["checks"][0]["message"]["violation"] == (
        "每行岗位名称中只能有一个对应人员，若有多人，则应分多行填写。"
    )
    assert rules["R08"]["checks"][0]["params"] == {
        "infer_role_from_duty": False,
        "person_placeholders": ["等", "…", "...", "外包", "附件", "不详", "待定"],
    }
    assert rules["R09"]["checks"][0]["params"] == {"split_opinions": True}
    assert len(rules["R13"]["checks"]) == 2
    assert rules["R14"]["checks"][0]["params"]["compare_system_names"] is False
    assert rules["R14"]["checks"][0]["params"]["validate_build_status"] is True
    assert pack["manifest"]["source_document_sha256"] == sha256_file(SOURCE)
    assert pack["manifest"]["source_document_sha256"] == (
        "7e28ef968b6bbe73640ab8b59e9cb6bf38fd7c296791aa62edff74cf694b6239"
    )


def test_publish_script_rebuilds_1934_deterministically() -> None:
    """1016 重庆发布脚本应能从 1.9.33 稳定重建 1.9.34。"""

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_1016_chongqing_1934.py"), "--verify-only"],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verified"] is True


def test_update_draft_is_idempotent(tmp_path: Path) -> None:
    """发布中断只留下草稿时，重复更新草稿也应得到相同内容。

    Args:
        tmp_path: pytest 提供的隔离规则包仓库目录。
    """

    store_root = tmp_path / "rulepacks"
    base_release = store_root / "releases/1.9.33"
    base_release.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "rulepacks/releases/1.9.33", base_release)
    store = RulePackStore(store_root, build_registry())
    draft = store.create_draft("rules-1016-chongqing-1.9.34", "1.9.33")

    update_draft(draft, SOURCE)
    first_hashes = tree_hashes(draft)
    update_draft(draft, SOURCE)

    assert tree_hashes(draft) == first_hashes

    shutil.rmtree(draft / "rules")
    update_draft(draft, SOURCE)

    assert tree_hashes(draft) == first_hashes


def test_chongqing_region_activates_1934_pipeline() -> None:
    """重庆地区索引和三类处理管线应同时切换到 1.9.34。"""

    index = json.loads((ROOT / "regions/index.json").read_text(encoding="utf-8"))
    profile = next(item for item in index["regions"] if item["region_code"] == "chongqing")
    manifest = json.loads((RELEASE / "manifest.json").read_text(encoding="utf-8"))

    assert profile["rulepack_version"] == "1.9.34"
    assert profile["rulepack_content_hash"] == manifest["content_hash"]
    assert "1.9.34" in CONFIRMED_RULE_VERSIONS
    assert "1.9.34" in PREPROCESSING_RULE_VERSIONS
    assert "1.9.34" in SYSTEM_TYPE_PREPROCESSING_RULE_VERSIONS
