"""验证确认稿第14条按整组责任主体判定适用范围。"""
from copy import deepcopy
import importlib

import pytest

from risk_audit.checks.registry import CheckContext
from risk_audit.models import Entity, FieldValue, Record


NAMES = {
    'province': '国网湖南省电力有限公司',
    'county': '国网湖南省电力有限公司凤凰县供电分公司',
    'city': '国网湖南省电力有限公司湘西供电分公司本部',
    'service': '国网湖南省电力有限公司供电服务中心',
    'material': '国网湖南省电力有限公司物资公司',
    'construction': '国网湖南省电力有限公司建设公司',
    'unknown': '尚未确认归属的中心',
}
PARAMS = {
    'restrictions': [
        {'id': 'province_departments', 'patterns': [r'省公司[-/][^-/,;、]+部(?:[-/][^-/,;、]+)?', r'省公司物资部(?:[-/][^-/,;、]+)?', r'国网[^-/;,、]+部(?:[-/][^-/,;、]+)?'], 'entity_patterns': [r'国网湖南省电力有限公司']},
        {'id': 'service_center', 'patterns': [r'(?:省)?供服中心(?:[-/][^-/,;、]+){0,2}'], 'entity_patterns': [r'国网湖南省电力有限公司供电服务中心']},
        {'id': 'material_departments', 'patterns': [r'物资公司[-/][^-/,;、]+部(?:[-/][^-/,;、]+)?'], 'entity_patterns': [r'国网湖南省电力有限公司物资公司']},
        {'id': 'construction_departments', 'patterns': [r'建设公司[-/][^-/,;、]+部(?:[-/][^-/,;、]+)?'], 'entity_patterns': [r'国网湖南省电力有限公司建设公司']},
        {'id': 'beijing_trading', 'patterns': [r'北京电力交易中心(?:[-/][^-/,;、]+){0,2}'], 'entity_patterns': []},
        {'id': 'province_and_material', 'patterns': [r'省公司', r'物资公司'], 'required_patterns': [r'省公司', r'物资公司'], 'entity_patterns': [r'国网湖南省电力有限公司', r'国网湖南省电力有限公司物资公司']},
    ],
    'known_entity_patterns': [r'国网湖南省电力有限公司.+供电分公司(?:本部)?'],
}
COVERAGE_PARAMS = {'generic_unit_names': ['省公司', '物资公司', '建设公司', '供服中心'],
                   'confirmed_aliases': {}, 'responsibility_applicability': PARAMS}


def operators():
    """取得被测模块；无参数。"""
    return importlib.import_module('risk_audit.checks.applicability_v180')


def context(text, entity='county', applicability='适用'):
    """构造真实审核上下文。

    参数 text 为责任主体原文，entity 为名册代码，applicability 为材料填写结论。
    """
    fields = {key: FieldValue(value, value, cell) for key, value, cell in [
        ('measure_id', 'M1', 'A3'), ('responsibility', text, 'F3'),
        ('applicability', applicability, 'H3')]}
    row = Record('matrix', entity, '06', 'default', 'matrix.xlsx', '矩阵', 3, fields, 'm1')
    entities = {code: Entity(code, name) for code, name in NAMES.items()}
    return CheckContext([row], [], [row], entities, {}, {'_applicability_entities': NAMES}, [], [])


@pytest.mark.parametrize('text,entity,expected', [
    ('省公司-财务部-主任', 'province', True),
    ('省公司物资部', 'province', True),
    ('省公司-物资部', 'county', False),
    ('国网财务部', 'province', True),
    ('国网财务部', 'city', False),
    ('供服中心-财务部-主任', 'service', True),
    ('省供服中心', 'county', False),
    ('物资公司-财务部-主任', 'material', True),
    ('物资公司-财务部', 'province', False),
    ('建设公司-财务部-主任', 'construction', True),
    ('建设公司-财务部', 'county', False),
    ('北京电力交易中心', 'province', False),
    ('北京电力交易中心-财务部-主任', 'unknown', False),
    ('省公司、物资公司', 'province', True),
    ('省公司、物资公司', 'material', True),
    ('省公司、物资公司', 'county', False),
])
def test_whole_group_restrictions_confirm_only_approved_entity_classes(text, entity, expected):
    """参数 text、entity、expected 分别是原文、主体代码及独立推导的预期适用结论。"""
    ctx = context(text, entity)
    result, evidence = operators().responsibility_applicability(ctx.records[0], ctx.resources, PARAMS)
    assert result is expected
    assert evidence['restriction_ids']
    assert evidence['responsibility_text'] == text


@pytest.mark.parametrize('separator', ['；', ';', '\n', '\r\n', '，', ',', '、'])
def test_group_separators_preserve_unit_department_position_slashes(separator):
    """参数 separator 是责任主体之间的分隔号；斜杠属于单位部门岗位结构。"""
    ctx = context('省公司/财务部/主任' + separator + '省公司/物资部/专责')
    result, evidence = operators().responsibility_applicability(ctx.records[0], ctx.resources, PARAMS)
    assert result is False
    assert evidence['responsibility_items'] == ['省公司/财务部/主任', '省公司/物资部/专责']


@pytest.mark.parametrize('text', [
    '省公司-财务部-主任；国网湖南省电力有限公司凤凰县供电分公司-财务部-主任',
    '国网湖南省电力有限公司凤凰县供电分公司-财务部-主任；物资公司-财务部-主任',
    '省公司、物资公司、国网湖南省电力有限公司凤凰县供电分公司',
])
def test_mixed_confirmed_local_and_other_responsibilities_do_not_limit_to_one_fragment(text):
    """参数 text 为含本单位及其他单位的完整并列责任主体，不能仅取其中限定片段。"""
    ctx = context(text)
    result, evidence = operators().responsibility_applicability(ctx.records[0], ctx.resources, PARAMS)
    assert result is True
    assert evidence['restriction_ids'] == []


@pytest.mark.parametrize('text', [
    '省公司-财务部-主任；未知公司-财务部-主任',
    '省公司-财务部-主任；待确认',
    '省公司-财务部-主任，另有其他单位承担责任',
    '省公司-财务部-', '仅省公司-财务部-主任',
    '省公司财务部负责办理业务', '各级单位-财务部-主任', '',
])
def test_unknown_or_incomplete_group_remains_unconfirmed(text):
    """参数 text 为未知或不完整整组原文；不得因其中存在已知片段自动通过。"""
    ctx = context(text)
    result, evidence = operators().responsibility_applicability(ctx.records[0], ctx.resources, PARAMS)
    assert result is None
    assert evidence['unavailable_reason']


def test_entity_membership_requires_full_roster_name():
    """无参数；缺失名册及未知单位归属必须待核实，县公司不能视为省本级。"""
    ctx = context('省公司-财务部-主任')
    op = operators().responsibility_applicability
    assert op(ctx.records[0], ctx.resources, PARAMS)[0] is False
    assert op(ctx.records[0], {}, PARAMS)[0] is None
    unknown = context('省公司-财务部-主任', 'unknown')
    assert op(unknown.records[0], unknown.resources, PARAMS)[0] is None


def test_resources_supply_shared_configuration_without_mutation():
    """无参数；复用资源配置而不改变名册、材料或配置。"""
    ctx = context('省公司-财务部-主任')
    resources = {**ctx.resources, 'responsibility_applicability': deepcopy(PARAMS)}
    before = deepcopy(resources)
    assert operators().responsibility_applicability(ctx.records[0], resources, {})[0] is False
    assert resources == before
    assert ctx.records[0].value('applicability') == '适用'


@pytest.mark.parametrize('entity,text,answer', [
    ('county', '省公司-财务部-主任', '是'),
    ('county', '省公司-财务部-主任', '适用'),
    ('province', '省公司-财务部-主任', '否'),
    ('province', '省公司-财务部-主任', '不适用：没有业务'),
    ('county', '北京电力交易中心', '适用'),
])
def test_r14_publishes_only_explicit_applicability_conflicts(entity, text, answer):
    """参数 entity、text、answer 是主体、责任主体原文和明确填写的适用答案。"""
    ctx = context(text, entity, answer)
    issues = operators().responsibility_applicability_check(ctx, PARAMS)
    assert len(issues) == 1
    assert issues[0]['record'] is ctx.records[0]
    assert issues[0]['kind'] == 'violation'
    assert issues[0]['evidence']['issue_type'] == 'responsibility_applicability_conflict'


@pytest.mark.parametrize('entity,answer', [('county', '否'), ('county', '不适用'), ('province', '是'), ('province', '适用')])
def test_matching_explicit_answer_passes_r14(entity, answer):
    """参数 entity、answer 为名册主体和与限制范围一致的明确答案。"""
    assert operators().responsibility_applicability_check(context('省公司-财务部-主任', entity, answer), PARAMS) == []


@pytest.mark.parametrize('answer', ['', '修改责任主体', '可能适用', '是，但不适用', '否，但仍适用'])
def test_r14_does_not_approve_unknown_or_conflicting_written_answers(answer):
    """参数 answer 为尚无明确结论或自相矛盾的适用答案。"""
    issues = operators().responsibility_applicability_check(context('省公司-财务部-主任', applicability=answer), PARAMS)
    assert issues and issues[0]['kind'] == 'limitation'
    assert issues[0]['evidence']['issue_type'] == 'responsibility_applicability_unresolved'


@pytest.mark.parametrize('field', ['responsibility', 'applicability'])
def test_missing_formula_cache_never_produces_confirmed_r14_result(field):
    """参数 field 为缓存缺失的字段名，读取不足应形成检查限制。"""
    ctx = context('省公司-财务部-主任')
    ctx.records[0].fields[field].state = 'formula_no_cache'
    issues = operators().responsibility_applicability_check(ctx, PARAMS)
    assert issues and issues[0]['kind'] == 'limitation'


def test_r14_context_roster_overrides_stale_resource_identity():
    """无参数；上下文名册确认县公司，旧资源中的省本级身份不得覆盖它。"""
    ctx = context('省公司-财务部-主任')
    ctx.resources = {'_applicability_entities': {'county': NAMES['province']}}
    issues = operators().responsibility_applicability_check(ctx, PARAMS)
    assert issues and issues[0]['kind'] == 'violation'
    assert ctx.resources['_applicability_entities']['county'] == NAMES['province']


def test_coverage_skips_only_confirmed_foreign_restriction():
    """无参数；县公司不承担省公司部门限定措施时不报告岗位责任清单缺失。"""
    assert operators().responsibility_coverage_v8(context('省公司-财务部-主任'), COVERAGE_PARAMS) == []


def test_coverage_checks_confirmed_local_restriction_even_when_material_says_no():
    """无参数；明确本单位适用但错误填否时仍核对责任覆盖，原始材料不可修改。"""
    ctx = context('省公司-财务部-主任', 'province', '否')
    issues = operators().responsibility_coverage_v8(ctx, COVERAGE_PARAMS)
    assert any(issue['evidence']['issue_type'] == 'duty_record_missing' for issue in issues)
    assert all(issue['record'] is ctx.records[0] for issue in issues)
    assert ctx.records[0].value('applicability') == '否'


def test_coverage_unknown_responsibility_with_no_answer_stays_unfinished():
    """无参数；未知责任归属即使填否也必须留下检查限制，不能自动跳过通过。"""
    issues = operators().responsibility_coverage_v8(context('未知公司-财务部-主任', applicability='否'), COVERAGE_PARAMS)
    assert any(issue['kind'] == 'limitation' and issue['evidence']['issue_type'] == 'responsibility_applicability_unresolved' for issue in issues)


def test_coverage_keeps_existing_coverage_for_mixed_local_group():
    """无参数；整组包含本单位的责任仍进入现有跨表责任覆盖。"""
    text = '省公司-财务部-主任；' + NAMES['county'] + '-财务部-主任'
    issues = operators().responsibility_coverage_v8(context(text), COVERAGE_PARAMS)
    assert any(issue['evidence']['issue_type'] == 'duty_record_missing' for issue in issues)


def test_ambiguous_overlapping_restrictions_remain_unconfirmed():
    """无参数；同一整组命中互斥配置范围时不得随列表顺序得出结论。"""
    params = deepcopy(PARAMS)
    params['restrictions'].append({'id': 'conflicting', 'patterns': [r'省公司[-/]财务部[-/]主任'], 'entity_patterns': [NAMES['county']]})
    ctx = context('省公司-财务部-主任')
    assert operators().responsibility_applicability(ctx.records[0], ctx.resources, params)[0] is None


@pytest.mark.parametrize('rule', [
    {'id': 'missing_scope', 'patterns': [r'省公司-财务部-主任']},
    {'id': 'bad_scope', 'patterns': [r'省公司-财务部-主任'], 'entity_patterns': NAMES['province']},
    {'id': 'bad_pattern', 'patterns': r'省公司-财务部-主任', 'entity_patterns': []},
    {'id': 'bad_regex', 'patterns': ['['], 'entity_patterns': []},
])
def test_invalid_restriction_config_cannot_be_treated_as_universal_exclusion(rule):
    """参数 rule 为缺失范围或类型、正则不合法的限定配置，不能自动排除本单位。"""
    ctx = context('省公司-财务部-主任')
    result, evidence = operators().responsibility_applicability(ctx.records[0], ctx.resources, {'restrictions': [rule]})
    assert result is None
    assert evidence['unavailable_reason']


def test_coverage_requires_unresolved_evidence_when_responsibility_source_missing():
    """无参数；责任栏目未读取且材料填否时仍应记录未完成，不能漏掉责任来源限制。"""
    ctx = context('', applicability='否')
    del ctx.records[0].fields['responsibility']
    issues = operators().responsibility_coverage_v8(ctx, COVERAGE_PARAMS)
    assert any(issue['kind'] == 'limitation' and issue['evidence']['field'] == 'responsibility' for issue in issues)


@pytest.mark.parametrize('text,aliases,expected', [
    ('县公司-财务部-主任', {'县公司': {'entity_code': 'county', 'confirmed': True}}, True),
    ('县公司-财务部-主任；省公司-财务部-主任', {'县公司': {'entity_code': 'county', 'confirmed': True}}, True),
    ('县公司-财务部-主任；未知公司-财务部-主任', {'县公司': {'entity_code': 'county', 'confirmed': True}}, None),
    ('县公司-财务部-主任', {'县公司': {'entity_code': 'county', 'confirmed': False}}, None),
    ('县公司-财务部-主任', {'县公司': {'entity_code': 'absent', 'confirmed': True}}, None),
    ('县公司-财务部-主任', {'县公司': {'entity_code': 'county', 'confirmed': True}, '县 公司': {'entity_code': 'city', 'confirmed': True}}, None),
    ('县公司二号-财务部-主任', {'县公司': {'entity_code': 'county', 'confirmed': True}}, None),
])
def test_confirmed_aliases_require_unique_exact_unit_identity(text, aliases, expected):
    """参数 text 为整组原文、aliases 为已确认简称资源、expected 为主体身份预期结论。"""
    ctx = context(text)
    ctx.resources['entity_aliases'] = aliases
    assert operators().responsibility_applicability(ctx.records[0], ctx.resources, PARAMS)[0] is expected


@pytest.mark.parametrize('text', [
    NAMES['county'] + '-财务部-主任',
    '省公司-财务部-主任；' + NAMES['county'] + '-财务部-主任',
])
@pytest.mark.parametrize('answer', ['否', '不适用：本单位没有该业务'])
def test_r14_does_not_force_applicability_for_ordinary_local_or_mixed_groups(text, answer):
    """参数 text 是非六类限定的完整本地或混合组，answer 为材料原有明确不适用答案。"""
    ctx = context(text, applicability=answer)
    result, evidence = operators().responsibility_applicability(ctx.records[0], ctx.resources, PARAMS)
    assert result is True
    assert evidence['restriction_ids'] == []
    assert operators().responsibility_applicability_check(ctx, PARAMS) == []
    assert operators().responsibility_coverage_v8(ctx, COVERAGE_PARAMS) == []
    assert ctx.records[0].value('applicability') == answer


@pytest.mark.parametrize('text', ['省公司', '物资公司', '省公司；省公司', '物资公司；物资公司'])
def test_combination_restriction_requires_each_required_actor(text):
    """参数 text 只含组合规则的一个主体类别，不得命中必须并列的组合范围。"""
    ctx = context(text)
    result, evidence = operators().responsibility_applicability(ctx.records[0], ctx.resources, PARAMS)
    assert result is None
    assert evidence['restriction_ids'] == []


def test_required_patterns_use_generic_configuration_instead_of_specific_rule_id():
    """无参数；新增的通用组合规则必须要求各必需模式出现，不能依赖省公司物资规则 id。"""
    params = {'restrictions': [{'id': 'generic_combination', 'patterns': [r'主体甲', r'主体乙'],
                               'required_patterns': [r'主体甲', r'主体乙'], 'entity_patterns': []}]}
    ctx = context('主体甲')
    op = operators().responsibility_applicability
    assert op(ctx.records[0], ctx.resources, params)[0] is None
    ctx.records[0].fields['responsibility'].current = '主体甲；主体乙'
    assert op(ctx.records[0], ctx.resources, params)[0] is False


@pytest.mark.parametrize('required', [r'省公司', [123], ['[']])
def test_invalid_required_patterns_are_unconfirmed(required):
    """参数 required 为类型或正则无效的必需组合配置，错误配置不得自动排除本单位。"""
    params = deepcopy(PARAMS)
    params['restrictions'][-1]['required_patterns'] = required
    ctx = context('省公司、物资公司')
    assert operators().responsibility_applicability(ctx.records[0], ctx.resources, params)[0] is None


@pytest.mark.parametrize('use_alias', [False, True])
@pytest.mark.parametrize('suffix', ['', '-财务部-主任', '/财务部/主任'])
def test_roster_identity_precedes_generic_guowang_department_pattern(use_alias, suffix):
    """参数 use_alias 表示使用已确认简称，suffix 是完整部门岗位后缀；真实独立主体不可视作省公司部门。"""
    full_name = '国网湖南省电力公司长沙培训服务部'
    alias = '国网长沙培训服务部'
    ctx = context((alias if use_alias else full_name) + suffix, 'training', '否')
    ctx.entities['training'] = Entity('training', full_name)
    ctx.resources = {'_applicability_entities': {**NAMES, 'training': full_name},
                     'entity_aliases': {alias: {'entity_code': 'training', 'confirmed': True}}}
    params = deepcopy(PARAMS)
    params['known_entity_patterns'].append(full_name)
    result, evidence = operators().responsibility_applicability(ctx.records[0], ctx.resources, params)
    assert result is True
    assert evidence['restriction_ids'] == []
    assert operators().responsibility_applicability_check(ctx, params) == []


@pytest.mark.parametrize('separator', ['-', '/'])
def test_department_head_title_is_not_mistaken_for_responsibility_action(separator):
    """参数 separator 为单位部门岗位分隔号；部门负责人是完整岗位名称而非负责动作。"""
    ctx = context(separator.join(['省公司', '财务部', '部门负责人']))
    result, evidence = operators().responsibility_applicability(ctx.records[0], ctx.resources, PARAMS)
    assert result is False
    assert evidence['restriction_ids'] == ['province_departments']
    assert operators().responsibility_applicability_check(ctx, PARAMS)[0]['kind'] == 'violation'
