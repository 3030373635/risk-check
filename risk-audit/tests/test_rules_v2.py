from pathlib import Path
from copy import deepcopy
import pytest
from openpyxl import Workbook

from risk_audit.checks.registry import CheckContext, build_registry
from risk_audit.checks.duty_details import field_constraints, roles_same_duty, responsibility_phrase, duty_signature
from risk_audit.checks.coverage import responsibility_coverage
from risk_audit.configuration.loader import load_pack
from risk_audit.configuration.validator import ConfigError, validate_pack
from risk_audit.engine import run_engine
from risk_audit.models import FieldValue, Record, FileRecord, Entity, ParsedSheet
from risk_audit.readers.excel import parse_workbook, _persons
from risk_audit.opinion_text import advice_v2

ROOT = Path(__file__).resolve().parents[2]
DRAFT = ROOT / 'risk-audit/rulepacks/drafts/v2-120'


def row(role='经办', name='张三', pid=None, duty=None, n=3, **kw):
    data = {'measure_id': 'M1', 'role': role, 'person_names': name, 'position': '核算专责',
            'duty': duty if duty is not None else '对报表的准确性负' + ('审核' if role == '审核' else '主体') + '责任', **kw}
    fields = {k: FieldValue(v, v, f'A{n}') for k, v in data.items()}
    r = Record('position_duty', '205H', '10', 'default', 'duties.xlsx', '岗位责任清单', n, fields, f'r{n}')
    r.person_keys = _persons(name, pid or '')
    return r


def context(rows):
    return CheckContext(rows, [], rows, {'205H': Entity('205H', '信通')}, {}, {}, ['205H'], [{'business_code': '10', 'variant_id': 'default'}])


def params(check_id):
    return next(c['params'] for r in load_pack(DRAFT)['rules'] for c in r['checks'] if c['check_id'] == check_id)


@pytest.mark.parametrize('check,field,text', [
    *[('department_specific', 'department', s) for s in ['管理部门', '各部门', '需求部门', '用工部门', '归口管理部门', '待定']],
    *[('position_specific', 'position', s) for s in ['某某', '待定', '相关人员', '管理人员', '出差人员', '需求人员', '报账人员', '部门负责人']],
    *[('person_required', 'person_names', s) for s in ['', '张三等', '……', '待定', '张三、李四等']],
])
def test_v2_placeholder_examples(check, field, text):
    r = row(**{field: text})
    issues = field_constraints(context([r]), params(check))
    assert any(x['evidence']['issue_type'] == 'field_not_concrete' for x in issues)
    assert '请' in advice_v2(check, issues[0]['kind'], issues[0]['evidence'])


@pytest.mark.parametrize('sep', ['、', '，', '；', ',', ';'])
def test_multiple_positions_require_separate_rows(sep):
    r = row(position='核算专责' + sep + '稽核专责')
    issues = field_constraints(context([r]), params('position_specific'))
    assert [x['evidence']['issue_type'] for x in issues] == ['multiple_positions']
    assert advice_v2('position_specific', 'violation', issues[0]['evidence']) == '请拆分岗位，每个岗位一行，方便后期大表合并。'


def test_concrete_fields_and_multiple_people_are_allowed():
    r = row(department='财务部', position='核算专责', person_names='张三、李四')
    for check in ['department_specific', 'position_specific', 'person_required']:
        assert not field_constraints(context([r]), params(check))


@pytest.mark.parametrize('text', [
    '对报表的准确性负主体责任', '对信息真实性负有审核责任', '对资料完整性承担审批责任',
    '对账表一致性负责任', '对信息\n准确性、完整性\n负有主体责任',
    '对移交协议签订及时性负有审核责任', '对报告编制有效性负主体责任',
    '对预算执行合规性负审核责任', '对项目可研经济性负审核责任',
    '对采购事项的必要性承担审核责任',
])
def test_responsibility_attributes_can_be_alternatives(text):
    assert not responsibility_phrase(context([row(duty=text)]), params('broad_responsibility_pattern'))


@pytest.mark.parametrize('text', ['负责采购', '完成审核', '对采购负主体责任', '承担主体责任', '', '对信息的准确性不负责任', '不对信息的准确性承担责任', '对报表的准确性进行检查。承担主体责任', '对资产属性负审核责任'])
def test_action_only_or_missing_attribute_is_insufficient(text):
    assert responsibility_phrase(context([row(duty=text)]), params('broad_responsibility_pattern'))


def test_same_ids_and_same_duties_flag_only_actual_pair():
    a, b, c = row(pid='E1'), row('审核', pid='E1', n=4), row('审核', name='李四', pid='E2', n=5)
    issues = roles_same_duty(context([a, b, c]), {})
    assert len(issues) == 2 and {x['record'].row for x in issues} == {3, 4}
    assert all(x['kind'] == 'violation' for x in issues)
    assert '同样的岗位职责' in advice_v2('handler_reviewer_overlap', 'violation', issues[0]['evidence'])


def test_same_name_without_ids_requires_confirmation():
    issues = roles_same_duty(context([row(), row('审核', n=4)]), {})
    assert len(issues) == 2 and all(x['kind'] == 'review' for x in issues)
    assert all(advice_v2('handler_reviewer_overlap', x['kind'], x['evidence']).startswith('待核实：') for x in issues)


def test_distinct_ids_resolve_same_name():
    assert not roles_same_duty(context([row(pid='A'), row('审核', pid='B', n=4)]), {})


@pytest.mark.parametrize('duty', ['对台账的准确性负审核责任', '审核报表，对报表的准确性负审核责任'])
def test_different_duties_are_outside_v2_conflict_condition(duty):
    assert not roles_same_duty(context([row(pid='A'), row('审核', pid='A', n=4, duty=duty)]), {})


@pytest.mark.parametrize('field,value', [('entity_code', 'OTHER'), ('business_code', '06'), ('variant_id', 'other')])
def test_same_duty_never_crosses_scope(field, value):
    b = row('审核', n=4, pid='A');setattr(b, field, value)
    assert not roles_same_duty(context([row(pid='A'), b]), {})


def test_different_measure_never_conflicts():
    assert not roles_same_duty(context([row(pid='A'), row('审核', pid='A', n=4, measure_id='M2')]), {})


def test_missing_role_is_reported_but_explicit_responsibility_still_supports_comparison():
    a, b = row(role='', pid='A'), row('审核', n=4, pid='A')
    del a.fields['role']
    issues = roles_same_duty(context([a, b]), {})
    assert sum(x['evidence']['issue_type'] == 'explicit_role_missing' for x in issues) == 1
    assert sum(x['evidence']['issue_type'] == 'same_duty_person_overlap' for x in issues) == 2


def test_invalid_role_does_not_fall_back_to_responsibility():
    issues = roles_same_duty(context([row(role='复核'), row('审核', n=4)]), {})
    assert [x['evidence']['issue_type'] for x in issues] == ['invalid_role']


def test_same_person_missing_duty_cannot_be_treated_as_different_duties():
    issues = roles_same_duty(context([row(pid='A', duty=''), row('审核', pid='A', n=4)]), {})
    assert len(issues) == 2 and all(x['evidence']['issue_type'] == 'separation_duty_missing' for x in issues)


def test_missing_scope_does_not_merge_unidentified_measures():
    issues = roles_same_duty(context([row(measure_id=''), row('审核', n=4, measure_id='')]), {})
    assert len(issues) == 2 and all(x['evidence']['issue_type'] == 'separation_scope_missing' for x in issues)


def test_reader_uses_actual_person_id_column(tmp_path):
    path = tmp_path / '岗位内控责任清单.xlsx'
    w = Workbook();s = w.active;s.title = '岗位内控责任清单'
    s.append(['岗位内控责任清单']);s.append(['控制措施编号', '部门', '岗位名称', '人员姓名', '人员编号', '岗位职责', '角色'])
    s.append(['M1', '财务部', '核算专责', '张三', 'E1', '对报表准确性负主体责任', '经办']);w.save(path)
    f = FileRecord(path, Path(path.name), 'h', 'xlsx', '205H', [], False, '10', 'default', 'three_lists')
    records = parse_workbook(f, path, load_pack(DRAFT)['field_aliases'])[0].records
    assert records[0].person_keys == [{'name': '张三', 'person_id': 'E1', 'identity_reliable': True}]
    assert _persons('张三、李四', 'E1、E2')[0]['person_id'] is None


def test_pack_executes_confirmed_v2_scope_and_reports_unresolved_mapping():
    pack = load_pack(DRAFT);validate_pack(pack, build_registry())
    by_code = {r['display_code']: r for r in pack['rules']}
    assert not by_code['R04']['enabled'] and not by_code['R07']['enabled']
    assert [c['operator'] for c in by_code['R09']['checks']] == ['reference_exists', 'set_subset']
    assert by_code['R13']['checks'][0]['params']['match_scope'] == 'measure'
    assert by_code['R08']['scope']['business_codes']['exclude'] == []
    assert params('matrix_duty_coverage')['unresolved_kind'] == 'review'
    m = row(applicability='是', responsibility='信通-各部门-相关人员');m.record_type = 'matrix'
    d = row(department='财务部')
    ctx = context([m]);ctx.all_records = [m, d]
    issues = responsibility_coverage(ctx, params('matrix_duty_coverage'))
    assert len(issues) == 1 and issues[0]['kind'] == 'review'
    assert advice_v2('matrix_duty_coverage', 'review', issues[0]['evidence']).startswith('待核实：')


@pytest.mark.parametrize('operator,param,value', [
    ('field_constraints', 'placeholder_patterns', ['[']), ('field_constraints', 'separators', '、'),
    ('responsibility_phrase', 'attributes', []), ('responsibility_coverage', 'unresolved_kind', 'pass'),
])
def test_new_config_rejects_invalid_parameters(operator, param, value):
    pack = load_pack(DRAFT)
    check = next(c for r in pack['rules'] for c in r['checks'] if c['operator'] == operator)
    check['params'][param] = value
    with pytest.raises(ConfigError): validate_pack(pack, build_registry())
