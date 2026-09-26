from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.loader import load_pack
from risk_audit.configuration.validator import validate_pack
from risk_audit.engine import run_engine
from risk_audit.models import FieldValue
from test_deleted_text import record, duty
from test_engine import minimal_files

ROOT = Path(__file__).resolve().parents[1]


def run_current(matrices, target, baselines=None):
    pack = load_pack(ROOT / 'rulepacks/releases/1.4.1')
    validate_pack(pack, build_registry())
    assert not any(c['operator'] == 'set_subset' for r in pack['rules'] if r['enabled'] for c in r['checks'])
    pack['manifest']['status'] = 'draft'
    pack['rules'] = [r for r in pack['rules'] if r['display_code'] in {'R09', 'R13'}]
    registry = build_registry()
    def forbidden(*args, **kwargs):
        raise AssertionError('Retired carrier matching must not run')
    for key, capability in list(registry._items.items()):
        if key[0] == 'set_subset':
            registry._items[key] = replace(capability, runner=forbidden)
    limits = []
    findings, statuses = run_engine(pack, registry, minimal_files([*matrices, target]),
        {'205H': SimpleNamespace(name='信通')}, baselines or {}, limitations=limits)
    assert not any(s.status == 'failed' or s.check_id == 'carrier_subset' for s in statuses)
    assert not any(f.check_id == 'carrier_subset' for f in findings)
    assert not any(f['check_id'] == 'carrier_subset' for f in limits)
    return findings, limits


@pytest.mark.parametrize('with_matching_baseline', [False, True])
def test_unlisted_carrier_is_no_longer_checked_even_with_shared_template_gap(with_matching_baseline):
    matrix = record(text='合同', deleted=False)
    matrix.fields['control_measure'] = FieldValue('审核入库单', '审核入库单', 'J3')
    target = duty('对入库单的准确性负有审核责任。')
    target.fields['carrier'] = FieldValue('入库单', '入库单', 'V3')
    baselines = {('06', 'default'): {'path': '0818.xlsx', 'sha256': 'fixture',
        'measures': [{'measure_id': 'M1', 'control_measure': '审核入库单', 'carrier': '合同'}]}}
    assert run_current([matrix], target, baselines if with_matching_baseline else {}) == ([], [])


@pytest.mark.parametrize('field', ['control_measure', 'carrier'])
@pytest.mark.parametrize('text', ['入库单', '及'])
def test_original_deleted_text_still_reports_under_same_measure(field, text):
    findings, limits = run_current([record(text=text, field=field)], duty('对' + text + '负审核责任'))
    assert not limits
    assert len(findings) == 1 and findings[0].display_code == 'R13'
    assert findings[0].evidence['deleted_texts'] == [text]
    assert findings[0].evidence['matches'][0]['source_field'] == field


def test_final_retained_text_is_allowed_even_when_carrier_column_omits_it():
    matrix = record(text='入库单')
    matrix.fields['control_measure'] = FieldValue('审核入库单', '审核入库单', 'J3')
    assert run_current([matrix], duty('对入库单的准确性负有审核责任。')) == ([], [])


def test_no_reappearance_and_already_struck_target_do_not_report():
    assert run_current([record(text='入库单')], duty('对其他记录负责')) == ([], [])
    target = record('position_duty', text='入库单', field='duty', deleted=True)
    assert run_current([record(text='入库单')], target) == ([], [])


@pytest.mark.parametrize('scope', [{'mid':'M2'}, {'entity':'OTHER'}, {'business':'07'}, {'variant':'other'}])
def test_deleted_text_does_not_leak_from_other_measure_or_scope(scope):
    matrix = record(text='当前合同', deleted=False)
    assert run_current([matrix, record(text='入库单', **scope)], duty('对入库单负责')) == ([], [])


def test_number_reference_check_remains_separate_from_carrier_matching():
    findings, _ = run_current([record(text='合同', deleted=False)], duty('对入库单负责', mid='M2'))
    assert any(f.check_id == 'measure_exists' for f in findings)
    assert not any(f.check_id == 'carrier_subset' for f in findings)
