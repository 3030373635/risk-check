"""从实际上传的风控矩阵和三清单确定审核范围。"""
from collections import defaultdict
from pathlib import Path
import re

from risk_audit.business_identity import normalize_business_text
from risk_audit.inventory import identify_business
from risk_audit.util import norm_text, write_json


BUSINESS_DIRECTORY_KEYWORDS = (
    '营销售电', '交易与购电', '交易购电', '电网基建', '迁改工程', '配网工程',
    '设备(资产)管理', '设备资产管理', '物资(服务)采购与实施', '物资服务采购与实施',
    '数字化与研发投入', '科研投入', '职工福利保障与薪酬管理',
    '薪酬福利保障与薪酬管理', '员工报账', '员工报销',
)


def _matches_configured_business_directory(name, business_registry, business_id=None):
    """判断业务目录；name 为目录名，business_registry 为模板目录，business_id 可限定已识别业务。"""
    if not isinstance(business_registry, dict) or business_registry.get('schema_version') != '2.0':
        return False
    normalized_name = normalize_business_text(name)
    for business in business_registry.get('businesses', []):
        if business_id and business.get('business_id') != business_id:
            continue
        aliases = list(business.get('aliases', []))
        aliases.extend(alias for variant in business.get('variants', []) for alias in variant.get('aliases', []))
        if any(normalize_business_text(alias) in normalized_name for alias in aliases):
            return True
    return False


def unit_directory(path, root, business_registry=None, business_id=None, business_code=None):
    """取得单位目录；path/root 为材料与输入根，business_registry/business_id/business_code 描述当前业务。"""
    directory = path.parent
    while directory != root and directory != directory.parent:
        name = directory.name
        normalized_name = norm_text(name)
        entity_like = bool(re.search(r'(?:有限公司|分公司|中心|研究院|设计院|供电所)(?:本部)?$', name))
        normalized_code = str(business_code or '').lstrip('0') or '0'
        numbered_directory = bool(
            business_code and re.match(rf'^\D*0*{re.escape(normalized_code)}(?:\D|$)', name)
        )
        configured_business = _matches_configured_business_directory(name, business_registry, business_id)
        named_business = any(keyword in normalized_name for keyword in BUSINESS_DIRECTORY_KEYWORDS)
        # 含单位后缀的目录只有同时带业务编号时才按业务跳过，防止“供电服务有限公司”被误判。
        if (configured_business or named_business) and (not entity_like or numbered_directory):
            directory = directory.parent
            continue
        if entity_like:
            break
        business, _ = identify_business(Path(name))
        # 业务名称优先于其中的公司称谓，如“07物资…-计量分公司（负责人…）”。
        if '公司' in name or '本部' in name or not business:
            break
        directory = directory.parent
    return directory


def submission_group(file, root, business_registry=None):
    """返回隔离分组键；file/root 为扫描记录与输入根，business_registry 用于排除新版业务目录。"""
    if file.entity_code:
        return file.entity_code
    return '@目录:' + str(unit_directory(
        file.source, root, business_registry, file.business_id, file.business_code,
    ).relative_to(root))


def resolve_submission_scopes(root, files, entities, aliases, batch, *, include_hidden=False, source_report=None,
                              business_registry=None):
    """按实际上传材料确定审核范围。

    root 为输入目录，files 为扫描记录，entities/aliases 为主体名册及简称，batch 为本批标识；
    include_hidden/source_report 保留给现有调用方，business_registry 用于识别并跳过业务目录；
    范围不再读取适用性或应用清单附件。
    返回按主体分组的范围和核对报告。
    """
    root = Path(root).resolve()
    report = {'mode': 'uploaded_materials', 'batch': batch, 'sources': [], 'declarations': [], 'alerts': []}
    uploaded = defaultdict(set)
    for file in files:
        code = submission_group(file, root, business_registry)
        # 先保留主体桶，确保仅有说明材料时仍能复制原件并记录主体处理结果。
        uploaded[code]
        # 说明材料不增加业务；待解析 Excel 仍保留，以便通过真实表头识别矩阵或三清单。
        if file.material_type == 'explanation':
            continue
        if file.business_code:
            uploaded[code].add((file.business_id or file.business_code, file.business_code, file.variant_id))
    for code in uploaded:
        if code not in entities:
            report['alerts'].append({'type': 'scope_entity_unconfirmed', 'unit_label': code,
                                     'message': '本单位按单位目录名确定范围，主体代码仍待确认；同名单位目录已合并。'})
    scopes = {code: {'batch': batch, 'entity_codes': [code] if code in entities else [],
                     'businesses': [{'business_id': business_id, 'business_code': business_code,
                                     'variant_id': variant, 'required': True}
                                    for business_id, business_code, variant in sorted(keys)],
                     'source': '实际上传的风控矩阵及三清单'} for code, keys in sorted(uploaded.items())}
    report['submission_scopes'] = scopes
    report['entity_identities'] = {file.entity_code: file.preservation['entity_identity']
                                   for file in files if 'entity_identity' in file.preservation}
    return scopes, report


def write_submission_scope_report(directory, report, entities):
    """保存范围及集中提示；directory 为报告目录，report 为解析结果，entities 为主体名册。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / '本次审核范围.json'
    text_path = directory / '本次审核范围.md'
    write_json(json_path, report)
    lines = ['# 本次审核范围', '', '本次仅按实际上传的风控矩阵和三清单确定审核范围。',
             '适用性匹配统计表和应用清单不参与范围判断，也不产生漏报或声明冲突提示。', '',
             '| 单位 | 主体代码 | 清单匹配状态 | 纳入审核的业务及变体 |', '| --- | --- | --- | --- |']
    if report['mode'] == 'manual_override':
        lines[2] = '本次使用手动范围覆盖文件；范围外的实际上传材料仍会进入审核。'
    for code, scope in report['submission_scopes'].items():
        identity = report.get('entity_identities', {}).get(code, {})
        name = identity.get('entity_name') or (entities[code].name if code in entities else code.removeprefix('@目录:'))
        status = identity.get('entity_registry_message') or ('主体已匹配会计主体清单' if code in entities else '主体代码待确认，已继续审核')
        businesses = '、'.join(item['business_code'] + (f"（{item['variant_id']}）" if item['variant_id'] != 'default' else '')
                               for item in scope['businesses'])
        lines.append(f'| {name} | {code if code in entities else "待确认"} | {status} | {businesses or "无已确定业务"} |')
    lines.extend(['', '## 核对提示', ''])
    for alert in report['alerts']:
        location = f"{alert.get('entity_code') or alert.get('unit_label', '')} {alert.get('business_code', '')} {alert.get('file', '')}".strip()
        lines.append(f"- {location}：{alert['message']}")
    if not report['alerts']:
        lines.append('未发现范围核对提示。')
    lines.extend(['', '## 范围来源', ''])
    if report['sources']:
        lines.extend(f"- {source['file']}（SHA-256：{source['sha256']}）" for source in report['sources'])
    else:
        lines.append('- 实际上传的风控矩阵和三清单')
    text_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {'mode': report['mode'], 'entities': len(report['submission_scopes']),
            'alerts': len(report['alerts']), 'report': str(text_path), 'json': str(json_path)}
