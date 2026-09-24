from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from risk_audit.checks.coverage import responsibility_coverage_v2
from risk_audit.checks.responsibility_names import responsibility_unit_specific
from risk_audit.configuration.loader import load_pack
from risk_audit.configuration.validator import ConfigError, validate_pack
from risk_audit.engine import run_engine
from risk_audit.models import FieldValue
from risk_audit.opinion_text import advice_v2
from test_coverage_exact_pairs import record, context, PARAMS
from test_engine import minimal_files

NAMES = ['各级单位','各单位','相关单位','有关单位','所属单位','各级公司','各公司','相关公司','有关公司']
UNIT = {'field':'responsibility','generic_unit_names':NAMES}
COVERAGE = {**PARAMS,'generic_unit_names':NAMES}


def unit(row):
    return responsibility_unit_specific(SimpleNamespace(records=[row]), UNIT)


@pytest.mark.parametrize('name', NAMES)
def test_explicit_generic_unit_is_an_independent_violation(name):
    m=record(responsibility=name+'-资产使用保管部门-主任')
    assert responsibility_coverage_v2(context(m,[record('position_duty')]),COVERAGE)==[]
    issue=unit(m)[0]
    assert issue['kind']=='violation' and issue['evidence']['generic_unit_names']==[name]
    assert issue['evidence']['source_cell']=='A3'
    message=advice_v2('entity_name_specific','violation',issue['evidence'])
    assert '具体单位名称' in message and '待核实' not in message and '对应' not in message


@pytest.mark.parametrize('text', ['国网湖南信通公司-综合管理部-主任','信通公司-综合管理部-主任',
                                  '资产使用保管部门-主任','国网湖南信通公司-综合管理部-各级单位联络专责',
                                  '国网湖南信通公司-各单位联络部-主任','本单位-资产使用保管部门-主任'])
def test_concrete_names_abbreviations_and_embedded_words_are_not_flagged(text):
    assert unit(record(responsibility=text))==[]


def test_generic_unit_in_other_field_is_not_a_unit_name_violation():
    assert unit(record(duty='审核各级单位提报的材料'))==[]


def test_parent_unit_and_multiple_responsibilities_are_kept_with_evidence():
    m=record(responsibility='省公司—各级单位—综合管理部—主任；各单位-财务部-核算专责；各级单位-人资部-主任')
    e=unit(m)[0]['evidence']
    assert e['generic_unit_names']==['各级单位','各单位']
    assert len(e['matches'])==3


def test_whitespace_and_dash_variants_are_normalized():
    assert unit(record(responsibility=' 各 级 单 位 － 资产使用保管部门 － 主任 '))[0]['kind']=='violation'


@pytest.mark.parametrize('text', ['各级单位','各级单位-资产使用保管部门'])
def test_explicit_unit_is_still_flagged_when_other_parts_are_incomplete(text):
    assert unit(record(responsibility=text))[0]['kind']=='violation'


def test_deleted_unit_text_is_not_current_responsibility():
    m=record(responsibility='国网湖南信通公司-资产使用保管部门-主任')
    m.fields['responsibility'].raw='各级单位'+m.value('responsibility')
    m.fields['responsibility'].deleted_spans=[{'text':'各级单位','start':0,'end':4}]
    assert unit(m)==[]


@pytest.mark.parametrize('missing', ['measure','duties','scope','applicability'])
def test_unit_specificity_does_not_depend_on_coverage_prerequisites(missing):
    m=record(responsibility='各级单位-资产使用保管部门-主任')
    if missing=='measure':m.fields['measure_id'].current=''
    if missing=='scope':m.entity_code=''
    if missing=='applicability':m.fields['applicability'].current=''
    assert unit(m)[0]['kind']=='violation'


def test_explicitly_inapplicable_measure_is_skipped():
    assert unit(record(responsibility='各级单位-资产使用保管部门-主任',applicability='不适用'))==[]


def test_unreadable_responsibility_is_review_not_violation():
    m=record();m.fields['responsibility'].state='formula_no_cache'
    assert unit(m)[0]['kind']=='review'
    del m.fields['responsibility']
    assert unit(m)[0]['kind']=='review'
    assert unit(record(responsibility=''))==[]


def test_real_pattern_explains_the_actual_department_position_difference():
    m=record(responsibility='各级单位-资产使用保管部门-资产使用保管人员')
    ds=[record('position_duty',row=133,position='设备专责')]
    e=responsibility_coverage_v2(context(m,ds),COVERAGE)[0]['evidence']
    assert e['mapping_details'][0]['duty_rows'][0]['row']==133
    message=advice_v2('matrix_duty_coverage','review',e)
    assert '各级单位' not in message and '设备专责' in message and '资产使用保管人员' in message
    assert '待核实' in message


@pytest.mark.parametrize('field,value',[('entity_code','OTHER'),('business_code','07'),('variant_id','other'),('measure_id','M2')])
def test_explanatory_candidates_are_restricted_to_the_same_scope(field,value):
    m=record(responsibility='各级单位-资产使用保管部门-资产使用保管人员')
    wrong=record('position_duty',position='不能引用的岗位')
    if field=='measure_id':wrong.fields[field].current=value
    else:setattr(wrong,field,value)
    related=record('position_duty',row=9,department='财务部',position='核算专责')
    e=responsibility_coverage_v2(context(m,[wrong,related]),COVERAGE)[0]['evidence']
    assert e['mapping_details'][0]['duty_positions']==[]
    assert '不能引用的岗位' not in advice_v2('matrix_duty_coverage','review',e)


def configured_pack():
    p=load_pack(Path(__file__).resolve().parents[1]/'rulepacks/releases/1.2.4')
    p['manifest']['status']='draft'
    p['rules']=[r for r in p['rules'] if r['rule_id']=='duties.coverage']
    coverage=p['rules'][0]
    coverage['checks'][0]['operator_version']=2
    coverage['checks'][0]['params']['generic_unit_names']=NAMES
    specificity=deepcopy(coverage)
    specificity['rule_id']='duties.unit_specificity'
    specificity['checks']=[{'check_id':'entity_name_specific','operator':'responsibility_unit_specific','operator_version':1,
                            'params':UNIT.copy(),'on_unavailable':'review','message':{'review':'{advice_v2}（第5条）','violation':'{advice_v2}（第5条）'},'location_policy':'row'}]
    p['rules'].append(specificity)
    return p


def test_both_checks_execute_and_unit_name_comes_first(registry):
    p=configured_pack();validate_pack(p,registry)
    rows=[record(responsibility='各级单位-资产使用保管部门-资产使用保管人员'),record('position_duty',position='设备专责')]
    findings,statuses=run_engine(p,registry,minimal_files(rows),{'205H':SimpleNamespace(name='信通')},{})
    assert [f.check_id for f in findings]==['entity_name_specific','matrix_duty_coverage']
    assert sum(s.findings for s in statuses)==2


@pytest.mark.parametrize('disabled',['duties.coverage','duties.unit_specificity'])
def test_checks_can_be_disabled_independently(registry,disabled):
    p=configured_pack()
    next(r for r in p['rules'] if r['rule_id']==disabled)['enabled']=False
    rows=[record(responsibility='各级单位-资产使用保管部门-资产使用保管人员'),record('position_duty',position='设备专责')]
    findings,_=run_engine(p,registry,minimal_files(rows),{'205H':SimpleNamespace(name='信通')},{})
    assert len(findings)==1 and findings[0].rule_id!=disabled


def test_confirmed_coverage_mapping_does_not_waive_generic_unit(registry):
    p=configured_pack();m=record(responsibility='各级单位-资产使用保管部门-资产使用保管人员')
    p['rules'][0]['checks'][0]['params']['confirmed_mappings']={m.value('responsibility'):[{'department':'资产使用保管部门','position':'设备专责'}]}
    findings,_=run_engine(p,registry,minimal_files([m,record('position_duty',position='设备专责')]),{'205H':SimpleNamespace(name='信通')},{})
    assert [f.check_id for f in findings]==['entity_name_specific']


@pytest.mark.parametrize('invalid',[[],[''],[1],'各级单位'])
def test_generic_unit_configuration_requires_explicit_names(registry,invalid):
    p=configured_pack();p['rules'][1]['checks'][0]['params']['generic_unit_names']=invalid
    with pytest.raises(ConfigError):validate_pack(p,registry)
