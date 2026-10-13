"""从实际上传的风控矩阵和三清单确定审核范围。"""
from collections import defaultdict
from pathlib import Path

from risk_audit.util import write_compact_json


def unit_directory(path, root, business_directory=None):
    """按固定层级取得主体目录。

    Args:
        path: 当前材料的绝对路径。
        root: 用户选择的输入根目录。
        business_directory: 扫描阶段确定的相对业务目录；无法确定时为 None。
    """

    path = Path(path).resolve()
    root = Path(root).resolve()
    if business_directory is not None:
        relative_business_directory = Path(business_directory)
        if relative_business_directory == Path('.'):
            # 输入根本身是业务目录，主体应取根目录的外层目录。
            return root.parent
        subject_directory = (root / relative_business_directory).resolve().parent
        # 规范业务目录必须在输入根内，禁止使用越界路径归属主体。
        if subject_directory == root or root in subject_directory.parents:
            return subject_directory
        raise ValueError(f'业务目录超出输入范围：{business_directory}')
    business_directory = path.parent
    if business_directory == root:
        # 直接选择业务目录时，主体目录是输入根的外层目录，不能把业务自身重复当主体。
        return root.parent
    subject_directory = business_directory.parent
    # 只接受输入根内部的直接上级；异常路径回退到输入根，禁止向外层猜测主体。
    if subject_directory == root or root in subject_directory.parents:
        return subject_directory
    return root


def relative_directory_identity(directory, root):
    """返回主体目录相对输入根的稳定身份。

    Args:
        directory: 已按固定层级定位的主体目录。
        root: 用户选择的输入根目录。
    """

    directory = Path(directory).resolve()
    root = Path(root).resolve()
    if directory == root.parent:
        return f'../{directory.name}'
    return directory.relative_to(root).as_posix()


def submission_group(file, root, business_registry=None):
    """返回隔离分组键；file/root 为扫描记录与输入根，business_registry 用于排除新版业务目录。"""
    if file.entity_code:
        return file.entity_code
    directory = unit_directory(file.source, root, file.business_directory)
    return '@目录:' + relative_directory_identity(directory, root)


def resolve_submission_scopes(
    root,
    files,
    entities,
    aliases,
    batch,
    *,
    include_hidden=False,
    source_report=None,
    business_registry=None,
    entity_mode='registry',
):
    """按实际上传材料确定审核范围。

    root 为输入目录，files 为扫描记录，entities/aliases 为主体名册及简称，batch 为本批标识；
    include_hidden/source_report 保留给现有调用方，business_registry 用于识别并跳过业务目录，
    entity_mode 控制是否生成名册待确认提示；
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
        if file.business_id or file.business_code:
            uploaded[code].add((file.business_id or file.business_code, file.business_code or '', file.variant_id))
    for code in uploaded:
        if code not in entities and entity_mode == 'registry':
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
    write_compact_json(json_path, report)
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
        if identity.get('entity_registry_status') == 'directory_matched':
            entity_code = '不适用（目录识别）'
        else:
            entity_code = code if code in entities else '待确认'
        lines.append(f'| {name} | {entity_code} | {status} | {businesses or "无已确定业务"} |')
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
