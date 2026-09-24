"""0916 更新规则的行为验收，参数均使用真实材料行。"""
import copy
import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.loader import load_pack
from risk_audit.configuration.validator import ConfigError, validate_pack
from risk_audit.engine import run_engine
from risk_audit.models import FieldValue, FileRecord, ParsedSheet
from risk_audit.opinion_text import advice_v2
from risk_audit.util import sha256_file
from test_confirmed_v180 import record, context, pair, overlaps, PHRASE, MAPPING, DEPARTMENT, POSITION


ROOT = Path(__file__).resolve().parents[1]

DEPARTMENT_0916_3 = {
    "field": "department",
    "placeholders": ["管理部门", "各部门", "项目管理部门", "资产使用保管部门"],
    "placeholder_patterns": ["管理部门|各部门|项目管理部门|资产使用保管部门"],
    "separators": [],
    "exact_exceptions": [],
}
POSITION_0916_3 = {
    "field": "position",
    "placeholders": ["人员", "管理人员", "资产使用保管人员", "勘查人员"],
    "placeholder_patterns": ["人员|管理人员|资产使用保管人员|勘查人员"],
    "separators": ["、", "，", ",", "；", ";"],
    "exact_exceptions": ["查勘人员"],
}


def run(name, rows, params, matrices=()):
    """调用新版能力；name 为函数名，rows/params/matrices 为清单、配置和当前矩阵。"""
    module = import_module('risk_audit.checks.confirmed_v180')
    assert hasattr(module, name), f'{name} 尚未实现'
    return getattr(module, name)(context(rows, matrices), params)


@pytest.mark.parametrize("department", ["财务管理部门", "省级项目管理部门办公室"])
def test_0916_3_department_keywords_are_matched_inside_longer_names(department):
    """department 为含泛称关键词的长名称；不能再被旧版整格匹配漏掉。"""
    issues = run("field_constraints_v5", [record(department=department)], DEPARTMENT_0916_3)

    assert [item["evidence"]["issue_type"] for item in issues] == ["field_not_concrete"]


@pytest.mark.parametrize("department", ["项目管理部门-建设部", "资产使用保管部门-变检公司"])
def test_0916_3_concrete_department_suffix_remains_accepted(department):
    """department 为泛称加具体后缀；新版包含匹配不能破坏明确部门例外。"""
    assert not run("field_constraints_v5", [record(department=department)], DEPARTMENT_0916_3)


@pytest.mark.parametrize("position", ["采购人员专责", "项目建设人员（兼岗）", "现场查勘人员"])
def test_0916_3_position_keywords_are_matched_inside_longer_names(position):
    """position 为含岗位泛称关键词的长名称；包含匹配应要求继续明确岗位。"""
    issues = run("field_constraints_v5", [record(position=position)], POSITION_0916_3)

    assert [item["evidence"]["issue_type"] for item in issues] == ["field_not_concrete"]


def test_0916_3_only_exact_survey_position_is_exempted():
    """精确的查勘人员按新文档放行，增加前后缀后不扩大例外范围。"""
    assert not run("field_constraints_v5", [record(position="查勘人员")], POSITION_0916_3)
    issues = run("field_constraints_v5", [record(position="现场查勘人员")], POSITION_0916_3)

    assert [item["evidence"]["issue_type"] for item in issues] == ["field_not_concrete"]


def test_0916_6_rulepack_enables_retained_alignment_rules():
    """1.9.6规则包必须保留第16、17条关联能力，并停止不相容岗位拆分检查。"""
    pack = load_pack(ROOT / "rulepacks/releases/1.9.6")
    rule = next(item for item in pack["rules"] if item["rule_id"] == "duties.completeness")
    checks = {item["check_id"]: item for item in rule["checks"]}

    department_check = checks["department_specific"]
    assert department_check["operator_version"] == 5
    department_issues = run(
        "field_constraints_v5",
        [record(department="财务管理部门")],
        department_check["params"],
    )
    position_check = checks["position_specific"]
    assert position_check["operator_version"] == 5
    position_issues = run(
        "field_constraints_v5",
        [record(position="查勘人员"), record(row=4, position="现场查勘人员")],
        position_check["params"],
    )
    person_check = checks["person_required"]
    assert person_check["operator_version"] == 4
    person_issues = run(
        "field_constraints_v5",
        [record(person_names="张三"), record(row=4, person_names="张三等")],
        person_check["params"],
    )

    assert [item["evidence"]["issue_type"] for item in department_issues] == ["field_not_concrete"]
    assert [item["record"].row for item in position_issues] == [4]
    assert [item["record"].row for item in person_issues] == [4]
    system_rule = next(item for item in pack['rules'] if item['rule_id'] == 'systems.matrix_changes')
    applicability_rule = next(item for item in pack['rules'] if item['rule_id'] == 'matrices.measure_applicability')
    responsibility_rule = next(item for item in pack['rules'] if item['rule_id'] == 'matrices.responsibility_department')
    incompatible = next(item for item in pack['rules'] if item['rule_id'] == 'lists.incompatible_specificity')
    position = next(item for item in incompatible['checks'] if item['check_id'] == 'position_specific')
    assert system_rule['enabled'] and applicability_rule['enabled'] and responsibility_rule['enabled']
    assert position['params']['separators'] == []
    assert pack['manifest']['source_document_sha256'] == sha256_file(ROOT / '规则来源/风控矩阵0916-6.docx')


def test_0916_6_person_keyword_is_matched_inside_full_name():
    """人员姓名中包含“待定”时应提示具体到人员，不能只匹配整格“待定”。"""
    pack = load_pack(ROOT / "rulepacks/releases/1.9.6")
    rule = next(item for item in pack["rules"] if item["rule_id"] == "duties.completeness")
    person_check = next(item for item in rule["checks"] if item["check_id"] == "person_required")

    issues = run(
        "field_constraints_v4",
        [record(person_names="张三待定")],
        person_check["params"],
    )

    assert [item["evidence"]["issue_type"] for item in issues] == ["field_not_concrete"]


def test_0916_6_rule_5_only_checks_department_position_and_person():
    """第5条不得继续输出职责、编号缺失或矩阵责任覆盖意见。"""
    pack = copy.deepcopy(load_pack(ROOT / "rulepacks/releases/1.9.6"))
    pack["rules"] = [item for item in pack["rules"] if item["rule_id"] in {
        "duties.completeness", "duties.coverage",
    }]
    matrices = [record(
        "matrix",
        row=3,
        measure_id="业务-1.名称-控制措施01",
        applicability="适用",
        responsibility="财务部",
    )]
    duties = [record(
        row=10,
        measure_id="",
        duty="",
        department="财务部",
        position="核算专责",
        person_names="张三",
    )]
    sheets = [
        ParsedSheet("风控矩阵", "matrix", [2], {}, {}, 10, 3, matrices),
        ParsedSheet("岗位职责清单", "position_duty", [2], {}, {}, 10, 3, duties),
    ]
    file = FileRecord(Path("材料.xlsx"), Path("材料.xlsx"), "hash", "xlsx", "205H", [], False,
                      "06", "default", "matrix", sheets)

    findings, _ = run_engine(
        pack,
        build_registry(),
        [file],
        {"205H": type("Entity", (), {"name": "测试单位"})()},
        {},
    )

    assert findings == []


def test_0916_3_rulepack_rejects_invalid_exact_exceptions():
    """精确例外必须是非空字符串数组，避免错误配置静默扩大放行范围。"""
    pack = load_pack(ROOT / "rulepacks/releases/1.9.1")
    rule = next(item for item in pack["rules"] if item["rule_id"] == "duties.completeness")
    check = next(item for item in rule["checks"] if item["check_id"] == "position_specific")
    check["params"]["exact_exceptions"] = ["   "]

    with pytest.raises(ConfigError):
        validate_pack(pack, build_registry())


def test_0916_3_rulepack_runs_r05_and_r06b_through_engine():
    """真实引擎必须对岗位清单及不相容岗位A/B/C执行相同的新口径。"""
    pack = copy.deepcopy(load_pack(ROOT / "rulepacks/releases/1.9.1"))
    pack["rules"] = [item for item in pack["rules"] if item["rule_id"] in {
        "duties.completeness", "lists.incompatible_specificity"
    }]
    duty_rows = [
        record(row=3, department="财务管理部门", position="查勘人员", person_names="张三", duty="职责", measure_id="M1"),
        record(row=4, department="财务部", position="现场查勘人员", person_names="李四", duty="职责", measure_id="M2"),
    ]
    incompatible_rows = [
        record("incompatible_position", row=5, department="财务管理部门", position_a="查勘人员", position_b="查勘人员", position_c="查勘人员"),
        record("incompatible_position", row=6, department="财务部", position_a="查勘人员", position_b="现场查勘人员", position_c="采购人员专责"),
    ]
    sheets = [
        ParsedSheet("岗位责任清单", "position_duty", [2], {}, {}, 10, 3, duty_rows),
        ParsedSheet("不相容岗位清单", "incompatible_position", [2], {}, {}, 10, 3, incompatible_rows),
    ]
    file = FileRecord(Path("三清单.xlsx"), Path("三清单.xlsx"), "hash", "xlsx", "205H", [], False,
                      "06", "default", "three_lists", sheets)

    findings, statuses = run_engine(
        pack,
        build_registry(),
        [file],
        {"205H": type("Entity", (), {"name": "测试单位"})()},
        {},
    )

    assert all(status.status == "executed" for status in statuses)
    assert {(item.rule_id, item.row, item.evidence["field"]) for item in findings} == {
        ("duties.completeness", 3, "department"),
        ("duties.completeness", 4, "position"),
        ("lists.incompatible_specificity", 5, "department"),
        ("lists.incompatible_specificity", 6, "position_b"),
        ("lists.incompatible_specificity", 6, "position_c"),
    }
    assert pack["manifest"]["source_document_sha256"] == sha256_file(ROOT / "规则来源/风控矩阵0916-3.docx")


def test_0916_3_publish_script_verifies_existing_release():
    """验证旧发布产物时应支持只读模式，不能把当前激活版本回退到1.9.1。"""
    active_version = json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))["version"]
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/publish_rules_0916_3.py"), "--verify-only"],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verified"] is True
    assert json.loads(result.stdout)["activated"] is False
    assert json.loads((ROOT / "rulepacks/active.json").read_text(encoding="utf-8"))["version"] == active_version


def test_0916_4_publish_script_verifies_existing_release():
    """0916-4发布产物必须可重建验证，且只读验证不改变激活状态。"""
    active_version = json.loads((ROOT / 'rulepacks/active.json').read_text(encoding='utf-8'))['version']
    result = subprocess.run(
        [sys.executable, str(ROOT / 'tools/publish_rules_0916_4.py'), '--verify-only'],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['verified'] is True
    assert json.loads(result.stdout)['activated'] is False
    assert json.loads((ROOT / 'rulepacks/active.json').read_text(encoding='utf-8'))['version'] == active_version


def test_0916_5_publish_script_verifies_existing_release():
    """1.9.5 编号修复产物必须可重建验证，且只读验证不改变激活版本。"""
    active_version = json.loads((ROOT / 'rulepacks/active.json').read_text(encoding='utf-8'))['version']
    result = subprocess.run(
        [sys.executable, str(ROOT / 'tools/publish_rule_numbers_195.py'), '--verify-only'],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['verified'] is True
    assert json.loads(result.stdout)['activated'] is False
    assert json.loads((ROOT / 'rulepacks/active.json').read_text(encoding='utf-8'))['version'] == active_version


def test_0916_6_publish_script_verifies_existing_release():
    """1.9.6规则包必须可重建验证，且只读验证不改变激活版本。"""
    active_version = json.loads((ROOT / 'rulepacks/active.json').read_text(encoding='utf-8'))['version']
    result = subprocess.run(
        [sys.executable, str(ROOT / 'tools/publish_rules_0916_6.py'), '--verify-only'],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['verified'] is True
    assert json.loads(result.stdout)['activated'] is False
    assert json.loads((ROOT / 'rulepacks/active.json').read_text(encoding='utf-8'))['version'] == active_version


@pytest.mark.parametrize('changed', ['department', 'person_names'])
def test_separation_requires_equal_department_and_complete_names(changed):
    """changed 指定不同字段；只重合部分姓名或跨部门不提示同职责冲突。"""
    rows = pair()
    for row in rows:
        row.fields['department'] = row.fields['position'].__class__('财务部', '财务部', f'C{row.row}')
    rows[1].fields[changed].current = '建设部' if changed == 'department' else '张三、李四'
    assert not overlaps(run('roles_same_duty_v4', rows, {}, [record('matrix', measure_id='M1')]))


@pytest.mark.parametrize('duty', ['负主体责任', '负责采购'])
def test_standard_phrase_requires_quality_attribute(duty):
    """duty 为无责任属性的原文，新版必须提示标准句式。"""
    issues = run('responsibility_phrase_v8', [record(duty=duty)], PHRASE)
    assert [item['evidence']['issue_type'] for item in issues] == ['responsibility_phrase_missing']


def test_standard_phrase_accepts_named_position_subject():
    """具体岗位名称不得破坏第10条责任句式骨架的识别。"""
    duty = '价格管理人员对智慧共享财务平台中发电机组状态的调整的准确性、及时性负有主体责任。'

    assert not run('responsibility_phrase_v8', [record(duty=duty)], PHRASE)


def test_matching_role_among_multiple_types_is_accepted():
    """满足本行经办对应主体责任时，多种责任不再额外提示；无参数。"""
    assert not run('value_mapping_v5', [record(role='经办', duty='对资料真实性负主体责任和审核责任')], MAPPING)


def test_incompatibility_position_c_is_checked():
    """岗位C与A/B遵守同一具体性标准；无参数。"""
    row = record('incompatible_position', department='财务部', position_a='核算专责', position_b='审核专责', position_c='经办人员')
    issues = run('field_constraints_v4', [row], POSITION)
    assert [item['evidence']['field'] for item in issues] == ['position_c']


def test_duplicate_rows_report_all_duplicates_by_six_columns():
    """完整六字段相同时提示重复，允许多人员一岗并区分主体；无参数。"""
    values = dict(measure_id='M1', duty='对资料真实性负主体责任', department='财务部', position='核算专责', person_names='张三、李四', role='经办')
    rows = [record(row=number, **values) for number in (3, 4, 5)]
    rows[2].entity_code = 'OTHER'
    issues = run('duplicate_duties', rows, {})
    assert {item['record'].row for item in issues} == {3, 4}


def test_duplicate_rows_accept_confirmed_equivalent_duty_wording():
    """已确认等价的连接词、系统轨迹名称及责任句式应生成同一职责比较结果。"""
    common = dict(measure_id='M1', department='财务部', position='核算专责', person_names='张三', role='审核')
    rows = [
        record(row=3, duty='对员工清册信息、工资发放明细表与系统变更审批轨迹的监督检查准确性、及时性负有审核责任', **common),
        record(row=4, duty='对员工清册信息、工资发放明细表、系统审批轨迹的监督检查准确性、及时性负审核责任', **common),
    ]

    issues = run('duplicate_duties', rows, {})

    assert {item['record'].row for item in issues} == {3, 4}


@pytest.mark.parametrize('different_duty', [
    '对员工清册信息、工资发放明细表、系统审批轨迹的监督检查准确性、及时性不负审核责任',
    '对员工清册信息、工资发放明细表、系统审批结果的监督检查准确性、及时性负审核责任',
    '对员工清册信息、工资发放明细表、系统审批轨迹的监督检查准确性、及时性负主体责任',
])
def test_duplicate_rows_reject_material_semantic_differences(different_duty):
    """否定、职责对象或责任类型变化时，即使文本高度相似也不能判为重复。"""
    common = dict(measure_id='M1', department='财务部', position='核算专责', person_names='张三', role='审核')
    rows = [
        record(row=3, duty='对员工清册信息、工资发放明细表、系统审批轨迹的监督检查准确性、及时性负审核责任', **common),
        record(row=4, duty=different_duty, **common),
    ]

    assert not run('duplicate_duties', rows, {})


def test_duplicate_advice_lists_all_counterpart_rows():
    """重复意见应列出同组其他文件、工作表和行号，不能只输出概括文案。"""
    values = dict(measure_id='M1', duty='对资料准确性负审核责任', department='财务部',
                  position='核算专责', person_names='张三', role='审核')
    issues = run('duplicate_duties', [record(row=number, **values) for number in (3, 4, 5)], {})
    first = next(item for item in issues if item['record'].row == 3)

    assert advice_v2('duplicate_duties', 'violation', first['evidence']) == (
        '本行与《材料.xlsx》“position_duty”第4、5行内容重复，重复项保留一个即可。'
    )


def test_active_duplicate_rule_renders_counterpart_rows_through_engine():
    """激活规则包必须在最终审核意见中渲染重复位置，不能只在内部证据中保存。"""
    active = json.loads((ROOT / 'rulepacks/active.json').read_text(encoding='utf-8'))
    pack = copy.deepcopy(load_pack(ROOT / 'rulepacks/releases' / active['version']))
    pack['rules'] = [item for item in pack['rules'] if item['rule_id'] == 'duties.duplicate_rows']
    values = dict(measure_id='M1', duty='对资料准确性负审核责任', department='财务部',
                  position='核算专责', person_names='张三', role='审核')
    rows = [record(row=number, **values) for number in (3, 4)]
    sheet = ParsedSheet('岗位职责清单', 'position_duty', [2], {}, {}, 10, 3, rows)
    file = FileRecord(Path('三清单.xlsx'), Path('三清单.xlsx'), 'hash', 'xlsx', '205H', [], False,
                      '06', 'default', 'three_lists', [sheet])

    findings, _ = run_engine(pack, build_registry(), [file],
                             {'205H': type('Entity', (), {'name': '测试单位'})()}, {})

    assert {item.row: item.message for item in findings} == {
        3: '【第15条】本行与《材料.xlsx》“position_duty”第4行内容重复，重复项保留一个即可。',
        4: '【第15条】本行与《材料.xlsx》“position_duty”第3行内容重复，重复项保留一个即可。',
    }


def test_delivery_launcher_accepts_current_source_and_rulepack():
    """交付入口必须接受当前1.9.19源码，并能校验新版业务模板目录。"""
    result = subprocess.run(
        [sys.executable, str(ROOT.parent / 'run_audit.py'), '--doctor'],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['version'] == '1.9.19'
    assert payload['rulepack'] == '1.9.19'


def test_missing_duties_match_numeric_measure_parts():
    """业务范围内以控制点及措施数字对应，避免名称改写造成缺件；无参数。"""
    matrices = [record('matrix', row=3, measure_id='薪酬业务-2.工资管理-控制措施02'), record('matrix', row=4, measure_id='薪酬业务-12.疗养管理-控制措施05')]
    duties = [record(measure_id='薪酬业务-02.工资总额计划管理-控制措施2')]
    issues = run('missing_duty_measures', matrices, {}, duties)
    assert [item['record'].row for item in issues] == [4]


def test_measure_key_ignores_wording_and_zero_padding():
    """相同控制点及措施数字必须生成同一业务键；无参数。"""
    from risk_audit.checks.confirmed_v180 import measure_numbers

    assert measure_numbers('薪酬业务-02.工资总额计划管理-控制措施2') == (2, 2)
    assert measure_numbers('薪酬业务-2.其他文字-控制措施02') == (2, 2)


def test_measure_number_extraction_uses_one_dirty_tolerant_rule():
    """所有编号入口必须共用一套规则，跳过脏字符且不误取业务名中的电压数字。"""
    from risk_audit.checks.confirmed_v180 import measure_numbers
    from risk_audit.util import measure_id_key

    samples = {
        '科技项目管理业务-----14项目验收质量-控制措施01': (14, 1),
        '科技项目管理业务——————@@@@14项目验收质量-控制措施05': (14, 5),
        '35kV科技项目管理业务——————@@@@14项目验收质量-控制措施05': (14, 5),
        '设备（资产）管理-5.资产调拨办理02': (5, 2),
    }
    for value, expected in samples.items():
        assert measure_id_key(value) == expected
        assert measure_numbers(value) == expected


def test_system_rule_changes_require_matching_measure_and_system_name():
    """矩阵控制系统出现红字时，对应系统规则必须存在且系统名称一致；无参数。"""
    matrix = record('matrix', measure_id='业务-2.名称-控制措施02', control_system='ERP系统')
    matrix.fields['control_system'].red_spans = [{'text': 'ERP系统', 'start': 0, 'end': 5}]
    mismatched = record('system_rule', measure_id='业务-02.其他名称-控制措施2', system_name='财务管控系统')

    issues = run('system_rule_changes_v1', [matrix], {}, [mismatched])

    assert [item['evidence']['issue_type'] for item in issues] == ['system_rule_change_mismatch']
    matching = record('system_rule', measure_id='业务-02.其他名称-控制措施2', system_name='ERP系统')
    assert not run('system_rule_changes_v1', [matrix], {}, [matching])


@pytest.mark.parametrize(('applicability', 'has_duty', 'expected'), [
    ('不适用', False, False), ('适用', False, False), ('适用', True, False), ('不适用', True, True),
])
def test_applicability_alignment_skips_absent_carrier_and_checks_existing_measures(applicability, has_duty, expected):
    """applicability 为矩阵结论，has_duty 为岗位清单载体存在性；缺少载体时跳过。"""
    matrix = record('matrix', measure_id='业务-2.名称-控制措施02', applicability=applicability)
    duties = [record(measure_id='业务-02.其他名称-控制措施2')] if has_duty else []

    issues = run('measure_applicability_alignment_v1', [matrix], {}, duties)

    assert bool(issues) is expected
    if expected:
        assert issues[0]['evidence']['issue_type'] == 'measure_applicability_mismatch'


@pytest.mark.parametrize('state', ['source_missing', 'source_unavailable'])
def test_applicability_alignment_skips_unavailable_applicability_field(state):
    """适用性字段缺失或不可读取时由字段检查统一提示，不得逐行生成第16条；state 为来源状态。"""
    matrix = record('matrix', measure_id='业务-2.名称-控制措施02')
    if state == 'source_unavailable':
        matrix.fields['applicability'] = FieldValue(None, '', 'B3', formula='=A3', state='formula_no_cache')

    assert not run('measure_applicability_alignment_v1', [matrix], {}, [])


def test_responsibility_department_alignment_uses_confirmed_keywords_and_synonyms():
    """矩阵责任主体关键词必须在同措施岗位部门中有已配置的同义对应；无参数。"""
    params = {'keyword_mappings': {'财务': ['财务'], '发展': ['发展', '规划']}}
    matrix = record('matrix', measure_id='业务-2.名称-控制措施02', responsibility='财务部、发展部')
    duties = [record(row=4, measure_id='业务-02.其他名称-控制措施2', department='规划部')]

    issues = run('responsibility_department_alignment_v1', [matrix], params, duties)

    assert issues[0]['evidence']['issue_type'] == 'responsibility_department_mismatch'
    assert issues[0]['evidence']['missing_keywords'] == ['财务']
    duties.append(record(row=5, measure_id='业务-2.其他名称-控制措施02', department='财务资产部'))
    assert not run('responsibility_department_alignment_v1', [matrix], params, duties)


def test_active_rulepack_restores_rule_16_but_keeps_rule_17_removed():
    """激活规则包应恢复适用性联动，同时继续保持第17条删除结果。"""
    active = json.loads((ROOT / 'rulepacks/active.json').read_text(encoding='utf-8'))
    pack = copy.deepcopy(load_pack(ROOT / 'rulepacks/releases' / active['version']))
    pack['rules'] = [item for item in pack['rules'] if item['rule_id'] in {
        'matrices.measure_applicability', 'matrices.responsibility_department',
    }]
    matrices = [
        record('matrix', row=3, measure_id='业务-1.名称-控制措施01', applicability='适用'),
        record('matrix', row=4, measure_id='业务-2.名称-控制措施01', applicability='适用', responsibility='财务部'),
        record('matrix', row=5, measure_id='业务-3.名称-控制措施01', applicability='适用', responsibility='发展部门-计划管理人员'),
    ]
    duties = [
        record(row=10, measure_id='业务-2.名称-控制措施01', department='综合部'),
        record(row=11, measure_id='业务-3.名称-控制措施01', department='供电公司-发展策划部'),
    ]
    sheets = [
        ParsedSheet('风控矩阵', 'matrix', [2], {}, {}, 10, 3, matrices),
        ParsedSheet('岗位职责清单', 'position_duty', [2], {}, {}, 10, 3, duties),
    ]
    file = FileRecord(Path('材料.xlsx'), Path('材料.xlsx'), 'hash', 'xlsx', '205H', [], False,
                      '06', 'default', 'matrix', sheets)

    findings, _ = run_engine(
        pack,
        build_registry(),
        [file],
        {'205H': type('Entity', (), {'name': '测试单位'})()},
        {},
    )

    assert [(item.row, item.message) for item in findings] == [
        (3, '【第16条】请结合业务实际再次核实适用性。'),
    ]


def test_order_validation_uses_same_numeric_order_as_preprocessing():
    """已按数字排好时名称差异不产生倒序意见；无参数。"""
    rows = [record(row=3, measure_id='业务-2.Z名称-控制措施02'), record(row=4, measure_id='业务-2.A名称-控制措施05')]
    assert not run('ordered_records_v4', rows, {'field': 'measure_id'})


def test_noncontinuous_order_points_only_to_matching_identifier_rows():
    """不连续编号只落在重复编号行，并互相引用真实对应行；无参数。"""
    rows = [record('system_rule', row=3, measure_id='M1'), record('system_rule', row=4, measure_id='M2'), record('system_rule', row=5, measure_id='M1')]
    issues = run('ordered_records_v4', rows, {'field': 'measure_id'})
    noncontinuous = [item for item in issues if item['evidence']['reason'] == '同一措施记录不连续']

    assert {item['record'].row for item in noncontinuous} == {3, 5}
    assert {item['record'].row: item['evidence']['related_row'] for item in noncontinuous} == {3: 5, 5: 3}
    assert all([location['row'] for location in item['evidence']['problem_locations']] == [3, 5] for item in noncontinuous)


def test_active_rulepack_does_not_output_order_findings():
    """当前激活规则只对岗位清单实际排序，不得输出已删除的排序检查意见；无参数。"""
    active = json.loads((ROOT / 'rulepacks/active.json').read_text(encoding='utf-8'))
    pack = copy.deepcopy(load_pack(ROOT / 'rulepacks/releases' / active['version']))
    order_rules = [item for item in pack['rules'] if item['rule_id'] in {
        'lists.order', 'lists.order_system', 'lists.order_incompatible'
    }]
    pack['rules'] = order_rules
    rows = [record('system_rule', row=3, measure_id='M1'), record('system_rule', row=4, measure_id='M2'), record('system_rule', row=5, measure_id='M1')]
    sheet = ParsedSheet('系统控制规则清单', 'system_rule', [2], {'measure_id': 7}, {}, 10, 3, rows)
    file = FileRecord(Path('三清单.xlsx'), Path('三清单.xlsx'), 'hash', 'xlsx', '205H', [], False, '06', 'default', 'three_lists', [sheet])

    findings, _ = run_engine(pack, build_registry(), [file], {'205H': type('Entity', (), {'name': '测试单位'})()}, {})

    assert order_rules
    assert all(not item['enabled'] for item in order_rules)
    assert not findings
