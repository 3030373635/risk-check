"""生成与审核规则文档解耦的单位业务审核统计表。"""
from __future__ import annotations

from collections import defaultdict
import logging
import os
from pathlib import Path
import re
import tempfile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from risk_audit.models import FileRecord
from risk_audit.submission_scope import BUSINESS_DIRECTORY_KEYWORDS
from risk_audit.util import natural_key, norm_text
from risk_audit.writer import _output_path


RULE_LABEL_PATTERN = re.compile(r'【第(\d+)条】')
COUNT_SUFFIX_PATTERN = re.compile(r'\s+\d+\s*条[.。]?\s*$')


def _safe_excel_text(value: str) -> str:
    """将外部文本安全写入 Excel；value 为目录名或审核意见文本。"""
    return f"'{value}" if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value


def _business_directory_index(directories: tuple[str, ...], business_code: str | None) -> int | None:
    """识别业务目录位置；directories 为文件上级目录，business_code 为已识别业务编码。"""
    if not business_code:
        return None
    expected_code = business_code.zfill(2)
    strong_candidates = []
    weak_candidates = []
    unit_markers = ('公司', '本部', '中心', '研究院', '设计院', '供电所', '分部', '供电局', '电厂', '电站', '服务站', '项目部')
    for index, directory in enumerate(directories):
        normalized = norm_text(directory)
        has_business_name = any(keyword in normalized for keyword in BUSINESS_DIRECTORY_KEYWORDS)
        number = re.match(r'^\D*(\d{1,2})(?:\D|$)', directory)
        number_matches = bool(number and number.group(1).zfill(2) == expected_code)
        if not number_matches:
            continue
        # 同一编号可同时出现在业务和单位目录中，有明确业务名的目录优先级最高。
        if has_business_name:
            strong_candidates.append(index)
        elif not any(marker in directory for marker in unit_markers):
            weak_candidates.append(index)
    if strong_candidates:
        return strong_candidates[-1]
    return weak_candidates[-1] if weak_candidates else None


def _unit_and_business(file: FileRecord, package_name: str) -> tuple[str, str] | None:
    """计算单位路径和业务目录；file 为业务文件，package_name 为审核包根目录名。"""
    directories = file.relative_path.parts[:-1]
    configured_name = file.preservation.get('business_name', '')
    configured_label = (
        f'{file.business_code} {configured_name}'
        if file.business_code and configured_name else ''
    )
    if not directories:
        # 重号业务必须使用配置名称展示，不能再次归并为同一个“业务01”。
        business_name = configured_label or (f'业务{file.business_code}' if file.business_code else '根目录')
        return package_name, business_name
    business_index = _business_directory_index(directories, file.business_code)
    if business_index is None:
        # 无法通过编号或业务名识别时，使用约定的“直接父目录为业务”口径。
        business_index = len(directories) - 1
    business_name = configured_label or directories[business_index]
    # 真实包同时存在“单位/业务”与“业务/单位”，单位路径须排除已识别的业务目录。
    unit_directories = tuple(directory for index, directory in enumerate(directories) if index != business_index)
    unit_name = '/'.join(part for part in (package_name, *unit_directories) if part)
    return unit_name, business_name


def _rule_numbers(message: str) -> tuple[int, ...]:
    """提取并规范规则序号组合；message 为已写入审核意见单元格的一条意见。"""
    numbers = [int(value) for value in RULE_LABEL_PATTERN.findall(message)]
    return tuple(sorted(set(numbers)))


def _normalized_message(message: str, rules: tuple[int, ...]) -> str:
    """统一审核意见展示文本；message 为原意见，rules 为排序后的规则组合。"""
    body = RULE_LABEL_PATTERN.sub('', message).strip()
    body = COUNT_SUFFIX_PATTERN.sub('', body).strip()
    prefix = ''.join(f'【第{number}条】' for number in rules)
    return f'{prefix}{body}'


def _program_opinions(program_text: str) -> list[str]:
    """拆分单元格内的程序意见；program_text 为写回器记录的完整程序文本。"""
    text = program_text.strip()
    if not text:
        return []
    starts = [match.start() for match in re.finditer(r'(?m)^【第\d+条】', text)]
    if not starts:
        return [text]
    return [text[start:starts[index + 1] if index + 1 < len(starts) else len(text)].strip()
            for index, start in enumerate(starts)]


def _collect_statistics(files: list[FileRecord], ownership: dict[str, list[dict]], package_name: str) -> dict:
    """归集审核统计；files 为文件，ownership 为实际写回单元格，package_name 为审核包名。"""
    logger = logging.getLogger(__name__)
    grouped = defaultdict(lambda: defaultdict(dict))
    file_by_output = {
        str(_output_path(file)): file
        # 晚表头或非标准文件名可能在扫描时仍是 unclassified，实际写回记录仍应纳入。
        for file in files if file.material_type != 'explanation'
    }
    for file in files:
        if file.material_type == 'explanation' or not file.business_code:
            continue
        unit_name, business_name = _unit_and_business(file, package_name)
        # 先登记已识别业务，即使没有程序意见也须在统计表中明确展示。
        grouped[unit_name].setdefault(business_name, {})
    for output_path, states in ownership.items():
        file = file_by_output.get(output_path)
        location = _unit_and_business(file, package_name) if file is not None else None
        if location is None:
            logger.warning('审核统计表未能定位意见所属业务目录：文件=%s', output_path)
            continue
        unit_name, business_name = location
        for state in states:
            for message in _program_opinions(state.get('program_text', '')):
                rules = _rule_numbers(message)
                if not rules:
                    logger.warning('审核统计表未能识别意见规则序号：文件=%s，意见=%s',
                                   output_path, message)
                    continue
                item = grouped[unit_name][business_name].setdefault(
                    rules,
                    {'message': _normalized_message(message, rules), 'occurrences': set()},
                )
                occurrence = (output_path, state.get('sheet', ''), state.get('cell', ''), message)
                # ownership 是最终写回结果，相同单元格内的完全相同意见只计一次。
                item['occurrences'].add(occurrence)
    return grouped


def write_audit_statistics(path: Path, files: list[FileRecord], ownership: dict[str, list[dict]],
                           package_name: str) -> None:
    """写入审核统计表；path 为目标，files 为材料，ownership 为写回结果，package_name 为包名。"""
    grouped = _collect_statistics(files, ownership, package_name)
    book = Workbook()
    sheet = book.active
    sheet.title = '审核统计表'
    sheet.append(['单位', '备注'])

    for unit_name in sorted(grouped, key=natural_key):
        business_sections = []
        for business_name in sorted(grouped[unit_name], key=natural_key):
            opinion_lines = []
            for rules, item in sorted(grouped[unit_name][business_name].items()):
                opinion_lines.append(f'{item["message"]} {len(item["occurrences"])}条。')
            business_sections.append(f'{business_name}：\n' + ('\n'.join(opinion_lines) or '无'))
        sheet.append([_safe_excel_text(unit_name), _safe_excel_text('\n\n'.join(business_sections))])

    header_fill = PatternFill('solid', fgColor='305496')
    header_font = Font(bold=True, color='FFFFFF')
    header_border = Border(
        left=Side(style='thin', color='D9E2F3'),
        right=Side(style='thin', color='D9E2F3'),
        top=Side(style='thin', color='D9E2F3'),
        bottom=Side(style='thin', color='D9E2F3'),
    )
    for cell in sheet[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.border = header_border
        cell.alignment = Alignment(horizontal='center', vertical='center')
    for row in sheet.iter_rows(min_row=2):
        row[0].alignment = Alignment(vertical='top')
        row[1].alignment = Alignment(wrap_text=True, vertical='top')
        line_count = max(2, str(row[1].value or '').count('\n') + 1)
        sheet.row_dimensions[row[0].row].height = min(409, line_count * 20)
    sheet.column_dimensions['A'].width = 36
    sheet.column_dimensions['B'].width = 100
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix='.audit-statistics-', suffix='.xlsx', dir=path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        # 先写同目录临时文件，成功后原子替换，避免中途失败留下损坏工作簿。
        book.save(temporary_path)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
