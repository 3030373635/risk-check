"""验证从报送材料自动确定范围，防止默认单位范围造成误报。"""
from pathlib import Path

from openpyxl import Workbook

from risk_audit.models import Entity, FileRecord


def uploaded(root, code, business, variant='default', material_type='matrix'):
    """创建扫描结果；root 为输入根目录，code 为主体，business/variant 为业务及变体，material_type 为材料类型。"""
    path = root / code / f'{business}风控矩阵.xlsx'
    return FileRecord(path, path.relative_to(root), '', 'xlsx', code, [], False, business, variant, material_type)


def attachment(root, code, name, rows):
    """保存构造附件；root 为输入目录，code 为主体目录，name 为文件名，rows 为表格行。"""
    path = root / code / name
    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    for row in rows:
        book.active.append(row)
    book.save(path)
    return path


def resolve(root, files):
    """执行真实范围解析；root 为输入目录，files 为扫描记录。"""
    from risk_audit.submission_scope import resolve_submission_scopes
    entities = {'A': Entity('A', '测试单位甲公司'), 'B': Entity('B', '测试单位乙公司')}
    return resolve_submission_scopes(root, files, entities, {}, 'first-batch-2026-09')


def test_uploaded_businesses_define_each_company_without_attachment(tmp_path):
    """tmp_path 为隔离目录；无附件时不得把甲公司范围套给乙公司。"""
    scopes, report = resolve(tmp_path, [uploaded(tmp_path, 'A', '07'), uploaded(tmp_path, 'B', '10')])
    assert [(x['business_code'], x['variant_id']) for x in scopes['A']['businesses']] == [('07', 'default')]
    assert [x['business_code'] for x in scopes['B']['businesses']] == ['10']
    assert report['alerts'] == []


def test_auxiliary_attachments_do_not_change_uploaded_business_scope(tmp_path):
    """tmp_path 为隔离目录；辅助附件声明不得增加、排除或提示实际上传业务。"""
    attachment(tmp_path, 'A', '附件1风控矩阵应用清单.xlsx', [
        ['单位', '设备（资产）管理', '物资（服务）采购与实施', '数字化与研发投入', '财务管理'],
        ['测试单位甲公司', '不适用', '适用', '部分适用', '适用'],
    ])
    attachment(tmp_path, 'A', '附件2风控矩阵适用性匹配统计表.xlsx', [
        ['风控矩阵名称', '适用控制措施数量', '备注'],
        ['员工报账', 8, None],
    ])
    scopes, report = resolve(tmp_path, [uploaded(tmp_path, 'A', '06'), uploaded(tmp_path, 'A', '07')])
    assert [x['business_code'] for x in scopes['A']['businesses']] == ['06', '07']
    assert report['mode'] == 'uploaded_materials'
    assert report['sources'] == []
    assert report['declarations'] == []
    assert report['alerts'] == []


def test_unreadable_auxiliary_attachment_is_ignored(tmp_path):
    """tmp_path 为隔离目录；不可读辅助附件不得产生提示或阻断内容审核。"""
    path = tmp_path / 'A' / '附件1应用清单.xlsx'
    path.parent.mkdir()
    path.write_bytes(b'bad workbook')
    scopes, report = resolve(tmp_path, [uploaded(tmp_path, 'A', '07')])
    assert scopes['A']['businesses'][0]['business_code'] == '07'
    assert report['sources'] == []
    assert report['alerts'] == []


def test_scope_excludes_explanations_and_keeps_excel_candidates_for_content_detection(tmp_path):
    """tmp_path 为隔离目录；说明材料不建立业务范围，待解析 Excel 仍须进入内容识别。"""
    from risk_audit import submission_scope
    entities = {'A': Entity('A', '测试单位甲公司')}
    files = [
        uploaded(tmp_path, 'A', '06', material_type='matrix'),
        uploaded(tmp_path, 'A', '07', material_type='three_lists'),
        uploaded(tmp_path, 'A', '08', material_type='unclassified'),
        uploaded(tmp_path, 'A', '09', material_type='explanation'),
    ]
    scopes, report = submission_scope.resolve_submission_scopes(tmp_path, files, entities, {}, 'batch')
    assert [item['business_code'] for item in scopes['A']['businesses']] == ['06', '07', '08']
    assert report['alerts'] == []


def test_explanation_only_group_is_retained_without_business_scope(tmp_path):
    """tmp_path 为隔离目录；仅有说明材料的主体仍须保留，以便复制原件并记录主体结果。"""
    path = tmp_path / 'A' / '主体说明.docx'
    file = FileRecord(path, path.relative_to(tmp_path), '', 'docx', 'A', [], False,
                      None, 'default', 'explanation')
    scopes, report = resolve(tmp_path, [file])
    assert scopes['A']['businesses'] == []
    assert report['alerts'] == []


def test_audit_without_scope_file_uses_uploaded_range_and_saves_evidence(tmp_path):
    """tmp_path 为隔离目录；完整试跑不得继续采用默认06～10范围。"""
    import json
    from run_audit import configure_soffice
    from risk_audit.runner import audit
    from test_audit_v180 import create_sample
    configure_soffice()
    root = Path(__file__).resolve().parents[2]
    input_root = tmp_path / 'input'
    create_sample(input_root)
    attachment(input_root, '.', '附件1应用清单.xlsx', [
        ['单位', '设备（资产）管理'], ['国网湖南省电力有限公司信息通信分公司', '不适用']])
    result = audit(input_root, tmp_path / 'output', root / 'risk-audit/rulepacks/releases/1.8.0',
                   root / 'reference-data/会计主体清单20260907.xlsx', root, tmp_path / 'runs')
    run_dir = Path(result['run_dir'])
    snapshot = json.loads((run_dir / 'snapshot.json').read_text())
    assert len(snapshot['submission_scopes']) == 1
    scope = next(iter(snapshot['submission_scopes'].values()))
    assert [x['business_code'] for x in scope['businesses']] == ['06']
    findings = json.loads((run_dir / 'findings.json').read_text())
    assert not any(x['evidence'].get('missing_material') and x['business_code'] in {'07', '08', '09', '10'} for x in findings)
    assert Path(result['submission_scope']['report']).is_file()
    warnings = json.loads((run_dir / 'warnings.json').read_text())
    attachment_warning_types = {
        'applicable_matrix_missing', 'uploaded_not_applicable', 'applicability_declaration_conflict',
        'scope_attachment_unrecognized', 'scope_attachment_unreadable', 'scope_attachment_unit_ambiguous',
        'scope_directory_entity_conflict',
    }
    assert not any(x['type'] in attachment_warning_types for x in warnings)
    assert (Path(result['audit_metadata_dir']) / '本次审核范围.md').is_file()


def test_unknown_company_names_keep_directory_ranges_separate(tmp_path):
    """tmp_path 为隔离目录；没有主体代码时也不能跨单位合并上传材料。"""
    first = uploaded(tmp_path, '甲单位', '10')
    second = uploaded(tmp_path, '乙单位', '07')
    first.entity_code = second.entity_code = None
    scopes, report = resolve(tmp_path, [first, second])
    assert sorted([x['business_code'] for x in scope['businesses']] for scope in scopes.values()) == [['07'], ['10']]
    assert report['alerts']  # 目录隔离不意味着会计主体已确认。


def test_single_unit_application_list_does_not_identify_uploaded_materials(tmp_path):
    """tmp_path 为隔离目录；辅助附件不得覆盖实际材料的主体识别结果。"""
    attachment(tmp_path, '甲单位', '附件1应用清单.xlsx', [
        ['单位', '员工报账'], ['测试单位甲公司', '适用']])
    file = uploaded(tmp_path, '甲单位', '10')
    file.entity_code = None
    file.parse_errors = ['无法确定会计主体']
    scopes, report = resolve(tmp_path, [file])
    assert set(scopes) == {'@目录:甲单位'}
    assert file.entity_code is None
    assert {item['type'] for item in report['alerts']} == {'scope_entity_unconfirmed'}


def test_business_folder_with_company_name_belongs_to_parent_unit(tmp_path):
    """tmp_path 为隔离目录；带负责人和公司名的业务文件夹不能被误识别为独立单位。"""
    file = uploaded(tmp_path, '甲单位/07物资（服务）采购与实施-计量分公司（负责人：某人）', '07')
    file.entity_code = None
    scopes, _ = resolve(tmp_path, [file])
    assert len(scopes) == 1


def test_business_directories_with_half_width_parentheses_belong_to_parent_unit(tmp_path):
    """tmp_path 为隔离目录；半角括号及空格变体的业务目录不得被误判为独立单位。"""
    first = uploaded(tmp_path, '甲单位/06设备(资产)管理-计量分公司', '06')
    second = uploaded(tmp_path, '甲单位/07 物资 ( 服务 ) 采购与实施-计量分公司', '07')
    first.entity_code = second.entity_code = None
    scopes, _ = resolve(tmp_path, [first, second])
    assert len(scopes) == 1
    businesses = next(iter(scopes.values()))['businesses']
    assert [item['business_code'] for item in businesses] == ['06', '07']


def test_child_unit_directories_keep_uploaded_materials_separate(tmp_path):
    """tmp_path 为隔离目录；上层目录相同的子单位仍按实际材料目录分组。"""
    first = uploaded(tmp_path, '株洲高新公司/设计本部', '10')
    second = uploaded(tmp_path, '株洲高新公司/高新本部', '07')
    first.entity_code = second.entity_code = None
    scopes, _ = resolve(tmp_path, [first, second])
    assert len(scopes) == 2


def test_conflicting_unit_lists_are_ignored_for_entity_binding(tmp_path):
    """tmp_path 为隔离目录；互相冲突的辅助清单不得参与材料主体绑定。"""
    attachment(tmp_path, '混合单位', '附件1应用清单-甲.xlsx', [
        ['单位', '员工报账'], ['测试单位甲公司', '适用']])
    attachment(tmp_path, '混合单位', '附件1应用清单-乙.xlsx', [
        ['单位', '员工报账'], ['测试单位乙公司', '适用']])
    file = uploaded(tmp_path, '混合单位', '10')
    file.entity_code = None
    _, report = resolve(tmp_path, [file])
    assert file.entity_code is None
    assert {item['type'] for item in report['alerts']} == {'scope_entity_unconfirmed'}
