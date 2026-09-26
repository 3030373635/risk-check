from __future__ import annotations

import json
import logging
import shutil
import uuid
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any
from time import perf_counter

from risk_audit.baselines import load_baselines
from risk_audit.checks.registry import build_registry
from risk_audit.configuration.loader import load_pack
from risk_audit.configuration.rule_selection import apply_rule_selection, load_audit_config
from risk_audit.configuration.validator import validate_pack
from risk_audit.engine import run_engine
from risk_audit.entities import load_entities
from risk_audit.entity_alerts import build_entity_alerts, write_entity_alerts
from risk_audit.entity_groups import prepare_entity_groups
from risk_audit.hidden_sheets import hidden_sheet_alerts, write_hidden_sheet_alerts
from risk_audit.inventory import scan_package
from risk_audit.readers.excel import parse_files
from risk_audit.snapshot import build_snapshot, save_snapshot
from risk_audit.util import sha256_file, write_json
from risk_audit.writer import OutputState, validate_output_paths, write_outputs
from risk_audit.program_logging import audit_logging
from risk_audit.models import CheckStatus, FileRecord, Finding
from risk_audit.review_tasks import build_review_tasks, write_review_tasks
from risk_audit.issue_routing import build_internal_diagnostics, write_internal_diagnostics
from risk_audit.submission_scope import resolve_submission_scopes, write_submission_scope_report, submission_group
from risk_audit.audit_statistics import write_audit_statistics
from risk_audit.progress import (
    AuditCancelled,
    AuditProgressEvent,
    CancelCheck,
    ProgressCallback,
    emit_progress,
    raise_if_cancelled,
)


CONFIRMED_RULE_VERSIONS = frozenset({
    '1.8.0', '1.9.0', '1.9.1', '1.9.2', '1.9.3', '1.9.4', '1.9.5', '1.9.6',
    '1.9.7', '1.9.8', '1.9.9', '1.9.10', '1.9.11', '1.9.12', '1.9.13', '1.9.14',
    '1.9.15', '1.9.16', '1.9.17', '1.9.18', '1.9.19',
})
PREPROCESSING_RULE_VERSIONS = frozenset({
    '1.9.0', '1.9.1', '1.9.2', '1.9.3', '1.9.4', '1.9.5', '1.9.6', '1.9.7',
    '1.9.8', '1.9.9', '1.9.10', '1.9.11', '1.9.12', '1.9.13', '1.9.14', '1.9.15',
    '1.9.16', '1.9.17', '1.9.18', '1.9.19',
})


def preprocess_business_file(
    file: FileRecord,
    pack: dict[str, Any],
    baselines: dict,
    work_dir: Path,
) -> None:
    """执行当前规则版本要求的数据预处理。

    Args:
        file: 已完成首次解析的业务材料。
        pack: 当前审核规则包。
        baselines: 当前业务的省公司基准数据。
        work_dir: 当前业务运行目录。
    """

    version = pack['manifest']['version']
    aliases = pack['field_aliases']
    if version in PREPROCESSING_RULE_VERSIONS:
        from risk_audit.output_0916 import sort_duties

        sort_duties(file, baselines, work_dir, aliases)
    if version in {'1.9.18', '1.9.19'}:
        from risk_audit.system_type_preprocessing import preprocess_system_types

        preprocess_system_types(file, work_dir, aliases)


def audit(
    input_root: str | Path,
    output_root: str | Path,
    rulepack: str | Path,
    entity_file: str | Path,
    project_root: str | Path,
    runs_root: str | Path,
    write: bool = True,
    run_id: str | None = None,
    *,
    include_hidden: bool = False,
    scope_file: str | Path | None = None,
    config_file: str | Path | None = None,
    enabled_rules: list[str] | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
    model_root: str | Path | None = None,
) -> dict[str, Any]:
    """执行审核或试跑。

    input_root/output_root 为输入/副本目录，rulepack 为规则包，entity_file 为主体名册，
    project_root 为基准根目录，runs_root 为运行记录目录，write 控制副本写入，
    run_id 为可选运行标识，include_hidden 控制隐藏表，scope_file 为可选手动范围覆盖文件，
    config_file 为可选审核运行配置，enabled_rules 为仅本次运行启用的规则展示编号，
    progress_callback 为可选进度接收器，cancel_check 为安全取消检查器，model_root 为
    可选语义模型目录；默认直接从实际上传的风控矩阵和三清单确定范围。
    """
    input_resolved = Path(input_root).resolve(); output_resolved = Path(output_root).resolve()
    if write and (input_resolved == output_resolved or input_resolved in output_resolved.parents or output_resolved in input_resolved.parents):
        raise ValueError("输入目录与输出目录相同或相互包含，已在任何写入前拒绝")
    run_id = run_id or str(uuid.uuid4())
    run_dir = Path(runs_root).resolve() / run_id; run_dir.mkdir(parents=True, exist_ok=False)
    with audit_logging(run_dir, run_id) as logger:
        started = perf_counter()
        emit_progress(progress_callback, AuditProgressEvent(stage='startup', message='正在准备审核任务'))
        try:
            logger.info('%s开始：%s', '审核' if write else '试跑', input_resolved.name)
            logger.debug('运行路径：模式=%s，输入=%s，输出=%s，日志=%s',
                         '审核' if write else '试跑', input_resolved, output_resolved, run_dir / 'audit.log')
            result = _audit(
                input_resolved, output_resolved, rulepack, entity_file, project_root,
                run_dir, run_id, write, include_hidden, scope_file, config_file,
                enabled_rules,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                model_root=model_root,
            )
            emit_progress(progress_callback, AuditProgressEvent(
                stage='completed',
                completed_units=len(result.get('business_results', [])),
                total_units=len(result.get('business_results', [])),
                message='审核任务已结束',
            ))
            logger.info('审核结束：主体=%d，意见=%d，告警=%d，写入完成=%s，耗时=%.2f秒',
                        len(result['entity_results']), result['findings'], result['warnings'],
                        result['write_completed'], perf_counter() - started)
            return result
        except AuditCancelled:
            emit_progress(progress_callback, AuditProgressEvent(stage='cancelled', message='审核任务已停止'))
            raise
        except BaseException as error:
            emit_progress(progress_callback, AuditProgressEvent(stage='failed', message=str(error)))
            raise


def _has_failures(files: list[FileRecord], statuses: list[CheckStatus], warnings: list[dict[str, Any]]) -> bool:
    """判断是否存在未完成审核；files 为材料，statuses 为检查状态，warnings 为输出告警。"""
    business_files = [file for file in files if file.material_type != 'explanation']
    return (any(not file.sheets or file.parse_errors for file in business_files)
            or any(status.status == 'failed' for status in statuses)
            or any(warning.get('type') in {'no_writable_sheet', 'output_incompatible_file'} for warning in warnings))


def _remove_legacy_opinion_outputs(output_root: Path) -> None:
    """清理输出目录中的旧辅助资料；output_root 为本次审核副本根目录。"""
    audit_directory = output_root / "_risk_audit"
    # 父目录链接可能指向输出目录之外；仅移除链接本身，禁止跟随链接清理外部文件。
    if audit_directory.is_symlink():
        audit_directory.unlink()
    elif audit_directory.exists() and not audit_directory.is_dir():
        raise RuntimeError(f"审核辅助输出路径不是目录，拒绝清理: {audit_directory}")
    elif audit_directory.is_dir():
        # _risk_audit 是程序管理目录；迁移后不得在交付目录留下旧辅助资料。
        shutil.rmtree(audit_directory)

    statistics_report = output_root / "审核统计表.xlsx"
    if statistics_report.is_symlink() or statistics_report.is_file():
        statistics_report.unlink()
    elif statistics_report.exists():
        raise RuntimeError(f"审核统计表路径不是文件，拒绝清理: {statistics_report}")


def _load_previous_ownership(runs_root: Path, output_root: Path, current_run_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """读取同一输出目录最近一次归属记录。

    runs_root 为运行记录根目录，output_root 为当前副本目录，current_run_dir 为本次运行目录；
    返回最近一次已写出运行保存的意见归属，供重复审核区分人工意见和程序意见。
    """
    candidates: list[tuple[float, Path]] = []
    for context_path in runs_root.glob("*/_risk_audit/run_context.json"):
        run_directory = context_path.parent.parent
        if run_directory == current_run_dir:
            continue
        try:
            context = json.loads(context_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        previous_output = context.get("output_root")
        if not previous_output or Path(previous_output).resolve() != output_root:
            continue
        ownership_path = context_path.parent / "ownership.json"
        if ownership_path.is_file():
            candidates.append((ownership_path.stat().st_mtime, ownership_path))
    if not candidates:
        return {}
    ownership_path = max(candidates, key=lambda item: item[0])[1]
    ownership = json.loads(ownership_path.read_text(encoding="utf-8"))
    if not isinstance(ownership, dict):
        raise RuntimeError(f"历史审核归属格式错误: {ownership_path}")
    return ownership


def _write_audit_result(directory: Path, result: dict[str, Any], findings: list[Finding],
                        statuses: list[CheckStatus], limitations: list[dict[str, Any]],
                        warnings: list[dict[str, Any]]) -> None:
    """保存当前审核结果。

    directory 为业务或主体目录，result 为摘要，findings/statuses 为意见和检查状态，
    limitations/warnings 为检查限制和输出告警。
    """
    write_json(directory / 'findings.json', [finding.to_dict() for finding in findings])
    write_json(directory / 'statuses.json', [asdict(status) for status in statuses])
    write_json(directory / 'limitations.json', limitations)
    write_json(directory / 'warnings.json', warnings)
    write_json(directory / 'result.json', result)


def _business_keys(scope: dict[str, Any], files: list[FileRecord]) -> list[tuple[str, str, str]]:
    """确定独立业务单元；scope 为主体范围，files 为主体文件，返回业务 ID、展示编号及变体。"""
    keys = {(business.get('business_id') or business['business_code'], business['business_code'],
             business.get('variant_id', 'default'))
            for business in scope['businesses'] if business.get('required', True)}
    # 上传文件即使被手动标记为不要求报送，也须读取并保留；说明材料单独原样输出。
    keys.update((file.business_id or file.business_code or '', file.business_code or '', file.variant_id)
                for file in files if file.material_type != 'explanation')
    return sorted(keys)


def _audit(input_root: Path, output_root: Path, rulepack: str | Path, entity_file: str | Path,
           project_root: str | Path, run_dir: Path, run_id: str, write: bool,
           include_hidden: bool, scope_file: str | Path | None,
           config_file: str | Path | None, enabled_rules: list[str] | None,
           progress_callback: ProgressCallback | None, cancel_check: CancelCheck | None,
           model_root: str | Path | None) -> dict[str, Any]:
    """按主体、业务及矩阵类型执行增量审核。

    input_root/output_root 为已校验目录，rulepack 为规则包，entity_file 为主体名册，
    project_root 为基准根目录，run_dir/run_id 为本次运行目录及标识，write 控制副本输出，
    include_hidden 控制隐藏表，scope_file 为可选手动范围文件，config_file 为运行配置，
    enabled_rules 为仅本次运行启用的规则展示编号，progress_callback/cancel_check
    为桌面任务的进度和取消接口，model_root 为可选语义模型目录。
    """
    input_resolved, output_resolved = input_root, output_root
    logger = logging.getLogger(__name__)
    audit_directory = run_dir / '_risk_audit'
    report_directory = audit_directory if write else run_dir
    if write:
        # 在任何业务副本写出前固定输出目录映射，中断运行的 ownership 也可被下一次重跑恢复。
        write_json(audit_directory / 'run_context.json', {
            'run_id': run_id,
            'output_root': str(output_resolved),
        })
    logger.debug('加载规则包和主体名册')
    registry = build_registry(); pack = load_pack(rulepack); validate_pack(pack, registry)
    settings = load_audit_config(config_file) if config_file else {"disabled_rules": []}
    # 运行选择只修改内存中的规则副本，不改写已发布规则包。
    pack["_runtime_rule_selection"] = apply_rule_selection(
        pack,
        settings,
        enabled_rules=enabled_rules,
    )
    validate_pack(pack, registry)
    entities, missing_code = load_entities(entity_file)
    logger.info('扫描报送材料')
    emit_progress(progress_callback, AuditProgressEvent(stage='scan', message='正在扫描报送材料'))
    if pack['manifest']['version'] in CONFIRMED_RULE_VERSIONS:
        from risk_audit.inventory_v180 import scan_package_v180
        files = scan_package_v180(input_resolved, entities, pack['entity_aliases'], pack['baseline_registry'])
    else:
        files = scan_package(input_resolved, entities, pack["entity_aliases"], pack['baseline_registry'])
    logger.debug('扫描完成：文件=%d', len(files))
    # 证据冲突不能确定材料归属；单纯未匹配名册则继续审核并在最终结果标注。
    conflicted_files = [file for file in files if file.entity_conflict]
    if conflicted_files:
        entity_alerts = build_entity_alerts(conflicted_files, entities, missing_code)
        entity_report = write_entity_alerts(report_directory, entity_alerts)
        errors = []
        for file in conflicted_files:
            reason = '主体证据冲突'
            message = f'{reason}：{file.relative_path}'
            errors.append(message)
            logger.error('会计主体匹配失败，审核终止：%s，候选代码=%s，识别证据=%s',
                         message, file.entity_code or '无', file.entity_evidence or ['无'])
        raise ValueError(f'会计主体匹配失败，已终止审核（{len(errors)}份文件）：'
                         + '；'.join(errors) + f'。请核对主体名册或简称映射；详细报告：{entity_report}')
    # 每个主体使用本次材料的范围，历史默认单位范围不参与实际报送审核。
    scopes, scope_report = resolve_submission_scopes(input_resolved, files, entities, pack['entity_aliases'],
                                                    pack['submission_scope'].get('batch', ''), include_hidden=include_hidden,
                                                    business_registry=pack['baseline_registry'])
    # 仅依据业务材料建立本次主体分组；辅助附件不补充或覆盖主体归属。
    entity_groups = prepare_entity_groups(
        files, input_resolved, entities, missing_code, pack['baseline_registry'],
    )
    entity_alerts = build_entity_alerts(files, entities, missing_code)
    for alert in entity_alerts:
        logger.warning('主体清单匹配提示，已继续审核：%s，%s，文件=%s',
                       alert['unit_label'], alert['reason'], [item['file'] for item in alert['files']])
    scopes, scope_report = resolve_submission_scopes(input_resolved, files, entities, pack['entity_aliases'],
                                                    pack['submission_scope'].get('batch', ''),
                                                    include_hidden=include_hidden, source_report=scope_report,
                                                    business_registry=pack['baseline_registry'])
    if scope_file:
        automatic_scopes = scopes
        scopes = json.loads(Path(scope_file).read_text(encoding='utf-8'))
        if not isinstance(scopes,dict):raise ValueError('应报范围文件必须按会计主体代码分组')
        for ec,scope in scopes.items():
            if ec and ec not in entities:raise ValueError(f'应报范围存在未知主体代码: {ec}')
            if not isinstance(scope,dict) or scope.get('entity_codes')!=([ec] if ec else []):raise ValueError(f'应报范围的主体代码不一致: {ec}')
            if not isinstance(scope.get('businesses'),list) or any(not isinstance(x,dict) or not all(k in x for k in ('business_id','business_code','variant_id','required')) for x in scope['businesses']):raise ValueError(f'业务应报范围格式错误: {ec}')
        # 清单外主体没有可填写的正式代码，手动范围仍自动保留其实际上传业务。
        scopes.update({key: value for key, value in automatic_scopes.items() if key not in entities})
        if {f.entity_code or '' for f in files}-set(scopes):raise ValueError('应报范围未覆盖输入中的部分会计主体')
        scope_report['mode'] = 'manual_override'
        scope_report['submission_scopes'] = scopes
        scope_report['sources'].append({'path': str(Path(scope_file).resolve()), 'file': str(scope_file),
                                        'sha256': sha256_file(Path(scope_file))})
    # 扫描及范围识别结束后才有真实工作单元总数，之前不得伪造百分比。
    total_units = sum(
        len(_business_keys(
            scopes[entity_key],
            [file for file in files if ((file.entity_code or '') if scope_file else submission_group(
                file, input_resolved, pack['baseline_registry'],
            )) == entity_key],
        ))
        for entity_key in scopes
    )
    emit_progress(progress_callback, AuditProgressEvent(
        stage='validation',
        completed_units=0,
        total_units=total_units,
        message='材料扫描及范围校验完成',
    ))
    raise_if_cancelled(cancel_check)
    for file in files:
        file._rules_version = pack['manifest']['version']
    if write:
        # 提前检查所有文件的潜在转换目标，不能等后续主体解析后才发现覆盖冲突。
        validate_output_paths(files, anticipate_conversion=True)
        # 复用输出目录时同步移除旧版程序报告，避免被误认为本次审核产物。
        _remove_legacy_opinion_outputs(output_resolved)
    entity_keys = list(scopes)
    logger.info('识别到%d个主体：%s', len(entity_keys),
                '、'.join(f'{entities[key].name}（{key}）' if key in entities else
                         f'{entity_groups[key]["entity_name"]}（主体代码待确认）' for key in entity_keys))
    write_submission_scope_report(report_directory, scope_report, entities)
    snapshot = build_snapshot(pack, files, registry.versions(), run_id, {"path": str(Path(entity_file).resolve()), "sha256": sha256_file(Path(entity_file).resolve())}, include_hidden=include_hidden)
    from risk_audit.util import sha256_json
    snapshot['submission_scopes'] = scopes
    snapshot['scope_source'] = scope_report
    snapshot.pop('snapshot_hash'); snapshot['snapshot_hash'] = sha256_json(snapshot)
    save_snapshot(run_dir / 'snapshot.json', snapshot)
    from risk_audit.applicability import attach_context
    attach_context(pack, files, entities)
    if pack.get('semantic_config'):
        resolved_model_root = (Path(model_root).resolve() if model_root is not None
                               else Path(project_root).resolve() / 'risk-audit/models/bge-small-zh-v1.5')
        pack['_semantic_runtime']={'model_dir':str(resolved_model_root),
                                   'cache_path':str(Path(project_root).resolve()/'risk-audit/.cache/semantic.sqlite3'),
                                   'run_id':run_id}
    entity_report = write_entity_alerts(report_directory, entity_alerts)
    configured_baselines = sum(len(business.get('variants', []))
                               for business in pack['baseline_registry'].get('businesses', []))
    if not configured_baselines:
        configured_baselines = len(pack['baseline_registry'].get('entries', []))
    logger.info('加载公共基准：%d份', configured_baselines)
    baselines = load_baselines(Path(project_root).resolve(), pack["baseline_registry"], pack["field_aliases"], run_dir, include_hidden=include_hidden)
    logger.debug('公共基准统一加载完成：基准=%d，审核主体=%d，后续主体共用已加载数据', len(baselines), len(scopes))
    limitations: list[dict[str, Any]] = []
    findings: list[Finding] = []
    statuses: list[CheckStatus] = []
    warnings: list[dict[str, Any]] = []
    previous_ownership = _load_previous_ownership(run_dir.parent, output_resolved, run_dir) if write else {}
    output_state = OutputState(ownership=dict(previous_ownership), previous_ownership=previous_ownership)
    entity_results: list[dict[str, Any]] = []
    business_results: list[dict[str, Any]] = []
    # 主体顺序在扫描阶段固定，主体内部的每个业务独立完成解析、审核和输出。
    for index, entity_key in enumerate(entity_keys, 1):
        entity_started = perf_counter()
        identity = entity_groups.get(entity_key, {'entity_name': entities[entity_key].name if entity_key in entities else entity_key,
                                                 'entity_registry_status': 'matched',
                                                 'entity_registry_message': '主体已匹配会计主体清单'})
        entity_name = identity['entity_name']
        group_files = [file for file in files if ((file.entity_code or '') if scope_file else submission_group(
            file, input_resolved, pack['baseline_registry'],
        )) == entity_key]
        entity_dir = run_dir / 'entities' / f'{index:04d}'
        logger.info('主体审核开始 [%d/%d]：%s（%s），%d份文件',
                    index, len(entity_keys), entity_name, entity_key, len(group_files))
        unit_keys = _business_keys(scopes[entity_key], group_files)
        explanation_files = [file for file in group_files if file.material_type == 'explanation']
        group_findings: list[Finding] = []
        group_statuses: list[CheckStatus] = []
        group_limitations: list[dict[str, Any]] = []
        group_warnings: list[dict[str, Any]] = []
        group_business_results: list[dict[str, Any]] = []
        entity_result = {'entity_key': entity_key, 'entity_code': entity_key if entity_key in entities else '',
                         'entity_name': entity_name, 'input_files': len(group_files), 'run_dir': str(entity_dir),
                         'entity_registry_status': identity['entity_registry_status'],
                         'entity_registry_message': identity['entity_registry_message'],
                         'business_results': group_business_results, 'total_businesses': len(unit_keys)}
        for unit_index, (business_id, business_code, variant_id) in enumerate(unit_keys, 1):
            raise_if_cancelled(cancel_check)
            unit_started = perf_counter()
            unit_dir = entity_dir / 'businesses' / f'{unit_index:04d}'
            unit_label = business_code or '未识别'
            if variant_id != 'default':
                unit_label += f'（{variant_id}）'
            unit_files = [file for file in group_files if file.material_type != 'explanation'
                          and (file.business_id or file.business_code or '', file.variant_id) == (business_id, variant_id)]
            emit_progress(progress_callback, AuditProgressEvent(
                stage='audit',
                completed_units=len(business_results),
                total_units=total_units,
                current_entity=entity_name,
                current_business=unit_label,
                current_file=str(unit_files[0].relative_path) if unit_files else None,
                message='正在执行审核规则',
            ))
            logger.info('业务%s [%d/%d]：解析%d份文件', unit_label, unit_index, len(unit_keys), len(unit_files))
            # 各业务的旧格式转换副本独立保存，下一业务不得覆盖已输出业务的读取结果。
            parse_files(unit_files, pack['field_aliases'], unit_dir, include_hidden=include_hidden,
                        input_overrides=pack.get('input_overrides'), entity_aliases=pack['entity_aliases'],
                        parser_policy=pack.get('parser_policy'), semantic_lexicon=pack.get('semantic_lexicon'))
            for file in unit_files:
                if file.sheets and not file.parse_errors:
                    file._include_hidden = include_hidden
                    preprocess_business_file(file, pack, baselines, unit_dir)
            if not scope_file:
                scopes, scope_report = resolve_submission_scopes(input_resolved, files, entities, pack['entity_aliases'],
                                                                pack['submission_scope'].get('batch', ''),
                                                                include_hidden=include_hidden, source_report=scope_report,
                                                                business_registry=pack['baseline_registry'])
            snapshot['submission_scopes'] = scopes
            snapshot['scope_source'] = scope_report
            snapshot.pop('snapshot_hash'); snapshot['snapshot_hash'] = sha256_json(snapshot)
            save_snapshot(run_dir / 'snapshot.json', snapshot)
            write_submission_scope_report(report_directory, scope_report, entities)
            local = deepcopy(pack)
            # 主体是否已报送材料由扫描名单确定；缺报某个业务不能被当作整个主体未报送。
            local['_submitted_entity_codes'] = [entity_key] if any(
                file.material_type != 'explanation' for file in group_files) else []
            local['submission_scope'] = {**deepcopy(scopes[entity_key]), 'businesses': [
                business for business in scopes[entity_key]['businesses']
                if (business.get('business_id') or business['business_code'], business.get('variant_id', 'default'))
                == (business_id, variant_id)]}
            # 清单外主体也执行资料完整性检查，分组标识不表示正式会计主体代码。
            local['submission_scope']['entity_codes'] = [entity_key]
            # 规则上下文只包含当前业务的矩阵和三清单；主体说明仅用于说明存在性检查。
            unit_baselines = {key: value for key, value in baselines.items() if key == (business_id, variant_id)}
            unit_limitations: list[dict[str, Any]] = []
            unit_findings, unit_statuses = run_engine(local, registry, unit_files + explanation_files,
                                                     entities, unit_baselines, run_id, unit_limitations)
            unit_warnings = []
            if write:
                unit_warnings, _ = write_outputs(unit_files, unit_findings, output_resolved,
                    entities=entities if pack['manifest']['version'] in CONFIRMED_RULE_VERSIONS else None,
                    output_state=output_state,
                    baselines=unit_baselines if pack['manifest']['version'] in PREPROCESSING_RULE_VERSIONS else None,
                    metadata_dir=audit_directory)
            for warning in unit_warnings:
                logger.warning('业务%s输出告警：%s', unit_label, warning)
            unit_completed = write and not _has_failures(unit_files, unit_statuses, unit_warnings)
            unit_result = {'entity_key': entity_key, 'entity_code': entity_result['entity_code'],
                           'entity_name': entity_name, 'business_id': business_id,
                           'business_code': business_code, 'variant_id': variant_id,
                           'entity_registry_status': identity['entity_registry_status'],
                           'entity_registry_message': identity['entity_registry_message'],
                           'input_files': len(unit_files), 'findings': len(unit_findings), 'warnings': len(unit_warnings),
                           'limitations': len(unit_limitations), 'write_completed': unit_completed,
                           'run_dir': str(unit_dir), 'elapsed_seconds': round(perf_counter() - unit_started, 2)}
            _write_audit_result(unit_dir, unit_result, unit_findings, unit_statuses, unit_limitations, unit_warnings)
            group_business_results.append(unit_result)
            business_results.append(unit_result)
            group_findings.extend(unit_findings); findings.extend(unit_findings)
            group_statuses.extend(unit_statuses); statuses.extend(unit_statuses)
            group_limitations.extend(unit_limitations); limitations.extend(unit_limitations)
            group_warnings.extend(unit_warnings); warnings.extend(unit_warnings)
            # 每完成一个业务就保存主体进度，异常时不能把部分输出误标为整个主体已完成。
            entity_result.update({'findings': len(group_findings), 'warnings': len(group_warnings),
                                  'limitations': len(group_limitations), 'completed_businesses': len(group_business_results),
                                  'write_completed': False,
                                  'elapsed_seconds': round(perf_counter() - entity_started, 2)})
            _write_audit_result(entity_dir, entity_result, group_findings, group_statuses, group_limitations, group_warnings)
            logger.log(logging.INFO if unit_completed or not write else logging.WARNING,
                       '业务%s%s：意见=%d，耗时=%.2f秒', unit_label,
                       '输出完成' if write and unit_completed else '试跑完成' if not write else '处理结束（存在未完成项）',
                       len(unit_findings), perf_counter() - unit_started)
            emit_progress(progress_callback, AuditProgressEvent(
                stage='audit',
                completed_units=len(business_results),
                total_units=total_units,
                current_entity=entity_name,
                current_business=unit_label,
                message='业务审核已完成',
            ))
            raise_if_cancelled(cancel_check)
        if write and explanation_files:
            # 主体说明原样复制一次，不重复写入每个业务的输出及程序归属记录。
            explanation_warnings, _ = write_outputs(explanation_files, [], output_resolved,
                                                     output_state=output_state, metadata_dir=audit_directory)
            group_warnings.extend(explanation_warnings); warnings.extend(explanation_warnings)
        attach_context(pack, files, entities)
        group_completed = write and not _has_failures(group_files, group_statuses, group_warnings)
        entity_result.update({'findings': len(group_findings), 'warnings': len(group_warnings),
                              'limitations': len(group_limitations), 'completed_businesses': len(group_business_results),
                              'write_completed': group_completed, 'elapsed_seconds': round(perf_counter() - entity_started, 2)})
        _write_audit_result(entity_dir, entity_result, group_findings, group_statuses, group_limitations, group_warnings)
        entity_results.append(entity_result)
        logger.log(logging.INFO if group_completed or not write else logging.WARNING,
                   '%s：%s（%s），意见=%d，写入完成=%s，耗时=%.2f秒',
                   '主体输出完成' if write and group_completed else '主体试跑完成' if not write else '主体处理结束（存在未完成项）',
                   entity_name, entity_key,
                   len(group_findings), group_completed, perf_counter() - entity_started)
        logger.debug('主体记录：%s', entity_dir)
    raise_if_cancelled(cancel_check)
    logger.info('生成汇总报告')
    emit_progress(progress_callback, AuditProgressEvent(
        stage='output',
        completed_units=len(business_results),
        total_units=total_units,
        message='正在生成汇总结果',
    ))
    scope_summary = write_submission_scope_report(report_directory, scope_report, entities)
    hidden_alerts = hidden_sheet_alerts(files)
    hidden_summary = write_hidden_sheet_alerts(report_directory, hidden_alerts, include_hidden=include_hidden)
    if pack.get('semantic_config'):
        from risk_audit.semantic import get_assistant,close_assistant
        from risk_audit.applicability import interpret_record
        assistant=get_assistant(pack)
        semantic_report=assistant.report()
        from risk_audit.semantic_vocabulary import write_semantic_vocabulary
        semantic_report['vocabulary']=write_semantic_vocabulary(run_dir,files,semantic_report,findings,limitations)
        write_json(run_dir/'semantic_diagnostics.json',semantic_report)
        write_json(run_dir/'applicability_interpretations.json',[
            {'file':r.file_path,'sheet':r.sheet,'row':r.row,'entity_code':r.entity_code,
             'business_id':r.business_id,'business_code':r.business_code,
             'variant_id':r.variant_id,'text':r.value('applicability'),'interpretation':interpret_record(r,pack).to_dict()}
            for f in files for s in f.sheets for r in s.records if r.record_type=='matrix'])
        close_assistant(pack)
    internal = build_internal_diagnostics(limitations, files, scopes)
    internal_summary = write_internal_diagnostics(report_directory, internal)
    capability_summary = None
    if pack.get('parser_policy', {}).get('version') == 3:
        from risk_audit.capability_report import build_capability_report, write_capability_report
        capability = build_capability_report(files, limitations, findings, scopes)
        capability_summary = write_capability_report(report_directory, capability)
    tasks=build_review_tasks(files,findings,scopes)
    task_summary=write_review_tasks(report_directory,tasks)
    from risk_audit.responsibility_patterns import build_patterns, write_patterns
    responsibility_summary = write_patterns(report_directory, build_patterns(files,findings,limitations,scopes))
    warnings.extend(scope_report['alerts'])
    if write:
        audit_statistics_report = output_resolved / '审核统计表.xlsx'
        # 统计表是独立代码产物，不依赖已删除的意见反馈规则或报告。
        write_audit_statistics(audit_statistics_report, files, output_state.ownership, input_resolved.name)
    warnings.extend({'type': 'hidden_sheets_notice', 'file': a['file'], 'sheets': a['sheets'],
                     'message': '存在隐藏工作表，请按需查看处理提示；未默认纳入审核。' if not include_hidden else '本次明确选择纳入隐藏表，请查看实际处理结果。',
                     'report': hidden_summary['report']} for a in hidden_alerts)
    warnings.extend({'type': 'entity_code_unconfirmed', 'unit_label': a['unit_label'],
                     'files': [f['file'] for f in a['files']], 'reason': a['reason'],
                     'report': str(entity_report)} for a in entity_alerts)
    for file in files:
        # 意见列与其他区域业务字段冲突时保留原内容，明确提示布局限制。
        warnings.extend({'type': 'audit_column_conflict', 'file': str(file.relative_path), **conflict}
                        for conflict in file.preservation.get('audit_column_conflicts', []))
    for file in files:
        if sha256_file(file.source) != file.sha256: raise RuntimeError(f"输入文件运行期间发生变化: {file.source}")
    for source in scope_report['sources']:
        if sha256_file(Path(source['path'])) != source['sha256']:
            raise RuntimeError(f"范围来源文件运行期间发生变化: {source['path']}")
    write_json(run_dir / "findings.json", [x.to_dict() for x in findings])
    write_json(run_dir / "statuses.json", [asdict(x) for x in statuses])
    write_json(run_dir / "limitations.json", limitations)
    inventory = [{"path": str(f.relative_path), "sha256": f.sha256, "true_format": f.true_format, "entity_code": f.entity_code, "entity_evidence": f.entity_evidence, "entity_conflict": f.entity_conflict, "business_id": f.business_id, "business_code": f.business_code, "variant_id": f.variant_id, "material_type": f.material_type, "parse_errors": f.parse_errors, "sheets": [{"title": s.title, "type": s.sheet_type, "records": len(s.records), "columns": s.columns, "business_header_paths": s.business_header_paths, "audit_columns": s.audit_columns, "output_column": s.output_column} for s in f.sheets], "preservation": f.preservation} for f in files]
    write_json(run_dir / "inventory.json", inventory)
    write_json(run_dir / "warnings.json", warnings)
    parsed_business_files = [f for f in files if f.material_type != "explanation"]
    # 即使同批其他文件已写入，无业务表的文件仍属于未审核，隐藏提示不能掩盖它。
    unparsed_file_paths = [str(file.relative_path) for file in parsed_business_files if not file.sheets]
    has_failures = _has_failures(files, statuses, warnings)
    result = {"run_id": run_id, "run_dir": str(run_dir), "output_root": str(output_resolved), "input_files": len(parsed_business_files), "parsed_files": sum(bool(f.sheets) for f in parsed_business_files), "records": sum(len(s.records) for f in files for s in f.sheets), "findings": len(findings), "statuses": {k: sum(x.status == k for x in statuses) for k in {x.status for x in statuses}}, "missing_code_entities": len(missing_code), "write_completed": write and not has_failures, "warnings": len(warnings)}
    result['unparsed_files'] = len(unparsed_file_paths)
    result['unparsed_file_paths'] = unparsed_file_paths
    result["limitations"] = len(limitations)
    result['internal_diagnostics'] = internal_summary
    if capability_summary is not None:
        result['capability_diagnostics'] = capability_summary
    result['responsibility_patterns'] = responsibility_summary
    result['hidden_sheets'] = hidden_summary
    result['review_tasks']=task_summary
    result['submission_scope'] = scope_summary
    result["has_check_limits"] = bool(scope_report['alerts'] or unparsed_file_paths or limitations or entity_alerts or any(f.severity=='review' for f in findings)
                                    or any(warning.get('type') == 'audit_column_conflict' for warning in warnings))
    result["entity_code_alerts"] = {
        "groups": len(entity_alerts), "files": sum(a['file_count'] for a in entity_alerts),
        "report": str(entity_report),
        "message": '未匹配主体已继续审核并标注，正式代码待确认；请复核依赖名册身份的检查。' if entity_alerts else '主体代码均已识别。',
    }
    result['entity_results'] = entity_results
    result['business_results'] = business_results
    result['log_file'] = str(run_dir / 'audit.log')
    if write:
        result['audit_statistics_report'] = str(audit_statistics_report)
        result['audit_metadata_dir'] = str(audit_directory)
    write_json(run_dir / "summary.json", result)
    return result
