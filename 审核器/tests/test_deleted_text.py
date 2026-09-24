from types import SimpleNamespace
from pathlib import Path
import pytest
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from risk_audit.models import Record, FieldValue, FileRecord
from risk_audit.readers.excel import deleted_spans, current_text, parse_workbook
from risk_audit.checks.deleted_content import deleted_text_reappears
from risk_audit.configuration.validator import validate_pack,ConfigError

P={"source_fields":["control_measure","carrier"],"target_field":"duty","match_scope":"measure"}

def record(kind='matrix', text='已删台账', mid='M1', entity='205H', business='06', variant='default', field='carrier', deleted=True):
    fv=FieldValue(text,'' if deleted else text,'V3',deleted_spans=[{'text':text,'start':0,'end':len(text)}] if deleted else [])
    fields={'measure_id':FieldValue(mid,mid,'I3'),'carrier':FieldValue('','','V3'),'control_measure':FieldValue('','','J3'),field:fv}
    return Record(kind,entity,business,variant,kind+'.xlsx',kind,3,fields,kind+entity+mid)

def run(matrices,duty,**params):
    return deleted_text_reappears(SimpleNamespace(records=[duty],all_records=[*matrices,duty]),{**P,**params})

def duty(text='对已删台账的准确性负责',**kwargs):
    return record('position_duty',text=text,field='duty',deleted=False,**kwargs)

def test_contiguous_struck_font_runs_join_and_retained_text_stays_separate():
    c=SimpleNamespace(value=CellRichText([TextBlock(InlineFont(strike=True),'旧'),TextBlock(InlineFont(strike=True,b=True),'台账'),'替代',TextBlock(InlineFont(strike=True),'旧流程')]),font=Font(strike=True))
    assert [x['text'] for x in deleted_spans(c)]==['旧台账','旧流程']
    assert current_text(c)=='替代'

def test_plain_whole_cell_strike_is_detected_but_red_and_underline_are_not():
    assert deleted_spans(SimpleNamespace(value='旧流程',font=Font(strike=True)))[0]['text']=='旧流程'
    assert deleted_spans(SimpleNamespace(value='保留文字',font=Font(color='FF0000',underline='single')))==[]

@pytest.mark.parametrize('field',['control_measure','carrier'])
def test_exact_deleted_text_in_duty_is_a_violation_with_coordinates(field):
    issues=run([record(field=field)],duty())
    assert len(issues)==1 and issues[0]['kind']=='violation'
    assert issues[0]['evidence']['matches'][0]['source_cell']=='V3'
    assert issues[0]['evidence']['matches'][0]['target_cell']=='V3'

def test_no_deletion_or_no_reappearance_produces_no_r09_opinion():
    assert run([record(deleted=False)],duty())==[]
    assert run([record()],duty('对新台账的准确性负责'))==[]
    assert run([record()],duty('对当前矩阵未列出的另一单据负责'))==[]

def test_exact_original_is_not_tokenized_or_matched_by_synonym():
    assert run([record(text='负责旧台账审核。')],duty('对旧台账负责'))==[]
    assert run([record(text='验收单')],duty('对验收记录负责'))==[]
    assert run([record(text='旧\n台账')],duty('对旧 台账负责'))[0]['kind']=='violation'

def test_single_character_is_not_silently_filtered():
    assert run([record(text='及')],duty('经办及审核'))[0]['kind']=='violation'
    assert run([record(text='。')],duty('检查原文。'))[0]['kind']=='violation'

@pytest.mark.parametrize('override',[{'entity':'OTHER'},{'business':'07'},{'variant':'other'},{'mid':'M2'}])
def test_other_scopes_do_not_leak_deleted_words(override):
    issues=run([record(**override),record(text='不会重现的删除内容')],duty())
    assert issues==[]

def test_duty_text_already_struck_is_not_effective_reappearance():
    d=record('position_duty',field='duty',deleted=True)
    assert run([record()],d)==[]

def test_missing_formula_cache_is_not_a_clean_result():
    d=duty();d.fields['duty'].state='formula_no_cache'
    assert run([record()],d)[0]['kind']=='review'

def test_missing_measure_id_explains_the_actual_prerequisite():
    issues=run([record()],duty(mid=''))
    assert len(issues)==1 and issues[0]['kind']=='review'
    assert issues[0]['evidence']['issue_type']=='deletion_measure_missing'
    assert '控制措施编号' in issues[0]['evidence']['unavailable_reason']

def test_fully_struck_matrix_row_is_retained_without_activating_other_checks(tmp_path,pack):
    p=tmp_path/'matrix.xlsx';w=Workbook();s=w.active;s.title='风控矩阵';s.append(['风控矩阵']);s.append(['控制措施编号','控制措施','控制载体','责任主体'])
    s.append(['M1','已删除的流程','已删台账','已删除的岗位'])
    for c in s[3]:c.font=Font(strike=True)
    w.save(p)
    f=FileRecord(p,Path(p.name),'x','xlsx','205H',[],False,'06','default','matrix')
    rows=parse_workbook(f,p,pack['field_aliases'])[0].records
    assert len(rows)==1 and rows[0].record_type=='deleted_matrix'
    assert run(rows,duty())[0]['kind']=='violation'

def test_operator_scope_parameter_is_validated(pack,registry):
    r=next(x for x in pack['rules'] if x['display_code']=='R09')
    r['checks']=[{'check_id':'deleted_text_in_duties','operator':'deleted_text_reappears','operator_version':1,'params':P,'on_unavailable':'review','message':{'violation':'{advice}','review':'{advice}'},'location_policy':'row'}]
    validate_pack(pack,registry)
    r['checks'][0]['params']={**P,'match_scope':'province'}
    with pytest.raises(ConfigError):validate_pack(pack,registry)
