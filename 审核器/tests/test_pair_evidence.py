from copy import deepcopy
from pathlib import Path
from risk_audit.checks.registry import build_registry
from risk_audit.configuration.loader import load_pack
from risk_audit.engine import run_engine
from risk_audit.models import Entity,FieldValue,Record,ParsedSheet,FileRecord

ROOT=Path(__file__).resolve().parents[2]

def make(n,role,names):
    duty='对用工真实性负'+('主体' if role=='经办' else '审核')+'责任'
    data={'measure_id':'M1','duty':duty,'role':role,'position':'项目专责','person_names':'、'.join(names)}
    r=Record('position_duty','205H','10','default','duties.xlsx','岗位职责清单',n,{k:FieldValue(v,v,f'A{n}') for k,v in data.items()},f'r{n}')
    r.person_keys=[{'name':x,'person_id':None} for x in names]
    return r

def execute(rows):
    p=load_pack(ROOT/'审核器/rulepacks/releases/1.2.5')
    p['rules']=[r for r in p['rules'] if r['display_code']=='R08']
    f=FileRecord(Path('duties.xlsx'),Path('duties.xlsx'),'x','xlsx','205H',[],False,'10','default','three_lists',sheets=[ParsedSheet('岗位职责清单','position_duty',[2],{}, {},8,3,rows)])
    return run_engine(p,build_registry(),[f],{'205H':Entity('205H','信通')},{},'test-pairs')[0]

def test_engine_keeps_each_person_and_counterpart():
    rows=[make(3,'经办',['甲','乙']),make(4,'审核',['甲']),make(5,'审核',['乙'])]
    fs=execute(rows)
    assert len(fs)==4
    at3=[f for f in fs if f.row==3]
    assert {(f.evidence['related_row'],f.evidence['conflict_people']) for f in at3}=={(4,'甲'),(5,'乙')}
    assert all(f.severity=='review' for f in fs)

def test_pair_identity_is_stable_when_input_order_changes():
    rows=[make(3,'经办',['甲','乙']),make(4,'审核',['甲']),make(5,'审核',['乙'])]
    assert {f.finding_key for f in execute(rows)}=={f.finding_key for f in execute(list(reversed(rows)))}
