from pathlib import Path
from copy import deepcopy
import pytest
from openpyxl import Workbook

from risk_audit.checks.carriers_v4 import parse_carrier_references_v4 as parse, carrier_aliases_v4
from risk_audit.checks.quality_checks import set_subset_v4, ordered_records_v2
from risk_audit.checks.registry import CheckContext
from risk_audit.configuration.loader import load_pack
from risk_audit.engine import run_engine
from risk_audit.issue_routing import build_internal_diagnostics, route_issue
from risk_audit.models import FieldValue, Record, FileRecord, ParsedSheet
from risk_audit.readers.excel import parse_workbook
from risk_audit.util import sha256_file

POLICY={'version':1,'technical_issues':'internal','unfinished_is_pass':False}
PARAMS={'source_field':'carrier','target_field':'carrier','target_record_type':'matrix','fallback_explicit_field':'duty'}

def record(kind='position_duty',row=3,**values):
    values={'measure_id':'M1',**values}
    return Record(kind,'205H','06','default','a.xlsx',kind,row,
                  {k:FieldValue(v,v,f'A{row}') for k,v in values.items()},f'{kind}-{row}')

def context(duty, matrix, baselines=None):
    return CheckContext([duty],[],[duty,matrix],{},baselines or {},{},['205H'],[])

@pytest.mark.parametrize('duty,allowed',[
    ('对增值税发票的准确性负有审核责任。',{'发票'}),
    ('对报销审批单的准确性负主体责任。',{'成本费用报销审批单'}),
    ('对工资发放明细表的准确性负审核责任。',{'工资发放明细表及汇总表'}),
    ('对合同的准确性负主体责任。',{'相关的合同或协议等'}),
    ('对追责的及时性负审核责任。',set()),
    ('对程序的合规性负主体责任。',set()),
    ('对使用范围、标准的准确性负主体责任。',set()),
    ('对服务内容、付款方式等合同关键信息的准确性负审核责任。',{'合同'}),
    ('对劳保用品入账明细表、供应商发货单、职工领取记录中的准确性负审核责任。',{'劳保用品入账明细表','供应商发货单','职工领取记录'}),
])
def test_confirmed_grammar(duty,allowed):
    result=parse(duty,allowed=allowed)
    assert result['status'] in {'matched','not_applicable'},result

@pytest.mark.parametrize('duty,allowed',[
    ('对发票的准确性负审核责任。',{'增值税发票'}),
    ('对报销审批单的准确性负审核责任。',{'成本费用报销申请单'}),
    ('对报销审批单的准确性负审核责任。',{'成本费用报销审批单','其他费用报销审批单'}),
    ('对会议申请单及陌生验收报告的准确性负审核责任。',{'会议申请单'}),
    ('对核对其他公司财务表的准确性负审核责任。',{'财务表'}),
    ('对追责记录表的及时性负审核责任。',set()),
])
def test_unsafe_equivalences_and_unknown_documents_still_block(duty,allowed):
    assert parse(duty,allowed=allowed)['status'] in {'unavailable','unresolved'}

def test_aliases_are_current_measure_only():
    assert carrier_aliases_v4({'成本费用报销审批单'})['报销审批单']=='成本费用报销审批单'
    assert '报销审批单' not in carrier_aliases_v4({'其他费用申请单'})

def test_engine_does_not_publish_parser_failure_or_call_it_pass(registry,project_root):
    pack=load_pack(project_root/'risk-audit/rulepacks/drafts/quality-v1.3.0')
    pack['rules']=[r for r in pack['rules'] if r['display_code']=='R09']
    d=record(duty='对《陌生报告》及尚待完善的复杂事项关系的准确性负审核责任。',carrier='')
    m=record('matrix',4,carrier='已知报告')
    sheets=[ParsedSheet(r.sheet,r.record_type,[2],{}, {},8,3,[r]) for r in (d,m)]
    f=FileRecord(Path('a.xlsx'),Path('a.xlsx'),'hash','xlsx','205H',[],False,'06','default','three_lists',sheets)
    limits=[]
    findings,statuses=run_engine(pack,registry,[f],{}, {},limitations=limits)
    assert any(x.evidence.get('missing_references')==['陌生报告'] for x in findings)
    assert not any(x.evidence.get('issue_type')=='carrier_parse_unavailable' for x in findings)
    assert any(x['evidence'].get('issue_type')=='carrier_parse_unavailable' for x in limits)
    assert any(x.status=='partial' and x.limitations for x in statuses)
    groups=build_internal_diagnostics(limits,[f],{'205H':{'businesses':[{'business_code':'06','variant_id':'default'}]}})
    assert groups and all(not x['auto_pass'] for x in groups)

def test_blank_role_stays_dependency_and_material_gap_is_retained():
    r=record(role='',duty='对记录准确性负审核责任。')
    review={'record':r,'kind':'review','evidence':{}}
    routed=route_issue(review,{'check_id':'explicit_role_responsibility'},POLICY)
    assert routed['kind']=='limitation' and routed['evidence']['blocked_by']=='handler_reviewer_overlap'
    original={'record':r,'kind':'violation','evidence':{'issue_type':'explicit_role_missing'}}
    direct=route_issue(original,{'check_id':'handler_reviewer_overlap'},POLICY)
    assert direct['kind']=='violation' and direct['evidence']['issue_type']=='explicit_role_missing'

def test_template_inconsistency_never_expands_allowed_set():
    d=record(carrier='',duty='对入库单的准确性负审核责任。')
    m=record('matrix',4,carrier='费用明细表',control_measure='核对入库单')
    b={'path':'base.xlsx','sha256':'frozen','measures':[{'measure_id':'M1','carrier':'费用明细表','control_measure':'核对入库单','sheet':'矩阵','row':39}]}
    result=set_subset_v4(context(d,m,{('06','default'):b}),PARAMS)
    assert len(result)==1 and result[0]['kind']=='limitation'
    assert result[0]['evidence']['baseline_relation']['sha256']=='frozen'
    changed=deepcopy(b);changed['measures'][0]['measure_id']='M2'
    result=set_subset_v4(context(d,m,{('06','default'):changed}),PARAMS)
    assert result[0]['evidence']['issue_type']=='carrier_reference_unmatched'
    m.fields['carrier']=FieldValue('合同','合同','B4')
    assert set_subset_v4(context(d,m,{('06','default'):b}),PARAMS)[0]['kind']=='review'

def test_sheet_order_retains_all_old_issue_evidence():
    from risk_audit.checks.registry import ordered_records
    rows=[record(row=i+3,measure_id=v) for i,v in enumerate(['M2','M1','M2','M3','M1'])]
    ctx=CheckContext(rows,[],rows,{}, {('06','default'):{'measure_order':['M1','M2','M3']}},{},[],[])
    old=ordered_records(ctx,{'field':'measure_id'});new=ordered_records_v2(ctx,{'field':'measure_id'})
    assert len(new)==1 and new[0]['evidence']['issue_count']==len(old)
    assert [(x['row'],x['reason'],x['measure_id']) for x in new[0]['evidence']['affected_locations']]==[(x['record'].row,x['evidence']['reason'],x['evidence']['measure_id']) for x in old]

def parsed_matrix(tmp_path,pack,head,values,hidden=()):
    w=Workbook();s=w.active;s.title='风控矩阵'
    s.append(['风控矩阵'])
    s.append(['控制措施编号','控制措施','责任主体','控制载体',*head])
    for i,tail in enumerate(values,3):s.append([f'M{i}','措施','保靖公司-财务部-主任','审批单',*tail])
    for c in hidden:s.column_dimensions[c].hidden=True
    path=tmp_path/'m.xlsx';w.save(path)
    f=FileRecord(path,Path('m.xlsx'),sha256_file(path),'xlsx','205H',['标准全称:国网湖南省电力有限公司保靖县供电分公司'],False,'06','default','matrix')
    f.sheets=parse_workbook(f,path,pack['field_aliases'])
    return f.sheets[0],f

@pytest.mark.parametrize('header',['保靖适用情况','保靖县适用情况','保靖公司矩阵适用情况','矩阵变化情况是否适用'])
def test_unit_header_forms(tmp_path,pack,header):
    s,_=parsed_matrix(tmp_path,pack,[header],[['是'],['否']])
    assert s.columns['applicability']==5

def test_explicit_unit_header_wins_generic_but_foreign_and_conflicting_sources_do_not(tmp_path,pack):
    s,f=parsed_matrix(tmp_path,pack,['是否适用','保靖适用情况'],[['否','是'],['是','否']])
    assert s.columns['applicability']==6
    s,_=parsed_matrix(tmp_path,pack,['凤凰适用情况','备注'],[['是','否'],['是','否']])
    assert 'applicability' not in s.columns
    s,_=parsed_matrix(tmp_path,pack,['保靖适用情况','保靖县适用情况'],[['是','否'],['是','否']])
    assert 'applicability' not in s.columns

def test_value_profile_is_unique_preserves_blanks_and_accepts_only_hidden_source(tmp_path,pack):
    s,f=parsed_matrix(tmp_path,pack,['备注'],[['是'],['否'],[None]],hidden=['E'])
    assert s.columns['applicability']==5
    assert [x.value('applicability') for x in s.records]==['是','否','']
    assert any(x['reason']=='explicit_applicability_content_profile' for x in f.preservation['column_selections'])
    s,_=parsed_matrix(tmp_path,pack,['备注'],[['是'],['参照公司流程']])
    assert 'applicability' not in s.columns
    s,_=parsed_matrix(tmp_path,pack,['备注',None],[['是','否'],['否','是']])
    assert 'applicability' not in s.columns

def test_new_policy_is_validated(registry,pack):
    from risk_audit.configuration.validator import validate_pack,ConfigError
    pack['result_policy']={'version':1,'technical_issues':'internal','unfinished_is_pass':True}
    with pytest.raises(ConfigError): validate_pack(pack,registry)

@pytest.mark.parametrize('text,okay',[
    ('国网商城上架商品与物流信息一致性具有审核责任。',True),
    ('对发票开具的完整性、合规性具有审核责任。',True),
    ('对部门员工出差内容具有审核责任。',False),
    ('对合同的准确性不具有审核责任。',False),
    ('对合同的完整性没有审核责任。',False),
    ('本岗位无需对合同准确性承担审核责任。',False),
    ('负责审核合同。',False),
])
def test_positive_responsibility_statements_still_require_quality_and_object(text,okay):
    from risk_audit.checks.quality_checks import responsibility_phrase_v4
    d=record(duty=text);ctx=CheckContext([d],[],[d],{}, {}, {},[],[])
    assert (not responsibility_phrase_v4(ctx,{'attributes':['准确性','一致性','完整性','合规性']}))==okay

def test_actions_must_never_be_reported_as_invented_document_names():
    for text in ('对使用统一合同文本起草合同的准确性负主体责任。','对所管辖干部出差内容按照审批文件的准确性负审批责任。'):
        p=parse(text,allowed={'付款申请单'})
        assert p['status']=='unavailable'
        assert not any('起草' in x or '所管辖' in x for x in p['references'])

def test_invoice_alternative_composes_with_subtype_without_inverse_permission():
    p=parse('对增值税发票税率与合同条款进行初审负有主体责任。',allowed={'发票或收据','合同或协议'})
    assert p['status']=='matched',p
    assert parse('对发票准确性负审核责任。',allowed={'增值税专用发票'})['status'] in {'unresolved','unavailable'}

def test_export_rejects_internal_diagnostics_before_any_write(tmp_path):
    from types import SimpleNamespace
    from risk_audit.writer import write_outputs
    with pytest.raises(ValueError,match='不得写入单位审核意见'):
        write_outputs([], [SimpleNamespace(evidence={'publication_channel':'internal'})], tmp_path/'export',
                      metadata_dir=tmp_path/'metadata')
    assert not (tmp_path/'export').exists()

def test_responsibility_wording_groups_across_measures_but_keeps_locations():
    from risk_audit.review_tasks import build_review_tasks
    rows=[record(row=3,duty='负责审核合同',role='审核'),record(row=4,measure_id='M2',duty='负责审核合同',role='审核')]
    sheets=[ParsedSheet('position_duty','position_duty',[2],{}, {},8,3,rows)]
    f=FileRecord(Path('a.xlsx'),Path('a.xlsx'),'hash','xlsx','205H',[],False,'06','default','three_lists',sheets)
    fs=[{'finding_key':str(r.row),'severity':'violation','check_id':'broad_responsibility_pattern','display_code':'R10',
         'entity_code':r.entity_code,'business_code':r.business_code,'variant_id':r.variant_id,'file_path':r.file_path,'sheet':r.sheet,'row':r.row,
         'message':'缺少责任表述','evidence':{'issue_type':'responsibility_phrase_missing'}} for r in rows]
    tasks=build_review_tasks([f],fs,{'205H':{'businesses':[{'business_code':'06','variant_id':'default'}]}})
    assert len(tasks)==1 and tasks[0]['affected_rows']==2 and not tasks[0]['resolution_applies_automatically']

def test_container_and_complete_policy_names_do_not_become_truncated_references():
    p=parse('对储备库的储备项目记录的准确性负审核责任。',allowed={'储备项目记录'})
    assert p['status']=='matched',p
    p=parse('对资金分级授权审批制度执行的准确性负审核责任。',allowed={'资金分级授权审批制度'},terminology={'资金分级授权'})
    assert '资金分级授权' not in p['references'] and p['status']=='matched',p
    p=parse('对资金分级授权审批制度执行的准确性负审核责任。',allowed={'合同'},terminology={'资金分级授权'})
    assert p['status'] in {'unavailable','unresolved'} and '资金分级授权' not in p['references'],p

@pytest.mark.parametrize('name,allowed',[
    ('项目初设报告',{'项目初设/施设报告'}),
    ('项目施设报告',{'项目初设/施设报告'}),
    ('发票',{'发票及相关原始单据'}),
    ('增值税发票',{'发票及相关原始单据'}),
])
def test_remaining_parallel_carrier_forms(name,allowed):
    assert parse(f'对{name}的准确性负审核责任。',allowed=allowed)['status']=='matched'

def test_explicit_restatement_not_general_contract_equivalence():
    p=parse('对供用电合同签订以及合同的真实性负主体责任。',allowed={'供用电合同','合同统一文本'})
    assert '合同' not in p['unresolved'] and '供用电合同' in p['references']
    p=parse('对合同的真实性负主体责任。',allowed={'供用电合同'})
    assert '合同' in p['unresolved']

def test_contract_text_is_a_document_label_but_draft_and_template_are_not_synonyms():
    assert parse('对合同签订的及时性负主体责任。',allowed={'合同文本'})['status']=='matched'
    assert parse('对合同文本的准确性负审核责任。',allowed={'合同'})['status']=='matched'
    assert parse('对合同的真实性负主体责任。',allowed={'合同模板'})['status'] in {'unresolved','unavailable'}
    assert parse('对合同的真实性负主体责任。',allowed={'合同文本(草案)'})['status'] in {'unresolved','unavailable'}
