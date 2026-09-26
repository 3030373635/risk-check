"""为清单外主体建立本次审核分组，保留正式代码与名册匹配状态。"""
from pathlib import Path

from risk_audit.submission_scope import unit_directory
from risk_audit.entities import material_directory_name
from risk_audit.util import norm_text


def source_entity_name(file, root, business_registry=None):
    """提取报送名称；file/root 为扫描记录与输入根，business_registry 用于排除业务目录。"""
    return material_directory_name(
        file.source, root, business_registry, file.business_id, file.business_code,
    )


def prepare_entity_groups(files, root, entities, missing_code=(), business_registry=None):
    """返回主体元数据并赋予单位目录名分组标识。

    files 为无主体冲突的扫描记录，root 为输入根目录，entities 为正式名册，
    missing_code 为名册中代码为空的单位，business_registry 用于排除新版业务目录；
    内部标识只用于关联，不写入正式名册。
    """
    root = Path(root).resolve()
    groups = {}
    for file in files:
        candidate = file.entity_code
        if candidate in entities:
            name = entities[candidate].name
            key, status, message = candidate, 'matched', '主体已匹配会计主体清单'
        else:
            name = source_entity_name(file, root, business_registry)
            directory = unit_directory(
                file.source, root, business_registry, file.business_id, file.business_code,
            )
            missing = [entity for entity in missing_code if norm_text(entity.name) == norm_text(name)]
            if missing:
                name, status, message = missing[0].name, 'missing_code', '会计主体清单中有单位名称，但单位代码为空'
            elif candidate:
                status, message = 'code_not_listed', '识别主体代码不在会计主体清单中'
            elif name != '未识别单位（见文件路径）':
                status, message = 'not_listed', '主体不在会计主体清单中'
            else:
                status, message = 'unconfirmed', '未识别到单位目录，请核实材料目录'
            if name != '未识别单位（见文件路径）':
                # 清单外主体直接按单位目录名分组，使所有业务目录中的同名单位统一审核。
                key = '@名称:' + name
            else:
                # 无法取得单位目录名时保留完整目录路径，避免把未知单位错误合并。
                key = '@目录:' + str(directory.relative_to(root))
            # 非空分组标识使矩阵、三清单及资料级检查继续执行；正式代码仍为空。
            file.entity_code = key
            file.parse_errors = [error for error in file.parse_errors if error != '无法确定会计主体']
        identity = {'entity_name': name, 'entity_code': candidate if candidate in entities else '',
                    'candidate_code': candidate, 'group_key': key,
                    'entity_registry_status': status, 'entity_registry_message': message}
        file.preservation['entity_identity'] = identity
        groups.setdefault(key, identity)
    return groups
