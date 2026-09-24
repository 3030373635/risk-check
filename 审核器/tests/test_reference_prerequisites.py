from risk_audit.models import Record, FieldValue
from risk_audit.checks.registry import CheckContext, set_subset

def record(kind, row, *, measure='M1', entity='E1', business='06', carrier='', duty=''):
    fields={k:FieldValue(v,v,f'A{row}') for k,v in {'measure_id':measure,'carrier':carrier,'duty':duty}.items()}
    return Record(kind,entity,business,'default','source.xlsx',kind,row,fields,f'{kind}-{row}')

PARAMS={'source_field':'carrier','target_field':'carrier','target_record_type':'matrix','fallback_explicit_field':'duty','alias_resource':'carrier_aliases'}

def run(duty,*matrices):
    return set_subset(CheckContext([duty],[],[duty,*matrices],{},{},{},['E1'],[]),PARAMS)

def test_blank_keys_must_not_join_unrelated_blank_rows():
    d=record('position_duty',9,measure='',duty='对《审批单》的准确性负主体责任')
    a=record('matrix',14,measure='',carrier='审批单')
    b=record('matrix',15,measure='',carrier='验收单')
    issues=run(d,a,b)
    assert len(issues)==1
    assert issues[0]['kind']=='review'
    assert issues[0]['evidence']['issue_type']=='carrier_measure_id_missing'
    assert not issues[0]['evidence'].get('matrix_carrier_sets')

def test_unknown_entities_must_not_join_each_other():
    d=record('position_duty',9,entity='',duty='对《审批单》的准确性负主体责任')
    m=record('matrix',14,entity='',carrier='审批单')
    issues=run(d,m)
    assert len(issues)==1 and issues[0]['evidence']['issue_type']=='carrier_scope_unconfirmed'

def test_unreadable_target_carrier_must_not_look_like_absent_reference():
    d=record('position_duty',9,duty='对《审批单》的准确性负主体责任')
    m=record('matrix',14)
    m.fields['carrier']=FieldValue('=A1','','B14',formula='=A1',state='formula_no_cache')
    issues=run(d,m)
    assert len(issues)==1 and issues[0]['evidence']['issue_type']=='carrier_target_unavailable'

def test_readable_matched_reference_still_passes():
    assert not run(record('position_duty',9,duty='对《审批单》的准确性负主体责任'),record('matrix',14,carrier='审批单'))

def test_actual_conflicting_carriers_keep_conflict_evidence():
    issues=run(record('position_duty',9,duty='对《审批单》的准确性负主体责任'),record('matrix',14,carrier='审批单'),record('matrix',15,carrier='验收单'))
    assert len(issues)==1 and issues[0]['evidence']['issue_type']=='matrix_ambiguous'
    assert len(issues[0]['evidence']['matrix_carrier_sets'])==2
