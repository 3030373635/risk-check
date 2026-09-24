from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from risk_audit.checks.deleted_content import deleted_text_reappears_v2, numbered_items
from risk_audit.configuration.loader import load_pack
from risk_audit.configuration.validator import validate_pack
from risk_audit.engine import run_engine
from risk_audit.models import FieldValue
from test_deleted_text import P, record, duty, run as legacy_run
from test_engine import minimal_files


def matrix(old='2.项目初设报告。\n3.技术鉴定报告', current='2.拟退役资产技术鉴定表。', **kwargs):
    row = record(text=old, **kwargs)
    field = kwargs.get('field', 'carrier')
    row.fields[field].raw = old + '\n' + current
    row.fields[field].current = current
    return row


def run(matrices, target):
    return deleted_text_reappears_v2(SimpleNamespace(records=[target], all_records=[*matrices, target]), P)


def test_real_technical_01_deleted_list_item_and_malformed_duty():
    source = matrix()
    target = duty('对技术鉴定报告的退役资产技术鉴定组织开展准确性负有审核责任。')
    # Frozen capability v1 still has its old whole-span semantics.
    assert legacy_run([source], target) == []
    issues = run([source], target)
    assert len(issues) == 1 and issues[0]['kind'] == 'violation'
    evidence = issues[0]['evidence']
    assert evidence['deleted_texts'] == ['技术鉴定报告']
    hit = evidence['matches'][0]
    assert hit['extraction'] == 'numbered_item'
    assert source.fields['carrier'].raw[hit['source_start']:hit['source_end']] == '技术鉴定报告'
    assert hit['parent_span']['text'] == '2.项目初设报告。\n3.技术鉴定报告'


def test_real_technical_02_final_retained_report_is_allowed():
    source = matrix('1.可研批复文件或项目建议书；\n2.项目初设报告。\n3.技术鉴定报告', '1.拟退役资产技术鉴定表；\n2.技术鉴定报告。')
    assert run([source], duty('对技术鉴定报告的线上流程准确性负有审批责任。')) == []


@pytest.mark.parametrize('field', ['control_measure', 'carrier'])
def test_final_effective_content_in_either_source_field_is_retained(field):
    source = matrix()
    source.fields[field].current = '对技术鉴定报告负有责任。'
    assert run([source], duty('对技术鉴定报告负责。')) == []


@pytest.mark.parametrize('scope', [{'mid':'M2'}, {'entity':'OTHER'}, {'business':'07'}, {'variant':'other'}])
def test_retention_does_not_leak_across_scope(scope):
    issues = run([matrix(), matrix(current='技术鉴定报告', **scope)], duty('对技术鉴定报告负责。'))
    assert issues[0]['kind'] == 'violation'


def test_partial_retention_still_reports_other_deleted_item():
    issues = run([matrix(current='技术鉴定报告')], duty('对技术鉴定报告及项目初设报告负责。'))
    assert issues[0]['evidence']['deleted_texts'] == ['项目初设报告']


def test_whole_old_list_in_duty_is_allowed_when_all_entries_are_retained_and_renumbered():
    old = '2.甲报告；3.乙报告。'
    assert run([matrix(old, '1.乙报告；2.甲报告。')], duty(old)) == []


def test_conflicting_duplicate_matrices_require_confirmation():
    issues = run([matrix(), matrix(current='技术鉴定报告')], duty('对技术鉴定报告负责。'))
    assert len(issues) == 1 and issues[0]['kind'] == 'review'
    assert issues[0]['evidence']['issue_type'] == 'deletion_current_conflict'


@pytest.mark.parametrize('word', ['及', '计划', '。'])
def test_short_originals_still_checked_unless_retained(word):
    source = record(text=word)
    assert run([source], duty('对' + word + '负责'))[0]['kind'] == 'violation'
    source.fields['control_measure'].current = word
    assert run([source], duty('对' + word + '负责')) == []


def test_no_arbitrary_sentence_or_qualified_name_tokenization():
    assert run([record(text='负责旧台账审核。')], duty('对旧台账负责')) == []
    assert run([matrix('1.技术鉴定报告（含拟退役技术鉴定表）。')], duty('对技术鉴定报告负责')) == []


def test_partial_strike_of_a_list_entry_is_not_a_whole_deleted_item():
    source = matrix('1.技术', '鉴定报告')
    source.fields['carrier'].raw = '1.技术鉴定报告'
    assert run([source], duty('对技术鉴定报告负责')) == []


@pytest.mark.parametrize('old', ['1.甲报告；2.乙报告。', '1.甲报告\n2.乙报告。', '（1）甲报告；（2）乙报告。'])
def test_numbered_boundaries_preserve_original_offsets(old):
    items = numbered_items(old)
    assert [x['text'] for x in items] == ['甲报告', '乙报告']
    assert all(old[x['start']:x['end']] == x['text'] for x in items)


def test_numbered_subclauses_inside_parentheses_are_not_separate_carrier_items():
    old = '1.鉴定报告（含：1.鉴定表；2.附件）；2.审批表。'
    assert [x['text'] for x in numbered_items(old)] == ['鉴定报告（含：1.鉴定表；2.附件）', '审批表']
    assert run([matrix(old)], duty('对附件负责')) == []


def test_struck_target_and_no_synonyms_stay_clean():
    assert run([matrix()], record('position_duty', text='技术鉴定报告', field='duty', deleted=True)) == []
    assert run([matrix('1.验收单。')], duty('对验收记录负责')) == []


def engine(matrices=None, target=None, *, enabled=True, reverse=False, custom=False, version=2, overlay=False):
    from risk_audit.checks.registry import build_registry
    p = load_pack(Path(__file__).resolve().parents[1] / 'rulepacks/releases/1.2.1')
    p['manifest']['status'] = 'draft'
    p['rules'] = [r for r in p['rules'] if r['display_code'] in {'R09', 'R13'}]
    r13 = next(r for r in p['rules'] if r['display_code'] == 'R13')
    r13['enabled'] = enabled
    r13['checks'][0]['operator_version'] = version
    if overlay:
        p['overlays'].append({'priority':99, 'scope':{'business_code':'06'}, 'path':r13['rule_id']+'.enabled', 'value':False})
    if custom:
        r09 = next(r for r in p['rules'] if r['display_code'] == 'R09')
        next(c for c in r09['checks'] if c['check_id'] == 'carrier_subset')['message']['review'] = '自定义：{advice_v2}（保留模板）'
    if reverse: p['rules'].reverse()
    registry = build_registry()
    validate_pack(p, registry)
    rows = [*(matrices or [matrix()]), target or duty('对技术鉴定报告的准确性负有审核责任。')]
    return run_engine(p, registry, minimal_files(rows), {'205H':SimpleNamespace(name='信通')}, {})


@pytest.mark.parametrize('reverse', [False, True])
def test_r13_supersedes_only_same_reference_with_audit_trail_and_correct_counts(reverse):
    findings, statuses = engine(reverse=reverse)
    assert [f.display_code for f in findings] == ['R13']
    evidence = findings[0].evidence
    assert evidence['superseded_carrier_checks'][0]['references'] == ['技术鉴定报告']
    assert '请核实是否漏填' in evidence['superseded_carrier_checks'][0]['message']
    assert '最终保留内容中也未列出' in findings[0].message
    assert sum(s.findings for s in statuses) == len(findings)


def test_partial_r09_keeps_other_reference_and_custom_template():
    findings, statuses = engine(target=duty('对技术鉴定报告及验收单的准确性负有审核责任。'), custom=True)
    r09 = next(f for f in findings if f.display_code == 'R09')
    assert r09.evidence['missing_references'] == ['验收单']
    assert '技术鉴定报告' not in r09.message
    assert r09.message.startswith('自定义：') and r09.message.endswith('（保留模板）')
    assert sum(s.findings for s in statuses) == len(findings)


@pytest.mark.parametrize('options', [{'enabled':False}, {'overlay':True}, {'version':1}])
def test_disabled_or_legacy_r13_does_not_silently_remove_r09(options):
    findings, _ = engine(**options)
    assert any(f.check_id == 'carrier_subset' for f in findings)


def test_single_deleted_word_does_not_suppress_a_longer_carrier():
    findings, _ = engine(matrices=[record(text='报告')])
    assert {f.display_code for f in findings} == {'R09', 'R13'}


def test_retained_current_report_is_clean_in_both_rules():
    findings, _ = engine(matrices=[matrix(current='1.技术鉴定报告。')])
    assert findings == []


def test_unparseable_fragment_survives_r13_precedence():
    findings, _ = engine(target=duty('对技术鉴定报告及某项未知安排的准确性负有审核责任。'))
    assert any(f.evidence.get('issue_type') == 'carrier_parse_unavailable' for f in findings)
    assert any(f.display_code == 'R13' for f in findings)
