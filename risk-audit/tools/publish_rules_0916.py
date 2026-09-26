"""按 0916 新规则发布并激活 v1.9.0，可复现规则差异。"""
from copy import deepcopy
from pathlib import Path
import json
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'risk-audit/src'))

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.util import norm_text, sha256_file, write_json

DEPARTMENTS = '各级单位、牵头部门、牵头管理部门、项目管理部门、实物资产管理部门、建设管理部门、使用保管部门、专业管理部门、会签部门、归口管理部门、项目承担部门、项目需求部门、用工管理部门、实施部门、食堂经费管理部门、职工教育经费管理部门、职工体检实施部门、各部门、资金发起部门、会议承办部门、接待承办部门、办公用品管理部门、公务车辆管理部门、建设单位'.split('、')
POSITIONS = '采购人员、合同管理人员、收货人员、监督人员、领料人员、管理人员、审批人员、复核人员、审核人员、柜收人员、签订人员、监控人员、装表人员、勘查人员、办理人员、核算人员、受理人员、收费人员、收取人员、结算人员、财务人员、技经人员、规划人员、合规人员、计划人员、保管人员、前期人员、招标人员、需求提报人员、物资上架人员、履约人员、物资调拨人员、经办人员、匹配人员、负责人、项目负责人、出差报销审核人'.split('、')


def update_draft(draft: Path, source: Path) -> None:
    """更新新版规则草稿；draft 为从旧规则复制的目录，source 为新版源文档。"""
    rules = {path.name: json.loads(path.read_text()) for path in (draft / 'rules').glob('*.json')}
    for rule in rules.values():
        rule['revision'] += 1; rule['source_reference'] = '风控矩阵-0916-1.docx 有效正文（排除修订删除）'
    for filename in ('R14.json', 'R13.json', 'R13b.json'):
        rules[filename]['enabled'] = False
        rules[filename]['disabled_reason'] = '0916 正文删除本独立规则；删除内容同步检查已归并第9条'
    rules['R14.json']['disabled_reason'] = '0916 正文已删除第14条，不再按责任主体限定主体适用范围'
    coverage = rules['R05b.json']['checks'][0]
    coverage['operator_version'] = 7; coverage['params'].pop('responsibility_applicability', None)
    # 彻底移除旧责任主体适用限制，防止它继续改变覆盖和不相容清单检查。
    (draft / 'responsibility_applicability.json').unlink()
    for filename in ('R05.json', 'R06b.json'):
        for check in rules[filename]['checks']:
            if check['operator'] != 'field_constraints':
                continue
            check['operator_version'] = 4
            values = DEPARTMENTS if check['params']['field'] == 'department' else POSITIONS if check['params']['field'] == 'position' else []
            check['params']['placeholders'] = list(dict.fromkeys([*check['params']['placeholders'], *values]))
            if check['params']['field'] == 'department':
                generic = '|'.join(re.escape(norm_text(value)) for value in check['params']['placeholders'])
                check['params']['placeholder_patterns'] = [rf'^(?:{generic})(?:[/、,，；;](?:{generic}))*$']
    role_check = rules['R08.json']['checks'][0]
    role_check.update(operator_version=4, check_id='handler_reviewer_overlap_0916')
    for filename, version, text in [('R10.json', 8, '岗位职责请按照国网标准句式编制。'), ('R11.json', 5, '经办对应主体责任、审核对应审核责任、审批对应审批责任。')]:
        check = rules[filename]['checks'][0]; check['operator_version'] = version
        check['message'] = {kind: f'【第{10 if filename == "R10.json" else 11}条】{text}' for kind in ('violation', 'review')}
    deletion = deepcopy(rules['R13.json']['checks'][0]); deletion['check_id'] = 'deleted_text_in_duty_0916'
    deletion['message'] = {'violation': '【第9条】矩阵中对应的措施或载体已删除，请核对岗位职责描述是否规范。', 'review': '【第9条】{unavailable_reason}'}
    rules['R09.json']['checks'].append(deletion)
    incompatible = deepcopy(rules['R13b.json'])
    incompatible.update(rule_id='references.deleted_in_incompatible_duty', display_code='R09', enabled=True, title='矩阵删除内容与不相容岗位职责同步')
    incompatible.pop('disabled_reason', None); incompatible['checks'] = [deepcopy(deletion)]
    rules['R09b.json'] = incompatible
    # 新版改为岗位清单预处理实际排序，旧第12条检查已删除，不再输出意见。
    for filename in ('R12.json', 'R12b.json', 'R12c.json'):
        rules[filename]['enabled'] = False
        rules[filename]['disabled_reason'] = '0916有效新增内容要求对岗位清单实际排序，已删除原第12条排序检查及对外意见'
    duplicate = deepcopy(rules['R05.json'])
    duplicate.update(rule_id='duties.duplicate_rows', display_code='R15', title='岗位职责六字段重复项', revision=1)
    duplicate['checks'] = [{'check_id': 'duplicate_duties', 'operator': 'duplicate_duties', 'operator_version': 1, 'params': {}, 'on_unavailable': 'review', 'location_policy': 'row', 'message': {kind: '【第15条】重复项保留一个即可。' for kind in ('violation', 'review')}}]
    rules['R15.json'] = duplicate
    missing = deepcopy(rules['R05b.json'])
    missing.update(rule_id='matrices.missing_duty_measures', display_code='R15', title='岗位清单未覆盖措施编号', revision=1)
    missing['checks'] = [{'check_id': 'missing_duty_measures', 'operator': 'missing_duty_measures', 'operator_version': 1, 'params': {}, 'on_unavailable': 'review', 'location_policy': 'row', 'message': {kind: '【第15条】请核实适用性。' for kind in ('violation', 'review')}}]
    rules['R15b.json'] = missing
    for filename, rule in rules.items():
        write_json(draft / 'rules' / filename, rule)
    catalog = json.loads((draft / 'field_catalog.json').read_text()); aliases = json.loads((draft / 'field_aliases.json').read_text())
    catalog['position_c'] = deepcopy(catalog['position_b']); aliases['incompatible_position']['position_c'] = ['岗位C', '岗位 C', '岗位c']
    aliases['position_duty']['person_names'] += ['实际执行人姓名', '实际执行人员姓名']
    write_json(draft / 'field_catalog.json', catalog); write_json(draft / 'field_aliases.json', aliases)
    manifest = json.loads((draft / 'manifest.json').read_text())
    manifest.update(version='1.9.0', engine_compatibility='>=1.9.0,<2.0.0', audit_as_of='2026-09-16', title='风控矩阵0916更新规则', source_document_sha256=sha256_file(source))
    write_json(draft / 'manifest.json', manifest)


def main() -> None:
    """发布并激活本次新版规则；无参数，仅在全新版本目录中执行。"""
    source = ROOT / 'risk-audit/规则来源/风控矩阵-0916-1.docx'
    if not source.exists():
        shutil.copy2('/Users/67m/Desktop/风控矩阵-0916-1.docx', source)
    store = RulePackStore(ROOT / 'risk-audit/rulepacks', build_registry())
    draft = store.create_draft('rules-0916-1.9.0', '1.8.0')
    update_draft(draft, source); store.validate(draft)
    release = store.publish(draft.name, '1.9.0'); store.activate('1.9.0')
    print(json.dumps({'release': str(release), 'activated': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
