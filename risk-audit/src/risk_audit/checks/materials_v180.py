"""确认稿的材料完整性、说明存在性与清单适用范围。"""
from collections import defaultdict
from dataclasses import replace

from risk_audit.util import measure_id_key, norm_text


def material_categories(file):
    """返回已报送的材料类别；file 为材料记录，工作表用于补充合并材料类别。"""
    types = {sheet.sheet_type for sheet in file.sheets}
    categories = set()
    if 'matrix' in types:
        categories.add('matrix')
    # 三清单是否已报送按文件类别判断，未匹配工作表只影响行级审核范围。
    if file.material_type == 'three_lists' or types & {'position_duty', 'incompatible_position', 'system_rule'}:
        categories.add('three_lists')
    return categories


def required_documents_v3(ctx, params):
    """按主体、业务、材料类别检查；ctx 为上下文，params 为所需类别及命名/说明开关。"""
    from risk_audit.checks.registry import required_documents_v2
    # 沿用命名和读取错误提示；报送状态按文件类别判断，合并材料由实际工作表补充类别。
    legacy = {**params, 'material_types': [], 'explanation_mode': False}
    out = required_documents_v2(ctx, legacy)
    if not params.get('material_types'):
        parse_error_evidence = {
            (str(file.relative_path), '；'.join(file.parse_errors))
            for file in ctx.files if file.parse_errors
        }
        # 读取错误只由材料完整性规则负责，避免命名和说明规则重复上报。
        out = [
            issue for issue in out
            if (issue['evidence'].get('file_path'), issue['evidence'].get('unavailable_reason'))
            not in parse_error_evidence
        ]
    present = defaultdict(list)
    for file in ctx.files:
        for category in material_categories(file):
            present[(file.entity_code, file.business_id or file.business_code, file.variant_id, category)].append(file)
    for code in ctx.scope_entity_codes:
        for business in ctx.scope_businesses:
            if not business.get('required', True):
                continue
            business_id = business.get('business_id') or business['business_code']
            bc, variant = business['business_code'], business.get('variant_id', 'default')
            required_categories = params.get('material_types', [])
            category_files = {
                category: present[(code, business_id, variant, category)]
                for category in required_categories
            }
            # 风控矩阵和三清单是可独立报送的业务材料；任一类别存在即满足业务材料要求。
            single_material_allowed = set(required_categories) == {'matrix', 'three_lists'}
            has_alternative_material = single_material_allowed and any(category_files.values())
            for category, files in category_files.items():
                evidence = {'entity_code': code, 'business_id': business_id,
                            'business_code': bc, 'variant_id': variant}
                if not files:
                    if has_alternative_material:
                        continue
                    out.append({'record': None, 'kind': 'violation', 'evidence': {**evidence, 'missing_material': category}})
                    continue
                # 同内容副本不计多个版本，分开报送的三张清单也不互相视为版本。
                seen = defaultdict(set)
                for file in files:
                    for sheet in file.sheets:
                        if (sheet.sheet_type == 'matrix') == (category == 'matrix'):
                            seen[sheet.sheet_type].add(file.sha256)
                if any(len(hashes) > 1 for hashes in seen.values()):
                    out.append({'record': None, 'kind': 'review', 'evidence': {**evidence, 'files': [str(f.relative_path) for f in files], 'unavailable_reason': f'同一业务存在多个不同内容的{category}版本，请明确正式版本'}})
    if params.get('explanation_mode'):
        # 业务独立审核时使用扫描主体名单，不能把缺报业务误判为主体材料全部缺失。
        codes = (set(ctx.resources['submitted_entity_codes']) if 'submitted_entity_codes' in ctx.resources else
                 {f.entity_code for f in ctx.files if material_categories(f) and f.entity_code})
        docs = [f for f in ctx.files if f.material_type == 'explanation' and f.true_format in {'doc', 'docx', 'pdf'} and not f.entity_conflict and not f.parse_errors]
        for code in sorted(set(ctx.scope_entity_codes) ^ codes):
            if not any(file.entity_code == code for file in docs):
                out.append({'record': None, 'kind': 'violation', 'evidence': {'entity_code': code, 'business_code': '', 'variant_id': 'default', 'missing_material': '主体差异说明'}})
    return out


def required_documents_v4(ctx, params):
    """按新命名口径检查材料；ctx 为审核上下文，params 可用 require_entity_name 控制主体名称提示。"""
    return required_documents_v3(ctx, params)


def applicable_list_rows(ctx, params):
    """过滤明确不适用于本主体的清单明细；ctx 为上下文，params 为责任主体限定配置。"""
    from risk_audit.checks.applicability_v180 import responsibility_applicability
    matrices = defaultdict(list)
    for row in ctx.all_records:
        if row.record_type == 'matrix':
            matrices[(row.entity_code, row.business_code, row.variant_id, measure_id_key(row.value('measure_id')))].append(row)
    selected = []
    for row in ctx.records:
        sources = matrices[(row.entity_code, row.business_code, row.variant_id, measure_id_key(row.value('measure_id')))]
        decisions = [responsibility_applicability(source, ctx.resources, params)[0] for source in sources]
        # 无编号、无矩阵或存在未决来源时保留，避免用缺少资料掩盖实际问题。
        if decisions and all(decision is False for decision in decisions):
            continue
        selected.append(row)
    return replace(ctx, records=selected)


def incompatible_constraints(ctx, params):
    """检查适用的不相容岗位清单具体性；ctx 为上下文，params 为字段约束参数。"""
    from risk_audit.checks.confirmed_v180 import field_constraints_v2
    return field_constraints_v2(applicable_list_rows(ctx, ctx.resources.get('responsibility_applicability', {})), params)


def applicable_deleted_text(ctx, params):
    """核查适用清单中的删除原文；ctx 为上下文，params 为源字段、目标字段及编号范围。"""
    from risk_audit.checks.confirmed_v180 import deleted_text_reappears_v3
    if ctx.records and ctx.records[0].record_type == 'incompatible_position':
        ctx = applicable_list_rows(ctx, ctx.resources.get('responsibility_applicability', {}))
    return deleted_text_reappears_v3(ctx, params)


def applicable_ordered_records(ctx, params):
    """检查适用清单顺序；ctx 为上下文，params 为排序字段及基准参数。"""
    from risk_audit.checks.confirmed_v180 import ordered_records_v3
    if ctx.records and ctx.records[0].record_type == 'incompatible_position':
        ctx = applicable_list_rows(ctx, ctx.resources.get('responsibility_applicability', {}))
    return ordered_records_v3(ctx, params)
