from pathlib import Path
from openpyxl import Workbook
from risk_audit.models import FileRecord
from risk_audit.readers.excel import parse_workbook
from risk_audit.checks.schema_v2 import schema_contains_v2
from risk_audit.checks.registry import CheckContext
from risk_audit.util import sha256_file


def make(tmp_path,pack,name,headers):
    w=Workbook();s=w.active;s.title='风控矩阵';s.append(['风控矩阵']);s.append(headers)
    s.append(['M1']*len(headers));p=tmp_path/name;w.save(p)
    f=FileRecord(p,Path(name),sha256_file(p),'xlsx','E',[],False,'06','default','matrix')
    f.sheets=parse_workbook(f,p,pack['field_aliases']);return f


def test_reordering_and_confirmed_alias_does_not_mean_deleted_column(tmp_path,pack):
    headers=['控制措施编号','控制措施','责任主体','控制载体','业务自定义字段']
    a=make(tmp_path,pack,'a.xlsx',headers)
    b=make(tmp_path,pack,'b.xlsx',['业务自定义字段','控制措施','控制措施编号','内控载体','责任主体'])
    baseline={('06','default'):{'fields':list(a.sheets[0].columns),'business_header_paths':a.sheets[0].business_header_paths}}
    ctx=CheckContext([], [b],[],{},baseline,{'field_aliases':pack['field_aliases']},['E'],[])
    params={'sheet_types':['matrix'],'compare_baseline':True,'required_fields':[]}
    assert schema_contains_v2(ctx,params)==[]
    c=make(tmp_path,pack,'c.xlsx',headers[:-1]);ctx.files=[c]
    issues=schema_contains_v2(ctx,params)
    assert len(issues)==1 and issues[0]['evidence']['missing_fields']==['业务自定义字段']


def test_input_override_is_bound_to_source_hash_and_cannot_read_manual_opinions(tmp_path,pack):
    import pytest
    f=make(tmp_path,pack,'d.xlsx',['控制措施编号','控制措施','责任主体','缺少表头','审核意见'])
    item={'file_path':'d.xlsx','sha256':f.sha256,'sheet':'风控矩阵','field':'carrier','column':4,'reason':'审核人员确认'}
    p=parse_workbook(f,f.source,pack['field_aliases'],input_overrides=[item])[0]
    assert p.columns['carrier']==4
    with pytest.raises(ValueError):parse_workbook(f,f.source,pack['field_aliases'],input_overrides=[{**item,'sha256':'a'*64}])
    with pytest.raises(ValueError):parse_workbook(f,f.source,pack['field_aliases'],input_overrides=[{**item,'column':5}])


def test_automatic_column_mapping_takes_precedence_over_input_override(tmp_path, pack):
    """tmp_path 为临时目录，pack 为规则包；明确表头必须覆盖同文件上的旧列确认。"""
    file = make(
        tmp_path,
        pack,
        'automatic-column.xlsx',
        ['控制措施编号', '控制载体', '控制措施', '责任主体'],
    )
    override = {
        'file_path': 'automatic-column.xlsx',
        'sha256': file.sha256,
        'sheet': '风控矩阵',
        'field': 'carrier',
        'column': 4,
        'reason': '历史人工确认',
    }

    parsed = parse_workbook(
        file,
        file.source,
        pack['field_aliases'],
        input_overrides=[override],
    )[0]

    assert parsed.columns['carrier'] == 2
    assert not any(
        item['reason'] == 'confirmed_input_override' and item['field'] == 'carrier'
        for item in file.preservation['column_selections']
    )


def test_stale_input_override_is_ignored_when_field_is_automatically_mapped(tmp_path, pack):
    """tmp_path 为临时目录，pack 为规则包；文件内容变化后仍应使用当前明确表头。"""
    file = make(
        tmp_path,
        pack,
        'changed-column.xlsx',
        ['控制措施编号', '控制措施', '责任主体', '控制载体'],
    )
    override = {
        'file_path': 'changed-column.xlsx',
        'sha256': 'a' * 64,
        'sheet': '风控矩阵',
        'field': 'carrier',
        'column': 2,
        'reason': '历史人工确认',
    }

    parsed = parse_workbook(
        file,
        file.source,
        pack['field_aliases'],
        input_overrides=[override],
    )[0]

    assert parsed.columns['carrier'] == 4


def test_missing_field_opinion_uses_canonical_label_instead_of_entity_alias(tmp_path, pack):
    """tmp_path 为临时目录，pack 为规则包；缺少适用性时必须输出通用字段名。"""
    file = make(tmp_path, pack, 'missing-applicability.xlsx', [
        '控制措施编号', '控制措施', '控制系统', '控制载体', '责任主体',
    ])
    context = CheckContext([], [file], [], {}, {}, {'field_aliases': pack['field_aliases']}, ['E'], [])
    params = {
        'sheet_types': ['matrix'],
        'required_fields': ['applicability'],
        'field_labels': {'applicability': '是否适用/是否适用及原因'},
    }

    issues = schema_contains_v2(context, params)

    assert issues[0]['evidence']['unavailable_reason'] == (
        '未能读取必需字段：是否适用/是否适用及原因。请先核实字段对应或是否缺列。'
    )
    assert '信通公司' not in issues[0]['evidence']['unavailable_reason']
