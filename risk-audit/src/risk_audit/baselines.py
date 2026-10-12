from __future__ import annotations

from pathlib import Path
from typing import Any

from risk_audit.models import FileRecord
from risk_audit.readers.excel import parse_files
from risk_audit.util import norm_text, sha256_file


def _normalized_header_paths(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "coordinate": item["coordinate"],
            "column": item["column"],
            "path": [
                {"name": norm_text(part["name"]), "coordinate": part["coordinate"]}
                for part in item["path"]
            ],
        }
        for item in paths
    ]


def _baseline_entries(registry: dict[str, Any]) -> list[dict[str, Any]]:
    """展开基准配置；registry 为模板目录，返回带业务身份的模板条目。"""
    if registry.get("schema_version") == "2.0":
        return [
            {
                "business_id": business["business_id"],
                "business_code": business["business_code"],
                "business_name": business["business_name"],
                "variant_id": variant["variant_id"],
                **variant["template"],
            }
            for business in registry.get("businesses", [])
            for variant in business.get("variants", [])
        ]
    # 历史发布包只用于复核旧运行；当前发布包必须使用 schema_version 2.0。
    return [
        {**item, "business_id": item["business_code"], "business_name": item["business_code"]}
        for item in registry.get("entries", [])
    ]


def _unavailable_baseline(item: dict[str, Any], reason: str) -> dict[str, Any]:
    """构造不可用模板占位数据。

    Args:
        item: 带业务身份和模板路径的基准配置。
        reason: 模板不存在、哈希变化或解析失败的具体原因。
    """

    return {
        "available": False,
        "error": reason,
        "path": item["path"],
        "sha256": item.get("sha256", ""),
        "business_id": item["business_id"],
        "business_code": item["business_code"],
        "business_name": item["business_name"],
        "fields": [],
        "business_header_paths": [],
        "measure_order": [],
        "matrix_rows": [],
        "responsibilities": [],
        "measures": [],
    }


def load_baselines(project_root: Path, registry: dict[str, Any], aliases: dict, work_dir: Path, *, include_hidden: bool = False) -> dict[tuple[str, str], dict[str, Any]]:
    """加载指定基准；project_root 为基准根目录，registry 为冻结定位，aliases 为字段别名，work_dir 为转换目录，include_hidden 控制隐藏表。"""
    files = []
    requested_sheets = {}
    entries_by_key = {}
    out = {}
    for item in _baseline_entries(registry):
        key = (item["business_id"], item.get("variant_id", "default"))
        entries_by_key[key] = item
        path = (project_root / item["path"]).resolve()
        if not path.exists():
            out[key] = _unavailable_baseline(item, f"冻结基准不存在: {path}")
            continue
        try:
            digest = sha256_file(path)
            if item.get("sha256") and item["sha256"] != digest:
                out[key] = _unavailable_baseline(item, f"冻结基准哈希变化: {path}")
                continue
            file_format = "xls" if path.read_bytes()[:8] == bytes.fromhex("D0CF11E0A1B11AE1") else "xlsx"
        except OSError as error:
            out[key] = _unavailable_baseline(item, f"冻结基准无法读取: {path}: {error}")
            continue
        requested_sheets[key] = item.get("sheet")
        file = FileRecord(path, Path(item["path"]), digest, file_format, "BASELINE", ["frozen-registry"],
                          False, item["business_code"], item.get("variant_id", "default"), "matrix",
                          business_id=item["business_id"])
        file.preservation["business_name"] = item["business_name"]
        files.append(file)
    if files:
        parse_files(files, aliases, work_dir / "baselines", include_hidden=include_hidden)
    for file in files:
        key = (file.business_id or "", file.variant_id)
        item = entries_by_key[key]
        if file.parse_errors:
            out[key] = _unavailable_baseline(item, f"基准解析失败 {file.source}: {file.parse_errors}")
            continue
        requested = requested_sheets[key]
        selected_sheets = [s for s in file.sheets if s.sheet_type == "matrix" and (not requested or s.title == requested)]
        if requested and not selected_sheets:
            reason = f"冻结基准指定工作表未纳入解析（不存在、隐藏或未识别为业务表）: {file.source}#{requested}"
            out[key] = _unavailable_baseline(item, reason)
            continue
        all_records = [r for s in selected_sheets for r in s.records]
        records = [r for r in all_records if r.record_type == "matrix"]
        header_paths = [
            path
            for sheet in selected_sheets
            for path in _normalized_header_paths(sheet.business_header_paths)
        ]
        out[key] = {
            "available": True,
            "path": str(file.relative_path), "sha256": file.sha256,
            "business_id": file.business_id or "", "business_code": file.business_code or "",
            "business_name": file.preservation.get("business_name", ""),
            "fields": sorted({f for r in records for f in r.fields}),
            "business_header_paths": header_paths,
            "measure_order": [r.value("measure_id") for r in records if r.value("measure_id")],
            # 规则 20 需要同时按关键控制点、控制措施编号和责任主体筛选冻结模板行。
            "matrix_rows": [{
                "measure_id": r.value("measure_id"),
                "key_control_point": r.value("key_control_point"),
                "responsibility": r.value("responsibility"),
            } for r in records if r.value("measure_id")],
            # 保存来源单元格定位，输出核对列时直接复制原责任主体修订文字及样式。
            "responsibilities": [{"measure_id": r.value('measure_id') or str(r.fields['measure_id'].raw or ''),
                                  "source_path": str(getattr(file, '_converted_path', file.source)),
                                  "sheet": r.sheet, "cell": r.fields['responsibility'].coordinate}
                                 for r in all_records if 'measure_id' in r.fields and 'responsibility' in r.fields],
            "measures": [{"measure_id": r.value('measure_id'), "carrier": r.value('carrier'),
                          "control_measure": r.value('control_measure'), "sheet": r.sheet, "row": r.row}
                         for r in records if r.value('measure_id') and all(k in r.fields and r.fields[k].state != 'formula_no_cache' for k in ('carrier', 'control_measure'))],
        }
    return out
