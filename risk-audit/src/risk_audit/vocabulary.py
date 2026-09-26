"""Build a traceable recognition vocabulary from unreviewed business fields.

Occurrence is evidence of spelling, never evidence of correctness or equivalence.
This module does not consume human opinions, approve aliases or amend rule packs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import csv
import re
import zipfile
from lxml import etree

from risk_audit.configuration.loader import load_pack
from risk_audit.configuration.validator import validate_pack
from risk_audit.checks.registry import build_registry
from risk_audit.entities import load_entities
from risk_audit.inventory import scan_package
from risk_audit.readers.excel import parse_files, AUDIT_HEADER
from risk_audit.util import norm_text, sha256_file, write_json


def carrier_candidates(text: str) -> list[str]:
    """Split explicit numbered lists; retain qualifiers and compound names."""
    items = []; part = []; depth = 0
    for char in text:
        if char in "（(【[《": depth += 1
        elif char in "）)】]》" and depth: depth -= 1
        if char in ";；\n、，," and depth == 0:
            items.append("".join(part)); part = []
        else: part.append(char)
    items.append("".join(part))
    result = []
    for item in items:
        value = re.sub(r"^\s*(?:\d+[.．、)]|[（(]\d+[)）])\s*", "", item).strip(" \t\r。；;，,")
        if value.startswith("《") and value.endswith("》"):
            value = value[1:-1]
        if value: result.append(value)
    return result


def admissible_term(value: str, field: str) -> bool:
    n = norm_text(value)
    if not 2 <= len(n) <= (100 if field == "carrier" else 55): return False
    if AUDIT_HEADER.search(value) or n.startswith("="): return False
    if n in {"无", "不涉及", "不适用", "暂无", "待定", "部门", "岗位名称", "控制载体"}: return False
    if re.search(r"[。；;\n]|(?:负有?|承担).{0,8}责任|请(?:明确|填写|修改)", value): return False
    return True


def select_mining_sheets(file) -> tuple[set[str], dict[str, str]]:
    """Use visible business sheets only; hidden content cannot seed new terms."""
    workbook = Path(getattr(file, "_converted_path", file.source))
    states = {}
    if workbook.exists() and zipfile.is_zipfile(workbook):
        with zipfile.ZipFile(workbook) as z:
            root = etree.fromstring(z.read("xl/workbook.xml"))
            states = {x.get("name"): x.get("state", "visible") for x in root.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet")}
    reasons = {}
    markers = ("典设", "过渡", "核对过程", "原集体企业", "（原）", "(原)")
    for sheet in file.sheets:
        if states.get(sheet.title, 'visible') != 'visible': reasons[sheet.title] = '隐藏工作表默认忽略，不参与词表生成'
        elif any(x in sheet.title for x in markers): reasons[sheet.title] = "明确的典设、过渡或历史表"
    return {s.title for s in file.sheets if s.title not in reasons}, reasons


def build_vocabulary(inputs: list[str | Path], output: str | Path, rulepack: str | Path,
                     entity_file: str | Path, holdout_keys: list[str] | None = None) -> dict[str, Any]:
    output = Path(output).resolve()
    roots = [Path(x).resolve() for x in inputs]
    if any(output == root or root in output.parents or output in root.parents for root in roots):
        raise ValueError("词表输出目录不得与输入目录重叠")
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True)
    pack = load_pack(rulepack)
    validate_pack(pack, build_registry())
    entities, _ = load_entities(entity_file)
    files = []; seen_paths = set()
    for root in roots:
        for file in scan_package(root, entities, pack["entity_aliases"]):
            if file.source in seen_paths or file.material_type not in {"matrix", "three_lists"}: continue
            seen_paths.add(file.source)
            files.append(file)
    files.sort(key=lambda f: str(f.source))
    manifest = [{"path": str(f.source), "sha256": f.sha256, "entity_code": f.entity_code,
                 "business_code": f.business_code, "variant_id": f.variant_id,
                 "holdout": any(key in str(f.source) for key in (holdout_keys or []))} for f in files]
    write_json(output / "source_manifest.json", manifest)
    # Each file gets its own directory, avoiding collisions during old XLS conversion.
    catalog: dict[tuple[str, str], dict] = {}
    file_reports = []; records = []; stats = Counter(); entity_counts = Counter()
    for index, (file, source_info) in enumerate(zip(files, manifest)):
        parse_files([file], pack["field_aliases"], output / "parsing" / f"{index:04d}", entity_aliases=pack['entity_aliases'],
                    parser_policy=pack.get('parser_policy'),semantic_lexicon=pack.get('semantic_lexicon'))
        selected_sheets, sheet_exclusions = select_mining_sheets(file)
        held = source_info["holdout"]
        eligible = not file.parse_errors and bool(file.entity_code and file.business_code) and file.variant_id != "unknown"
        stats["files"] += 1; stats["holdout_files" if held else "mining_files"] += 1
        if file.sheets: stats["parsed_files"] += 1
        if file.parse_errors: stats["files_with_errors"] += 1
        entity_counts[file.entity_code or "UNRESOLVED"] += 1
        file_reports.append({**source_info, "errors": file.parse_errors, "eligible_for_vocabulary": eligible and not held,
                             "sheets": [{"name": s.title, "type": s.sheet_type, "records": len(s.records), "mining_exclusion": sheet_exclusions.get(s.title)} for s in file.sheets]})
        for sheet in file.sheets:
            for record in sheet.records:
                if record.record_type not in {"matrix", "position_duty"}: continue
                # The verification fixture deliberately excludes person names and audit columns.
                kept = {name: {"current": value.current, "coordinate": value.coordinate, "state": value.state}
                        for name, value in record.fields.items()
                        if name in {"measure_id", "carrier", "duty", "department", "position", "responsibility", "applicability"}}
                records.append({"record_type": record.record_type, "entity_code": record.entity_code,
                                "business_code": record.business_code, "variant_id": record.variant_id,
                                "file_path": str(file.source), "sheet": record.sheet, "row": record.row,
                                "record_id": record.record_id, "holdout": held, "fields": kept,
                                "source_eligible": eligible and sheet.title in selected_sheets})
                fields = ["carrier"] if record.record_type == "matrix" else ["department", "position"]
                for field in fields:
                    fv = record.fields.get(field)
                    if fv is None or fv.state == "formula_no_cache": continue
                    terms = carrier_candidates(fv.current) if field == "carrier" else [fv.current]
                    for term in terms:
                        if not admissible_term(term, field): continue
                        key = (field, norm_text(term))
                        entry = catalog.setdefault(key, {"field": field, "term": key[1], "spellings": set(),
                            "occurrences": 0, "mining_occurrences": 0, "holdout_occurrences": 0, "eligible_occurrences": 0,
                            "entities": set(), "businesses": set(), "sources": [], "source_keys": set()})
                        entry["spellings"].add(term); entry["occurrences"] += 1
                        if eligible and not held and sheet.title in selected_sheets: entry["eligible_occurrences"] += 1
                        entry["holdout_occurrences" if held else "mining_occurrences"] += 1
                        entry["entities"].add(file.entity_code or "UNRESOLVED")
                        entry["businesses"].add(file.business_code or "UNRESOLVED")
                        source_key = (str(file.source), sheet.title, fv.coordinate)
                        if source_key not in entry["source_keys"]:
                            entry["source_keys"].add(source_key)
                            entry["sources"].append({"file": str(file.source), "sha256": file.sha256,
                                "sheet": sheet.title, "cell": fv.coordinate, "entity_code": file.entity_code,
                                "business_code": file.business_code, "variant_id": file.variant_id,
                                "holdout": held, "eligible": eligible and sheet.title in selected_sheets,
                                "text": term, "source_cell_text": fv.current})
        if sha256_file(file.source) != file.sha256: raise RuntimeError(f"源文件发生变化：{file.source}")
    exported = []; recognized = defaultdict(list)
    for key, entry in sorted(catalog.items()):
        entry.pop("source_keys")
        entry["spellings"] = sorted(entry["spellings"])
        entry["entities"] = sorted(entry["entities"]); entry["businesses"] = sorted(entry["businesses"])
        mining_sources = [x for x in entry["sources"] if not x["holdout"] and x["eligible"]]
        entry["status"] = "recognition_only" if mining_sources else "candidate_only"
        entry["approved_alias"] = False; entry["approved_correctness"] = False
        if mining_sources: recognized[entry["field"]].append(entry["term"])
        exported.append(entry)
    vocabulary = {"schema_version": "1.0", "terms": {field: sorted(recognized[field]) for field in ("carrier", "department", "position")},
                  "metadata": {"purpose": "完整名称识别；不是合格词白名单，不扩大当前措施允许载体集合，不建立主体或名称等价关系",
                    "source_manifest_sha256": sha256_file(output / "source_manifest.json"),
                    "holdout_keys": holdout_keys or [], "field_aliases_sha256": pack["_resource_hashes"]["field_aliases.json"], "source_roots": [str(x) for x in roots]}}
    write_json(output / "terminology.json", vocabulary)
    write_json(output / "catalog.json", exported)
    write_json(output / "file_reports.json", file_reports)
    write_json(output / "records.json", records)
    with (output / "词条来源与待确认项.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream); writer.writerow(["类型", "词条", "识别用途", "出现次数", "主体", "业务", "来源文件", "工作表", "单元格", "原始写法", "是否已确认等价或合格"])
        for entry in exported:
            sample = next((x for x in entry["sources"] if not x["holdout"]), entry["sources"][0])
            writer.writerow([entry["field"], entry["term"], entry["status"], entry["occurrences"], "、".join(entry["entities"]),
                             "、".join(entry["businesses"]), sample["file"], sample["sheet"], sample["cell"], sample["text"], "否"])
    summary = {**dict(stats), "entities": dict(sorted(entity_counts.items())), "records": len(records),
               "eligible_mining_records": sum(x["source_eligible"] and not x["holdout"] for x in records),
               "eligible_holdout_records": sum(x["source_eligible"] and x["holdout"] for x in records),
               "excluded_sheets": sum(bool(s["mining_exclusion"]) for f in file_reports for s in f["sheets"]),
               "candidate_counts": dict(Counter(x["field"] for x in exported)),
               "recognition_counts": {key: len(value) for key, value in vocabulary["terms"].items()}, "output": str(output)}
    write_json(output / "summary.json", summary)
    return summary
