"""OOXML 兼容加载器回归测试。"""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook

from risk_audit.readers.ooxml import load_compatible_workbook


def workbook_with_sort_state(path: Path, row_range: str) -> None:
    """构造指定整行排序范围的工作簿；参数为路径和待测 ref。"""
    book = Workbook()
    sheet = book.active
    sheet.append(['序号'])
    sheet.append([1])
    sheet.auto_filter.ref = 'A1:A2'
    sheet.auto_filter.add_sort_condition('A2:A2')
    book.save(path)

    with ZipFile(path) as archive:
        parts = {info.filename: (info, archive.read(info.filename)) for info in archive.infolist()}
    info, worksheet_xml = parts['xl/worksheets/sheet1.xml']
    replacement = f'<sortState ref="{row_range}">'.encode()
    invalid_xml = worksheet_xml.replace(b'<sortState ref="A1:A2">', replacement)
    assert invalid_xml != worksheet_xml
    parts['xl/worksheets/sheet1.xml'] = (info, invalid_xml)
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        for part_info, content in parts.values():
            archive.writestr(part_info, content)


def test_ooxml_et_extension_is_loaded_from_binary_content(tmp_path):
    """OOXML 内容使用 .et 后缀时仍可读取；参数 tmp_path 为测试目录。"""
    path = tmp_path / '工作簿.et'
    book = Workbook()
    book.active['A1'] = '可读'
    book.save(path)

    loaded = load_compatible_workbook(path)

    assert loaded.active['A1'].value == '可读'
    loaded.close()


@pytest.mark.parametrize('row_range', ['0:2', '2:1', '1:1048577'])
def test_invalid_row_only_sort_state_is_not_silently_repaired(tmp_path, row_range):
    """越界或逆序的整行范围必须保持报错；参数为目录和待测 ref。"""
    path = tmp_path / '非法排序范围.xlsx'
    workbook_with_sort_state(path, row_range)

    with pytest.raises(ValueError, match='Unable to read workbook'):
        load_compatible_workbook(path)
