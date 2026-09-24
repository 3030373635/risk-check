from copy import deepcopy
from pathlib import Path
import pytest
from risk_audit.models import Record,FieldValue,FileRecord,ParsedSheet
from risk_audit.review_tasks import build_review_tasks,write_review_tasks
from risk_audit.configuration.validator import validate_pack,ConfigError


SCOPES={'E':{'businesses':[{'business_code':'06','variant_id':'default'}]}}


def fixture():
    rows=[Record('position_duty','E','06','default','a.xlsx','岗位',i,{'measure_id':FieldValue('M1','M1','G'+str(i))},str(i)) for i in [3,4]]
    f=FileRecord(Path('a.xlsx'),Path('a.xlsx'),'hash','xlsx','E',[],False,'06','default','three_lists',
                 [ParsedSheet('岗位','position_duty',[2],{'measure_id':7},{},8,3,rows)])
    findings=[]
    for r in rows:
        for check in ['person_required','handler_reviewer_overlap']:
            findings.append({'finding_key':check+str(r.row),'severity':'review','check_id':check,'display_code':'R05',
                'entity_code':'E','business_code':'06','variant_id':'default','file_path':r.file_path,'sheet':r.sheet,'row':r.row,
                'message':'人员列无法识别','evidence':{'field':'person_names','issue_type':'field_unavailable'}})
    return f,findings


def test_missing_person_column_is_one_task_with_complete_trace(tmp_path):
    f,findings=fixture();before=deepcopy(findings)
    tasks=build_review_tasks([f],findings,SCOPES)
    assert len(tasks)==1 and tasks[0]['finding_count']==4 and tasks[0]['affected_rows']==2
    assert tasks[0]['kind']=='读取处理' and findings==before
    summary=write_review_tasks(tmp_path,tasks)
    assert summary['in_scope_review_tasks']==1 and summary['input_findings']==4


def test_real_empty_values_do_not_become_column_missing_tasks():
    f,findings=fixture()
    for r in f.sheets[0].records:r.fields['person_names']=FieldValue('','','E'+str(r.row))
    tasks=build_review_tasks([f],findings,SCOPES)
    assert all(t['kind']!='读取处理' for t in tasks)


def test_unconfirmed_scope_never_enters_in_scope_business_queue():
    f,findings=fixture()
    for finding in findings:finding['entity_code']=''
    tasks=build_review_tasks([f],findings,SCOPES)
    assert len(tasks)==1 and tasks[0]['scope']=='归属待确认'
    assert not tasks[0]['resolution_applies_automatically']


@pytest.mark.parametrize('change',[{'field':'manual_review'},{'sha256':'old'},{'column':0},{'column':True}])
def test_invalid_input_overrides_are_rejected(pack,registry,change):
    item={'file_path':'a.xlsx','sha256':'a'*64,'sheet':'岗位','field':'person_names','column':3,'reason':'审核人员确认'}
    pack['input_overrides']=[{**item,**change}]
    with pytest.raises(ConfigError):validate_pack(pack,registry)
