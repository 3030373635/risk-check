from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest
from risk_audit.checks.registry import build_registry
from risk_audit.configuration.loader import load_pack
from risk_audit.engine import run_engine
from risk_audit.models import FieldValue
from test_carriers_v102 import record,PARAMS
from test_carriers_v102 import run as old_carriers
from test_deleted_final_text import matrix
from test_deleted_text import duty
from test_engine import minimal_files
from test_rules_v2 import context,row,params

def carrier_issues(target,matrices):
    from risk_audit.checks.registry import CheckContext
    ctx=CheckContext([target],[],[target,*matrices],{},{},{},['205H'],[])
    reg=build_registry();cap=reg.get('set_subset',2) or reg.get('set_subset',1)
    return cap.runner(ctx,PARAMS)

def responsibility(text):
    reg=build_registry();cap=reg.get('responsibility_phrase',2) or reg.get('responsibility_phrase',1)
    return cap.runner(context([row(duty=text)]),params('broad_responsibility_pattern'))

def test_filled_carrier_cell_cannot_hide_extra_duty_reference():
    d=record('position_duty',carriers='货物交接单；货物入库单',duty='对监造记录、物流信息一致性负有主体责任。')
    m=record('matrix',carriers='货物交接单；货物入库单')
    assert old_carriers(d,[m])==[]
    issues=carrier_issues(d,[m])
    assert issues and any(i['evidence'].get('source_field')=='duty' for i in issues)
    assert '监造记录' in str(issues) and '物流信息' in str(issues)

def test_full_source_sentence_preserves_second_responsibility_clause():
    d=record('position_duty',carriers='货物交接单；货物入库单',duty='对到货物资的外观、规格、型号、数量等方面进行验收，对监造记录、物流信息一致性负有主体责任。')
    issues=carrier_issues(d,[record('matrix',carriers='货物交接单；货物入库单')])
    assert any('监造记录' in i['evidence'].get('unavailable_reason','') and '物流信息' in i['evidence'].get('unavailable_reason','') for i in issues)
    assert not any(i['evidence'].get('missing_references')==['到货物资'] for i in issues)

def test_multiclause_action_is_not_a_single_document_name():
    d=record('position_duty',carriers='货物交接单',duty='负责会同物资部门对到货物资验收无误后，进行电子签字签章，自动生成财务凭证。')
    issues=carrier_issues(d,[record('matrix',carriers='货物交接单')])
    assert any(i['evidence']['issue_type']=='carrier_parse_unavailable' for i in issues)
    assert not any(i['evidence'].get('missing_references') for i in issues)

def test_quoted_document_with_commas_remains_an_explicit_reference():
    d=record('position_duty',carriers='验收单',duty='对《收支、结存核对表》的准确性负责。')
    issues=carrier_issues(d,[record('matrix',carriers='验收单')])
    assert any(i['evidence'].get('missing_references')==['收支、结存核对表'] for i in issues)

def test_correspondence_word_does_not_start_a_new_responsibility_clause():
    d=record('position_duty',carriers='验收单',duty='对费用明细表与验收单对应关系的准确性负有审核责任。')
    issues=carrier_issues(d,[record('matrix',carriers='验收单')])
    assert '费用明细表' in str(issues)

def test_earlier_clause_keeps_explicit_known_carriers_after_clause_refinement():
    from risk_audit.checks.carriers import parse_carrier_references_v2
    parsed=parse_carrier_references_v2('对照制度要求，对费用审批单、发票、发放汇总表，开展评价，对监督评价准确性负有审核责任。',allowed={'验收单'},terminology={'费用审批单','发票','发放汇总表'})
    assert set(parsed['references'])=={'费用审批单','发票','发放汇总表'}
    assert set(parsed['unresolved'])==set(parsed['references'])

def test_two_different_sources_keep_both_missing_reference_sets():
    d=record('position_duty',carriers='列内申请单',duty='对《正文审批单》的准确性负主体责任。')
    issues=carrier_issues(d,[record('matrix',carriers='验收单')])
    missing={i['evidence'].get('source_field'):i['evidence'].get('missing_references') for i in issues if i['evidence']['issue_type']=='carrier_reference_unmatched'}
    assert missing=={'carrier':['列内申请单'],'duty':['正文审批单']}

def test_readable_both_sources_matching_are_clean():
    d=record('position_duty',carriers='验收单；审批单',duty='对验收单的准确性负主体责任。')
    assert carrier_issues(d,[record('matrix',carriers='验收单；审批单')])==[]

def test_unreadable_carrier_formula_cannot_be_treated_as_blank():
    d=record('position_duty',duty='对验收单的准确性负主体责任。')
    d.fields['carrier']=FieldValue('=A1','','B3',formula='=A1',state='formula_no_cache')
    issues=carrier_issues(d,[record('matrix',carriers='验收单')])
    assert any(i['evidence'].get('source_field')=='carrier' and i['evidence']['issue_type']=='carrier_source_unavailable' for i in issues)

def test_unreadable_duty_formula_is_not_masked_by_carrier_column():
    d=record('position_duty',carriers='验收单')
    d.fields['duty']=FieldValue('=A1','','C3',formula='=A1',state='formula_no_cache')
    issues=carrier_issues(d,[record('matrix',carriers='验收单')])
    assert any(i['evidence'].get('source_field')=='duty' and i['evidence']['issue_type']=='carrier_source_unavailable' for i in issues)

def test_missing_matrix_emits_one_prerequisite_not_two_source_errors():
    issues=carrier_issues(record('position_duty',carriers='验收单',duty='对其他表的准确性负主体责任。'),[])
    assert len(issues)==1 and issues[0]['evidence']['issue_type']=='matrix_ambiguous'

def test_v2_does_not_mutate_parsed_input():
    d=record('position_duty',carriers='验收单',duty='对《其他表》的准确性负主体责任。')
    before=deepcopy(d)
    carrier_issues(d,[record('matrix',carriers='验收单')])
    assert d==before

@pytest.mark.parametrize('text',[
    '负责编制并签订供用电合同，对合同真实性、有效性、准确性负责。',
    '对资料的完整性负责。','对数据真实性和准确性共同负责。',
    '对资料真实性、完整性直接负责。',
    '对对应关系准确性负责。',
])
def test_equivalent_responsible_predicate_is_accepted(text):
    assert responsibility(text)==[]

@pytest.mark.parametrize('text',[
    '负责合同编制。','对合同负责。','对资料完整性不负责。','不对资料完整性负责。',
    '无需对资料完整性负责。','不必对资料完整性负责。',
    '对资料完整性负责审核。','对资料完整性负责人进行检查。',
    '对资料完整性进行核对，负责归档。','对资料完整性进行核对。负责。',
    '对准确性、完整性负责。','对真实性完整性负责。',
    '对资料完整性开展核验，但不对附件完整性负责。',
    '不能对资料完整性负责。','无须直接对资料完整性负责。',
    '不对对应关系准确性负责。',
])
def test_action_only_negation_job_titles_and_distant_predicates_still_fail(text):
    assert responsibility(text)

def run_dual_engine(*,deleted=False):
    pack=load_pack(Path(__file__).resolve().parents[1]/'rulepacks/releases/1.2.5')
    pack['rules']=[r for r in pack['rules'] if r['display_code'] in {'R09','R13'}]
    cap=build_registry().get('set_subset',2)
    for r in pack['rules']:
        for c in r['checks']:
            if c['operator']=='set_subset':c['operator_version']=2 if cap else 1
        if r['display_code']=='R13':r['enabled']=deleted
    m=matrix() if deleted else matrix(old='',current='验收单')
    d=duty('对技术鉴定报告的准确性负有审核责任。')
    d.fields['carrier']=FieldValue('技术鉴定报告','技术鉴定报告','H3')
    return run_engine(pack,build_registry(),minimal_files([m,d]),{'205H':SimpleNamespace(name='信通')},{})

def test_engine_dedup_keeps_different_source_cells():
    findings,_=run_dual_engine()
    assert len([f for f in findings if f.check_id=='carrier_subset'])==2
    assert {f.evidence['source_field'] for f in findings if f.check_id=='carrier_subset'}=={'carrier','duty'}

def test_deletion_supersedes_duty_reference_but_not_carrier_column():
    findings,statuses=run_dual_engine(deleted=True)
    r09=[f for f in findings if f.check_id=='carrier_subset']
    r13=[f for f in findings if f.display_code=='R13']
    assert len(r09)==1 and r09[0].evidence['source_field']=='carrier'
    assert len(r13)==1
    assert r13[0].evidence['superseded_carrier_checks'][0]['evidence']['source_field']=='duty'
    assert sum(s.findings for s in statuses)==len(findings)
