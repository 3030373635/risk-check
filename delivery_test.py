"""Run portable source tests; excluded original-material cases are explicit."""
import os
from importlib.util import find_spec
from pathlib import Path
import pytest
from run_audit import configure_soffice

ROOT = Path(__file__).resolve().parent
EXCLUDED = {
    'test_manual_extraction_expected_scopes': 'Requires the original manually reviewed Xintong submission.',
    'test_real_five_biff_et_files_are_included': 'Requires the original Xiangxi submission.',
    'test_real_xls_signature_and_cache_preserving_conversion': 'Requires the original Xintong XLS.',
    'test_r03_split_applicability_tail_stays_review': 'Requires a historical output workbook.',
    'test_real_sample_preserves_missing_pair_and_department_specificity_checks': 'Requires original Xintong asset-management workbooks.',
    'test_real_asset_title_change_keeps_only_the_unresolved_logistics_position': 'Requires original Xintong asset-management workbooks.',
    'test_independent_acceptance_suite': 'Requires the original workspace .work/review acceptance suite and its original-material fixtures.',
}

class DeliverySelection:
    def pytest_collection_modifyitems(self, config, items):
        keep = [item for item in items if item.name not in EXCLUDED]
        omitted = [item for item in items if item.name in EXCLUDED]
        config.hook.pytest_deselected(items=omitted)
        items[:] = keep

if __name__ == '__main__':
    os.chdir(ROOT)
    configure_soffice()
    print('Original-material tests excluded from this source package:')
    for name, reason in EXCLUDED.items():
        print(f'  {name}: {reason}')
    arguments = ['risk-audit/tests', '-q', '-rs', '--junitxml=交付测试结果.xml']
    if find_spec('numpy') is None:
        print('Optional NumPy is absent: tests/test_semantic_v150.py is not collected. Install requirements-semantic.lock for these tests.')
        arguments.append('--ignore=risk-audit/tests/test_semantic_v150.py')
    raise SystemExit(pytest.main(arguments, plugins=[DeliverySelection()]))
