"""从冻结的重庆 1024 确认稿及附件构建、只读复验 1.9.36 规则包。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile

import openpyxl
import xlrd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from risk_audit.business_identity import normalize_business_text
from risk_audit.checks.registry import build_registry
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.util import sha256_file, write_json


BASE_VERSION = "1.9.35"
TARGET_VERSION = "1.9.36"
DRAFT_NAME = "rules-1024-chongqing-1.9.36"
SOURCE = ROOT / "规则来源/风控矩阵1024-重庆.docx"
REFERENCES = ROOT / "baselines/chongqing/reference-data"
STANDARD_SOURCE = REFERENCES / "附件1：标准库.xlsx"
COMPANY_SOURCE = REFERENCES / "附件2：公司名称与单位性质匹配表.xls"


def compile_standard_library(registry: dict) -> dict:
    """编译附件并保留原单元格来源；registry 为当前重庆业务身份注册表。"""
    workbook = openpyxl.load_workbook(STANDARD_SOURCE, data_only=True)
    try:
        sheet = workbook.active
        if [sheet.cell(2, column).value for column in range(1, 6)] != ["矩阵名称", "矩阵类型", "控制措施编码", "重庆控制措施编号", "控制措施内容"]:
            raise ValueError("标准库表头与冻结口径不一致")
        unit_columns = {sheet.cell(3, column).value: column for column in range(6, sheet.max_column + 1)}
        measures = []
        for row in range(6, sheet.max_row + 1):
            values = [sheet.cell(row, column).value for column in range(1, 6)]
            if not any(value is not None for value in values):
                continue
            if not all(values):
                raise ValueError(f"标准库第{row}行缺少必需字段")
            normalized = normalize_business_text(values[0]).removesuffix("业务")
            matches = [business for business in registry["businesses"] if normalized in {
                normalize_business_text(name).removesuffix("业务") for name in [business["business_name"], *business.get("aliases", [])]
            }]
            if len(matches) != 1:
                raise ValueError(f"标准库业务无法唯一匹配：{values[0]}")
            measures.append({
                "matrix_name": values[0], "matrix_type": values[1], "standard_code": values[2],
                "measure_id": values[3], "control_measure": values[4],
                "business_id": matches[0]["business_id"], "business_code": matches[0]["business_code"],
                "applicable_cells": {nature: f"{sheet.title}!{sheet.cell(row, column).coordinate}" for nature, column in unit_columns.items() if sheet.cell(row, column).value == "是"},
            })
    finally:
        workbook.close()
    company_book = xlrd.open_workbook(COMPANY_SOURCE, on_demand=True)
    try:
        sheet = company_book.sheet_by_index(0)
        headers = sheet.row_values(1)
        name_column, nature_column = headers.index("公司名称"), headers.index("单位性质")
        companies = []
        for row in range(2, sheet.nrows):
            name, nature = sheet.cell_value(row, name_column), sheet.cell_value(row, nature_column)
            if not name or nature not in unit_columns:
                raise ValueError(f"公司性质表第{row + 1}行缺少公司或性质不在标准库中")
            companies.append({"company_name": name, "unit_nature": nature, "source_cell": f"{sheet.name}!E{row + 1}"})
    finally:
        company_book.release_resources()
    return {
        "schema_version": "1.0", "companies": companies, "measures": measures,
        "sources": [{"name": path.name, "sha256": sha256_file(path)} for path in (STANDARD_SOURCE, COMPANY_SOURCE)],
    }


def new_rule(code: str, title: str, operator: str, location: str) -> dict:
    """创建新增包级规则；code/title 为展示信息，operator 为能力名，location 为落点。"""
    return {
        "schema_version": "1.0", "rule_id": "chongqing." + operator.removeprefix("chongqing_"),
        "display_code": code, "title": title, "source_reference": SOURCE.name + " 接受修订后的有效正文",
        "revision": 1, "enabled": True,
        "scope": {"record_type": "package", "business_codes": {"include": ["*"], "exclude": []}, "entity_codes": {"include": ["*"], "exclude": []}},
        "checks": [{"check_id": operator.removeprefix("chongqing_"), "operator": operator, "operator_version": 1,
                    "params": {}, "on_unavailable": "review", "location_policy": location,
                    "message": {"violation": "{opinion}", "review": "{opinion}"}}],
    }


def update_draft(draft: Path) -> None:
    """替换为六条规则并冻结附件；draft 为从 1.9.35 复制出的草稿目录。"""
    rules_path = draft / "rules"
    previous = {path.stem: json.loads(path.read_text()) for path in rules_path.glob("*.json")}
    rules = []
    for old_code, new_code in (("R01", "R01"), ("R11", "R02"), ("R13", "R03"), ("R14", "R05"), ("P01", "P01")):
        rule = deepcopy(previous[old_code])
        rule.update(display_code=new_code, revision=rule["revision"] + 1, source_reference=SOURCE.name + " 接受修订后的有效正文")
        if new_code == "R01":
            rule["checks"][0]["operator"] = "chongqing_required_documents_1024"
            rule["checks"][0]["operator_version"] = 1
        if new_code == "R03":
            rule["checks"][1].update(operator="chongqing_incompatible_alignment_1024", operator_version=1, check_id="incompatible_position_alignment_1024")
        rules.append(rule)
    rules.extend([
        new_rule("R04", "不相容岗位清单与岗位内控责任清单逻辑匹配性审核", "chongqing_incompatible_duty_alignment", "row"),
        new_rule("R06", "标准库控制措施完整性审核", "chongqing_standard_library_completeness", "material"),
    ])
    # 草稿内移除旧规则，已发布历史规则包保持冻结。
    for path in rules_path.glob("*.json"):
        path.unlink()
    for rule in rules:
        write_json(rules_path / f'{rule["display_code"]}.json', rule)
    write_json(draft / "overlays.json", [])
    registry = json.loads((draft / "baseline_registry.json").read_text())
    write_json(draft / "standard_library.json", compile_standard_library(registry))
    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(version=TARGET_VERSION, audit_as_of="2026-10-24", title="重庆风控矩阵及三清单 1024 六条审核规则包", source_document_sha256=sha256_file(SOURCE), engine_compatibility=">=2.2.3,<3.0.0")
    write_json(manifest_path, manifest)


def build_release(store: RulePackStore) -> tuple[Path, Path]:
    """构建并验证发布包；store 为目标规则仓库，返回草稿与不可变发布目录。"""
    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft)
    store.validate(draft)
    return draft, store.publish(DRAFT_NAME, TARGET_VERSION)


def tree_hashes(root: Path) -> dict[str, str]:
    """返回文件内容摘要；root 为待复验的草稿或发布包目录。"""
    return {path.relative_to(root).as_posix(): sha256_file(path) for path in sorted(root.rglob("*")) if path.is_file()}


def verify_release(store: RulePackStore) -> Path:
    """临时重建并比对发布产物；store 为包含 1.9.35 与 1.9.36 的仓库。"""
    release = store.root / "releases" / TARGET_VERSION
    with tempfile.TemporaryDirectory(prefix="risk-audit-1024-") as directory:
        temporary_root = Path(directory)
        baseline = temporary_root / "releases" / BASE_VERSION
        baseline.parent.mkdir(parents=True)
        shutil.copytree(store.root / "releases" / BASE_VERSION, baseline)
        generated_draft, generated_release = build_release(RulePackStore(temporary_root, build_registry()))
        for actual, generated in ((store.root / "drafts" / DRAFT_NAME, generated_draft), (release, generated_release)):
            if tree_hashes(actual) != tree_hashes(generated):
                raise RuntimeError(f"冻结产物与重新构建内容不一致：{actual}")
    store.validate(release)
    return release


def main() -> None:
    """执行构建或只读确定性复验；命令行 --verify-only 禁止创建正式产物。"""
    parser = argparse.ArgumentParser(description="构建或复验重庆 1024 规则包")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    store = RulePackStore(ROOT / "rulepacks", build_registry())
    draft_exists = (store.root / "drafts" / DRAFT_NAME).exists()
    release_exists = (store.root / "releases" / TARGET_VERSION).exists()
    if args.verify_only or draft_exists or release_exists:
        if not draft_exists or not release_exists:
            raise RuntimeError("复验要求草稿与发布包同时存在")
        release = verify_release(store)
        verified = True
    else:
        _, release = build_release(store)
        verified = False
    print(json.dumps({"release": str(release), "verified": verified}, ensure_ascii=False))


if __name__ == "__main__":
    main()
