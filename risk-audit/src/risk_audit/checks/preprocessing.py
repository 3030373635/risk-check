"""生成数据预处理阶段需要写回的提示。"""

from __future__ import annotations

from typing import Any

from risk_audit.system_type_preprocessing import (
    SYSTEM_TYPE_VALUES,
    classify_system_owner,
)
from risk_audit.util import norm_text


def system_type_notice_v1(context: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    """返回无法自动确定系统类型的预处理提示。

    Args:
        context: 当前系统控制规则清单的检查上下文。
        params: 预处理提示保留的空配置参数。
    """

    del params
    issues = []
    for record in context.records:
        # 人工已填写任一合法枚举值时，不再生成补充提示。
        if norm_text(record.value("system_type")) in SYSTEM_TYPE_VALUES:
            continue
        if classify_system_owner(record.value("system_owner")) is not None:
            continue
        issues.append({
            "record": record,
            "kind": "violation",
            "evidence": {"issue_type": "system_type_preprocessing_unresolved"},
        })
    return issues
