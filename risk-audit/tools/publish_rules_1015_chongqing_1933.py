"""根据重庆 1015 审核确认稿发布重新编号的 1.9.33 规则包。"""

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


BASE_VERSION = "1.9.32"
TARGET_VERSION = "1.9.33"
DRAFT_NAME = "rules-1015-chongqing-1.9.33"
SOURCE_NAME = "风控矩阵1015-重庆.docx"
SOURCE_REFERENCE = f"{SOURCE_NAME} 接受修订后的有效正文"
RULE_LABEL_PATTERN = re.compile(r"^(?:【第\d+条】)+")
SELECTOR = {"include": ["*"], "exclude": []}

DEPARTMENT_EXCEPTIONS = [
    "项目管理部门", "实物资产管理部门", "资产使用保管部门", "资产实物管理部门",
    "办公用品管理部门", "各部门", "牵头管理部门", "归口管理部门",
    "因公出国（境）经费归口管理部门", "公务车辆管理部门", "会议承办部门",
    "会议管理部门", "接待承办部门", "资金支付发起部门", "迁改工程项目管理部门",
    "设备部门", "配网项目管理部门", "物资需求部门", "科技部门", "科技项目承担部门",
    "项目承担部门", "实物管理部门", "用工管理部门", "员工管理部门",
    "职工教育经费管理部门", "职工疗养实施部门", "社保管理部门", "其他福利管理部门",
    "其他福利实施部门", "食堂经费管理部门", "食堂经费实施部门", "行政公章管理部门",
    "合同承办部门", "业务部门",
]

POSITION_EXCEPTIONS = [
    "业务受理人员", "业扩报装管理人员", "现场勘查人员", "业务审核人员", "合同签订人员",
    "合同管理人员", "合同结算人员", "合同经办人员", "合同审核人员", "计量管理人员",
    "计量管理复核人员", "计量装表人员", "电费管理人员", "购电费结算人员",
    "电费电价与购电管理人员", "资金管理人员", "稽查监控人员", "项目管理人员",
    "实物资产管理人员", "资产使用保管人员", "工程财务管理人员", "工程管理人员",
    "工程财务人员", "设备资产管理人员", "设备资产运维人员", "资产实物管理人员",
    "办公用品管理人员", "电商采购执行与监督人员", "出差人员", "预算管理人员",
    "成本管理人员", "报账人员", "负责人", "部门负责人", "消防与安保管理人员",
    "纪检管理人员", "出差报销审核人", "车辆管理人员", "劳动保护管理人员", "审批人员",
    "计划管理人员", "可研核准管理人员", "计划人员", "前期规划人员", "投资管理人员",
    "法律合规人员", "技经人员", "项目经理", "履约管理人员", "物资管理人员",
    "迁改工程管理人员", "项目前期人员", "工程管理人员及设备资产管理人员",
    "招投标管理人员", "质量监督人员", "物资招标人员", "往来管理人员",
    "归口费用审核人员", "招标采购管理人员", "采购计划管理人员", "供应管理人员",
    "仓储管理人员", "专业仓管理人员", "电商管理人员", "物资上架人员",
    "废旧物资管理人员", "物资配送人员", "协议匹配人员", "物资履约人员",
    "物资调拨人员", "招标采购人员", "物资收货人员", "需求提报人员", "管理人员",
    "保险管理人员", "职工疗养管理人员", "绩效管理人员", "薪酬管理人员",
    "劳动组织管理人员", "员工管理人员", "员工奖惩管理人员", "培训管理人员",
    "职工疗养实施人员", "社保公积金管理人员", "财务信息化管理人员",
    "会计核算管理人员", "行政公章管理人员", "授权管理人员", "相关业务管理人员",
    "报表管理人员", "财税管理人员", "档案管理人员", "支付审核人员", "电价管理人员",
    "对账编制人员", "会计档案管理人员", "会计岗位人员", "经办人", "经办人员",
    "财务负责人", "密钥持有人员", "票据管理人员", "融资管理人员", "收入管理人员",
    "印鉴持有人", "税务管理人员", "税收管理人员", "资金审核人员", "资金预算人员",
    "总账管理人员", "产权管理人员", "资产与产权管理人员",
]


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
) -> dict[str, Any]:
    """生成一个 1015 规则检查配置。

    Args:
        check_id: 稳定检查标识。
        operator: 能力注册名称。
        params: 传递给能力的规则参数。
        message: 不含规则序号的意见模板。
        location_policy: 问题写回定位策略。

    Returns:
        可直接写入规则 JSON 的检查配置。
    """

    return {
        "check_id": check_id,
        "location_policy": location_policy,
        "message": {"review": message, "violation": message},
        "on_unavailable": "review",
        "operator": operator,
        "operator_version": 1,
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
        base_rules: 1.9.32 按稳定规则标识建立的规则映射。

    Returns:
        目标文件名到 1015 规则配置的映射。
    """

    rules = {
        "P01.json": _renumbered(base_rules["preprocessing.system_type_notice"], "P01", "系统类型预处理未决提示"),
        "P02a.json": _preprocessing_rule(base_rules["lists.order"], "岗位内控责任清单排序预处理"),
        "P02b.json": _preprocessing_rule(base_rules["lists.order_system"], "系统控制规则清单排序预处理"),
        "P02c.json": _preprocessing_rule(base_rules["lists.order_incompatible"], "不相容岗位清单排序预处理"),
        "R01.json": _renumbered(base_rules["documents.completeness"], "R01", "资料提交完整性"),
        "R02.json": _renumbered(base_rules["entities.explanation"], "R02", "会计主体与提交资料对应情况"),
        "R03.json": _renumbered(base_rules["schema.applicability"], "R03", "基准字段及适用性填写情况"),
        "R04.json": _renumbered(base_rules["duties.completeness"], "R04", "岗位清单中部门、岗位名称和人员姓名"),
        "R05.json": _renumbered(base_rules["lists.incompatible_exists"], "R05", "不相容岗位清单存在性"),
        "R06.json": _rule(
            "chongqing.duty_separation_1015",
            "R06",
            "经办与审核、监督人员分离",
            "package",
            [_check("handler_reviewer_supervisor_overlap", "chongqing_duty_separation")],
        ),
        "R07.json": _rule(
            "chongqing.matrix_duty_alignment_1015",
            "R07",
            "岗位内控责任清单与矩阵一致性审核",
            "package",
            [_check("matrix_duty_measure_alignment", "chongqing_matrix_duty_alignment")],
        ),
        "R08.json": _rule(
            "chongqing.responsibility_phrase_1015",
            "R08",
            "岗位职责基本责任表述",
            "position_duty",
            [_check("responsibility_phrase_1015", "chongqing_responsibility_phrase")],
        ),
        "R09.json": _rule(
            "chongqing.role_responsibility_mapping_1015",
            "R09",
            "角色与责任类型对应",
            "position_duty",
            [_check("role_responsibility_mapping_1015", "chongqing_role_responsibility_mapping")],
        ),
        "R10.json": _renumbered(base_rules["duties.duplicate_rows"], "R10", "岗位职责清单中重复项筛查"),
        "R11.json": _rule(
            "chongqing.system_control_consistency_1015",
            "R11",
            "系统控制规则清单控制方式匹配性判断",
            "package",
            [_check("system_control_consistency_1015", "chongqing_system_control_consistency")],
        ),
        "R12.json": _rule(
            "chongqing.responsibility_format_1015",
            "R12",
            "矩阵中责任主体规范性审核",
            "package",
            [_check("responsibility_format_1015", "chongqing_responsibility_format")],
        ),
        "R13.json": _rule(
            "chongqing.responsibility_name_alignment_1015",
            "R13",
            "矩阵中责任主体与岗位内控责任清单一致性审核",
            "package",
            [_check(
                "responsibility_name_alignment_1015",
                "chongqing_responsibility_name_alignment",
                params={
                    "department_exceptions": DEPARTMENT_EXCEPTIONS,
                    "position_exceptions": POSITION_EXCEPTIONS,
                },
            )],
        ),
        "R14.json": _rule(
            "chongqing.person_multiple_positions_1015",
            "R14",
            "岗位责任清单中一人多岗审核",
            "package",
            [_check("person_multiple_positions_1015", "chongqing_person_multiple_positions")],
        ),
    }
    return rules


def update_draft(draft: Path, source: Path) -> None:
    """重建草稿规则目录并更新 1015 来源信息。

    Args:
        draft: 从重庆 1.9.32 复制出的规则草稿目录。
        source: 仓库内冻结的 1015 重庆规则来源文档。
    """

    rules_directory = draft / "rules"
    base_rules = {
        rule["rule_id"]: rule
        for rule_path in rules_directory.glob("*.json")
        for rule in [json.loads(rule_path.read_text(encoding="utf-8"))]
    }
    # 草稿是一次性生成目录，先移除旧编号文件可避免删除规则继续执行。
    shutil.rmtree(rules_directory)
    rules_directory.mkdir()
    for filename, rule in _target_rules(base_rules).items():
        write_json(rules_directory / filename, rule)

    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        version=TARGET_VERSION,
        audit_as_of="2026-10-15",
        title="重庆风控矩阵及三清单 1015 审核确认稿规则包",
        source_document_sha256=sha256_file(source),
    )
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore, source: Path) -> tuple[Path, Path]:
    """生成并校验重庆 1.9.33 草稿与发布包。

    Args:
        store: 规则包仓库。
        source: 已冻结的 1015 重庆规则来源文档。

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
    """临时重建并比对现有重庆 1.9.33 发布产物。

    Args:
        store: 当前规则包仓库。
        source: 已冻结的 1015 重庆规则来源文档。

    Returns:
        已通过确定性比对的发布目录。
    """

    actual_draft = store.root / "drafts" / DRAFT_NAME
    actual_release = store.root / "releases" / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError("1.9.33 草稿与发布包必须同时存在")

    with tempfile.TemporaryDirectory(prefix="risk-audit-rules-1015-chongqing-1933-") as temporary_directory:
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
    """返回仓库内冻结的 1015 重庆规则来源文档。

    Returns:
        1015 重庆 Word 规则来源路径。
    """

    source = ROOT / "risk-audit/规则来源" / SOURCE_NAME
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def main() -> None:
    """生成或只读复验重庆 1.9.33 规则包。"""

    parser = argparse.ArgumentParser(description="生成或复验重庆 1.9.33 规则包")
    parser.add_argument("--verify-only", action="store_true", help="只验证已有发布产物")
    args = parser.parse_args()
    source = source_document()
    store = RulePackStore(ROOT / "risk-audit/rulepacks", build_registry())
    draft_exists = (store.root / "drafts" / DRAFT_NAME).exists()
    release_exists = (store.root / "releases" / TARGET_VERSION).exists()

    if args.verify_only and not (draft_exists and release_exists):
        raise RuntimeError("--verify-only 要求 1.9.33 草稿与发布包已存在")
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
        raise RuntimeError("1.9.33 发布包存在但草稿缺失，无法执行确定性复验")
    else:
        _, release = build_release(store, source)
        verified = False

    print(json.dumps({"release": str(release), "verified": verified}, ensure_ascii=False))


if __name__ == "__main__":
    main()
