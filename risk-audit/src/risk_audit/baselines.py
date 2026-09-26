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


def load_baselines(project_root: Path, registry: dict[str, Any], aliases: dict, work_dir: Path, *, include_hidden: bool = False) -> dict[tuple[str, str], dict[str, Any]]:
    """加载指定基准；project_root 为基准根目录，registry 为冻结定位，aliases 为字段别名，work_dir 为转换目录，include_hidden 控制隐藏表。"""
    files = []
    expected_hashes = {}; requested_sheets = {}
    for item in _baseline_entries(registry):
        path = (project_root / item["path"]).resolve()
        if not path.exists(): raise FileNotFoundError(f"冻结基准不存在: {path}")
        digest = sha256_file(path)
        if item.get("sha256") and item["sha256"] != digest: raise ValueError(f"冻结基准哈希变化: {path}")
        key = (item["business_id"], item.get("variant_id", "default"))
        expected_hashes[key] = digest
        requested_sheets[key] = item.get("sheet")
        fmt = "xls" if path.read_bytes()[:8] == bytes.fromhex("D0CF11E0A1B11AE1") else "xlsx"
        file = FileRecord(path, Path(item["path"]), digest, fmt, "BASELINE", ["frozen-registry"],
                          False, item["business_code"], item.get("variant_id", "default"), "matrix",
                          business_id=item["business_id"])
        file.preservation["business_name"] = item["business_name"]
        files.append(file)
    parse_files(files, aliases, work_dir / "baselines", include_hidden=include_hidden)
    out = {}
    for file in files:
        if file.parse_errors: raise ValueError(f"基准解析失败 {file.source}: {file.parse_errors}")
        key = (file.business_id or "", file.variant_id)
        requested = requested_sheets[key]
        selected_sheets = [s for s in file.sheets if s.sheet_type == "matrix" and (not requested or s.title == requested)]
        if requested and not selected_sheets: raise ValueError(f"冻结基准指定工作表未纳入解析（不存在、隐藏或未识别为业务表）: {file.source}#{requested}")
        all_records = [r for s in selected_sheets for r in s.records]
        records = [r for r in all_records if r.record_type == "matrix"]
        header_paths = [
            path
            for sheet in selected_sheets
            for path in _normalized_header_paths(sheet.business_header_paths)
        ]
        out[key] = {
            "path": str(file.relative_path), "sha256": file.sha256,
            "business_id": file.business_id or "", "business_code": file.business_code or "",
            "business_name": file.preservation.get("business_name", ""),
            "fields": sorted({f for r in records for f in r.fields}),
            "business_header_paths": header_paths,
            "measure_order": [r.value("measure_id") for r in records if r.value("measure_id")],
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
