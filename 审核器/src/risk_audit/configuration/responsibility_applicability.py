"""校验责任主体适用范围，禁止不完整或非法正则进入冻结规则。"""
import re


def validate_responsibility_applicability(config):
    """返回配置错误；config 为 restrictions 和 known_entity_patterns 组成的适用范围配置。"""
    errors = []
    if not isinstance(config, dict) or set(config) - {'restrictions', 'known_entity_patterns'}:
        return ['responsibility_applicability: 只允许 restrictions 和 known_entity_patterns']
    restrictions = config.get('restrictions')
    if not isinstance(restrictions, list) or not restrictions:
        return ['responsibility_applicability.restrictions: 必须为非空规则数组']
    ids = set()
    for index, restriction in enumerate(restrictions):
        prefix = f'responsibility_applicability.restrictions[{index}]'
        if not isinstance(restriction, dict) or not {'id', 'patterns', 'entity_patterns'} <= set(restriction) or set(restriction) - {'id', 'patterns', 'entity_patterns', 'required_patterns'}:
            errors.append(prefix + ': 必须包含 id、patterns、entity_patterns')
            continue
        identity = restriction['id']
        if not isinstance(identity, str) or not identity.strip() or identity in ids:
            errors.append(prefix + ': id 必须为不重复的非空字符串')
        else:
            ids.add(identity)
        for key in ('patterns', 'entity_patterns'):
            values = restriction[key]
            if not isinstance(values, list) or (key == 'patterns' and not values):
                errors.append(prefix + '.' + key + ': 必须为正则数组，责任主体模式不得为空')
                continue
            errors.extend(_patterns(values, prefix + '.' + key))
        if 'required_patterns' in restriction:
            values = restriction['required_patterns']
            if not isinstance(values, list) or not values:
                errors.append(prefix + '.required_patterns: 必须为非空正则数组')
            else:
                errors.extend(_patterns(values, prefix + '.required_patterns'))
    errors.extend(_patterns(config.get('known_entity_patterns', []), 'responsibility_applicability.known_entity_patterns'))
    return errors


def _patterns(values, prefix):
    """校验正则数组；values 为模式列表，prefix 为错误定位路径。"""
    if not isinstance(values, list):
        return [prefix + ': 必须为正则数组']
    errors = []
    for pattern in values:
        try:
            if not isinstance(pattern, str) or not pattern:
                raise ValueError('空模式')
            re.compile(pattern)
        except (re.error, ValueError):
            errors.append(prefix + ': 非法或空正则')
    return errors
