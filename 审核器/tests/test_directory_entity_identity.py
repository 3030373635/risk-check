"""验证主体只按单位目录识别，文件名称不影响归属及清单外标记。"""
import pytest
from openpyxl import Workbook

from risk_audit.entity_groups import prepare_entity_groups
from risk_audit.inventory_v180 import scan_package_v180
from risk_audit.models import Entity, FileRecord


@pytest.mark.parametrize('filename', [
    '07风控矩阵-随意简称.xlsx',
    '07风控矩阵-其他服务中心.xlsx',
    '07风控矩阵-另一个已登记有限公司.xlsx',
])
def test_registered_directory_ignores_filename(tmp_path, filename):
    """tmp_path 为测试根目录，filename 为任意报送名称；目录全称决定归属。"""
    name = '湖南朗晟电力产业发展有限公司计量分公司'
    directory = tmp_path / name / '07物资（服务）采购与实施-随意简称'
    directory.mkdir(parents=True)
    Workbook().save(directory / filename)
    entities = {'209Z': Entity('209Z', name), 'OTHER': Entity('OTHER', '另一个已登记有限公司')}
    files = scan_package_v180(tmp_path, entities, {})
    groups = prepare_entity_groups(files, tmp_path, entities)
    assert list(groups) == ['209Z']
    assert files[0].entity_code == '209Z'
    assert not files[0].entity_conflict


def test_unlisted_directory_remains_one_marked_subject(tmp_path):
    """tmp_path 为测试根目录；同目录的不同文件名不能拆主体或借用已登记代码。"""
    name = '清单外样例有限公司'
    directory = tmp_path / name / '07物资（服务）采购与实施'
    directory.mkdir(parents=True)
    for filename in ['07风控矩阵-另一个已登记有限公司.xlsx', '07三清单-其他服务中心.xlsx']:
        Workbook().save(directory / filename)
    entities = {'OTHER': Entity('OTHER', '另一个已登记有限公司')}
    files = scan_package_v180(tmp_path, entities, {})
    groups = prepare_entity_groups(files, tmp_path, entities)
    assert len(groups) == 1
    identity = next(iter(groups.values()))
    assert identity['entity_name'] == name
    assert identity['entity_code'] == ''
    assert identity['entity_registry_status'] == 'not_listed'
    assert identity['entity_registry_message'] == '主体不在会计主体清单中'
    assert len({file.entity_code for file in files}) == 1
    assert all(not file.entity_conflict for file in files)


def test_same_unlisted_directory_name_merges_across_all_business_directories(tmp_path):
    """tmp_path 为测试根目录；任意业务目录下的同名单位目录必须合并为一个主体。"""
    name = '清单外样例有限公司'
    files = []
    for business_directory, business_code, material_type in [
        ('历史业务目录', '06', 'matrix'),
        ('未来新增业务目录', '42', 'three_lists'),
    ]:
        path = tmp_path / business_directory / name / f'{business_code}{material_type}.xlsx'
        files.append(FileRecord(path, path.relative_to(tmp_path), 'hash', 'xlsx', None, [], False,
                                business_code, 'default', material_type))

    groups = prepare_entity_groups(files, tmp_path, {})

    assert list(groups) == [f'@名称:{name}']
    assert {file.entity_code for file in files} == {f'@名称:{name}'}


def test_unlisted_child_directory_does_not_inherit_parent(tmp_path):
    """tmp_path 为测试根目录；清单外子单位目录不能沿父目录寻找正式代码。"""
    parent = tmp_path / '已登记母公司有限公司'
    directory = parent / '清单外子公司有限公司' / '07物资（服务）采购与实施'
    directory.mkdir(parents=True)
    Workbook().save(directory / '07风控矩阵.xlsx')
    entities = {'PARENT': Entity('PARENT', parent.name)}
    files = scan_package_v180(tmp_path, entities, {})
    groups = prepare_entity_groups(files, tmp_path, entities)
    identity = next(iter(groups.values()))
    assert identity['entity_code'] == ''
    assert identity['entity_name'] == '清单外子公司有限公司'
    assert identity['entity_registry_status'] == 'not_listed'
