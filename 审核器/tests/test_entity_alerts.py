from copy import deepcopy
from pathlib import Path
from risk_audit.entity_alerts import build_entity_alerts, write_entity_alerts
from risk_audit.models import Entity, FileRecord


def file(path, code=None, conflict=False, evidence=()):
    return FileRecord(Path('/source')/path, Path(path), 'hash', 'xlsx', code, list(evidence), conflict, '06', 'default', 'three_lists')


def test_missing_code_is_grouped_once_with_all_files_and_no_mutation(tmp_path):
    files=[file('17样例服务有限公司/06/三清单.xlsx'),file('17样例服务有限公司/09/矩阵.xlsx')]
    before=deepcopy(files)
    alerts=build_entity_alerts(files,{},[Entity(None,'样例服务有限公司',source_row=12)])
    assert len(alerts)==1 and alerts[0]['file_count']==2
    assert alerts[0]['files'][0]['master_rows_without_code']==[{'name':'样例服务有限公司','row':12}]
    assert files==before and all(f.entity_code is None for f in files)
    report=write_entity_alerts(tmp_path,alerts).read_text()
    assert '2份文件' in report and '第12行' in report and '单位代码为空' in report


def test_conflict_retains_evidence_and_never_assigns_code():
    alerts=build_entity_alerts([file('甲有限公司/06/三清单.xlsx',conflict=True,evidence=['标准全称:甲有限公司','标准全称:乙有限公司'])],{'A':Entity('A','甲有限公司'),'B':Entity('B','乙有限公司')})
    assert '冲突' in alerts[0]['reason']
    assert len(alerts[0]['files'][0]['evidence'])==2 and alerts[0]['files'][0]['candidate_code'] is None


def test_invalid_alias_code_is_not_treated_as_confirmed():
    alerts=build_entity_alerts([file('甲有限公司/06/三清单.xlsx',code='UNKNOWN')],{'A':Entity('A','甲有限公司')})
    assert '不在当前主体清单' in alerts[0]['reason']


def test_confirmed_entity_has_no_alert_even_if_business_is_unknown(tmp_path):
    f=file('甲有限公司/06/三清单.xlsx',code='A');f.business_code=None
    alerts=build_entity_alerts([f],{'A':Entity('A','甲有限公司')})
    assert alerts==[]
    assert '没有待确认' in write_entity_alerts(tmp_path,alerts).read_text()


def test_unit_label_is_available_when_input_is_a_business_subdirectory():
    f=file('三清单.xlsx')
    f.source=Path('/source/17样例服务有限公司/09业务/三清单.xlsx')
    alerts=build_entity_alerts([f],{},[Entity(None,'样例服务有限公司',source_row=12)])
    assert alerts[0]['unit_label']=='样例服务有限公司'
    assert alerts[0]['files'][0]['master_rows_without_code'][0]['row']==12
