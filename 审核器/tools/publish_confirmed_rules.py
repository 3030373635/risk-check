"""从冻结 v1.7.0 发布已确认的审核确认稿 V1，不覆盖已有发行版。"""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '审核器/src'))

from risk_audit.checks.registry import build_registry
from risk_audit.configuration import RulePackStore
from risk_audit.entities import load_entities
from risk_audit.util import norm_text, sha256_file, write_json

DEPARTMENTS = ['管理部门', '实施部门', '各部门', '需求部门', '用工部门', '归口管理部门', '会签部门', '实物资产', '资产使用', '委托', '项目管理部门', '项目建设部门', '待定', '项目承担部门', '项目需求部门', '资产使用保管部门', '相关部门', '有关部门', '需求提报部门', '委托方财务部门']
POSITIONS = ['某某', '待定', '相关人员', '管理人员', '实施人员', '接待人员', '承接人员', '出差人员', '需求人员', '报账人员', '部门负责人', '需求审核人员', '合同签订人员', '合同经办人员', '合同审核人员', '合同结算人员', '往来管理人员', '使用保管人员', '项目管理人员', '需求提报人', '负责人']


def applicability_configuration():
    """构造六类限定主体配置；无参数，正式单位全名取自当前包内名册。"""
    tail = r'(?:[-/][^-/,;、]+)?'
    departments = r'[-/][^-/,;、]+部' + tail
    entities, _ = load_entities(ROOT / '审核/会计主体清单20260907.xlsx')
    return {
        'restrictions': [
            {'id': 'province_departments', 'patterns': [r'省公司' + departments, r'省公司物资部' + tail, r'国网[^-/;,、]*部' + tail], 'entity_patterns': [r'国网湖南省电力有限公司(?:本部)?']},
            {'id': 'service_center', 'patterns': [r'(?:省)?供服中心(?:[-/][^-/,;、]+){0,2}'], 'entity_patterns': [re.escape(norm_text(entities['20JQ'].name))]},
            {'id': 'material_departments', 'patterns': [r'物资公司' + departments], 'entity_patterns': [re.escape(norm_text(entities['2005'].name))]},
            {'id': 'construction_departments', 'patterns': [r'建设公司' + departments], 'entity_patterns': [re.escape(norm_text(entities['20K3'].name))]},
            {'id': 'beijing_trading', 'patterns': [r'北京电力交易中心(?:[-/][^-/,;、]+){0,2}'], 'entity_patterns': []},
            {'id': 'province_and_material', 'patterns': [r'省公司', r'物资公司'], 'required_patterns': [r'省公司', r'物资公司'], 'entity_patterns': [r'国网湖南省电力有限公司(?:本部)?', re.escape(norm_text(entities['2005'].name))]},
        ],
        # 已提供名册中的完整单位名是已确认身份；不对陌生简称或新单位猜测类别。
        'known_entity_patterns': sorted({re.escape(norm_text(entity.name)) for entity in entities.values()}),
    }


def update_draft(draft):
    """按批准口径更新草稿；draft 为从 v1.7.0 复制的草稿目录。"""
    config = applicability_configuration()
    write_json(draft / 'responsibility_applicability.json', config)
    write_json(draft / 'result_policy.json', {'version': 2, 'technical_issues': 'internal', 'unfinished_is_pass': False})
    catalog = json.loads((draft / 'field_catalog.json').read_text())
    aliases = json.loads((draft / 'field_aliases.json').read_text())
    for field in ('duty', 'position'):
        catalog[field]['record_types'].append('incompatible_position')
    catalog['control_measure']['record_types'].append('position_duty')
    aliases['position_duty']['control_measure'] = ['控制措施']
    aliases['incompatible_position'].update({'duty': ['岗位职责', '职责描述'], 'position': ['岗位名称']})
    aliases['matrix']['applicability_reason'] += ['原因', '是否适用原因', '备注']
    write_json(draft / 'field_catalog.json', catalog)
    write_json(draft / 'field_aliases.json', aliases)
    rules = {}
    for path in (draft / 'rules').glob('*.json'):
        rule = json.loads(path.read_text())
        rule['revision'] += 1
        rule['source_reference'] = '审核确认稿 V1 及 2026-09-15 用户批准执行口径'
        number = int(rule['display_code'][1:])
        for check in rule['checks']:
            check['message'] = {kind: f'【第{number}条】{{advice_v2}}' for kind in ('violation', 'review')}
        rules[path.name] = rule
    versions = {'required_documents': 3, 'field_constraints': 2, 'roles_same_duty': 3, 'reference_exists': 2, 'responsibility_phrase': 7, 'value_mapping': 4, 'ordered_records': 3, 'deleted_text_reappears': 3, 'responsibility_coverage': 8}
    for rule in rules.values():
        for check in rule['checks']:
            if check['operator'] in versions:
                check['operator_version'] = versions[check['operator']]
    duties = rules['R05.json']
    # 新确认稿仅要求具体措施编号，不额外要求岗位职责编号。
    duties['checks'] = [check for check in duties['checks'] if check['check_id'] != 'duty_id_required']
    checks = {check['check_id']: check for check in duties['checks']}
    checks['department_specific']['params']['placeholders'] = DEPARTMENTS + ['无', '-', '/']
    checks['position_specific']['params']['placeholders'] = POSITIONS + ['无', '-', '/']
    checks['person_required']['message']['violation'] = '【第5条】请具体到人员。'
    coverage = rules['R05b.json']['checks'][0]
    coverage['params']['responsibility_applicability'] = config
    # 矩阵泛称允许经确认映射，不再额外要求每个单位名称必须具体。
    rules['R05c.json']['enabled'] = False
    rules['R05c.json']['disabled_reason'] = '确认稿允许矩阵泛称，按实际部门岗位映射核查，不单独按单位泛称出具意见'
    incompatible = deepcopy(duties)
    incompatible.update(rule_id='lists.incompatible_specificity', display_code='R06', title='不相容岗位部门岗位具体性')
    incompatible['scope']['record_type'] = 'incompatible_position'
    incompatible['checks'] = [deepcopy(checks[name]) for name in ('department_specific', 'position_specific')]
    for check in incompatible['checks']:
        check['operator_version'] = 3
        check['message'] = {kind: '【第6条】{advice_v2}' for kind in ('violation', 'review')}
    rules['R06b.json'] = incompatible
    rules['R09.json']['checks'][0]['message'] = {
        'violation': '【第9条】本行引用措施“{reference}”在本主体当前矩阵中不存在、未填写或明确不适用，请核对编号及适用性。',
        'review': '【第9条】待核实：{unavailable_reason}。',
    }
    rules['R10.json']['checks'][0]['params']['attributes'] = ['准确性', '一致性', '真实性', '完整性', '合规性', '有效性', '及时性']
    rules['R10.json']['checks'][0]['message'] = {
        'violation': '【第10条】岗位职责请按照国网标准句式编制。',
        'review': '【第10条】待核实：{unavailable_reason}。',
    }
    rules['R11.json']['checks'][0]['message'] = {
        'violation': '【第11条】经办对应主体责任、审核对应审核责任、审批对应审批责任。',
        'review': '【第11条】待核实：{unavailable_reason}。',
    }
    rules['R13.json']['checks'][0]['message']['violation'] = '【第13条】矩阵中对应的措施和载体已删除，请核对岗位职责描述是否规范。'
    deletion = deepcopy(rules['R13.json'])
    deletion.update(rule_id='deleted_matrix_text.in_incompatible_duties', title='矩阵删除原文与不相容岗位职责核对')
    deletion['scope']['record_type'] = 'incompatible_position'
    rules['R13b.json'] = deletion
    ordering = rules['R12.json']['checks'][0]['message']
    for kind in ordering:
        ordering[kind] = '【第12条】{reason}，本行措施“{measure_id}”与“{related_sheet}”第{related_row}行位置相关，请将同一措施连续排列并按基准措施顺序调整。'
    for name in ('R12b.json', 'R12c.json'):
        rules[name]['checks'][0]['message'] = deepcopy(ordering)
    applicability = deepcopy(rules['R05b.json'])
    applicability.update(rule_id='matrices.responsibility_applicability', display_code='R14', title='责任主体适用性匹配', revision=1)
    applicability['checks'] = [{'check_id': 'responsibility_applicability_match', 'operator': 'responsibility_applicability', 'operator_version': 1, 'params': config, 'on_unavailable': 'review', 'location_policy': 'row', 'message': {'violation': '【第14条】请核实适用性匹配情况。', 'review': '【第14条】待核实：{unavailable_reason}。'}}]
    rules['R14.json'] = applicability
    for name, rule in rules.items():
        write_json(draft / 'rules' / name, rule)
    manifest = json.loads((draft / 'manifest.json').read_text())
    manifest.update(version='1.8.0', engine_compatibility='>=1.8.0,<2.0.0', audit_as_of='2026-09-15', title='风控矩阵及三清单审核确认稿V1及批准执行口径')
    source = ROOT / '审核器/规则来源/审核确认稿V1.docx'
    if source.is_file():
        manifest['source_document_sha256'] = sha256_file(source)
    write_json(draft / 'manifest.json', manifest)


def main():
    """发布新规则；命令行 --activate 控制是否同时激活，不覆盖既有冻结发行版。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--activate', action='store_true')
    args = parser.parse_args()
    store = RulePackStore(ROOT / '审核器/rulepacks', build_registry())
    draft = store.create_draft('confirmed-v1-1.8.0', '1.7.0')
    update_draft(draft)
    store.validate(draft)
    release = store.publish(draft.name, '1.8.0')
    if args.activate:
        store.activate('1.8.0')
    print(json.dumps({'release': str(release), 'activated': args.activate}, ensure_ascii=False))


if __name__ == '__main__':
    main()
