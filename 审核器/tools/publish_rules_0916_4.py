"""按《风控矩阵0916-4》生成、验证并激活 v1.9.4 规则包。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '审核器/src'))

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.util import sha256_file, write_json


DRAFT_NAME = 'rules-0916-4-1.9.4'
BASE_VERSION = '1.9.3'
TARGET_VERSION = '1.9.4'
SOURCE_NAME = '风控矩阵0916-4.docx'
SOURCE_REFERENCE = f'{SOURCE_NAME} 有效正文（排除文档内示例和说明性重复）'
RESPONSIBILITY_KEYWORDS = {
    '财务': ['财务'],
    '发展': ['发展', '规划'],
}


def _check(check_id: str, operator: str, params: dict, display_code: str) -> dict:
    """生成规则检查配置；check_id/operator/params/display_code 分别为检查标识、能力、参数和展示条款。"""
    return {
        'check_id': check_id,
        'location_policy': 'row',
        'message': {
            'review': f'【{display_code}】{{advice_v2}}',
            'violation': f'【{display_code}】{{advice_v2}}',
        },
        'on_unavailable': 'review',
        'operator': operator,
        'operator_version': 1,
        'params': params,
    }


def _matrix_rule(rule_id: str, title: str, check: dict, revision: int) -> dict:
    """生成矩阵行规则；rule_id/title/check/revision 为稳定标识、名称、检查配置和修订号。"""
    return {
        'checks': [check],
        'display_code': 'R15',
        'enabled': True,
        'revision': revision,
        'rule_id': rule_id,
        'schema_version': '1.0',
        'scope': {
            'business_codes': {'exclude': [], 'include': ['*']},
            'entity_codes': {'exclude': [], 'include': ['*']},
            'record_type': 'matrix',
        },
        'source_reference': SOURCE_REFERENCE,
        'title': title,
    }


def update_draft(draft: Path, source: Path) -> None:
    """同步0916-4规则草稿；draft 为草稿目录，source 为冻结来源文档。"""
    rules_dir = draft / 'rules'
    rules = {path.name: json.loads(path.read_text(encoding='utf-8')) for path in rules_dir.glob('*.json')}
    for rule in rules.values():
        rule['source_reference'] = SOURCE_REFERENCE

    # 第六条只核查具体性，不再对不相容岗位的多岗位分隔符提出拆分意见。
    incompatible = rules['R06b.json']
    incompatible['revision'] += 1
    for check in incompatible['checks']:
        if check['check_id'] == 'position_specific':
            check['params']['separators'] = []

    system_rule = rules['R07.json']
    system_rule.pop('disabled_reason', None)
    system_rule.update(
        enabled=True,
        revision=system_rule['revision'] + 1,
        rule_id='systems.matrix_changes',
        title='矩阵控制系统修订与系统规则对应',
        scope={**system_rule['scope'], 'record_type': 'matrix'},
        checks=[_check('system_rule_changes', 'system_rule_changes', {}, '第7条')],
    )

    rules['R15b.json'] = _matrix_rule(
        'matrices.measure_applicability',
        '岗位清单已有措施与矩阵适用性一致性',
        _check('measure_applicability_alignment', 'measure_applicability_alignment', {}, '第15条'),
        2,
    )
    rules['R15c.json'] = _matrix_rule(
        'matrices.responsibility_department',
        '矩阵责任主体与岗位部门对应',
        _check('responsibility_department_alignment', 'responsibility_department_alignment',
               {'keyword_mappings': RESPONSIBILITY_KEYWORDS}, '第15条'),
        1,
    )

    for filename, rule in rules.items():
        write_json(rules_dir / filename, rule)

    manifest = json.loads((draft / 'manifest.json').read_text(encoding='utf-8'))
    manifest.update(
        version=TARGET_VERSION,
        engine_compatibility='>=1.9.4,<2.0.0',
        audit_as_of='2026-09-16',
        title='风控矩阵0916-4更新规则',
        source_document_sha256=sha256_file(source),
    )
    write_json(draft / 'manifest.json', manifest)


def build_release(store: RulePackStore, source: Path) -> tuple[Path, Path]:
    """生成并校验发布包；store 为规则仓库，source 为0916-4来源文档。"""
    draft = store.create_draft(DRAFT_NAME, BASE_VERSION)
    update_draft(draft, source)
    store.validate(draft)
    return draft, store.publish(draft.name, TARGET_VERSION)


def tree_hashes(root: Path) -> dict[str, str]:
    """计算目录逐文件哈希；root 为规则草稿或发布目录。"""
    return {str(path.relative_to(root)): sha256_file(path) for path in sorted(root.rglob('*')) if path.is_file()}


def verify_existing_release(store: RulePackStore, source: Path) -> Path:
    """临时重建并核对发布产物；store 为正式仓库，source 为0916-4来源文档。"""
    actual_draft = store.root / 'drafts' / DRAFT_NAME
    actual_release = store.root / 'releases' / TARGET_VERSION
    if not actual_draft.is_dir() or not actual_release.is_dir():
        raise RuntimeError('1.9.4草稿与发布包必须同时存在')
    with tempfile.TemporaryDirectory(prefix='risk-audit-rules-') as temporary_directory:
        temporary_root = Path(temporary_directory) / 'rulepacks'
        temporary_base = temporary_root / 'releases' / BASE_VERSION
        temporary_base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(store.root / 'releases' / BASE_VERSION, temporary_base)
        temporary_store = RulePackStore(temporary_root, build_registry())
        generated_draft, generated_release = build_release(temporary_store, source)
        for label, actual, generated in (
            ('draft', actual_draft, generated_draft), ('release', actual_release, generated_release),
        ):
            if tree_hashes(actual) != tree_hashes(generated):
                raise RuntimeError(f'现有{label}与临时重建结果不一致')
    store.validate(actual_release)
    return actual_release


def source_document(verify_only: bool) -> Path:
    """取得并冻结规则来源；verify_only 控制只读验证时不得复制外部文件。"""
    destination = ROOT / '审核器/规则来源' / SOURCE_NAME
    if destination.exists():
        return destination
    if verify_only:
        raise FileNotFoundError(destination)
    external = ROOT.parent / SOURCE_NAME
    if not external.exists():
        raise FileNotFoundError(external)
    # 规则来源随发布包冻结，后续验证不再依赖桌面附件位置。
    shutil.copy2(external, destination)
    return destination


def main() -> None:
    """生成或复验1.9.4；--verify-only 只验证产物，不修改激活版本。"""
    parser = argparse.ArgumentParser(description='生成或复验1.9.4规则包')
    parser.add_argument('--verify-only', action='store_true', help='只验证发布产物，不修改当前激活版本')
    args = parser.parse_args()
    source = source_document(args.verify_only)
    store = RulePackStore(ROOT / '审核器/rulepacks', build_registry())
    draft_exists = (store.root / 'drafts' / DRAFT_NAME).exists()
    release_exists = (store.root / 'releases' / TARGET_VERSION).exists()
    if draft_exists or release_exists:
        release = verify_existing_release(store, source)
        verified = True
    else:
        _, release = build_release(store, source)
        verified = False
    if not args.verify_only:
        store.activate(TARGET_VERSION)
    print(json.dumps({'release': str(release), 'activated': not args.verify_only, 'verified': verified}, ensure_ascii=False))


if __name__ == '__main__':
    main()
