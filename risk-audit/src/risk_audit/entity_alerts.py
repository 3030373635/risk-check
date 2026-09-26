"""Summarize unresolved entity identities once per source unit, with evidence."""
from collections import defaultdict
from pathlib import Path
import re

from risk_audit.util import norm_text, write_json


def build_entity_alerts(files, entities, missing_code=()):
    """集中记录匹配提示；files 为扫描记录，entities 为名册，missing_code 为缺代码的名册单位。"""
    groups = defaultdict(list)
    for file in files:
        if file.entity_code in entities and not file.entity_conflict:
            continue
        directory = next((part for part in file.relative_path.parts[:-1]
                          if re.search(r'公司(?:本部)?$', part)), '')
        if not directory:
            directory = next((part for part in reversed(file.source.parent.parts)
                              if re.search(r'公司(?:本部)?$', part)), '')
        # This is a verbatim directory label, not a confirmed entity mapping.
        label = re.sub(r'^\d+[\s.、-]*', '', directory) if directory else '未识别单位（见文件路径）'
        identity = file.preservation.get('entity_identity', {})
        label = identity.get('entity_name', label)
        missing = [e for e in missing_code if (norm_text(e.name) == norm_text(label) if identity else
                                             norm_text(e.name) in norm_text(str(file.source)))]
        if identity and not file.entity_conflict:
            reason = identity['entity_registry_message']
        elif file.entity_conflict:
            reason = '主体识别证据冲突，无法唯一确认代码'
        elif file.entity_code:
            reason = f'识别候选代码{file.entity_code}不在当前主体清单中'
        elif missing:
            reason = '主体清单中有同名单位，但单位代码为空'
        else:
            reason = '未能通过当前主体清单及已确认简称唯一匹配代码'
        groups[(label, reason)].append({
            'file': str(file.relative_path), 'source': str(file.source),
            'business_code': file.business_code, 'candidate_code': identity.get('candidate_code', file.entity_code),
            'group_key': identity.get('group_key', ''),
            'entity_registry_status': identity.get('entity_registry_status', 'unconfirmed'),
            'evidence': list(file.entity_evidence),
            'master_rows_without_code': [{'name': e.name, 'row': e.source_row} for e in missing],
        })
    return [{'unit_label': label, 'reason': reason, 'file_count': len(items), 'files': items,
             'next_step': ('主体证据冲突，审核已终止。请确认材料归属后重新审核。' if '冲突' in reason else
                           '已继续审核。请确认标准单位全称及会计主体代码，补充主体清单或确认简称映射后复核依赖名册身份的检查。')}
            for (label, reason), items in sorted(groups.items())]


def write_entity_alerts(directory, alerts):
    """保存集中提示；directory 为输出目录，alerts 为包含匹配状态和原始证据的提示列表。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report = directory / '主体代码待确认.md'
    write_json(directory / 'entity_alerts.json', alerts)
    lines = ['# 主体代码待确认', '']
    if not alerts:
        lines.append('本次材料没有待确认的主体代码。')
    else:
        lines.extend([f'本次有{len(alerts)}组主体识别提示，涉及{sum(a["file_count"] for a in alerts)}份文件。目录单位名称仅用于定位，尚未确认为会计主体。',
                      '', '未匹配名册的主体已继续审核，正式主体代码仍待确认；主体证据冲突时终止审核。请复核依赖名册身份的检查。同一原因集中提示，不按每一行重复增加提醒。', ''])
        for alert in alerts:
            lines.extend([f'## {alert["unit_label"]}', '', f'原因：{alert["reason"]}。', '', alert['next_step'], ''])
            for file in alert['files']:
                lines.append(f'- [原文件](<{file["source"]}>)：{file["file"]}')
                if file['evidence']:
                    lines.append('  识别证据：' + '；'.join(file['evidence']))
                for row in file['master_rows_without_code']:
                    lines.append(f'  主体清单：第{row["row"]}行，{row["name"]}的单位代码为空。')
            lines.append('')
    report.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return report
