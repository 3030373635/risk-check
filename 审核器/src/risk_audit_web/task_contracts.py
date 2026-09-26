"""Web 任务请求、状态、索引和事件的 JSON 契约。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, Mapping, TypeVar


SCHEMA_VERSION = "1.0"
TASK_STATUSES = frozenset({
    "running",
    "cancelling",
    "completed",
    "partial",
    "failed",
    "cancelled",
    "interrupted",
})


class ContractError(ValueError):
    """表示任务 JSON 不符合当前协议。"""


ContractType = TypeVar("ContractType")


def _known_fields(contract_type: type[ContractType], value: Mapping[str, Any]) -> dict[str, Any]:
    """提取数据类已知字段；参数为目标类型和原始映射。"""
    names = {field.name for field in fields(contract_type)}
    missing = [name for name in names if name not in value]
    if missing:
        raise ContractError(f"任务数据缺少必填字段：{', '.join(sorted(missing))}")
    return {name: value[name] for name in names}


def _validate_schema(schema_version: str) -> None:
    """验证协议版本；schema_version 为请求中的版本字符串。"""
    if schema_version != SCHEMA_VERSION:
        raise ContractError(f"不支持的任务协议版本：{schema_version}")


def _validate_absolute_paths(value: Mapping[str, str | None], field_names: tuple[str, ...]) -> None:
    """验证绝对路径字段；value 为对象字典，field_names 为需检查字段。"""
    for field_name in field_names:
        raw_path = value.get(field_name)
        if raw_path is not None and not Path(raw_path).is_absolute():
            raise ContractError(f"路径字段 {field_name} 必须是绝对路径：{raw_path}")


@dataclass(frozen=True)
class TaskRequest:
    """描述 Worker 的完整输入；所有路径均为绝对路径。"""

    schema_version: str
    task_id: str
    display_name: str
    input_root: str
    output_root: str
    rulepack: str
    entity_file: str
    baseline_root: str
    config_file: str | None
    soffice_path: str
    created_at: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskRequest":
        """解析任务请求；value 为 JSON 映射，返回已验证请求。"""
        known = _known_fields(cls, value)
        _validate_schema(str(known["schema_version"]))
        _validate_absolute_paths(known, (
            "input_root", "output_root", "rulepack", "entity_file",
            "baseline_root", "config_file", "soffice_path",
        ))
        return cls(**known)

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容字典；无参数。"""
        return asdict(self)


@dataclass(frozen=True)
class TaskState:
    """描述 Web 可轮询的单任务状态。"""

    schema_version: str
    task_id: str
    status: str
    stage: str
    completed_units: int
    total_units: int | None
    progress_percent: int | None
    current_entity: str | None
    current_business: str | None
    current_file: str | None
    worker_pid: int | None
    started_at: str | None
    heartbeat_at: str
    message: str
    error_code: str | None
    result_summary: dict[str, Any] | None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskState":
        """解析任务状态；value 为 JSON 映射，返回已验证状态。"""
        known = _known_fields(cls, value)
        _validate_schema(str(known["schema_version"]))
        if known["status"] not in TASK_STATUSES:
            raise ContractError(f"未知任务状态：{known['status']}")
        progress = known["progress_percent"]
        if progress is not None and (not isinstance(progress, int) or not 0 <= progress <= 100):
            raise ContractError(f"任务进度必须位于 0 到 100：{progress}")
        return cls(**known)

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容字典；无参数。"""
        return asdict(self)

    def with_updates(self, **changes: Any) -> "TaskState":
        """生成字段更新后的新状态；changes 为需要替换的字段。"""
        return replace(self, **changes)


@dataclass(frozen=True)
class TaskRecord:
    """描述便携任务索引中的一条记录。"""

    schema_version: str
    task_id: str
    display_name: str
    input_root: str
    output_root: str
    created_at: str
    state_path: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskRecord":
        """解析任务索引记录；value 为 JSON 映射。"""
        known = _known_fields(cls, value)
        _validate_schema(str(known["schema_version"]))
        _validate_absolute_paths(known, ("input_root", "output_root", "state_path"))
        return cls(**known)

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容字典；无参数。"""
        return asdict(self)


@dataclass(frozen=True)
class TaskEvent:
    """描述追加到诊断事件流的一次状态变化。"""

    schema_version: str
    task_id: str
    event: str
    occurred_at: str
    details: dict[str, Any]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskEvent":
        """解析任务事件；value 为 JSON 映射。"""
        known = _known_fields(cls, value)
        _validate_schema(str(known["schema_version"]))
        return cls(**known)

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容字典；无参数。"""
        return asdict(self)
