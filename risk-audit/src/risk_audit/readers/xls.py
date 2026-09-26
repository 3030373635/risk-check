from __future__ import annotations

import os
import subprocess
import tempfile
import zipfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import xlrd
from openpyxl import load_workbook
from lxml import etree
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter

# Web Worker 启动后会注入包内路径；默认值只用于明确的未配置错误提示。
SOFFICE = Path("soffice")
PROFILE_PATH: Path | None = None
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKGREL = "http://schemas.openxmlformats.org/package/2006/relationships"
PROFILE_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
    <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
    <prop oor:name="DisableActiveContent" oor:op="fuse"><value>true</value></prop>
  </item>
  <item oor:path="/org.openoffice.Office.Calc/Content/Update">
    <prop oor:name="Link" oor:op="fuse"><value>2</value></prop>
  </item>
</oor:items>
"""


def prepare_conversion_profile(profile: Path) -> None:
    """写入安全转换配置；profile 为本次 LibreOffice 用户目录。"""
    user = profile / "user"
    user.mkdir(parents=True, exist_ok=True)
    (user / "registrymodifications.xcu").write_text(PROFILE_SETTINGS, encoding="utf-8")


def configure_conversion_runtime(
    soffice_path: Path,
    profile_path: Path | None,
) -> None:
    """配置本进程的 LibreOffice；参数为可执行文件和可选独立 profile。"""
    resolved_soffice = soffice_path.resolve()
    if not resolved_soffice.is_file():
        raise ValueError(f"LibreOffice executable does not exist: {resolved_soffice}")
    global SOFFICE, PROFILE_PATH
    SOFFICE = resolved_soffice
    PROFILE_PATH = profile_path.resolve() if profile_path is not None else None


def _same_value(a: Any, b: Any, *, source_type: int | None = None) -> bool:
    """比较转换前后的单元格值。

    参数 a 为 BIFF 源值，b 为转换后的 OOXML 值，source_type 为可选的
    xlrd 单元格类型；错误单元格按 Excel 错误语义而非 BIFF 数字码比较。
    """
    if source_type == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(a, '#VALUE!') == b
    if a in (None, "") and b in (None, ""): return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)): return abs(float(a) - float(b)) < 1e-8
    return str(a) == str(b)


def _restore_merge_ranges(
    root: etree._Element,
    source_ranges: list[tuple[int, int, int, int]],
) -> None:
    """按 BIFF 源范围重建工作表的合并单元格节点。

    参数 root 为 OOXML 工作表根节点，source_ranges 为 xlrd 提供的
    零基、右侧开区间合并范围；函数直接更新 root，无返回值。
    """
    merge_cells = root.find(f"{{{MAIN}}}mergeCells")
    if merge_cells is not None:
        insert_index = root.index(merge_cells)
        root.remove(merge_cells)
    else:
        sheet_data = root.find(f"{{{MAIN}}}sheetData")
        insert_index = len(root) if sheet_data is None else root.index(sheet_data) + 1
    if not source_ranges:
        return

    merge_cells = etree.Element(f"{{{MAIN}}}mergeCells", count=str(len(source_ranges)))
    for row_low, row_high, column_low, column_high in source_ranges:
        # xlrd 使用零基、右侧开区间，OOXML 使用一基、闭区间坐标。
        start = f"{get_column_letter(column_low + 1)}{row_low + 1}"
        end = f"{get_column_letter(column_high)}{row_high}"
        etree.SubElement(merge_cells, f"{{{MAIN}}}mergeCell", ref=f"{start}:{end}")
    root.insert(insert_index, merge_cells)


def compare_conversion(source: Path, converted: Path) -> dict[str, Any]:
    old = xlrd.open_workbook(source, formatting_info=True)
    new = load_workbook(converted, data_only=True, read_only=False)
    diffs = []; merge_diffs = []; hidden_diffs = []; column_hidden_diffs = []
    for i, osheet in enumerate(old.sheets()):
        if i >= len(new.worksheets): diffs.append({"sheet": osheet.name, "error": "missing converted sheet"}); continue
        nsheet = new.worksheets[i]
        expected_state = {0: "visible", 1: "hidden", 2: "veryHidden"}.get(osheet.visibility, f"unknown:{osheet.visibility}")
        if nsheet.sheet_state != expected_state:
            hidden_diffs.append({"sheet": osheet.name, "source": expected_state, "converted": nsheet.sheet_state})
        for col in sorted(set(range(osheet.ncols))|set(osheet.colinfo_map)):
            info=osheet.colinfo_map.get(col)
            expected_hidden=bool(info.hidden) if info else False
            actual_hidden=any(d.hidden and (d.min or 0)<=col+1<=(d.max or 0) for d in nsheet.column_dimensions.values())
            if expected_hidden!=actual_hidden:
                column_hidden_diffs.append({'sheet':osheet.name,'column':col+1,'source':expected_hidden,'converted':bool(actual_hidden)})
        old_merges = {(rlo + 1, rhi, clo + 1, chi) for rlo, rhi, clo, chi in osheet.merged_cells}
        new_merges = {(m.min_row, m.max_row, m.min_col, m.max_col) for m in nsheet.merged_cells.ranges}
        if old_merges != new_merges: merge_diffs.append(osheet.name)
        non_anchor_merged = set()
        for rlo, rhi, clo, chi in osheet.merged_cells:
            non_anchor_merged.update((r, c) for r in range(rlo, rhi) for c in range(clo, chi) if (r, c) != (rlo, clo))
        for r in range(osheet.nrows):
            for c in range(osheet.ncols):
                if (r, c) in non_anchor_merged: continue
                ov = osheet.cell_value(r, c); nv = nsheet.cell(r + 1, c + 1).value
                # BIFF 错误缓存使用数字码，必须结合单元格类型与 OOXML 错误文本比较。
                if not _same_value(ov, nv, source_type=osheet.cell_type(r, c)):
                    diffs.append({"sheet": osheet.name, "row": r + 1, "column": c + 1, "source": ov, "converted": nv})
                    if len(diffs) >= 100: break
            if len(diffs) >= 100: break
    merged_nonanchors = sum(1 for s in old.sheets() for rlo, rhi, clo, chi in s.merged_cells for r in range(rlo, rhi) for c in range(clo, chi) if (r, c) != (rlo, clo) and s.cell_value(r, c) not in (None, ""))
    rich_cells = sum(len(s.rich_text_runlist_map) for s in old.sheets())
    new.close();old.release_resources()
    return {"source_format": "xls", "output_format": "xlsx", "value_differences": diffs, "merge_differences": merge_diffs, "hidden_state_differences": hidden_diffs, "column_visibility_differences":column_hidden_diffs, "rich_text_cells_detected": rich_cells, "merged_nonanchor_source_values_archived": merged_nonanchors, "formula_expressions_verified": False, "embedded_objects_verified": False, "passed": not diffs and not merge_diffs and not hidden_diffs and not column_hidden_diffs, "note": "已核对BIFF缓存值、合并范围、工作表及列的隐藏状态；富文本仅记录并恢复，未作独立等价核验。公式表达式由LibreOffice转换，嵌入对象未作全保真验证。"}


def restore_biff_formula_caches(source: Path, converted: Path) -> int:
    """LibreOffice may recalculate links during conversion; restore BIFF cached results."""
    old = xlrd.open_workbook(source, formatting_info=True)
    with zipfile.ZipFile(converted, "r") as zin:
        wb = etree.fromstring(zin.read("xl/workbook.xml"))
        rels = etree.fromstring(zin.read("xl/_rels/workbook.xml.rels"))
        targets = {x.get("Id"): x.get("Target") for x in rels.findall(f"{{{PKGREL}}}Relationship")}
        sheet_paths = []
        for s in wb.findall(f".//{{{MAIN}}}sheet"):
            target = targets[s.get(f"{{{REL}}}id")]
            sheet_paths.append("xl/" + target.lstrip("/").removeprefix("xl/"))
        replacements = {}; restored = 0
        # Restore hidden versus veryHidden exactly.
        for index, sheet in enumerate(wb.findall(f".//{{{MAIN}}}sheet")):
            if index < old.nsheets:
                visibility = old.sheet_by_index(index).visibility
                if visibility == 2: sheet.set("state", "veryHidden")
                elif visibility == 1: sheet.set("state", "hidden")
                else: sheet.attrib.pop("state", None)
        replacements["xl/workbook.xml"] = etree.tostring(wb, xml_declaration=True, encoding="UTF-8", standalone=True)
        palette = getattr(old, "colour_map", {})
        def rich_runs(osheet, r0, c0):
            text = str(osheet.cell_value(r0, c0)); runs = list(osheet.rich_text_runlist_map.get((r0, c0), []))
            # Some WPS BIFF files repeat a run at offset zero after later runs.
            # Walk ordered distinct boundaries so source characters appear once.
            if any(offset < 0 or offset > len(text) for offset, _ in runs):
                raise ValueError(f"BIFF富文本边界超出原文: {osheet.name}!R{r0+1}C{c0+1}")
            runs = sorted(dict(runs).items())
            if not runs: return None
            if runs[0][0] != 0:
                default_font = old.xf_list[osheet.cell_xf_index(r0, c0)].font_index
                runs.insert(0, (0, default_font))
            container = etree.Element(f"{{{MAIN}}}is")
            for i, (start, font_index) in enumerate(runs):
                end = runs[i + 1][0] if i + 1 < len(runs) else len(text)
                segment = text[start:end]
                if not segment: continue
                font = old.font_list[font_index]
                run = etree.SubElement(container, f"{{{MAIN}}}r"); rpr = etree.SubElement(run, f"{{{MAIN}}}rPr")
                if font.bold: etree.SubElement(rpr, f"{{{MAIN}}}b")
                if font.italic: etree.SubElement(rpr, f"{{{MAIN}}}i")
                if font.struck_out: etree.SubElement(rpr, f"{{{MAIN}}}strike")
                if font.underlined: etree.SubElement(rpr, f"{{{MAIN}}}u", val="single")
                etree.SubElement(rpr, f"{{{MAIN}}}rFont", val=font.name or "宋体")
                etree.SubElement(rpr, f"{{{MAIN}}}sz", val=str((font.height or 220) / 20))
                rgb = palette.get(font.colour_index)
                if rgb: etree.SubElement(rpr, f"{{{MAIN}}}color", rgb="FF%02X%02X%02X" % rgb)
                t = etree.SubElement(run, f"{{{MAIN}}}t")
                if segment.startswith(" ") or segment.endswith(" ") or "\n" in segment: t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                t.text = segment
            return container
        for index, sheet_path in enumerate(sheet_paths):
            if index >= old.nsheets: continue
            osheet = old.sheet_by_index(index)
            root = etree.fromstring(zin.read(sheet_path))
            # LibreOffice 会把“跨选区居中”改成合并，必须以 BIFF 源范围为准回写。
            _restore_merge_ranges(root, list(osheet.merged_cells))
            sheet_data = root.find(f"{{{MAIN}}}sheetData")
            rows_by_num = {int(x.get("r")): x for x in sheet_data.findall(f"{{{MAIN}}}row")}
            for cell in root.findall(f".//{{{MAIN}}}c"):
                if cell.find(f"{{{MAIN}}}f") is None: continue
                row, col = coordinate_to_tuple(cell.get("r")); r0, c0 = row - 1, col - 1
                if r0 >= osheet.nrows or c0 >= osheet.ncols: continue
                value = osheet.cell_value(r0, c0); ctype = osheet.cell_type(r0, c0)
                vnode = cell.find(f"{{{MAIN}}}v")
                if vnode is None: vnode = etree.SubElement(cell, f"{{{MAIN}}}v")
                if ctype == xlrd.XL_CELL_TEXT:
                    cell.set("t", "str"); vnode.text = str(value)
                elif ctype == xlrd.XL_CELL_BOOLEAN:
                    cell.set("t", "b"); vnode.text = "1" if value else "0"
                elif ctype == xlrd.XL_CELL_ERROR:
                    cell.set("t", "e"); vnode.text = xlrd.error_text_from_code.get(value, "#VALUE!")
                elif ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    cell.attrib.pop("t", None); vnode.text = None
                else:
                    cell.attrib.pop("t", None); vnode.text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
                restored += 1
            # Restore all BIFF rich runs, including explicit strike sections.
            for (r0, c0), runlist in osheet.rich_text_runlist_map.items():
                row_num, col_num = r0 + 1, c0 + 1
                row_node = rows_by_num.get(row_num)
                if row_node is None:
                    row_node = etree.SubElement(sheet_data, f"{{{MAIN}}}row", r=str(row_num)); rows_by_num[row_num] = row_node
                coord = f"{get_column_letter(col_num)}{row_num}"
                cell = next((x for x in row_node.findall(f"{{{MAIN}}}c") if x.get("r") == coord), None)
                if cell is None: cell = etree.SubElement(row_node, f"{{{MAIN}}}c", r=coord)
                if cell.find(f"{{{MAIN}}}f") is not None: continue
                for child in list(cell): cell.remove(child)
                cell.set("t", "inlineStr"); cell.append(rich_runs(osheet, r0, c0))
            # Preserve BIFF values stored in non-anchor merged cells as raw OOXML nodes.
            for rlo, rhi, clo, chi in osheet.merged_cells:
                for r0 in range(rlo, rhi):
                    for c0 in range(clo, chi):
                        if (r0, c0) == (rlo, clo): continue
                        value = osheet.cell_value(r0, c0)
                        if value in (None, ""): continue
                        row_num, col_num = r0 + 1, c0 + 1
                        row_node = rows_by_num.get(row_num)
                        if row_node is None: row_node = etree.SubElement(sheet_data, f"{{{MAIN}}}row", r=str(row_num)); rows_by_num[row_num] = row_node
                        coord = f"{get_column_letter(col_num)}{row_num}"
                        cell = next((x for x in row_node.findall(f"{{{MAIN}}}c") if x.get("r") == coord), None)
                        if cell is None: cell = etree.SubElement(row_node, f"{{{MAIN}}}c", r=coord)
                        for child in list(cell): cell.remove(child)
                        cell.set("t", "inlineStr"); isel = etree.SubElement(cell, f"{{{MAIN}}}is"); t = etree.SubElement(isel, f"{{{MAIN}}}t"); t.text = str(value)
            replacements[sheet_path] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        temp = converted.with_suffix(".cache-restored.xlsx")
        with zipfile.ZipFile(temp, "w") as zout:
            for info in zin.infolist(): zout.writestr(info, replacements.get(info.filename, zin.read(info.filename)))
    temp.replace(converted)
    return restored


def convert_xls(source: Path, destination_dir: Path) -> tuple[Path, dict[str, Any]]:
    """将真实 XLS 转换为 XLSX；参数为源文件和目标目录。"""
    if not SOFFICE.exists(): raise RuntimeError(f"bundled soffice missing: {SOFFICE}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    profile_context = (nullcontext(PROFILE_PATH) if PROFILE_PATH is not None
                       else tempfile.TemporaryDirectory(prefix="risk-audit-lo-"))
    with profile_context as profile_value:
        profile = Path(profile_value).resolve()
        prepare_conversion_profile(profile)
        # 参数列表直接传给子进程，禁止 shell 插值中文路径或用户材料名。
        cmd = [str(SOFFICE), f"-env:UserInstallation={profile.as_uri()}", "--headless", "--convert-to", "xlsx", "--outdir", str(destination_dir), str(source)]
        env = dict(os.environ, SAL_DISABLE_OPENCL="1", SAL_DISABLE_MACROS="1")
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=120)
        converted = destination_dir / f"{source.stem}.xlsx"
        if proc.returncode or not converted.exists(): raise RuntimeError(f"xls conversion failed: {proc.stdout} {proc.stderr}")
    restored = restore_biff_formula_caches(source, converted)
    report = compare_conversion(source, converted)
    report["formula_caches_restored"] = restored
    report["conversion_profile"] = {"isolated": True, "macros_disabled": True, "active_content_disabled": True, "external_links_update": "never"}
    if not report["passed"]: raise RuntimeError(f"xls conversion preservation check failed: {report}")
    return converted, report
