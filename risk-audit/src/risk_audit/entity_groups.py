"""为清单外主体建立本次审核分组，保留正式代码与名册匹配状态。"""
from pathlib import Path

from risk_audit.submission_scope import relative_directory_identity, unit_directory
from risk_audit.entities import material_directory_name
from risk_audit.util import norm_text


def source_entity_name(file, root):
    """提取报送名称；file 为扫描记录，root 为输入根目录。"""
    return material_directory_name(file.source, root, file.business_directory)


def prepare_entity_groups(
    files,
    root,
    entities,
    missing_code=(),
    business_registry=None,
    *,
    entity_mode='registry',
):
    """返回主体元数据并赋予单位目录名分组标识。

    files 为无主体冲突的扫描记录，root 为输入根目录，entities 为正式名册，
    missing_code 为名册中代码为空的单位，business_registry 用于排除新版业务目录；
    entity_mode 指定名册或业务上层目录识别；内部标识只用于关联。
    """
    root = Path(root).resolve()
    groups = {}
    for file in files:
        candidate = file.entity_code
        if entity_mode == 'parent_directory':
            directory = unit_directory(file.source, root, file.business_directory)
            name = directory.name
            # 完整相对路径是主体身份；显示名仅使用业务上级目录的原始名称。
            key = '@目录主体:' + relative_directory_identity(directory, root)
            status = 'directory_matched'
            message = '主体已按业务上层目录识别'
            candidate = None
            file.entity_code = key
            file.parse_errors = [
                error for error in file.parse_errors if error != '无法确定会计主体'
            ]
        elif candidate in entities:
            name = entities[candidate].name
            key, status, message = candidate, 'matched', '主体已匹配会计主体清单'
        else:
            name = source_entity_name(file, root)
            directory = unit_directory(file.source, root, file.business_directory)
            missing = [entity for entity in missing_code if norm_text(entity.name) == norm_text(name)]
            if missing:
                name, status, message = missing[0].name, 'missing_code', '会计主体清单中有单位名称，但单位代码为空'
            elif candidate:
                status, message = 'code_not_listed', '识别主体代码不在会计主体清单中'
            elif name != '未识别单位（见文件路径）':
                status, message = 'not_listed', '主体不在会计主体清单中'
            else:
                status, message = 'unconfirmed', '未识别到单位目录，请核实材料目录'
            # 清单外主体按完整目录路径分组，同名但不同路径的主体不得合并。
            key = '@目录:' + relative_directory_identity(directory, root)
            # 非空分组标识使矩阵、三清单及资料级检查继续执行；正式代码仍为空。
            file.entity_code = key
            file.parse_errors = [error for error in file.parse_errors if error != '无法确定会计主体']
        identity = {'entity_name': name, 'entity_code': candidate if candidate in entities else '',
                    'candidate_code': candidate, 'group_key': key,
                    'entity_registry_status': status, 'entity_registry_message': message}
        file.preservation['entity_identity'] = identity
        groups.setdefault(key, identity)
    return groups
