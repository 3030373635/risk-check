"""Compare logical business fields, preserving unknown multi-level headers."""
from collections import Counter
from risk_audit.readers.header_semantics import header_spec
from risk_audit.util import norm_text


def _field_label(field_id, field_labels, aliases):
    """返回缺失字段的展示名称；field_id 为逻辑字段，field_labels 为通用名称，aliases 为表头别名。"""
    return field_labels.get(field_id) or aliases.get(field_id, [field_id])[0]


def schema_contains_v2(ctx,p):
    """比对逻辑表头；ctx 为文件及基准上下文，p 为必需字段、基准和适用性尾列配置。"""
    out=[];aliases=ctx.resources.get('field_aliases',{}).get('matrix',{})
    reverse={norm_text(v):k for k,values in aliases.items() for v in values}
    ignored=set(p.get('ignore_fields',[]))
    def logical(path):return header_spec(path['path'][-1]['name'],'matrix',reverse)[0]
    def unknown_key(path):return tuple(norm_text(x['name']) for x in path['path'])
    for file in ctx.files:
        for s in file.sheets:
            if s.sheet_type not in p.get('sheet_types',[]):continue
            def emit(names,reason):
                """生成表结构待核实事项；names 为未识别字段，reason 为需要单位核实的原因。"""
                out.append({'record':s.records[0] if s.records else None,'kind':'review','evidence':{
                    # 空表没有明细记录，主体与业务仍须从文件保留，供资料级意见准确回填。
                    'entity_code':file.entity_code or '', 'business_id':file.business_id or file.business_code or '',
                    'business_code':file.business_code or '', 'variant_id':file.variant_id,
                    'issue_type':'schema_field_mapping','unavailable_reason':reason,'missing_fields':sorted(names),'file_path':str(file.relative_path),'sheet':s.title}})
            missing=set(p.get('required_fields',[]))-set(s.columns)
            baseline=ctx.baselines.get((file.business_id or file.business_code,file.variant_id),{})
            if p.get('compare_baseline'):
                missing|=set(baseline.get('fields',[]))-set(s.columns)-ignored
                actual=Counter(unknown_key(x) for x in s.business_header_paths if not logical(x))
                absent=[]
                for expected in baseline.get('business_header_paths',[]):
                    fid=logical(expected)
                    if fid:
                        if fid not in ignored and fid not in s.columns:missing.add(fid)
                        continue
                    key=unknown_key(expected)
                    if actual[key]:actual[key]-=1
                    else:absent.append('/'.join(key))
                if absent:emit(absent,'与0818基准相比，未找到以下业务字段：'+'、'.join(absent)+'。请确认是否删除或更名。')
            if missing:
                field_labels=p.get('field_labels',{})
                emit(missing,'未能读取必需字段：'+'、'.join(
                    _field_label(fid,field_labels,aliases) for fid in sorted(missing)
                )+'。请先核实字段对应或是否缺列。')
            if p.get('check_applicability_tail'):
                reference_cols={c['column'] for item in file.preservation.get('column_selections',[]) if item['sheet']==s.title and item.get('selected_column') is not None
                                for c in item['candidates'] if c.get('hidden') and not c.get('selected')}
                leaves=[x for x in s.business_header_paths if x['column'] not in reference_cols]
                app=s.columns.get('applicability');reason=s.columns.get('applicability_reason')
                if not app:continue  # Missing field already has one structural opinion.
                combined=any(x['column']==app and '是否适用及原因' in norm_text(x['path'][-1]['name']) for x in leaves)
                if leaves and combined and leaves[-1]['column']==app:continue
                if len(leaves)>=2 and (leaves[-2]['column'],leaves[-1]['column'])==(app,reason) and p.get('accept_split_applicability'):continue
                emit([],'适用性及原因未位于有效业务字段的最后。可使用最后一列“是否适用及原因”，或最后两列分别填写适用性和原因。')
    return out
