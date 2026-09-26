from copy import deepcopy
from pathlib import Path
import pytest
from openpyxl import Workbook

from risk_audit.configuration.loader import load_pack
from risk_audit.models import FileRecord, FieldValue
from risk_audit.readers.excel import parse_workbook
from risk_audit.util import sha256_file, norm_text
from risk_audit.responsibility_v170 import responsibility_phrase_v6, value_mapping_v3, parse_v2
from risk_audit.responsibility import responsibility_phrase_v5, value_mapping_v2
from risk_audit.applicability import interpret_record
from test_rule_quality_v160 import context, MAPPING, ATTRS
from test_semantic_v140 import matrix

ROOT = Path(__file__).resolve().parents[1]
COUNTY = '国网湖南省电力有限公司凤凰县供电分公司'
PARAMS = {'attributes': [*ATTRS['attributes'], '匹配性', '规范性']}


@pytest.fixture
def pack():
    return load_pack(ROOT / 'rulepacks/drafts/capability-v1.7.0')


@pytest.mark.parametrize('text', [
    '供应商对报告的准确性负有主体责任，本岗位负责转交材料。',
    '其他部门对报告的准确性负有主体责任，本岗位负责转交材料。',
    '对报告的准确性负有主体责任，但本岗位不承担上述责任。',
    '对报告的准确性负有主体责任。本岗位不承担主体责任。',
])
def test_other_actor_and_later_denial_do_not_pass(text):
    for operator, params in [(responsibility_phrase_v6, PARAMS), (value_mapping_v3, MAPPING)]:
        issues = operator(context(text, '经办'), params)
        assert issues and issues[0]['kind'] == 'violation'
        assert issues[0]['evidence']['issue_type'] in {'responsibility_other_actor', 'responsibility_self_contradiction'}


@pytest.mark.parametrize('text', [
    '本岗位对报告的准确性负有审核责任。',
    '供应商对报告的准确性负有主体责任，本岗位对资料完整性负有审核责任。',
    '对供应商提供资料的准确性负有审核责任。',
    '本岗位不承担审批责任，对报告的准确性负有审核责任。',
    '对报告准确性负有审核责任，但本岗位不承担审批责任。',
    '参照“供应商对准确性负有主体责任”的说明，本岗位对资料完整性负有审核责任。',
    '参照“本岗位不承担上述责任”的历史说明，本岗位对资料完整性负有审核责任。',
    '监督其他部门承担审批责任，对合同真实性承担审核责任。',
])
def test_explicit_current_responsibility_is_preserved(text):
    assert not responsibility_phrase_v6(context(text), PARAMS)
    assert not value_mapping_v3(context(text), MAPPING)


def test_matching_object_and_nominal_requirement():
    text = '负责按业票审核批次发起付款申请，准确匹配授权审批流程。对匹配准确性负有主体责任。'
    assert responsibility_phrase_v5(context(text, '经办'), PARAMS)
    assert not responsibility_phrase_v6(context(text, '经办'), PARAMS)
    assert responsibility_phrase_v6(context('对准确性负有主体责任。', '经办'), PARAMS)
    text = '负责对分包管理要求落实情况负有审核责任。'
    assert value_mapping_v2(context(text), MAPPING)
    assert not value_mapping_v3(context(text), MAPPING)
    assert responsibility_phrase_v6(context(text), PARAMS)[0]['evidence']['issue_type'] == 'responsibility_quality_missing'
    assert value_mapping_v3(context('本岗位要求供应商落实审核责任。'), MAPPING)


@pytest.mark.parametrize('text', [
    '对附件完整性负有审核在责任。',
    '对做好报销审核。按照财务业务流程要求，做好报销审核、检查材料完备审核责任。',
    '对需求论证项目情况主体责任',
])
def test_clear_incomplete_expressions_remain_material_problems(text):
    for fn, params in [(responsibility_phrase_v6, PARAMS), (value_mapping_v3, MAPPING)]:
        result = fn(context(text), params)
        assert result[0]['kind'] == 'violation'
        assert result[0]['evidence']['issue_type'] == 'responsibility_expression_malformed'


def test_unknown_grammar_stays_internal_and_cache_is_not_mutated():
    from risk_audit.issue_routing import route_issue
    text = '对合同的准确性承担未知句式审核责任。'
    issue = responsibility_phrase_v6(context(text), PARAMS)[0]
    assert route_issue(issue, {'check_id': 'broad_responsibility_pattern'}, {'version': 1})['kind'] == 'limitation'
    old = deepcopy(parse_v2('对合同准确性负审核责任。', tuple(PARAMS['attributes'])))
    responsibility_phrase_v6(context('对合同准确性负审核责任。'), PARAMS)
    assert old == parse_v2('对合同准确性负审核责任。', tuple(PARAMS['attributes']))


@pytest.mark.parametrize('text', [
    '对本单位采抄管理，对采集结果真实性、准确性，对采集异常结果进行核实；对异常数据进行补抄或拟合负有主体责任。',
    '对及时办理履约单据，确保项目进度与开票进度一致；配合物资管理部门进行长期挂账款项的原因核实，出具相关证明材料负有主体责任。',
    '对确认领料需求单的准确性、真实性。负有审核责任。',
])
def test_complete_parallel_or_adjacent_responsibility_fragments(text):
    assert not responsibility_phrase_v6(context(text), PARAMS)
    facts = parse_v2(text, tuple(PARAMS['attributes']))
    assert facts['normalized_text'] == norm_text(text)
    assert all('··' not in s['text'] for s in facts['statements'])


def test_explicit_quality_after_predicate_is_linked_but_actions_and_denials_are_not():
    assert not responsibility_phrase_v6(context('对合同条款负有审核责任，确保条款合规。'), PARAMS)
    assert responsibility_phrase_v6(context('对合同条款负有审核责任，确保合同不准确。'), PARAMS)
    assert responsibility_phrase_v6(context('对合同条款负有审核责任。其他部门确保条款合规。'), PARAMS)
    assert responsibility_phrase_v6(context('对合同条款负有审核责任，按照规定办理。'), PARAMS)


@pytest.mark.parametrize('text,passes', [
    ('“合同签订时明确知识产权权属”的要求（如审核合同中权属条款、专利技术归属约定）负有审核责任，确保“转化条款合规、收益不流失。', True),
    ('对合同条款负有审核责任，确保条款合规、收益不流失。', True),
    ('对合同条款负有审核责任，确保合同不准确、资料真实。', False),
    ('对合同条款负有审核责任，确保条款合规性尚未确认。', False),
    ('对合同条款负有审核责任，确保合同是否真实、准确。', False),
])
def test_postposed_quality_negation_has_a_scope(text, passes):
    assert (not responsibility_phrase_v6(context(text), PARAMS)) is passes


@pytest.mark.parametrize('text,category', [
    ('对做好会议过程管控负有主体责任。①做好会议筹备。②规范会议组织。③规范会议报销。', 'responsibility_quality_missing'),
    ('对物业管理费用的数量、质量具有审核责任评价，对计费周期、收费标准一致性复核。', 'responsibility_quality_missing'),
    ('对加强差旅报销审核力度，对同一时段连续出差公杂费、伙食补助费负有审核责任，避免重复计算。', 'responsibility_quality_missing'),
    ('对规范管理咨询费列支间接费用；最后一笔经费支出不得低于合同总额的10%负有主体责任。', 'responsibility_quality_missing'),
    ('对车辆费用准确性负进行复核；对保险费归口负有审核责任。', 'responsibility_expression_malformed'),
    ('盘点过程中要复核设备资产管理责任落实情况，及时更新资产使用保管人、运维单位等信息。', 'responsibility_assertion_missing'),
    ('建设单位安全费归口管理部门负责人对安全费管控付全面责任，把关安全费支付的及时性。', 'responsibility_expression_malformed'),
])
def test_actions_elsewhere_do_not_replace_explicit_quality(text, category):
    result = responsibility_phrase_v6(context(text), PARAMS)
    assert result[0]['kind'] == 'violation' and result[0]['evidence']['issue_type'] == category


@pytest.mark.parametrize('text', [
    '对合同真实性不承担责任；对台账承担审核责任。',
    '对合同准确性。其他部门承担审核责任。',
    '对合同准确性。检查文件。负有审核责任。',
    '对合同真实性由供应商保证；本岗位对资料保管负有审核责任。',
])
def test_independent_or_denied_clauses_cannot_supply_quality(text):
    assert responsibility_phrase_v6(context(text), PARAMS)


@pytest.mark.parametrize('text', [
    '对赴异地挂职发起出差申请，按照规定探亲在途期间出差申请选择原单位成本中心，挂职期间出差申请选择挂职单位成本中心负有主体责任。',
    '对报销发票、入库单、采购订单物品对应关系进行负有初审主体责任，确认购物清单通过税务防伪系统开具。',
    '对部门员工出差内容具有审核责任，重点审查出差申请单人员、地点、事由等信息。',
    '对虚列成本费用、套取资金私设“小金库”的违规行为承担全部责任，并依规接受追责处理。',
])
def test_user_confirmed_high_frequency_problems_remain(text):
    assert responsibility_phrase_v6(context(text), PARAMS)[0]['evidence']['issue_type'] == 'responsibility_quality_missing'


def test_unnamed_column_is_read_even_when_measure_description_is_ambiguous(tmp_path, pack):
    w = Workbook(); s = w.active; s.title = '风控矩阵'
    s.append(['风控矩阵']); s.append(['控制措施编号', '控制措施', '控制措施', '责任主体', None])
    s.append(['M1', '旧内容', '新内容', COUNTY + '-财务部-主任', '适用'])
    s.append(['M2', '旧内容2', '新内容2', COUNTY + '-财务部-主任', '不适用'])
    path = tmp_path / 'unnamed.xlsx'; w.save(path)
    f = FileRecord(path, Path('unnamed.xlsx'), sha256_file(path), 'xlsx', '2051', ['标准全称:' + COUNTY], False, '09', 'default', 'matrix')
    f._parser_policy = pack['parser_policy']; f._semantic_lexicon = pack['semantic_lexicon']
    parsed = parse_workbook(f, path, pack['field_aliases'])[0]
    assert 'control_measure' not in parsed.columns
    assert parsed.columns['applicability'] == 5
    assert [r.value('applicability') for r in parsed.records] == ['适用', '不适用']


def test_unnamed_generic_yes_no_and_named_test_results_not_used(tmp_path, pack):
    _, s = matrix(tmp_path, pack, [None], [['是'], ['否']])
    assert 'applicability' not in s.columns
    _, s = matrix(tmp_path, pack, ['验收结果'], [['适用'], ['不适用']])
    assert 'applicability' not in s.columns
    _, s = matrix(tmp_path, pack, [None], [['适用'], ['不适用'], ['合格']])
    assert 'applicability' not in s.columns


def test_change_notes_keep_blank_and_unknown_answers(tmp_path, pack):
    _, s = matrix(tmp_path, pack, ['矩阵变化内容说明'], [['不适用'], ['不适用'], [None], ['修改控制措施。']])
    assert s.columns['applicability'] == 5
    assert [r.value('applicability') for r in s.records] == ['不适用', '不适用', '', '修改控制措施。']


def test_explicit_unit_column_still_overrides_unqualified_template_column(tmp_path, pack):
    _, s = matrix(tmp_path, pack, ['是否适用', '凤凰分公司适用情况'], [['适用', '不适用'], ['适用', '适用']])
    assert s.columns['applicability'] == 6


def test_adjacent_reason_does_not_require_two_explicit_anchors(tmp_path, pack):
    _, s = matrix(tmp_path, pack, ['凤凰公司测试情况', None],
                  [['县公司无审计部门，故删除相关措施', '不适用'], ['修改控制措施', '适用']], unit=COUNTY, code='2051')
    assert s.columns['applicability'] == 6
    assert s.columns['applicability_reason'] == 5
    assert s.records[0].value('applicability_reason') == '县公司无审计部门，故删除相关措施'


def test_explicit_answers_pair_with_adjacent_unit_notes(tmp_path, pack):
    f, s = matrix(tmp_path, pack, ['凤凰公司测试情况', None],
                  [['市公司集中管控，县公司不适用', '不适用'],
                   ['市公司集中管控，县公司不适用', '不适用'],
                   ['修改控制措施', '适用'], [None, '适用']], unit=COUNTY, code='2051')
    assert s.columns['applicability'] == 6
    assert s.columns['applicability_reason'] == 5
    assert s.records[0].value('applicability_reason') == '市公司集中管控，县公司不适用'


def test_conflicting_answers_and_foreign_hidden_column_are_not_used(tmp_path, pack):
    _, s = matrix(tmp_path, pack, [None, None], [['适用', '不适用'], ['不适用', '适用']])
    assert 'applicability' not in s.columns
    _, s = matrix(tmp_path, pack, ['德源设计院'], [['不适用'], ['不适用']], hidden=['E'])
    assert 'applicability' not in s.columns
    _, s = matrix(tmp_path, pack, ['凤凰分公司测试情况', None], [['不适用', '适用'], ['不适用', '适用']])
    assert 'applicability' not in s.columns


@pytest.mark.parametrize('hidden', [(), ('E',)])
def test_hidden_unique_required_and_visible_duplicate(tmp_path, pack, hidden):
    _, s = matrix(tmp_path, pack, [None], [['适用'], ['不适用']], hidden=hidden)
    assert s.columns['applicability'] == 5
    _, s = matrix(tmp_path, pack, [None, None], [['适用', '适用'], ['不适用', '不适用']], hidden=['E'])
    assert s.columns['applicability'] == 6


@pytest.mark.parametrize('foreign_body,expected', [(False, True), (True, False)])
def test_wrong_owner_corrected_using_scoped_short_names(tmp_path, pack, foreign_body, expected):
    w = Workbook(); s = w.active; s.title = '风控矩阵'
    s.append(['风控矩阵']); s.append(['控制措施编号', '控制措施', '保靖公司责任主体', '控制载体'])
    s.append(['M1', '措施', '凤凰县公司-财务部-主任', '报告'])
    s.append(['M2', '措施', '凤凰公司-财务部-专责', '报告'])
    s.append(['M3', '措施', '古丈公司-财务部-专责' if foreign_body else '此条不适用县、支公司', '报告'])
    path = tmp_path / 'owner.xlsx'; w.save(path)
    f = FileRecord(path, Path('owner.xlsx'), sha256_file(path), 'xlsx', '2051', ['标准全称:' + COUNTY], False, '05', 'default', 'matrix')
    f._parser_policy = pack['parser_policy']; f._semantic_lexicon = pack['semantic_lexicon']
    parsed = parse_workbook(f, path, pack['field_aliases'])[0]
    assert ('responsibility' in parsed.columns) == expected
    choice = next(c for c in f.preservation['column_selections'] if c['field'] == 'responsibility')
    assert choice['candidates'][0]['header'] == '保靖公司责任主体'


@pytest.mark.parametrize('text,name,decision', [
    ('市公司集中管控，县公司不适用', COUNTY, 'not_applicable'),
    ('市公司集中管控，县公司不适用', '湘西自治州德能电力建设有限公司凤凰分公司', None),
    ('市公司集中管控，县公司不适用', '国网湖南省电力有限公司湘西供电分公司本部', None),
    ('可能市公司集中管控，县公司不适用', COUNTY, None),
    ('市公司集中管控，县公司不适用，但本单位仍适用', COUNTY, None),
    ('直接引用', COUNTY, None),
    ('暂无此业务，参照省公司', COUNTY, None),
])
def test_county_sentence_requires_confirmed_scope(pack, text, name, decision):
    row = context('x').records[0]; row.fields['applicability'] = FieldValue(text, text, 'H3')
    resources = {**pack, '_applicability_entities': {row.entity_code: name}}
    result = interpret_record(row, resources)
    assert result.decision == decision
    if decision: assert result.reason == '市公司集中管控'


def test_capability_report_separates_material_and_program_limits():
    from risk_audit.capability_report import build_capability_report
    from risk_audit.models import ParsedSheet
    row = context('对合同准确性承担未知句式审核责任。').records[0]
    row.fields['person_names'] = FieldValue('', '', 'C3')
    row.fields['applicability'] = FieldValue('修改责任主体', '修改责任主体', 'H3')
    f = FileRecord(Path('x.xlsx'), Path('x.xlsx'), 'hash', 'xlsx', '205H', [], False, '06', 'default', 'three_lists',
                   [ParsedSheet('岗位职责', 'position_duty', [2], {}, {}, 9, 3, [row])])
    common = {'entity_code': '205H', 'business_code': '06', 'variant_id': 'default',
              'file_path': 'x.xlsx', 'sheet': '岗位职责', 'row': 3}
    limits = []
    for field, issue, cause, check in [
        ('person_names', 'person_missing', 'dependency_incomplete', 'handler_reviewer_overlap'),
        ('duty', 'responsibility_structure_unresolved', 'parser_incomplete', 'broad_responsibility_pattern'),
        ('applicability', 'applicability_meaning_unresolved', 'interpretation_incomplete', 'applicability_values'),
        ('applicability', 'coverage_applicability_unknown', 'dependency_incomplete', 'matrix_duty_coverage'),
        ('responsibility', 'responsibility_unit_unavailable', 'field_source_unavailable', 'responsibility_unit_specific'),
    ]:
        limits.append({**common, 'display_code': 'R05', 'check_id': check,
                       'evidence': {'field': field, 'issue_type': issue, 'cause_type': cause}})
    findings = [{**common, 'check_id': 'person_required', 'finding_key': 'f1', 'message': '未填写姓名', 'severity': 'violation'}]
    report = build_capability_report([f], limits, findings, {'205H': {'businesses': [{'business_code': '06', 'variant_id': 'default'}]}})
    assert report['unfinished_checks'] == 5 and report['source_groups'] == 4 and report['affected_rows'] == 1
    assert report['by_category'] == {'material_precondition': 1, 'grammar_unsupported': 1,
                                     'interpretation_unsupported': 2, 'source_needs_investigation': 1}
    assert all(not d['auto_pass'] for d in report['details'])
    assert report['details'][0]['cell'] == 'C3'
    assert report['details'][0]['existing_material_findings'][0]['finding_key'] == 'f1'


@pytest.mark.parametrize('state,category,kind', [
    ('unselected', 'responsibility_unit_unavailable', 'limitation'),
    ('formula_no_cache', 'responsibility_unit_unavailable', 'limitation'),
    ('empty', 'responsibility_empty', 'violation'),
])
def test_coverage_does_not_publish_parser_failure_as_missing_material(pack, state, category, kind):
    from dataclasses import replace
    from risk_audit.models import Record
    from risk_audit.issue_routing import route_issue
    from risk_audit.checks.coverage_v170 import responsibility_coverage_v7
    ctx = context('对合同准确性负有审核责任。')
    duty = ctx.records[0]
    duty.fields.update({k: FieldValue(v, v, 'A3') for k, v in {'measure_id': 'M1', 'department': '财务部', 'position': '主任'}.items()})
    fields = {k: FieldValue(v, v, 'A3') for k, v in {'measure_id': 'M1', 'applicability': '适用'}.items()}
    if state != 'unselected': fields['responsibility'] = FieldValue('', '', 'X3', state=state)
    matrix_row = Record('matrix', '205H', '06', 'default', 'matrix.xlsx', '矩阵', 3, fields, 'm')
    ctx = replace(ctx, records=[matrix_row], all_records=[matrix_row, duty], resources=pack)
    params = next(c['params'] for r in pack['rules'] if r['display_code'] == 'R05' for c in r['checks'] if c['check_id'] == 'matrix_duty_coverage')
    result = responsibility_coverage_v7(ctx, params)
    assert len(result) == 1 and result[0]['evidence']['issue_type'] == category
    assert route_issue(result[0], {'check_id': 'matrix_duty_coverage'}, {'version': 1})['kind'] == kind
