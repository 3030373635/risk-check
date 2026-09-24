from pathlib import Path
from dataclasses import replace
from copy import deepcopy
import json
import pytest

from risk_audit.configuration.loader import load_pack
from risk_audit.checks.registry import CheckContext
from risk_audit.models import FieldValue, Record
from risk_audit.responsibility import responsibility_phrase_v5, value_mapping_v2
from risk_audit.issue_routing import route_issue
from risk_audit.applicability import interpret_record
from test_semantic_v140 import matrix

ROOT = Path(__file__).resolve().parents[1]
ATTRS = {'attributes':['准确性','完整性','真实性','一致性','合规性']}
MAPPING = {'mapping':{'经办':['主体责任'],'审核':['审核责任'],'审批':['审批责任']},'source_field':'role','text_field':'duty'}

def context(text, role='审核'):
    row=Record('position_duty','205H','06','default','x.xlsx','岗位职责',3,
               {'duty':FieldValue(text,text,'F3'),'role':FieldValue(role,role,'E3')},'r')
    return CheckContext([row],[],[row],{}, {}, {},[],[])

@pytest.mark.parametrize('text',[
    '对按照财务业务流程要求，做好报销审核、检查材料完备负有审核责任。',
    '对营销业务费列支情况、发票和业务作证资料齐全负有审核责任，确保完整性和合规性。',
    '发票提交人负有落实发票验真的主体责任。',
    '对落实发票验真负主体责任。',
    '对合同真实性、对台账准确性负审核责任。',
    '对核对合同与发票一致性负审核责任。',
    '对材料完整性负责。',
])
def test_equivalent_explicit_responsibility(text):
    assert not responsibility_phrase_v5(context(text),ATTRS)

@pytest.mark.parametrize('text',[
    '对合同准确性没有审核责任。','本岗位无需对合同准确性承担审核责任。',
    '对合同准确性不负审核责任。','负责审核合同。',
    '按照规定办理出差申请。','发票验真。',
    '审核发票和业务佐证资料齐全，确保完整性和合规性。',
])
def test_keyword_actions_and_denials_are_not_responsibility(text):
    assert responsibility_phrase_v5(context(text),ATTRS)

def test_only_missing_quality_is_reported_specifically():
    text='对赴异地挂职发起出差申请，按照规定选择成本中心负有主体责任。'
    issue=responsibility_phrase_v5(context(text),ATTRS)[0]
    assert issue['evidence']['issue_type']=='responsibility_quality_missing'
    from risk_audit.opinion_text import advice_v2
    assert '已写明承担责任' in advice_v2('broad_responsibility_pattern','violation',issue['evidence'])


@pytest.mark.parametrize('text',[
    '参与验收，核对实物性能、技术规范，对接收设备的质量和资料负责。',
    '规范自付单据与标准审核流程，对逐一核对自付单据人员、时间、地点，并审核费用标准是否符合公司规定负有审核责任。',
])
def test_technical_specification_and_action_are_not_quality_attributes(text):
    params={'attributes':[*ATTRS['attributes'],'规范性']}
    assert responsibility_phrase_v5(context(text),params)
    assert not responsibility_phrase_v5(context('对技术规范书的完整性负有审核责任。'),params)

@pytest.mark.parametrize('text,role,category',[
    ('发票提交人负有落实发票验真的主体责任。','经办',None),
    ('对费用准确性負审核责任。','审核','responsibility_role_unresolved'),
    ('对附件完整性负有审核在责任。','审核','responsibility_role_unresolved'),
    ('对营销退费管理，明确分级分类审批标准及系统建设负有审核责任。','审批','responsibility_role_conflict'),
    ('对出差申请单人员地点的真实性负责。','经办','responsibility_role_missing'),
    ('本岗位不承担审批责任，对合同真实性承担审核责任。','审核',None),
    ('参照“承担审批责任”的说明，对合同真实性承担审核责任。','审核',None),
    ('监督其他部门承担审批责任，对合同真实性承担审核责任。','审核','responsibility_role_unresolved'),
    ('对合同承担主体责任，对台账承担审核责任。','审核','responsibility_multiple_types'),
    ('对合同真实性没有审核责任。','审核','responsibility_role_missing'),
    ('对发票真实性具有复核责任，对购物清单负有审核责任。','审核',None),
])
def test_role_assertion_is_not_a_substring(text,role,category):
    result=value_mapping_v2(context(text,role),MAPPING)
    assert (result[0]['evidence']['issue_type'] if result else None)==category

def test_unparsed_grammar_is_internal_not_approved_or_published():
    issue=responsibility_phrase_v5(context('对合同的准确性承担未知句式审核责任。'),ATTRS)[0]
    policy={'version':1,'technical_issues':'internal','unfinished_is_pass':False}
    result=route_issue(issue,{'check_id':'broad_responsibility_pattern'},policy)
    assert result['kind']=='limitation' and result['evidence']['publication_channel']=='internal'
    malformed=responsibility_phrase_v5(context('对附件完整性负有审核在责任。'),ATTRS)[0]
    assert route_issue(malformed,{'check_id':'broad_responsibility_pattern'},policy)['kind']=='limitation'

@pytest.fixture
def quality_pack(): return load_pack(ROOT/'rulepacks/drafts/rule-quality-v1.6.0')

def test_nonstandard_header_requires_real_answers(tmp_path,quality_pack):
    f,s=matrix(tmp_path,quality_pack,['凤凰分公司矩阵测试情况'],[['适用'],['已修改控制措施'],[None]],hidden=['E'])
    # One anchor is not enough to repurpose an unfamiliar header.
    assert 'applicability' not in s.columns
    f,s=matrix(tmp_path,quality_pack,['凤凰分公司矩阵测试情况'],[['适用'],['不适用'],['已修改控制措施'],[None]],hidden=['E'])
    assert s.columns['applicability']==5
    assert [r.value('applicability') for r in s.records]==['适用','不适用','已修改控制措施','']
    choice=next(x for x in f.preservation['column_selections'] if x['field']=='applicability')
    assert choice['candidates'][0]['discovery']=='nonstandard_header_explicit_answers'
    _,s=matrix(tmp_path,quality_pack,['凤凰分公司测试情况'],[['是'],['否']])
    assert 'applicability' not in s.columns
    _,s=matrix(tmp_path,quality_pack,['验收结果'],[['适用'],['不适用']])
    assert 'applicability' not in s.columns

def test_fallback_keeps_foreign_unit_and_ambiguity_guards(tmp_path,quality_pack):
    _,s=matrix(tmp_path,quality_pack,['古丈分公司测试情况'],[['适用'],['不适用']],body='各级单位-财务部-主任')
    assert 'applicability' not in s.columns
    _,s=matrix(tmp_path,quality_pack,['凤凰分公司测试情况','凤凰分公司矩阵测试情况'],[['适用','不适用'],['不适用','适用']])
    assert 'applicability' not in s.columns

def test_levels_require_roster_and_selected_header(quality_pack):
    row=context('x').records[0]
    row.fields['applicability']=FieldValue('地市及区县','地市及区县','H3')
    resources={**quality_pack,'_applicability_entities':{'205H':'国网湖南省电力有限公司凤凰县供电分公司'},
               '_applicability_headers':{('x.xlsx','岗位职责'):'适用主体'}}
    assert interpret_record(row,resources).decision=='applicable'
    assert interpret_record(row,quality_pack).decision is None
    resources['_applicability_headers']={}
    assert interpret_record(row,resources).decision is None
    row.fields['applicability'].current='此条不适用县、支公司'
    assert interpret_record(row,resources).decision=='not_applicable'
    resources['_applicability_entities']['205H']='湘西自治州德能电力建设有限公司凤凰分公司'
    assert interpret_record(row,resources).decision is None


def test_multiline_headers_and_unit_control_columns(tmp_path,quality_pack):
    from openpyxl import Workbook
    from risk_audit.models import FileRecord
    from risk_audit.readers.excel import parse_workbook
    from risk_audit.util import sha256_file
    w=Workbook();s=w.active;s.title='风控矩阵'
    s.append(['风控矩阵']);s.append(['控制措施编号','湘西公司-控制措施','泸溪公司-控制措施','控制措施分类',None,None])
    s.append([None,None,None,'不相容岗位','分级授权','泸溪公司矩阵测试情况'])
    for col in ['A','B','C']:s.merge_cells(f'{col}2:{col}3')
    s.append(['M1','上级措施','本单位措施',None,None,'适用'])
    s.append(['M2','上级措施2','本单位措施2',None,None,'不适用'])
    path=tmp_path/'multi.xlsx';w.save(path)
    f=FileRecord(path,Path('multi.xlsx'),sha256_file(path),'xlsx','X',['标准全称:国网湖南省电力有限公司泸溪县供电分公司'],False,'05','default','matrix')
    f._parser_policy=quality_pack['parser_policy'];f._semantic_lexicon=quality_pack['semantic_lexicon']
    f.sheets=parse_workbook(f,path,quality_pack['field_aliases']);sheet=f.sheets[0]
    assert sheet.columns['control_measure']==3 and sheet.columns['applicability']==6
    assert len(sheet.records)==2 and sheet.records[0].value('control_measure')=='本单位措施'
    log=next(x for x in f.preservation['column_selections'] if x['field']=='applicability')
    assert log['candidates'][0]['coordinate']=='F3'

def test_pattern_grouping_retains_all_findings_and_does_not_infer_other_checks():
    from risk_audit.responsibility_patterns import build_patterns
    from risk_audit.models import FileRecord,ParsedSheet
    rows=[context('对合同真实性负责。','经办').records[0]]
    rows.append(replace(rows[0],entity_code='X',row=4,record_id='s'))
    f=FileRecord(Path('x.xlsx'),Path('x.xlsx'),'hash','xlsx','205H',[],False,'06','default','three_lists',
                 [ParsedSheet('岗位职责','position_duty',[2],{}, {},8,3,rows)])
    findings=[{'finding_key':str(r.row),'check_id':'explicit_role_responsibility','file_path':'x.xlsx',
               'sheet':'岗位职责','row':r.row,'severity':'violation','message':'缺主体责任',
               'evidence':{'issue_type':'responsibility_role_missing','expected':'主体责任'}} for r in rows]
    scopes={code:{'businesses':[{'business_code':'06','variant_id':'default'}]} for code in ['205H','X']}
    result=build_patterns([f],findings,[],scopes)
    assert len(result)==1 and result[0]['affected_rows']==2 and result[0]['entity_count']==2
    assert result[0]['unit_opinions']==2 and result[0]['automatic_approval'] is False
    assert {k for x in result[0]['locations'] for k in x['finding_keys']}=={'3','4'}


@pytest.mark.parametrize('sample',json.loads((ROOT/'tests/fixtures/responsibility_real_v160.json').read_text()),ids=lambda x:x['id'])
def test_labelled_real_workbook_samples(sample):
    ctx=context(sample['text'],sample['role'])
    for key,fn,params in [('R10',responsibility_phrase_v5,ATTRS),('R11',value_mapping_v2,MAPPING)]:
        expected=sample['expected_'+key]
        if expected is None: continue
        issues=fn(ctx,params)
        assert (issues[0]['evidence']['issue_type'] if issues else 'pass')==expected
