"""根据重庆 1016 审核确认稿发布重新编号的 1.9.34 规则包。"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-audit/src"))

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.util import sha256_file, write_json


BASE_VERSION = "1.9.33"
TARGET_VERSION = "1.9.34"
DRAFT_NAME = "rules-1016-chongqing-1.9.34"
SOURCE_NAME = "风控矩阵1016-重庆.docx"
SOURCE_REFERENCE = f"{SOURCE_NAME} 接受修订后的有效正文"
RULE_LABEL_PATTERN = re.compile(r"^(?:【第\d+条】)+")
SELECTOR = {"include": ["*"], "exclude": []}


def _scope(record_type: str) -> dict[str, Any]:
    """生成适用于全部主体和业务的规则范围。

    Args:
        record_type: 能力检查的记录类型。

    Returns:
        符合规则包结构的范围配置。
    """

    return {
        "record_type": record_type,
        "business_codes": copy.deepcopy(SELECTOR),
        "entity_codes": copy.deepcopy(SELECTOR),
    }


def _check(
    check_id: str,
    operator: str,
    *,
    params: dict[str, Any] | None = None,
    message: str = "{opinion}",
    location_policy: str = "row",
    operator_version: int = 1,
) -> dict[str, Any]:
    """生成一个 1016 规则检查配置。

    Args:
        check_id: 稳定检查标识。
        operator: 能力注册名称。
        params: 传递给能力的规则参数。
        message: 不含规则序号的意见模板。
        location_policy: 问题写回定位策略。
        operator_version: 规则能力版本。

    Returns:
        可直接写入规则 JSON 的检查配置。
    """

    return {
        "check_id": check_id,
        "location_policy": location_policy,
        "message": {"review": message, "violation": message},
        "on_unavailable": "review",
        "operator": operator,
        "operator_version": operator_version,
        "params": params or {},
    }


def _rule(
    rule_id: str,
    display_code: str,
    title: str,
    record_type: str,
    checks: list[dict[str, Any]],
    *,
    revision: int = 1,
) -> dict[str, Any]:
    """生成一条新的重庆确认稿规则。

    Args:
        rule_id: 不随展示顺序变化的稳定规则标识。
        display_code: 与 Word 顺序一致的展示编号。
        title: Word 中的规则标题。
        record_type: 能力执行边界。
        checks: 该规则包含的检查配置。
        revision: 当前稳定规则的修订版本。

    Returns:
        完整规则配置。
    """

    return {
        "checks": checks,
        "display_code": display_code,
        "enabled": True,
        "revision": revision,
        "rule_id": rule_id,
        "schema_version": "1.0",
        "scope": _scope(record_type),
        "source_reference": SOURCE_REFERENCE,
        "title": title,
    }


def _renumbered(base_rule: dict[str, Any], display_code: str, title: str) -> dict[str, Any]:
    """复制现有能力配置并移除模板内的历史编号。

    Args:
        base_rule: 1.9.32 中继续有效的规则配置。
        display_code: 1015 Word 对应的新展示编号。
        title: 1015 Word 中的规则标题。

    Returns:
        仅从 ``display_code`` 取得意见序号的新规则配置。
    """

    rule = copy.deepcopy(base_rule)
    rule.update(
        display_code=display_code,
        title=title,
        source_reference=SOURCE_REFERENCE,
        revision=int(rule["revision"]) + 1,
    )
    for check in rule["checks"]:
        for kind in ("review", "violation"):
            check["message"][kind] = RULE_LABEL_PATTERN.sub("", check["message"][kind])
    return rule


def _preprocessing_rule(base_rule: dict[str, Any], title: str) -> dict[str, Any]:
    """把原排序检查归入不带条款序号的数据预处理分组。

    Args:
        base_rule: 1.9.32 中对应清单的排序规则。
        title: 当前预处理规则标题。

    Returns:
        保持禁用意见检查、仅声明预处理归属的配置。
    """

    rule = copy.deepcopy(base_rule)
    rule.update(
        display_code="P02",
        title=title,
        source_reference=SOURCE_REFERENCE,
        revision=int(rule["revision"]) + 1,
    )
    return rule


def _target_rules(base_rules: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按 Word 接受修订后的顺序构造全部目标规则。

    Args:
        base_rules: 1.9.33 按稳定规则标识建立的规则映射。

    Returns:
        目标文件名到 1016 规则配置的映射。
    """

    schema_rule = _renumbered(
        base_rules["schema.applicability"],
        "R02",
        "基准字段及适用性填写情况",
    )
    schema_rule["checks"] = [
        check for check in schema_rule["checks"]
        if check["check_id"] == "matrix_schema"
    ]
    schema_check = schema_rule["checks"][0]
    schema_check["check_id"] = "matrix_schema_1016"
    # 新确认稿要求比较全部基准字段，并直接反馈具体差异字段。
    schema_check["params"].pop("ignore_fields", None)

    duty_completeness_rule = _renumbered(
        base_rules["duties.completeness"],
        "R04",
        "岗位清单中部门、岗位名称和人员姓名",
    )
    person_required_check = next(
        check for check in duty_completeness_rule["checks"]
        if check["check_id"] == "person_required"
    )
    person_required_check["params"]["placeholder_patterns"] = [
        "等|…|\\.{3}|外包|附件|不详|待定"
    ]

    incompatible_rule = _renumbered(
        base_rules["lists.incompatible_exists"],
        "R13",
        "不相容岗位清单审核",
    )
    incompatible_rule["checks"].append(_check(
        "incompatible_position_alignment_1016",
        "chongqing_incompatible_position_alignment",
    ))

    rules = {
        "P01.json": _renumbered(base_rules["preprocessing.system_type_notice"], "P01", "系统类型预处理未决提示"),
        "P02a.json": _preprocessing_rule(base_rules["lists.order"], "岗位内控责任清单排序预处理"),
        "P02b.json": _preprocessing_rule(base_rules["lists.order_system"], "系统控制规则清单排序预处理"),
        "P02c.json": _preprocessing_rule(base_rules["lists.order_incompatible"], "不相容岗位清单排序预处理"),
        "R01.json": _renumbered(base_rules["documents.completeness"], "R01", "资料提交完整性"),
        "R02.json": schema_rule,
        "R03.json": _renumbered(
            base_rules["chongqing.responsibility_format_1015"],
            "R03",
            "矩阵中责任主体规范性审核",
        ),
        "R04.json": duty_completeness_rule,
        "R05.json": _rule(
            "chongqing.person_name_single",
            "R05",
            "岗位清单中人员姓名规范性审核",
            "position_duty",
            [_check(
                "person_name_single",
                "text_pattern",
                params={"field": "person_names", "patterns": [r"^[^\s、，,]{1,4}$"]},
                message="每行岗位名称中只能有一个对应人员，若有多人，则应分多行填写。",
            )],
            revision=3,
        ),
        "R06.json": _renumbered(
            base_rules["chongqing.person_multiple_positions_1015"],
            "R06",
            "岗位清单中一人多岗审核",
        ),
        "R07.json": _renumbered(
            base_rules["chongqing.role_responsibility_mapping_1015"],
            "R07",
            "岗位清单中角色与责任类型对应",
        ),
        "R08.json": _rule(
            "chongqing.duty_separation_1016",
            "R08",
            "岗位清单中经办与审核人员分离",
            "package",
            [_check(
                "handler_reviewer_supervisor_overlap_1016",
                "chongqing_duty_separation",
                params={
                    "infer_role_from_duty": False,
                    "person_placeholders": ["等", "…", "...", "外包", "附件", "不详", "待定"],
                },
                operator_version=2,
            )],
        ),
        "R09.json": _rule(
            "chongqing.responsibility_phrase_1016",
            "R09",
            "岗位清单中岗位职责基本责任表述",
            "position_duty",
            [_check(
                "responsibility_phrase_1016",
                "chongqing_responsibility_phrase",
                params={"split_opinions": True},
                operator_version=2,
            )],
        ),
        "R10.json": _renumbered(base_rules["duties.duplicate_rows"], "R10", "岗位清单中重复项筛查"),
        "R11.json": _renumbered(
            base_rules["chongqing.matrix_duty_alignment_1015"],
            "R11",
            "岗位清单与矩阵一致性审核",
        ),
        "R12.json": _renumbered(
            base_rules["chongqing.responsibility_name_alignment_1015"],
            "R12",
            "矩阵中责任主体与岗位清单一致性审核",
        ),
        "R13.json": incompatible_rule,
        "R14.json": _rule(
            "chongqing.system_control_consistency_1016",
            "R14",
            "系统控制规则清单控制方式匹配性审核",
            "package",
            [_check(
                "system_control_consistency_1016",
                "chongqing_system_control_consistency",
                params={
                    "compare_system_names": False,
                    "matrix_missing_opinion": "需核实矩阵中是否有该条措施或控制方式是否正确",
                    "validate_build_status": True,
                },
                operator_version=2,
            )],
        ),
    }
    return rules


def update_draft(draft: Path, source: Path) -> None:
    """重建草稿规则目录并更新 1016 来源信息。

    Args:
        draft: 从重庆 1.9.33 复制出的规则草稿目录。
        source: 仓库内冻结的 1016 重庆规则来源文档。
    """

    rules_directory = draft / "rules"
    # 始终从冻结的 1.9.33 发布包取基线，发布中断后重复执行也不会叠加转换。
    base_rules_directory = draft.parents[1] / "releases" / BASE_VERSION / "rules"
    base_rules = {
        rule["rule_id"]: rule
        for rule_path in base_rules_directory.glob("*.json")
        for rule in [json.loads(rule_path.read_text(encoding="utf-8"))]
    }
    # 重建规则目录，避免已删除规则或上一次中断留下的文件继续执行。
    shutil.rmtree(rules_directory, ignore_errors=True)
    rules_directory.mkdir(parents=True)
    for filename, rule in _target_rules(base_rules).items():
        write_json(rules_directory / filename, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        audit_as_of="2026-10-16",
        title="重庆风控矩阵及三清单 1016 审核确认稿规则包",
        source_document_sha256=sha256_file(source),
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore, source: Path) -> tuple[Path, Path]:
    """生成并校验重庆 1.9.34 草稿与发布包。

    Args:
        store: 规则包仓库。
        source: 已冻结的 1016 重庆规则来源文档。

    Returns:
        新生成的草稿目录和不可变发布目录。
    """

    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft, source)
    store.validate(draft)
    return draft, store.publish(draft.name, TARGET_VERSION)


def tree_hashes(root: Path) -> dict[str, str]:
    """返回目录内全部文件的 SHA-256 映射。

    Args:
        root: 待计算哈希的规则草稿或发布目录。

    Returns:
        文件相对路径到 SHA-256 的映射。
    """

    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_existing_release(store: RulePackStore, source: Path) -> Path:
    """临时重建并比对现有重庆 1.9.34 发布产物。

    Args:
        store: 当前规则包仓库。
        source: 已冻结的 1016 重庆规则来源文档。

    Returns:
        已通过确定性比对的发布目录。
    """

    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.34 草稿与发布包必须同时存在")

    with tempfile.TemporaryDirectory(prefix="risk-audit-rules-1016-chongqing-1934-") as temporary_directory:
        temporary_root = Path(temporary_directory) / "rulepacks"
        temporary_base = temporary_root / "releases" / BASE_VERSION
        temporary_base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(store.root / "releases" / BASE_VERSION, temporary_base)
        temporary_store = RulePackStore(temporary_root, build_registry())
        generated_draft, generated_release = build_release(temporary_store, source)
        for label, actual, generated in (
            ("草稿", actual_draft, generated_draft),
            ("发布包", actual_release, generated_release),
        ):
            if tree_hashes(actual) != tree_hashes(generated):
                raise RuntimeError(f"现有{label}与临时重建结果不一致")

    store.validate(actual_release)
    return actual_release


def source_document() -> Path:
    """返回仓库内冻结的 1016 重庆规则来源文档。

    Returns:
        1016 重庆 Word 规则来源路径。
    """

    source = ROOT / "risk-audit/规则来源" / SOURCE_NAME
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def main() -> None:
    """生成或只读复验重庆 1.9.34 规则包。"""

    parser = argparse.ArgumentParser(description="生成或复验重庆 1.9.34 规则包")
    parser.add_argument("--verify-only", action="store_true", help="只验证已有发布产物")
    args = parser.parse_args()
    source = source_document()
    store = RulePackStore(ROOT / "risk-audit/rulepacks", build_registry())
    draft_exists = (store.root / "drafts" / DRAFT_NAME).exists()
    release_exists = (store.root / "releases" / TARGET_VERSION).exists()

    if args.verify_only and not (draft_exists and release_exists):
        raise RuntimeError("--verify-only 要求 1.9.34 草稿与发布包已存在")
    if draft_exists and release_exists:
        release = verify_existing_release(store, source)
        verified = True
    elif draft_exists:
        draft = store.root / "drafts" / DRAFT_NAME
        update_draft(draft, source)
        store.validate(draft)
        release = store.publish(DRAFT_NAME, TARGET_VERSION)
        verified = False
    elif release_exists:
        raise RuntimeError("1.9.34 发布包存在但草稿缺失，无法执行确定性复验")
    else:
        _, release = build_release(store, source)
        verified = False

    print(json.dumps({"release": str(release), "verified": verified}, ensure_ascii=False))


if __name__ == "__main__":
    main()
