"""Plain audit wording. Presentation only: never changes findings or evidence."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _responsibility_phrase_opinion(evidence: dict[str, Any]) -> str:
    """生成第10条诊断意见；evidence 为责任句式检查输出的结构化证据。"""
    if evidence.get("fixed_rule_0917_9"):
        return (
            "岗位职责请按照国网标准句式编制，“对……的一致性/准确性/真实性/"
            "完整性/有效性/及时性/合规性负主体/审核/审批责任。”"
        )
    facts = evidence.get("responsibility_facts", {})
    statements = facts.get("statements", [])
    source = facts.get("normalized_text", "")

    # 证据中的责任类型和属性按原出现顺序去重，避免意见重复罗列同一内容。
    roles = list(dict.fromkeys(
        role for statement in statements for role in statement.get("roles", [])
    ))
    qualities = list(dict.fromkeys(
        quality for statement in statements for quality in statement.get("qualities", [])
    ))
    local_statements = [
        statement for statement in statements
        if not any(statement.get(key) for key in (
            "negative", "quoted", "delegated", "other_actor", "denied_later", "scope_unresolved",
        ))
    ]
    role = roles[0] if roles else "主体责任"
    quality = "、".join(qualities) if qualities else "准确性、完整性或合规性"
    suggestion = f"建议句式：“对……的{quality}负{role}。”"

    if any(statement.get("denied_later") for statement in statements):
        return (
            f"岗位职责责任句式不完整：已识别“{role}”"
            + (f"和责任属性“{'、'.join(qualities)}”" if qualities else "")
            + "，但前后存在否定或撤销责任的矛盾表述。请结合实际保留一致、肯定的责任句式。"
            + suggestion
        )

    negative = next((statement for statement in statements if statement.get("negative")), None)
    if negative:
        return (
            f"岗位职责责任句式不完整：已识别“{role}”"
            + (f"和责任属性“{'、'.join(qualities)}”" if qualities else "")
            + "，但当前为否定责任表述，未形成本岗位肯定承担责任的句式。"
            "请按实际职责改为肯定、完整的责任表述。"
            + suggestion
        )

    other = next((statement for statement in statements if statement.get("other_actor")), None)
    if other:
        actor = other.get("actor", "其他主体")
        return (
            f"岗位职责责任句式不完整：已识别“{actor}”承担“{role}”"
            + (f"，且包含责任属性“{'、'.join(qualities)}”" if qualities else "")
            + "，但未识别本岗位肯定承担责任的表述。请结合本岗位实际分工补充。"
            + suggestion
        )

    missing_quality = next((
        statement for statement in local_statements
        if statement.get("object_present") and statement.get("roles") and not statement.get("qualities")
    ), None)
    if missing_quality:
        predicate = missing_quality.get("predicate", role)
        return (
            f"岗位职责责任句式不完整：已识别“{predicate}”和责任对象，但未明确责任属性。"
            "请结合实际补充“一致性、准确性、真实性、完整性、有效性、及时性、合规性”等至少一项责任属性。"
            "建议句式：“对……的一致性/准确性/真实性/完整性/有效性/及时性/合规性负主体/审核/审批责任。”"
        )

    if not statements:
        return (
            f"岗位职责责任句式不完整：已识别工作动作“{source}”，"
            "但未识别责任对象、责任属性和肯定承担责任的表述。请结合实际补充完整责任句式。"
            "建议句式：“对……的准确性、完整性或合规性负……责任。”"
        )

    return (
        f"岗位职责责任句式不完整：已识别“{role}”"
        + (f"和责任属性“{'、'.join(qualities)}”" if qualities else "")
        + "，但尚未形成责任对象、责任属性与肯定承担责任相互关联的完整句式。"
        "请结合实际补全并理顺表述。"
        + suggestion
    )


def advice_v2(check_id: str, kind: str, e: dict[str, Any]) -> str:
    """生成审核意见；check_id 为检查编号，kind 为结果类别，e 为检查证据。"""
    category = e.get('issue_type')
    if check_id == 'system_rule_changes' and category == 'system_rule_change_mismatch':
        return '请核实与矩阵对应的系统规则是否应该做出修改。'
    if check_id == 'measure_applicability_alignment' and category == 'measure_applicability_mismatch':
        return '请核实适用性。'
    if check_id == 'responsibility_department_alignment' and category == 'responsibility_department_mismatch':
        return '请核实是否与矩阵责任主体对应。'
    if check_id == 'broad_responsibility_pattern' and category == 'responsibility_phrase_missing':
        return _responsibility_phrase_opinion(e)
    if check_id == 'duplicate_duties' and category == 'duplicate_duty':
        grouped: dict[tuple[str, str], list[int]] = {}
        for location in e.get('duplicate_locations', []):
            key = (Path(location.get('file', '')).name, location.get('sheet', ''))
            grouped.setdefault(key, []).append(location.get('row'))
        references = []
        for (file_name, sheet), rows in sorted(grouped.items()):
            row_text = '、'.join(str(row) for row in sorted(set(rows)) if row is not None)
            references.append(f'《{file_name}》“{sheet}”第{row_text}行')
        counterpart = '；'.join(references)
        return f'本行与{counterpart}内容重复，重复项保留一个即可。'
    if check_id == 'handler_reviewer_overlap_0916':
        if category == 'explicit_role_missing':
            return '请补充明确角色。'
        if category == 'invalid_role':
            return '请核实角色。'
        if category == 'same_duty_person_overlap':
            return f'同一条措施，同样的岗位职责，经办审核不能同一人。涉及人员：{e["conflict_people"]}；对应《{Path(e["related_file"]).name}》{e["related_sheet"]}第{e["related_row"]}行。'
        return e.get('unavailable_reason') or advice_v2('handler_reviewer_overlap', kind, e)
    if category == 'sheet_measure_order':
        return f'本表存在同一措施的记录分散或措施顺序倒置，共{e["issue_count"]}处。请将同一措施的记录排在一起，并按矩阵中的措施顺序调整。'
    pending = '待核实：'
    if check_id == 'matrix_duty_coverage' and category == 'responsibility_empty':
        return '矩阵本行未填写责任主体，请补充承担本措施的责任主体，并与岗位清单对应。'
    if check_id=='matrix_schema' and category=='schema_field_mapping':
        return pending + e['unavailable_reason']
    if check_id == 'carrier_subset' and e.get('source_field'):
        label = '控制载体' if e.get('source_kind') == 'carrier_field' else '岗位职责'
        location = f"{label}（{e['source_cell']}）" if e.get('source_cell') else label
        if category == 'carrier_source_unavailable':
            return pending + f'{location}没有可读取的内容，请核实栏目、填写值或公式计算结果后再核对引用。'
        if category == 'carrier_reference_unmatched':
            names = '、'.join(f'“{x}”' for x in e.get('missing_references', []))
            return pending + f'{location}引用的{names}未在本措施矩阵的控制载体中找到，请核实名称或引用是否一致。'
        if category == 'carrier_parse_unavailable':
            fragment = e.get('unavailable_reason', '').partition('解析：')[2] or e.get('original_sentence', '')
            quote = fragment[:80] + ('……' if len(fragment) > 80 else '')
            return pending + f'{location}中“{quote}”暂未解析出明确的载体对应关系；请先确认是否涉及单据、记录等引用，如涉及，再与本措施矩阵核对。'
    if check_id == 'carrier_subset' and category in {
        'carrier_scope_unconfirmed', 'carrier_measure_id_missing', 'carrier_target_unavailable',
    }:
        return pending + e['unavailable_reason'].rstrip('。') + '，请补齐或确认后再核对。'
    if check_id == 'entity_name_specific':
        if kind == 'review':
            return pending + '未能读取矩阵“责任主体”栏目，请核实栏目及公式计算结果后，再检查责任单位名称。'
        names = '、'.join(f'“{x}”' for x in e['generic_unit_names'])
        return f'责任主体中的{names}表述过于笼统，请明确实际承担责任的具体单位名称。'
    if check_id in {'department_specific', 'position_specific', 'person_required'} and category:
        # 不相容岗位可拆为 A/B 两列，意见沿用岗位具体性的同一标准。
        field_key = 'position' if e['field'] in {'position_a', 'position_b', 'position_c', 'position_name'} else e['field']
        field = {'department': '部门', 'position': '岗位名称', 'person_names': '人员姓名'}[field_key]
        if category == 'field_unavailable':
            return pending + f'未能读取“{field}”栏目，请核实是否缺列或公式未保存计算结果。'
        if category == 'multiple_positions':
            return '请拆分岗位，每个岗位一行，方便后期大表合并。'
        return {'department': '请明确到具体部门。', 'position': '请明确到具体岗位名称。', 'person_names': '请具体到人员，填写实际执行人员姓名。'}[field_key]
    if check_id == 'matrix_duty_coverage':
        if category == 'duty_measure_name_conflict':
            names='、'.join(f'“{x}”' for x in e['candidate_measure_ids'])
            return pending + f'矩阵编号为“{e["measure_id"]}”，岗位清单中有{names}。两处的环节序号和措施序号相同，但名称不同，请核实并统一编号后核对岗位责任。'
        if category == 'coverage_scope_unconfirmed':
            return pending + '主体、业务或矩阵类型尚未明确，请确认本表归属后核对岗位清单。'
        if category == 'coverage_applicability_unknown':
            return pending + '本措施是否适用尚未明确，请确认后核对是否需要列入岗位责任清单。'
        if category == 'coverage_mapping_unconfirmed':
            if e.get('mapping_details'):
                parts = []
                detailed = {x['responsibility'] for x in e['mapping_details']}
                for item in e['mapping_details']:
                    dept, position = item['matrix_department'], item['matrix_position']
                    positions = item['duty_positions']
                    if positions:
                        names = '、'.join(f'“{x}”' for x in positions)
                        parts.append(f'“{dept}”在矩阵中填写的岗位为“{position}”，同一措施岗位清单中填写的岗位为{names}，请核实对应关系。')
                    elif item.get('department_candidates'):
                        departments = '、'.join(f'“{x}”' for x in item['department_candidates'])
                        parts.append(f'“{position}”在矩阵中属于“{dept}”，同一措施岗位清单中属于{departments}。请核实是否为同一部门，并统一名称。')
                    else:
                        parts.append(f'矩阵中的“{dept}—{position}”尚未与同一措施岗位清单的部门、岗位对应，请核实对应关系。')
                other = [x for x in e['responsibility_items'] if x not in detailed]
                if other:
                    names = '、'.join(f'“{x}”' for x in other)
                    parts.append(f'矩阵中的{names}尚未与岗位清单的具体部门、岗位对应，请核实对应关系。')
                return pending + ''.join(parts)
            names = '、'.join(f'“{x}”' for x in e.get('responsibility_items', []))
            return pending + (f'矩阵中的{names}尚未与岗位清单的具体部门、岗位对应，请核实对应关系。' if names else '矩阵本行责任主体未明确，请补充后与岗位清单对应。')
        if category == 'coverage_position_ambiguous':
            parts = []
            for item in e.get('ambiguous_positions', []):
                names = '、'.join(f'“{name}”' for name in item['positions'])
                parts.append(f'本措施下“{item["department"]}”有多个岗位标注“{item["grade"]}”：{names}')
            return pending + '；'.join(parts) + '。请明确矩阵中的职级称呼对应哪些具体岗位。'
    if check_id == 'handler_reviewer_overlap' and category:
        if category == 'separation_field_unavailable':
            label='角色' if e['field']=='role' else '人员姓名'
            return pending + f'本表“{label}”列未能识别，请先确认列位置，再核对经办与审核人员。'
        if category == 'explicit_role_missing': return '请补充明确角色，只能填写“经办”“审核”或“审批”。'
        if category == 'explicit_role_unavailable': return pending + '角色使用公式，但没有可读取的计算结果，请重新计算并保存后核对。'
        if category == 'invalid_role': return f'角色填写为“{e.get("actual_text", "")}”，请改为“经办”“审核”或“审批”。'
        if category == 'person_missing': return pending + '本行实际执行人员姓名未填全，请补充后核对经办与审核是否为同一人。'
        if category == 'separation_scope_missing': return pending + '主体、业务、矩阵类型或控制措施编号未明确，暂时无法核对人员分工。'
        if category == 'separation_duty_missing': return pending + '经办与审核人员存在重合，但岗位职责未填全或无法读取，请补充后核对。'
        if category == 'same_duty_person_overlap':
            ref = f'《{Path(e["related_file"]).name}》“{e["related_sheet"]}”第{e["related_row"]}行'
            people = e['conflict_people']
            positions = '、'.join(x for x in e.get('positions', []) if x)
            if kind == 'violation':
                return f'同一条措施，同样的岗位职责，经办审核不能同一人。涉及{people}，岗位为{positions}；对应{ref}，请调整分工。'
            return pending + f'本行与{ref}的经办、审核姓名均含“{people}”，且职责仅责任类型不同。请核实是否同一人；同一条措施、同样的岗位职责，经办审核不能同一人。'
    if check_id in {'broad_responsibility_pattern', 'explicit_role_responsibility'}:
        if category == 'responsibility_self_contradiction':
            return '岗位职责前文写明承担责任，后文又否认本岗位承担该责任，表述相互矛盾。请明确实际承担的责任并统一表述。'
        if category == 'responsibility_other_actor':
            return '本行只写明了其他主体承担的责任，未写明本岗位承担的责任。请结合本岗位实际分工补充。'
        if category == 'responsibility_expression_malformed':
            spans = e.get('responsibility_facts', {}).get('malformed_spans', [])
            fragment = spans[0]['text'] if spans else ''
            fragment = fragment[-70:]
            return f'岗位职责中的“{fragment}”责任表述不完整或有错字。请补全承担责任的表述并核对文字。'
    if check_id == 'broad_responsibility_pattern' and category:
        if category == 'responsibility_quality_missing':
            facts = e.get('responsibility_facts', {})
            if facts.get('parser_version') == 2 and facts.get('quality_terms'):
                statements = [s for s in facts.get('statements', []) if not any(s.get(k) for k in
                              ('negative', 'quoted', 'delegated', 'other_actor', 'denied_later'))]
                if statements:
                    fragment = statements[0]['text']
                    fragment = ('……' + fragment[-85:]) if len(fragment) > 85 else fragment
                    return f'责任表述“{fragment}”未明确责任属性。请说明对这项工作的准确性、完整性、合规性等哪一方面承担责任。'
            return '岗位职责已写明承担责任，但未说明对什么方面负责。请结合本项工作，补充准确性、完整性、合规性等至少一项责任属性。'
        if category == 'responsibility_assertion_missing':
            return '岗位职责未写明本岗位承担的责任，请补充类似“对……的准确性负……责任”的表述。'
        if kind == 'review': return pending + '未能读取岗位职责，请核实栏目是否保留、公式是否保存计算结果。'
        return '岗位职责缺少基本责任表述，请补充类似“对……的准确性负……责任”的内容。准确性、一致性、真实性或完整性等责任属性具备一项即可。'
    if check_id == 'explicit_role_responsibility' and category:
        expected=e.get('expected',''); role=e.get('role','')
        actual='、'.join('“'+x+'”' for x in e.get('actual_responsibilities',[]))
        if category == 'responsibility_role_conflict':
            return f'角色填写为“{role}”，职责却写为承担{actual}，与应承担的“{expected}”不一致。请核实实际分工并统一表述。'
        if category == 'responsibility_role_missing':
            return f'角色填写为“{role}”，职责未明确承担“{expected}”。请补充相应责任类型。'
        if category == 'responsibility_multiple_types':
            return pending + f'角色填写为“{role}”，职责中同时明确承担{actual}。请核实是否需要按实际分工拆分，或调整责任类型。'
    if check_id == 'carrier_subset' and category == 'carrier_parse_unavailable':
        fragment = e.get('unavailable_reason', '').partition('解析：')[2] or e.get('original_sentence', '')
        quote = fragment[:80] + ('……' if len(fragment) > 80 else '')
        return pending + f'职责中“{quote}”与本措施矩阵载体的对应关系尚未核清，请核实所指单据、台账等是否一致。'
    if check_id == 'deleted_text_in_duties' and kind == 'violation':
        if e.get('final_text_wins'):
            parts = []
            for text in e['deleted_texts']:
                match = next(x for x in e['matches'] if x['deleted_text'] == text)
                label = {'control_measure': '控制措施', 'carrier': '控制载体'}[match['source_field']]
                parts.append(f'“{text.strip()}”（矩阵“{label}”栏{match["source_cell"]}）')
            return '本措施矩阵已删除' + '；'.join(parts) + '，最终保留内容中也未列出。本行岗位职责仍有该内容，请删除相关表述并理顺语句。'
        parts = []
        seen = set()
        for match in e.get('matches', []):
            key = (match['deleted_text'], match['source_file'], match['source_sheet'], match['source_cell'])
            if key in seen: continue
            seen.add(key)
            parts.append(f'“{match["deleted_text"]}”（《{Path(match["source_file"]).name}》“{match["source_sheet"]}”{match["source_cell"]}）')
        return '本行岗位职责仍出现矩阵中已划删除线的原文：' + '；'.join(parts) + '。请同步删除或修改。'
    return advice(check_id, kind, e).replace('需人工核实：', '待核实：')


def advice(check_id: str, kind: str, evidence: dict[str, Any]) -> str:
    e = evidence
    reason = str(e.get("unavailable_reason", ""))
    pending = "需人工核实："
    field = e.get("field_label") or {
        "department": "部门名称", "position": "岗位名称", "person_names": "人员姓名",
        "measure_id": "控制措施编号", "duty_id": "岗位职责编号", "duty": "岗位职责",
        "applicability": "是否适用", "applicability_reason": "不适用原因",
    }.get(e.get("field"), "该项内容")
    if check_id == "deleted_text_in_duties":
        if kind == "review": return pending + reason.rstrip("。") + "。"
        first_by_text = {}
        for match in e.get("matches", []):
            first_by_text.setdefault(match["deleted_text"], match)
        parts = []
        for text, match in first_by_text.items():
            label = {"control_measure": "控制措施", "carrier": "控制载体"}[match["source_field"]]
            parts.append(f"“{text.strip()}”（矩阵“{label}”栏{match['source_cell']}）")
        return "本行岗位职责仍保留矩阵中已划删除线的原文：" + "；".join(parts) + "。请同步删除或修改。"
    if check_id == "carrier_subset":
        category = e.get("issue_type")
        if category == "carrier_reference_unmatched":
            names = "、".join(f"“{x}”" for x in e.get("missing_references", []))
            return pending + f"职责中提到{names}，但本措施的“控制载体”栏未列出。请核实是否漏填或名称不一致。"
        if category == "carrier_parse_unavailable":
            return pending + "本行职责表述较复杂，程序无法判断所用单据、台账等是否与矩阵一致，请对照本措施的“控制载体”栏核对。"
        if category == "matrix_ambiguous":
            if e.get("matrix_carrier_sets"):
                return pending + "矩阵中同一措施出现多次，且“控制载体”填写不一致。请核实应以哪一处为准。"
            return pending + "未找到本单位、本业务中与该编号对应的矩阵措施，暂时无法核对控制载体。请核实编号及矩阵是否齐全。"
    if check_id == "measure_exists":
        return (pending if kind == "review" else "") + f"本单位本业务的矩阵中未找到控制措施编号“{e.get('reference', '')}”，请核实编号是否填写正确、矩阵是否完整。"
    if check_id == "matrix_duty_coverage":
        if e.get("issue_type") == "duty_record_missing":
            return f"岗位责任清单中未列出控制措施“{e.get('measure_id', '')}”，请补充对应岗位职责、岗位和人员。"
        if e.get("issue_type") == "specific_responsibility_missing":
            names = "、".join(f"“{x}”" for x in e.get("missing_responsibilities", []))
            return pending + f"本措施的岗位责任清单中未找到矩阵列明的{names}，请核实是否漏列。"
        if e.get("issue_type") == "coverage_measure_id_missing":
            return "矩阵本行未填写控制措施编号，请补充后与岗位责任清单对应。"
        if "未找到对应岗位职责" in reason:
            return pending + "岗位责任清单中未找到本措施对应的记录，请核实是否漏列，并补充相关岗位及人员。"
        if "泛称" in reason:
            return pending + "矩阵使用了概括性岗位名称，程序无法确认与清单中具体岗位的对应关系。请核实本措施涉及的部门、岗位和人员是否已全部列入清单。"
        names = reason.partition("映射：")[2] or str(e.get("responsibility", ""))
        return pending + f"矩阵列出的“{names}”尚未与岗位责任清单中的具体岗位对应。请核实清单是否已列入相关岗位和人员。"
    if check_id == "handler_reviewer_overlap":
        names = e.get("conflict_people")
        match = re.search(r"姓名重合（(.+?)）", reason)
        names = names or (match.group(1) if match else "")
        if names:
            if kind == "violation":
                return f"本措施的经办与审核由同一人员承担（{names}），请调整分工，分别安排人员。"
            return pending + f"经办与审核名单出现相同姓名（{names}）。请核实是否为同一人；如为同一人，请调整分工。"
        if "姓名缺失" in reason:
            return "经办或审核人员姓名未填全，请补充后核实经办与审核是否由不同人员承担。"
        return pending + "本行未明确人员承担经办、审核还是审批职责，暂时无法核实经办与审核是否由不同人员承担。请确认职责分工。"
    if check_id == "matrix_schema":
        if "拆分符合性尚未确认" in reason:
            return pending + "“是否适用及原因”被拆为“是否适用”和“修改备注”两列。请确认是否符合本次报送格式，并核实不适用原因是否已填齐。"
        if e.get("missing_fields"):
            return "矩阵缺少省公司0818模板要求保留的栏目，请对照模板补齐。"
        return pending + "矩阵栏目与省公司0818模板的对应关系尚未核清，请对照模板检查有无缺列，以及“是否适用及原因”是否保留。"
    if check_id == "applicability_content":
        category = e.get("issue_type")
        if category == "applicability_empty": return "本行未填写是否适用，请补充。"
        if category == "not_applicable_reason_empty": return "本行已填写“不适用”，但未说明原因，请在原因或对应备注栏补充。"
        if category in {"applicability_cache_missing", "reason_cache_missing"}:
            label = "是否适用" if category == "applicability_cache_missing" else "原因或备注"
            return pending + f"“{label}”使用公式，但没有可读取的计算结果。请用Excel重新计算并保存后再核对。"
        return pending + f"本行适用情况填写为“{e.get('actual_text', '')}”，请明确适用或不适用。"
    if check_id in {"remove_smart_control_platform", "remove_smart_control_platform_from_matrix"}:
        if kind == "review":
            return pending + "程序暂时无法核清本行的系统内容，请检查是否仍保留“数字化项目智慧管控平台”的权限或规则。涉及该平台的内容应删除。"
        return "本单位不适用“数字化项目智慧管控平台”，请删除本行涉及该平台的系统权限或规则内容。"
    if check_id == "broad_responsibility_pattern":
        if kind == "review":
            return pending + "本行的责任表述暂时无法核清，请检查岗位职责是否填写完整、是否写明应承担的责任。"
        return "岗位职责中未写明应承担的责任，请补充类似“对……的准确性、一致性承担……责任”的表述。"
    if check_id == "explicit_role_responsibility":
        if kind == "violation":
            return f"本行填写的角色为“{e.get('role', '')}”，但岗位职责中未体现相应的{e.get('expected', '责任')}，请核对并修改。"
        if "同时出现" in reason:
            return pending + "岗位职责中同时写有多种责任，请核实与本行填写的经办、审核或审批角色是否一致。"
        return pending + "本行角色未填写清楚，暂时无法核对与岗位职责中的责任是否一致。请确认属于经办、审核还是审批。"
    if check_id.endswith("measure_order"):
        if e.get("reason") == "同一措施记录不连续":
            return f"同一控制措施的记录分散在不同位置，请将“{e.get('measure_id', '')}”的相关记录排在一起。"
        return f"“{e.get('measure_id', '')}”的排列顺序与参考顺序不一致，请按矩阵中的措施顺序调整。"
    if check_id == "not_applicable_reason" and kind == "violation":
        return "本行已填写“不适用”，但未说明原因，请补充不适用原因。"
    if check_id in {"department_specific", "position_specific", "person_required", "duty_id_required", "duty_required", "measure_id_required", "applicability_required", "not_applicable_reason"}:
        if kind == "review":
            if "公式" in reason:
                return pending + f"“{field}”使用公式，但文件中没有可读取的计算结果。请用Excel重新计算并保存后再核对。"
            return pending + f"表中尚未识别出“{field}”对应的栏目，请核实该栏目是否保留、表头是否清楚。"
        if e.get("reason") == "内容不具体":
            label = {"department": "部门名称", "position": "岗位名称"}.get(e.get("field"), field)
            return f"{label}不具体，请填写本单位实际的{label}。"
        return f"“{field}”未填写，请补充。"
    if check_id == "incompatible_sheet_exists":
        return "未找到“不相容岗位清单”，请核实是否漏报。"
    if check_id in {"required_matrix_and_lists", "file_naming"}:
        if e.get("missing_material"):
            name = {"matrix": "风控矩阵", "three_lists": "三清单"}.get(e["missing_material"], e["missing_material"])
            return f"本次应报材料中缺少{name}，请补报。"
        if e.get("naming_issue"):
            return f"文件名称需调整：{e['naming_issue']}。"
        if "存在多个" in reason:
            return pending + "同一单位、同一业务报送了多个版本，请明确本次审核使用哪一份。"
        return pending + "报送文件未能正常读取，或单位、业务归属尚未确认。请检查文件是否可正常打开，并核对文件名、目录与表内单位名称是否一致。"
    if check_id == "entity_difference_explanation":
        return pending + "报送材料与会计主体清单未能逐一对应，请核实材料归属；如确有主体差异，请提供相应的Word说明材料。"
    return pending + reason.rstrip("。；;") + "。" if reason else "请核实本项填写内容是否符合审核要求。"
