from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

np = pytest.importorskip("numpy")

from risk_audit.semantic import SemanticAssistant, LocalEncoder
from risk_audit.semantic_matching import parse_responsibility, position_conflicts, filter_applicability
from risk_audit.semantic_validation import validate_semantic_config
from risk_audit.configuration.loader import load_pack
from risk_audit.semantic_vocabulary import write_relation_groups
from risk_audit.util import sha256_json

ROOT=Path(__file__).resolve().parents[1]
SCOPE=('205H','06','default','M1')
NAMES={'国网湖南信通公司','信通公司'}


@pytest.fixture
def config():return load_pack(ROOT/'rulepacks/drafts/semantic-v1.5.0')['semantic_config']


class ConfidentEncoder:
    """All pairs have perfect semantic scores; structural contradictions must still win."""
    manifest={'max_tokens':512}
    tokenizer=SimpleNamespace(encode=lambda t:SimpleNamespace(ids=list(t)))
    def __init__(self):self.calls=[]
    def encode(self,texts):
        self.calls.extend(texts)
        return np.array([[1.,0.] for _ in texts])


def service(config):
    s=SemanticAssistant(config,{})
    s.encoder=ConfidentEncoder()
    return s


def entry(position,department='综合管理部',scope=SCOPE,row=8):
    return {'text':department+'-'+position,'department':department,'position':position,
            'scope':list(scope),'sources':[{'file':'duty.xlsx','sheet':'岗位责任清单','row':row,
            'department_cell':f'A{row}','position_cell':f'B{row}','sha256':'a'*64,'measure_id':scope[3]}]}


@pytest.mark.parametrize('q,c',[
 ('经理','副经理'),('副主任','主任'),('党委书记','党委副书记'),
 ('审核人员','审批人员'),('经办人员','审核人员'),('复核专责','审核专责'),
 ('四级职员','薪酬专责（五级职员）'),('薪酬专责(4级职员)','薪酬专责(五级职员)'),
 ('十一级职员','薪酬专责(一级职员)'),
 ('负有审核责任','不负有审核责任'),('资金管理专责','会计核算专责'),
 ('税务稽核专责','全面预算管理专责'),('法律事务专责','纪检专责'),
])
def test_critical_differences_override_even_perfect_similarity(config,q,c):
    s=service(config)
    result=s.suggest_responsibility('信通公司-综合管理部-'+q,[entry(c)],scope=SCOPE,unit_names=NAMES)
    assert not result['candidates'] and result['status']=='abstained'
    assert result['excluded_candidates'][0]['reasons']
    assert not s.encoder.calls


@pytest.mark.parametrize('part,value',[(0,'other'),(1,'07'),(2,'digital'),(3,'M2')])
def test_exact_scope_is_checked_again_at_retrieval_boundary(config,part,value):
    scope=list(SCOPE);scope[part]=value;s=service(config)
    result=s.suggest_responsibility('信通公司-综合管理部-主任',[entry('主任',scope=scope)],scope=SCOPE,unit_names=NAMES)
    assert not result['candidates'] and not s.encoder.calls


def test_only_verified_owner_prefix_is_removed(config):
    s=service(config)
    a=s.suggest_responsibility('国网湖南信通公司-综合管理部-主任',[entry('主任')],scope=SCOPE,unit_names=NAMES)
    assert a['query_fields']['department']=='综合管理部'
    assert all('信通公司' not in t for t in s.encoder.calls)
    b=s.suggest_responsibility('娄底市公司-综合管理部-主任',[entry('主任')],scope=SCOPE,unit_names=NAMES)
    assert not b['candidates'] and '单位前缀' in b['reason']
    assert a['candidates'][0]['sources'][0]['position_cell']=='B8'


def test_generic_owner_does_not_become_approved_identity(config):
    result=service(config).suggest_responsibility('各级单位-综合管理部-主任',[entry('主任')],
        scope=SCOPE,unit_names=NAMES,generic_units=['各级单位'])
    assert result['query_fields']['ownership']=='generic_owner_not_confirmed'
    assert result['candidates'][0]['automatic_equivalence'] is False


@pytest.mark.parametrize('text',['信通公司-领导干部','商旅云公司','信通公司-综合管理部-主任、经理'])
def test_incomplete_or_compound_structure_abstains(config,text):
    s=service(config)
    assert s.suggest_responsibility(text,[entry('主任')],scope=SCOPE,unit_names=NAMES)['status']=='abstained'
    assert not s.encoder.calls


def test_exact_position_and_multiple_close_candidates_are_explicit(config):
    result=service(config).suggest_responsibility('信通公司-综合管理部-主任',
        [entry('主任','综合管理室'),entry('主任','综合部')],scope=SCOPE,unit_names=NAMES)
    assert len(result['candidates'])==2 and result['ambiguous']
    assert all(c['position_exact'] and not c['automatic_equivalence'] for c in result['candidates'])


@pytest.mark.parametrize('department',['党委组织部','审计监管部','市场营销部','纪检监察部'])
def test_shared_job_title_cannot_override_different_department_function(config,department):
    s=service(config)
    result=s.suggest_responsibility('信通公司-财务部门-负责人',[entry('负责人',department)],scope=SCOPE,unit_names=NAMES)
    assert not result['candidates'] and not s.encoder.calls
    assert '部门明确职能不同' in result['excluded_candidates'][0]['reasons']


def test_long_candidate_does_not_block_short_candidate(config):
    result=service(config).suggest_responsibility('信通公司-综合管理部-主任',
        [entry('普通岗位'*200),entry('主任')],scope=SCOPE,unit_names=NAMES)
    assert [e['position'] for e in result['candidates']]==['主任']
    assert '长度上限' in result['excluded_candidates'][0]['reasons'][0]


@pytest.mark.parametrize('text',['湘西新增','是否适用','并非不适用','若开展业务则适用','不一定适用','适用但不适用'])
def test_applicability_does_not_invent_yes_no_from_high_similarity(config,text):
    result={'text':text,'candidates':[{'text':'适用','concept':'applicable','score':0.99},
                                    {'text':'不适用','concept':'not_applicable','score':0.8}]}
    filter_applicability(result,config['retrieval'])
    assert not result['candidates'] and result['status']=='abstained'


def test_close_opposite_applicability_classes_abstain(config):
    result={'text':'参考执行','candidates':[{'text':'适用','concept':'applicable','score':0.89},
                                        {'text':'不适用','concept':'not_applicable','score':0.88}]}
    filter_applicability(result,config['retrieval'])
    assert not result['candidates']


@pytest.mark.parametrize('change',[
 {'candidate_only':False},{'allowed_domains':['applicability','responsibility','carrier']},
 {'version':True},{'retrieval':{'min_position_score':0.1}},
])
def test_configuration_cannot_enable_auto_approval_or_retired_carriers(config,change):
    config.update(change)
    assert validate_semantic_config(config)


def test_relation_grouping_preserves_all_measure_specific_targets(config,tmp_path):
    s=service(config);sources={};events=[]
    for mid,row in [('M1',8),('M2',19)]:
        scope=(*SCOPE[:3],mid)
        e=s.suggest_responsibility('信通公司-综合管理部-主任',[entry('主任',scope=scope,row=row)],scope=scope,unit_names=NAMES)
        events.append(e)
        sources[sha256_json([e['domain'],e['text'],e['scope']])]=[{'file':'matrix.xlsx','sheet':'矩阵','row':row,
            'cell':f'C{row}','measure_id':mid}]
    summary=write_relation_groups(tmp_path,{'events':events},sources)
    groups=json.loads((tmp_path/'semantic_relation_groups.json').read_text())
    assert summary['groups']==1 and summary['relation_instances']==2
    assert groups[0]['measure_count']==2 and groups[0]['source_row_count']==2
    assert {i['targets'][0]['position_cell'] for i in groups[0]['instances']}=={'B8','B19'}
    assert groups[0]['automatic_equivalence'] is False


def test_memory_cache_is_bounded_and_batch_size_is_bounded():
    from collections import OrderedDict
    encoder=LocalEncoder.__new__(LocalEncoder);encoder.memory=OrderedDict()
    for i in range(4100):encoder._remember(str(i),np.array([1.,0.]))
    assert len(encoder.memory)==4096 and '0' not in encoder.memory
    for invalid in [0,17,True]:
        with pytest.raises(ValueError):encoder.encode([],batch_size=invalid)


def test_grade_notation_equivalence_preserves_full_number():
    assert not position_conflicts('十一级职员','薪酬专责(11级职员)')
    assert not position_conflicts('二十一级职员','薪酬专责(21级职员)')
    assert not position_conflicts('四级职员','薪酬专责(04级职员)')
