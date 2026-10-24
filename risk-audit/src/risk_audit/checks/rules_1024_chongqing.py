"""重庆 1024 确认稿的材料、不相容岗位和标准库检查。"""

from collections import Counter, defaultdict
from typing import Any

from risk_audit.checks.materials_v180 import material_categories, required_documents_v3
from risk_audit.checks.registry import CheckContext
from risk_audit.checks.rules_1015_chongqing import INCOMPATIBLE_EMPTY_VALUES, _issue, _measure_key, _scope_key
from risk_audit.standard_library import compare_standard_library
from risk_audit.util import norm_text


def required_documents(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """分别检查矩阵与三清单；ctx 为审核上下文，params 为材料类别及命名配置。"""
    issues = required_documents_v3(ctx, {**params, "material_types": []})
    present = defaultdict(list)
    for file in ctx.files:
        categories = material_categories(file)
        if file.material_type == "matrix":
            categories.add("matrix")
        for category in categories:
            present[(file.entity_code, file.business_id or file.business_code, file.variant_id, category)].append(file)
    for entity_code in ctx.scope_entity_codes:
        for business in ctx.scope_businesses:
            if not business.get("required", True):
                continue
            business_id = business.get("business_id") or business["business_code"]
            variant = business.get("variant_id", "default")
            for category in params.get("material_types", []):
                files = present[(entity_code, business_id, variant, category)]
                evidence = {"entity_code": entity_code, "business_id": business_id, "business_code": business["business_code"], "variant_id": variant}
                if not files:
                    issues.append({"record": None, "kind": "violation", "evidence": {**evidence, "missing_material": category}})
                else:
                    versions = defaultdict(set)
                    for file in files:
                        for sheet in file.sheets:
                            if (sheet.sheet_type == "matrix") == (category == "matrix"):
                                versions[sheet.sheet_type].add(file.sha256)
                    if any(len(hashes) > 1 for hashes in versions.values()):
                        issues.append({"record": None, "kind": "review", "evidence": {**evidence, "unavailable_reason": f"同一业务存在多个不同内容的{category}版本，请明确正式版本"}})
    return issues


def positions(record) -> list[tuple[str, str]]:
    """返回有效岗位字段与文字；record 为不相容清单记录，过滤无及不涉及占位值。"""
    return [(field, norm_text(record.value(field))) for field in ("position_a", "position_b", "position_c") if norm_text(record.value(field)) not in INCOMPATIBLE_EMPTY_VALUES]


def position_name(value: str) -> str:
    """提取岗位名称；value 可为岗位本身或部门-岗位，部门不参与岗位同名判断。"""
    return norm_text(value.replace("—", "-").replace("－", "-").split("-", 1)[-1])


def incompatible_alignment(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """执行新版第3条；ctx 为全部跨表记录，params 为保留参数，不要求岗位同部门。"""
    del params
    matrices = defaultdict(list)
    lists = defaultdict(list)
    issues = []
    for record in ctx.all_records:
        key = _measure_key(record)
        if not key:
            continue
        full_key = (*_scope_key(record), key)
        if record.record_type == "matrix":
            matrices[full_key].append(record)
        elif record.record_type == "incompatible_position" and positions(record):
            lists[full_key].append(record)
    for key, rows in matrices.items():
        related = lists.get(key, [])
        complete = any(all(norm_text(row.value(field)) not in INCOMPATIBLE_EMPTY_VALUES for field in ("position_a", "position_b")) for row in related)
        for row in rows:
            marked = norm_text(row.value("incompatible_control")) == "√"
            if marked and not complete:
                issues.append(_issue(row, "incompatible_list_missing", "请补充不相容岗位清单"))
            elif related and not marked:
                issues.append(_issue(row, "incompatible_matrix_unmarked", "请核实是否存在不相容岗位"))
    for rows in lists.values():
        counts = Counter(position_name(value) for row in rows for _, value in positions(row))
        for row in rows:
            if any(counts[position_name(value)] > 1 for _, value in positions(row)):
                issues.append(_issue(row, "incompatible_same_position", "岗位名称相同，请确认是否为不相容岗位"))
    return issues


def incompatible_duty_alignment(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """核对新版第4条岗位存在性；ctx 为矩阵和清单上下文，params 为保留参数。"""
    del params
    duties = defaultdict(set)
    for record in ctx.all_records:
        key = _measure_key(record)
        if record.record_type == "position_duty" and key:
            duties[(*_scope_key(record), key)].add(position_name(record.value("position")))
    issues = []
    for record in ctx.all_records:
        if record.record_type != "incompatible_position" or not positions(record):
            continue
        key = _measure_key(record)
        if not key:
            issues.append(_issue(record, "incompatible_measure_id_missing", "请补充控制措施编号，无法核对岗位内控责任清单"))
            continue
        missing = sorted({value for _, value in positions(record) if position_name(value) not in duties[(*_scope_key(record), key)]})
        if missing:
            issues.append(_issue(record, "incompatible_duty_position_missing", "请核实不相容岗位清单中填列的岗位名称在岗位内控责任清单中是否填列", missing_positions=missing, measure_id=record.value("measure_id")))
    return issues


def standard_library_completeness(ctx: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    """按主体汇总新版第6条；ctx 包含全部业务，params 为保留参数，正常项仅进入报告。"""
    del params
    issues = []
    for entity_code in ctx.scope_entity_codes:
        name = ctx.resources.get("_entity_names", {}).get(entity_code)
        if not name:
            entity = ctx.entities.get(entity_code)
            name = entity.name if entity else entity_code
        comparison = compare_standard_library(ctx.resources.get("standard_library", {}), name, ctx.all_records, entity_code=entity_code, blocked_business_ids=ctx.resources.get("_blocked_business_ids", []))
        if comparison["match_status"] != "matched":
            issues.append({"record": None, "kind": "review", "evidence": {"entity_code": entity_code, "opinion": comparison["opinion"], "issue_type": "standard_company_unconfirmed"}})
            continue
        grouped_rows = defaultdict(list)
        for row in comparison["rows"]:
            if row["status"] == "present":
                continue
            grouped_rows[(row["business_id"], row["status"])].append(row)
        for (_, status), rows in grouped_rows.items():
            row = rows[0]
            examples = "、".join(item["measure_id"] for item in rows[:3])
            # 全量缺项保存在独立结果表，意见按业务归集，避免超过 Excel 单元格长度。
            issues.append({"record": None, "kind": "review" if row["status"] == "unavailable" else "violation", "evidence": {
                "entity_code": entity_code, "issue_type": "standard_measure_" + row["status"],
                "opinion": f'{row["opinion"]}：{row["matrix_name"]}，共{len(rows)}条，编号示例：{examples}；详见标准库比对结果表',
                "business_id": row["business_id"], "business_code": row["business_code"],
                "standard_measures": [item["standard_code"] for item in rows], "status": status,
            }})
    return issues
