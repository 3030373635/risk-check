"""按单位、业务、规则编号和意见内容生成审核统计表。"""
from __future__ import annotations

from collections import defaultdict
import logging
import os
from pathlib import Path
import re
import tempfile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from risk_audit.audit_statistics_html import render_audit_statistics_html
from risk_audit.models import FileRecord
from risk_audit.util import natural_key
from risk_audit.writer import _output_path


RULE_LABEL_PATTERN = re.compile(r'【第(\d+)条】')
PREPROCESSING_LABEL = '【预处理】'
COUNT_SUFFIX_PATTERN = re.compile(r'\s+\d+\s*条[.。]?\s*$')


def _safe_excel_text(value: str) -> str:
    """将外部文本安全写入 Excel；value 为目录名或审核意见文本。"""
    return f"'{value}" if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value


def _unit_and_business(
    file: FileRecord,
    package_name: str,
    root_subject_name: str,
) -> tuple[str, str] | None:
    """按主体、业务、文件层级计算展示位置。

    Args:
        file: 当前业务文件。
        package_name: 审核包根目录名。
        root_subject_name: 输入根本身是业务目录时的外层主体目录名。
    """

    directories = file.relative_path.parts[:-1]
    if not directories:
        return root_subject_name or '未识别主题', package_name
    # 文件直接上级是业务目录，业务目录上级才是主体路径。
    business_name = directories[-1]
    unit_directories = directories[:-1]
    unit_name = '/'.join(part for part in (package_name, *unit_directories) if part)
    return unit_name, business_name


def _business_group_key(file: FileRecord, business_name: str) -> tuple[str, str]:
    """生成稳定业务分组键。

    Args:
        file: 当前业务文件。
        business_name: 当前文件解析出的业务显示名称。
    """

    # 有业务目录时以目录为唯一分组身份，文件上的识别结果不得拆分它。
    identity = f'directory:{file.relative_path.parent.as_posix()}'
    return identity, file.variant_id or 'default'


def _register_grouped_business(
    grouped: dict,
    file: FileRecord | None,
    package_name: str,
    root_subject_name: str,
) -> tuple[str, dict] | None:
    """登记文件所属业务并返回主体及业务统计容器。

    Args:
        grouped: 按主体和稳定业务身份归集的统计容器。
        file: 当前业务文件；无法定位文件时为 None。
        package_name: 审核包名称。
        root_subject_name: 输入根本身是业务目录时的外层主体目录名。
    """

    location = _unit_and_business(file, package_name, root_subject_name) if file is not None else None
    if location is None:
        return None
    unit_name, business_name = location
    business_key = _business_group_key(file, business_name)
    business = grouped[unit_name].setdefault(
        business_key,
        {'name': business_name, 'items': {}},
    )
    return unit_name, business


def _rule_numbers(message: str) -> tuple[int, ...]:
    """提取并规范规则序号组合；message 为已写入审核意见单元格的一条意见。"""
    numbers = [int(value) for value in RULE_LABEL_PATTERN.findall(message)]
    return tuple(sorted(set(numbers)))


def _opinion_group(message: str) -> tuple[str, tuple[int, ...]] | None:
    """返回统计分组。

    Args:
        message: 一条程序生成的审核意见。
    """

    rules = _rule_numbers(message)
    if rules:
        return "rule", rules
    if message.startswith(PREPROCESSING_LABEL):
        return "preprocessing", ()
    return None


def _normalized_message(
    message: str,
    group: tuple[str, tuple[int, ...]],
) -> str:
    """统一审核意见展示文本。

    Args:
        message: 原始程序意见。
        group: 意见类别及排序后的规则编号组合。
    """

    category, rules = group
    body = RULE_LABEL_PATTERN.sub('', message).strip()
    body = body.removeprefix(PREPROCESSING_LABEL).strip()
    body = COUNT_SUFFIX_PATTERN.sub('', body).strip()
    prefix = (
        PREPROCESSING_LABEL
        if category == "preprocessing"
        else ''.join(f'【第{number}条】' for number in rules)
    )
    return f'{prefix}{body}'


def _program_opinions(program_text: str) -> list[str]:
    """拆分单元格内的程序意见；program_text 为写回器记录的完整程序文本。"""
    text = program_text.strip()
    if not text:
        return []
    starts = [
        match.start()
        for match in re.finditer(r'(?m)^(?:【第\d+条】|【预处理】)', text)
    ]
    if not starts:
        return [text]
    return [text[start:starts[index + 1] if index + 1 < len(starts) else len(text)].strip()
            for index, start in enumerate(starts)]


def _collect_statistics(
    files: list[FileRecord],
    ownership: dict[str, list[dict]],
    package_name: str,
    business_failures: list[dict] | None = None,
    root_subject_name: str = '',
) -> dict:
    """归集审核统计。

    Args:
        files: 本次审核扫描到的文件。
        ownership: 实际写回审核意见的单元格归属。
        package_name: 审核包名称。
        business_failures: 已跳过业务的结构化失败信息。
        root_subject_name: 输入根本身是业务目录时的外层主体目录名。
    """
    logger = logging.getLogger(__name__)
    grouped = defaultdict(dict)
    file_by_output = {
        str(_output_path(file)): file
        # 晚表头或非标准文件名可能在扫描时仍是 unclassified，实际写回记录仍应纳入。
        for file in files if file.material_type != 'explanation'
    }
    for file in files:
        if file.material_type == 'explanation' or not (file.business_id or file.business_code):
            continue
        # 先登记目录业务；未匹配标准规则的目录即使没有程序意见也须明确展示。
        _register_grouped_business(grouped, file, package_name, root_subject_name)
    for output_path, states in ownership.items():
        file = file_by_output.get(output_path)
        registration = _register_grouped_business(grouped, file, package_name, root_subject_name)
        if registration is None:
            logger.warning('审核统计表未能定位意见所属业务目录：文件=%s', output_path)
            continue
        _, business = registration
        for state in states:
            for message in _program_opinions(state.get('program_text', '')):
                group = _opinion_group(message)
                if group is None:
                    logger.warning('审核统计表未能识别意见规则序号：文件=%s，意见=%s',
                                   output_path, message)
                    continue
                normalized_message = _normalized_message(message, group)
                # 规则编号相同但整改要求不同的意见必须分别统计，不能仅按规则编号合并。
                statistics_key = (group[0], group[1], normalized_message)
                item = business['items'].setdefault(
                    statistics_key,
                    {'message': normalized_message, 'occurrences': set()},
                )
                occurrence = (output_path, state.get('sheet', ''), state.get('cell', ''), message)
                # ownership 是最终写回结果，相同单元格内的完全相同意见只计一次。
                item['occurrences'].add(occurrence)
    file_by_relative_path = {str(file.relative_path): file for file in files}
    for index, failure in enumerate(business_failures or []):
        failure_files = failure.get('files') or []
        failed_file = next(
            (file_by_relative_path[path] for path in failure_files if path in file_by_relative_path),
            None,
        )
        registration = _register_grouped_business(grouped, failed_file, package_name, root_subject_name)
        if registration is None:
            logger.warning('审核统计表未能定位跳过业务：%s', failure)
            continue
        _, business = registration
        # 序号保证同一业务出现多条失败记录时不会互相覆盖。
        business['items'][('failure', index)] = {'failure': failure}
    return grouped


def _business_records(items: dict) -> list[str]:
    """生成一个业务的展示记录；items 为该业务归集后的意见和失败信息。"""
    records: list[str] = []
    for group, item in sorted(items.items()):
        if group[0] == 'failure':
            failure = item['failure']
            records.extend([
                '【审核状态】已跳过，未完成审核',
                f'【失败阶段】{failure["stage"]}',
                f'【涉及文件】{"；".join(failure.get("files") or ["未定位具体文件"])}',
                f'【异常类型】{failure["error_type"]}',
                f'【跳过原因】{failure["reason"]}',
                f'【详细日志】{failure["log_reference"]}',
            ])
            continue
        records.append(f'{item["message"]} {len(item["occurrences"])}条。')
    return records or ['无']


def _statistics_source_data(grouped: dict) -> dict[str, list[dict[str, object]]]:
    """转换为两种报告共用的数据结构；grouped 为按主体和业务归集的数据。"""
    units: list[dict[str, object]] = []
    for unit_name in sorted(grouped, key=natural_key):
        businesses = [
            {
                'name': business['name'],
                'records': _business_records(business['items']),
            }
            for business in sorted(
                grouped[unit_name].values(),
                key=lambda item: natural_key(item['name']),
            )
        ]
        units.append({'path': unit_name, 'businesses': businesses})
    return {'units': units}


def write_audit_statistics(
    path: Path,
    files: list[FileRecord],
    ownership: dict[str, list[dict]],
    package_name: str,
    business_failures: list[dict] | None = None,
    root_subject_name: str = '',
) -> None:
    """写入审核统计表。

    Args:
        path: 审核统计表输出路径。
        files: 本次审核扫描到的文件。
        ownership: 实际写回审核意见的单元格归属。
        package_name: 审核包名称。
        business_failures: 已跳过业务的结构化失败信息。
        root_subject_name: 输入根本身是业务目录时的外层主体目录名。
    """
    grouped = _collect_statistics(
        files,
        ownership,
        package_name,
        business_failures,
        root_subject_name,
    )
    source_data = _statistics_source_data(grouped)
    book = Workbook()
    sheet = book.active
    sheet.title = '审核统计表'
    sheet.append(['单位', '备注'])

    for unit in source_data['units']:
        for business in unit['businesses']:
            # 每个业务独占一行，避免主体全部内容超过 Excel 单元格字符上限。
            business_section = f'{business["name"]}：\n' + '\n'.join(business['records'])
            sheet.append([
                _safe_excel_text(unit['path']),
                _safe_excel_text(business_section),
            ])

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


def write_audit_statistics_html(
    path: Path,
    files: list[FileRecord],
    ownership: dict[str, list[dict]],
    package_name: str,
    business_failures: list[dict] | None = None,
    root_subject_name: str = '',
) -> None:
    """写入审核统计 HTML。

    Args:
        path: HTML 报告输出路径。
        files: 本次审核扫描到的文件。
        ownership: 实际写回审核意见的单元格归属。
        package_name: 审核包名称。
        business_failures: 已跳过业务的结构化失败信息。
        root_subject_name: 输入根本身是业务目录时的外层主体目录名。
    """
    grouped = _collect_statistics(
        files,
        ownership,
        package_name,
        business_failures,
        root_subject_name,
    )
    document = render_audit_statistics_html(_statistics_source_data(grouped))
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix='.audit-statistics-', suffix='.html', dir=path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        # HTML 同样先写入同目录临时文件，避免异常中断留下半份报告。
        temporary_path.write_text(document, encoding='utf-8')
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
