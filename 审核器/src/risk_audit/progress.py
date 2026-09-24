"""审核核心与桌面 Worker 之间的进度和取消契约。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class AuditProgressEvent:
    """描述审核核心的一次进度变化；字段均可安全序列化为 JSON。"""

    stage: str
    completed_units: int | None = None
    total_units: int | None = None
    current_entity: str | None = None
    current_business: str | None = None
    current_file: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回去除空值的 JSON 字典；无参数。"""
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }


class AuditCancelled(RuntimeError):
    """表示审核在安全工作单元边界收到用户取消。"""


ProgressCallback = Callable[[AuditProgressEvent], None]
CancelCheck = Callable[[], bool]


def emit_progress(
    callback: ProgressCallback | None,
    event: AuditProgressEvent,
) -> None:
    """发送进度事件；callback 为可选接收器，event 为待发送事件。"""
    if callback is not None:
        callback(event)


def raise_if_cancelled(cancel_check: CancelCheck | None) -> None:
    """在安全边界检查停止请求；cancel_check 为可选检查器，无返回值。"""
    if cancel_check is not None and cancel_check():
        raise AuditCancelled("用户已请求停止审核")
