"""任务索引、状态和事件的持久化。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import time
from typing import Any, Callable, Mapping

from risk_audit_web.task_contracts import (
    ContractError,
    SCHEMA_VERSION,
    TaskEvent,
    TaskRecord,
    TaskState,
    TaskRequest,
)
from risk_audit_web.diagnostics import load_active_rulepack
from risk_audit_web.task_paths import (
    PortablePaths,
    default_output_path,
    reserve_output_path,
    validate_task_paths,
)


STATE_READ_ATTEMPTS = 5
STATE_READ_RETRY_SECONDS = 0.01


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """原子写入 JSON；path 为目标文件，value 为可序列化映射。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            # 必须先把完整内容刷入磁盘，再用同目录原子替换发布新状态。
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def append_event(path: Path, event: TaskEvent) -> None:
    """追加单行 JSON 事件；path 为 events.jsonl，event 为事件对象。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


@dataclass(frozen=True)
class TaskIndexResult:
    """保存可用任务记录及被隔离的索引警告。"""

    records: list[TaskRecord]
    warnings: list[str]


@dataclass(frozen=True)
class RecoveredTask:
    """保存恢复后的索引记录及当前状态。"""

    record: TaskRecord
    state: TaskState


@dataclass(frozen=True)
class RecoveryResult:
    """保存可恢复任务和单任务读取警告。"""

    tasks: list[RecoveredTask]
    warnings: list[str]


class TaskStore:
    """管理便携目录内的任务索引和单任务状态。"""

    def __init__(self, data_root: Path) -> None:
        """初始化存储；data_root 为主程序旁的 data 目录。"""
        self.data_root = data_root
        self.tasks_path = data_root / "tasks.json"

    def list_tasks(self) -> TaskIndexResult:
        """读取全部有效任务；返回记录和损坏警告。"""
        if not self.tasks_path.exists():
            return TaskIndexResult([], [])
        try:
            payload = json.loads(self.tasks_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return TaskIndexResult([], [f"任务索引无法读取：{error}"])
        if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
            return TaskIndexResult([], ["任务索引格式错误：tasks 必须是数组"])
        records: list[TaskRecord] = []
        warnings: list[str] = []
        for index, item in enumerate(payload["tasks"]):
            try:
                if not isinstance(item, dict):
                    raise ContractError("记录必须是对象")
                records.append(TaskRecord.from_dict(item))
            except (ContractError, TypeError, ValueError) as error:
                task_label = item.get("task_id", index) if isinstance(item, dict) else index
                warnings.append(f"任务记录 {task_label} 已忽略：{error}")
        return TaskIndexResult(records, warnings)

    def add_task(self, record: TaskRecord) -> None:
        """新增任务索引；record 为已验证任务记录。"""
        current = self.list_tasks().records
        if any(item.task_id == record.task_id for item in current):
            raise ValueError(f"任务编号已经存在：{record.task_id}")
        atomic_write_json(self.tasks_path, {
            "schema_version": SCHEMA_VERSION,
            "tasks": [item.to_dict() for item in [*current, record]],
        })

    def create_task(
        self,
        *,
        input_root: Path,
        paths: PortablePaths,
        output_root: Path | None = None,
        created_at: datetime | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> TaskRecord:
        """创建并持久化任务；参数为输入路径、资源路径及可选输出、时间和编号源。"""
        creation_time = created_at or datetime.now().astimezone()
        token = (token_factory or (lambda: secrets.token_hex(2)))()
        task_id = f"{creation_time.strftime('%Y%m%d-%H%M%S')}-{token}"
        resolved_input = input_root.resolve(strict=False)
        # 客户只选择资料目录，任务名称统一由目录名和创建时间生成。
        display_name = f"{resolved_input.name or '审核任务'}-{creation_time.strftime('%Y%m%d-%H%M%S')}"
        created_output = False
        if output_root is None:
            preview_path = default_output_path(resolved_input, paths.outputs_root, creation_time)
            validate_task_paths(resolved_input, preview_path)
            resolved_output = reserve_output_path(resolved_input, paths.outputs_root, creation_time).resolve()
            created_output = True
        else:
            validate_task_paths(resolved_input, output_root)
            # ZIP 工具可能不保留空 outputs 目录，首次创建时需要自动补齐父路径。
            output_root.parent.mkdir(parents=True, exist_ok=True)
            output_root.mkdir(parents=False, exist_ok=False)
            resolved_output = output_root.resolve()
            created_output = True

        task_dir = resolved_output / ".task"
        request_path = task_dir / "request.json"
        state_path = task_dir / "state.json"
        timestamp = creation_time.isoformat(timespec="seconds")
        try:
            task_dir.mkdir(parents=False, exist_ok=False)
            request = TaskRequest(
                schema_version=SCHEMA_VERSION,
                task_id=task_id,
                display_name=display_name,
                input_root=str(resolved_input),
                output_root=str(resolved_output),
                rulepack=str(load_active_rulepack(paths.rulepacks).resolve()),
                entity_file=str(paths.entity_file.resolve()),
                baseline_root=str(paths.baseline_root.resolve()),
                config_file=str(paths.config_file.resolve()),
                soffice_path=str(paths.soffice.resolve()),
                created_at=timestamp,
            )
            state = TaskState(
                schema_version=SCHEMA_VERSION,
                task_id=task_id,
                status="running",
                stage="startup",
                completed_units=0,
                total_units=None,
                progress_percent=None,
                current_entity=None,
                current_business=None,
                current_file=None,
                worker_pid=None,
                started_at=None,
                heartbeat_at=timestamp,
                message="任务已创建，正在启动",
                error_code=None,
                result_summary=None,
            )
            atomic_write_json(request_path, request.to_dict())
            self.write_state(state_path, state)
            record = TaskRecord(
                schema_version=SCHEMA_VERSION,
                task_id=task_id,
                display_name=request.display_name,
                input_root=request.input_root,
                output_root=request.output_root,
                created_at=timestamp,
                state_path=str(state_path.resolve()),
            )
            self.add_task(record)
            return record
        except BaseException:
            # 只清理本次创建且尚未出现业务产物的精确目录，禁止触碰用户已有内容。
            if created_output and resolved_output.is_dir():
                entries = list(resolved_output.iterdir())
                if entries == [task_dir] and task_dir.is_dir():
                    shutil.rmtree(task_dir)
                if not any(resolved_output.iterdir()):
                    resolved_output.rmdir()
            raise

    def read_state(self, record: TaskRecord) -> TaskState:
        """读取任务状态；record 为索引记录。"""
        state_path = Path(record.state_path)
        for attempt in range(STATE_READ_ATTEMPTS):
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                break
            except PermissionError:
                if attempt == STATE_READ_ATTEMPTS - 1:
                    raise
                # Windows 原子替换目标文件时可能出现短暂的共享冲突。
                time.sleep(STATE_READ_RETRY_SECONDS)
        if not isinstance(payload, dict):
            raise ContractError("任务状态必须是 JSON 对象")
        return TaskState.from_dict(payload)

    def write_state(self, state_path: Path, state: TaskState) -> None:
        """原子写入任务状态；参数为状态路径和状态对象。"""
        atomic_write_json(state_path, state.to_dict())

    def append_event(self, events_path: Path, event: TaskEvent) -> None:
        """追加任务事件；参数为事件文件路径和事件对象。"""
        append_event(events_path, event)

    def mark_interrupted(
        self,
        record: TaskRecord,
        state: TaskState,
        message: str,
    ) -> TaskState:
        """将任务标记为中断；参数为索引记录、原状态和用户提示。"""
        interrupted = state.with_updates(
            status="interrupted",
            stage="interrupted",
            progress_percent=state.progress_percent,
            message=message,
            worker_pid=None,
        )
        self.write_state(Path(record.state_path), interrupted)
        return interrupted

    def recover_tasks(
        self,
        *,
        is_process_alive: Callable[[int], bool],
        now: datetime | None = None,
        heartbeat_timeout_seconds: int = 30,
    ) -> RecoveryResult:
        """恢复任务状态；参数为 PID 探测器、当前时间及心跳超时秒数。"""
        current_time = now or datetime.now().astimezone()
        recovered: list[RecoveredTask] = []
        warnings = list(self.list_tasks().warnings)
        terminal_statuses = {"completed", "partial", "failed", "cancelled", "interrupted"}
        for record in self.list_tasks().records:
            try:
                state = self.read_state(record)
            except (OSError, json.JSONDecodeError, ContractError, TypeError, ValueError) as error:
                warnings.append(f"任务 {record.task_id} 状态无法读取：{error}")
                continue
            if state.status not in terminal_statuses:
                try:
                    heartbeat = datetime.fromisoformat(state.heartbeat_at)
                    heartbeat_age = (current_time - heartbeat).total_seconds()
                except (TypeError, ValueError):
                    heartbeat_age = heartbeat_timeout_seconds + 1
                live = state.worker_pid is not None and is_process_alive(state.worker_pid)
                if not live or heartbeat_age > heartbeat_timeout_seconds:
                    state = self.mark_interrupted(
                        record,
                        state,
                        "任务进程已中断，可重新创建任务",
                    )
            recovered.append(RecoveredTask(record, state))
        return RecoveryResult(recovered, warnings)
