"""One reading per identical duty/role/problem pattern, all occurrences retained."""
from collections import Counter
from pathlib import Path
import csv

from risk_audit.util import norm_text, sha256_json, write_json

CHECKS = {'broad_responsibility_pattern', 'explicit_role_responsibility'}


def build_patterns(files, findings, limitations, scopes):
    records = {(r.file_path,r.sheet,r.row):r for f in files for s in f.sheets for r in s.records}
    hashes = {str(f.relative_path): f.sha256 for f in files}
    rows = {}
    for source, channel in ((findings, '单位意见'), (limitations, '内部解析')):
        for item in source:
            f = item.to_dict() if hasattr(item, 'to_dict') else item
            if f['check_id'] not in CHECKS: continue
            row_key = (f['file_path'],f['sheet'],f['row']); r = records.get(row_key)
            if r is None: continue
            allowed = {(b.get('business_id') or b['business_code'],b['variant_id']) for b in scopes.get(r.entity_code,{}).get('businesses',[])}
            if not r.entity_code or (r.business_id or r.business_code,r.variant_id) not in allowed: continue
            entry = rows.setdefault(row_key, {'record':r, 'issues':[]})
            e = f['evidence']
            entry['issues'].append({'check_id':f['check_id'], 'channel':channel, 'category':e.get('issue_type',''),
                                   'severity':f.get('severity','limitation'), 'message':f.get('message') or e.get('unavailable_reason',''),
                                   'finding_key':f.get('finding_key'), 'actual':e.get('actual_responsibilities',[]),
                                   'expected':e.get('expected','')})
    groups = {}
    for key, entry in rows.items():
        r = entry['record']; issues = sorted(entry['issues'],key=lambda x:x['check_id'])
        role, duty = norm_text(r.value('role')), norm_text(r.value('duty'))
        signature = [(x['check_id'],x['channel'],x['category'],x['severity'],x['expected'],x['actual']) for x in issues]
        identity = sha256_json([role,duty,signature])[:20]
        g = groups.setdefault(identity, {'pattern_id':identity,'role':role,'duty':duty,
            'problems':[{k:v for k,v in x.items() if k!='finding_key'} for x in issues],
            'locations':[], 'status':'待确认', 'automatic_approval':False,
            'scope_note':'仅归并第10、11条相同原文、角色及判定；不推导其他规则通过，不批量修改材料。'})
        g['locations'].append({'file':r.file_path,'sha256':hashes[r.file_path],'sheet':r.sheet,'row':r.row,
            'cell':r.fields['duty'].coordinate if 'duty' in r.fields else '', 'entity_code':r.entity_code,
            'business_id':r.business_id or r.business_code,'business_code':r.business_code,
            'variant_id':r.variant_id,'measure_id':r.value('measure_id'),
            'raw_duty':r.value('duty'),'finding_keys':[i['finding_key'] for i in issues if i['finding_key']]})
    for g in groups.values():
        g['affected_rows']=len(g['locations'])
        g['entity_count']=len({x['entity_code'] for x in g['locations']})
        g['unit_opinions']=sum(len(x['finding_keys']) for x in g['locations'])
    return sorted(groups.values(),key=lambda g:(-g['affected_rows'],g['pattern_id']))


def write_patterns(output, patterns):
    output = Path(output)
    write_json(output/'responsibility_patterns.json', patterns)
    with (output/'责任表述集中确认.csv').open('w',encoding='utf-8-sig',newline='') as h:
        w=csv.writer(h);w.writerow(['句式编号','角色','职责原文','具体问题','影响行数','涉及主体数','单位意见数','示例位置'])
        for p in patterns:
            loc=p['locations'][0]
            # Spreadsheet-safe plain text; source text remains intact in JSON.
            duty=p['duty'];duty="'"+duty if duty.startswith(('=','+','-','@')) else duty
            w.writerow([p['pattern_id'],p['role'],duty,'；'.join(x['channel']+'：'+x['message'] for x in p['problems']),
                        p['affected_rows'],p['entity_count'],p['unit_opinions'],f"{loc['file']}#{loc['sheet']}!{loc['cell']}"])
    summary={'patterns':len(patterns),'affected_rows':sum(p['affected_rows'] for p in patterns),
             'unit_opinions':sum(p['unit_opinions'] for p in patterns),
             'internal_patterns':sum(any(x['channel']=='内部解析' for x in p['problems']) for p in patterns),
             'automatic_approvals':0}
    write_json(output/'responsibility_patterns_summary.json',summary)
    return summary
