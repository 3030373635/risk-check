"""Visible-only audit scope with a separate, non-finding notice for reviewers."""
from pathlib import Path
from risk_audit.util import write_json


def hidden_sheet_alerts(files):
    return [{'file': str(f.relative_path), 'source': str(f.source),
             'policy': f.preservation.get('sheet_visibility_policy', 'visible_only'),
             'visible_business_sheets': [s.title for s in f.sheets if s.title not in {h['title'] for h in f.preservation.get('hidden_sheets', [])}],
             'sheets': f.preservation['hidden_sheets']}
            for f in files if f.preservation.get('hidden_sheets')]


def write_hidden_sheet_alerts(directory, alerts, *, include_hidden=False):
    directory = Path(directory)
    entries = [s for f in alerts for s in f['sheets']]
    skipped = sum(s['action'] == 'skipped_hidden' for s in entries)
    report = directory / '隐藏工作表处理提示.md'
    write_json(directory / 'hidden_sheet_alerts.json', alerts)
    summary = {'policy': 'include_hidden' if include_hidden else 'visible_only', 'files': len(alerts),
               'detected': len(entries), 'skipped': skipped,
               'audited': sum(s['action'] == 'audited' for s in entries), 'report': str(report)}
    lines = ['# 隐藏工作表处理提示', '']
    if not entries:
        lines.append('本次已解析材料未发现隐藏工作表。')
    else:
        lines.extend([f'发现{len(alerts)}份文件含{len(entries)}张隐藏工作表；按隐藏策略跳过{skipped}张。', '',
                      '本次已明确选择将隐藏表纳入读取范围，仍执行正式业务表识别及历史版本筛选。' if include_hidden else
                      '默认忽略hidden和veryHidden工作表，不生成这些表的业务记录、审核意见或跨表引用。这份清单只提示审核人员是否需要查看，不计为整改问题。', '',
                      '需要审核其中某张表时，可在副本中取消该表隐藏后重新审核；也可明确使用 --include-hidden 将本次输入包所有隐藏表纳入读取。原文件及隐藏状态不会由本提示改变。', ''])
        actions = {'skipped_hidden': '已忽略，未审核', 'audited': '已按明确选择纳入审核',
                   'excluded_historical': '已按历史版本规则排除', 'not_business': '未识别为正式业务表'}
        for file in alerts:
            lines.extend([f"## {file['file']}", '', f"[打开原文件](<{file['source']}>)", ''])
            for s in file['sheets']:
                lines.append(f"- {s['title']}：{s['state']}；{actions[s['action']]}。")
            if not file['visible_business_sheets']:
                lines.append('- 未识别到可审核的可见业务表，请审核人员确认是否需要查看隐藏内容。')
            lines.append('')
    directory.mkdir(parents=True, exist_ok=True)
    report.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return summary
