"""多个审核 Worker 的启动、快照读取和停止管理。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from risk_audit_web.platform_runtime import (
    is_process_alive,
    terminate_process,
    terminate_process_tree,
)
from risk_audit_web.task_contracts import SCHEMA_VERSION, TaskEvent, TaskRecord, TaskState
from risk_audit_web.task_store import TaskStore


ACTIVE_STATUSES = frozenset({"running", "cancelling"})


@dataclass(frozen=True)
class ManagedTask:
    """组合任务索引与当前状态。"""

    record: TaskRecord
    state: TaskState


@dataclass(frozen=True)
class TaskWarning:
    """描述一个被隔离的任务读取错误。"""

    task_id: str
    message: str


@dataclass(frozen=True)
class TaskSnapshot:
    """保存当前可用任务、警告和运行数量。"""

    tasks: list[ManagedTask]
    warnings: list[TaskWarning]
    running_count: int


class TaskManager:
    """管理不排队的独立 Worker 进程并提供同步快照。"""

    def __init__(
        self,
        store: TaskStore,
        executable: Path,
        *,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        process_alive: Callable[[int], bool] = is_process_alive,
        process_terminator: Callable[[int], bool] = terminate_process,
        process_tree_terminator: Callable[[int, float], bool] = terminate_process_tree,
        process_supervisor: Any | None = None,
        worker_arguments_factory: Callable[[Path, Path], list[str]] | None = None,
    ) -> None:
        """初始化管理器；参数为存储、主程序、进程依赖和 Worker 参数工厂。"""
        self.store = store
        self.executable = executable
        self.popen_factory = popen_factory
        self.process_alive = process_alive
        self.process_terminator = process_terminator
        self.process_tree_terminator = process_tree_terminator
        self.process_supervisor = process_supervisor
        self.worker_arguments_factory = worker_arguments_factory or (
            lambda executable, request: [str(executable), "--worker", str(request)]
        )
        self._processes: dict[str, Any] = {}

    def _records(self) -> dict[str, TaskRecord]:
        """返回按任务编号索引的有效记录；无参数。"""
        return {record.task_id: record for record in self.store.list_tasks().records}

    def _process_has_exited(self, record: TaskRecord, state: TaskState) -> bool:
        """判断 Worker 是否已退出；record/state 为任务索引和最新状态。"""
        process = self._processes.get(record.task_id)
        if process is not None:
            return process.poll() is not None
        if state.worker_pid is not None:
            return not self.process_alive(state.worker_pid)
        return False

    def _stable_state(self, record: TaskRecord, state: TaskState) -> TaskState:
        """稳定活动任务状态；record/state 为任务索引和首次读取状态。"""
        if state.status not in ACTIVE_STATUSES or not self._process_has_exited(record, state):
            return state
        # Worker 退出边界必须重读一次，避免覆盖它刚刚原子发布的终态。
        latest_state = self.store.read_state(record)
        if latest_state.status not in ACTIVE_STATUSES:
            return latest_state
        return self.store.mark_interrupted(
            record,
            latest_state,
            "任务进程已中断，可重新创建任务",
        )

    def snapshot(self) -> TaskSnapshot:
        """读取当前任务快照；无参数，返回隔离损坏记录后的稳定视图。"""
        index = self.store.list_tasks()
        tasks: list[ManagedTask] = []
        warnings = [TaskWarning("", message) for message in index.warnings]
        running_count = 0
        for record in index.records:
            try:
                state = self._stable_state(record, self.store.read_state(record))
                cancel_path = Path(record.output_root) / "_task/cancel.requested"
                if state.status == "running" and cancel_path.is_file():
                    # 取消标记只影响页面投影，持久状态仍由 Worker 独占写入。
                    state = state.with_updates(
                        status="cancelling",
                        message="正在安全停止，请等待当前工作单元完成",
                    )
                if state.status in ACTIVE_STATUSES:
                    running_count += 1
                tasks.append(ManagedTask(record, state))
            except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
                warnings.append(TaskWarning(record.task_id, str(error)))
        return TaskSnapshot(tasks, warnings, running_count)

    def get_task(self, task_id: str) -> ManagedTask | None:
        """读取一个任务；task_id 为任务编号，不存在或损坏时返回 None。"""
        return next(
            (task for task in self.snapshot().tasks if task.record.task_id == task_id),
            None,
        )

    def start_task(self, record: TaskRecord) -> int:
        """立即启动独立 Worker；record 为已持久化任务，返回子进程 PID。"""
        request_path = Path(record.output_root) / "_task/request.json"
        arguments = self.worker_arguments_factory(self.executable, request_path)
        options: dict[str, Any] = {
            "shell": False,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        process = self.popen_factory(arguments, **options)
        if self.process_supervisor is not None:
            self.process_supervisor.register(process)
        self._processes[record.task_id] = process
        # Worker 进程启动后独占持久状态写入，主进程只保存进程句柄。
        return int(process.pid)

    def request_cancel(self, task_id: str) -> bool:
        """请求安全停止一个任务；task_id 为任务编号，返回是否已发出请求。"""
        record = self._records().get(task_id)
        if record is None:
            return False
        task_dir = Path(record.output_root) / "_task"
        cancel_path = task_dir / "cancel.requested"
        if cancel_path.is_file():
            state = self.store.read_state(record)
            if state.status in ACTIVE_STATUSES:
                return True
            cancel_path.unlink(missing_ok=True)
            return False
        state = self.store.read_state(record)
        if state.status not in ACTIVE_STATUSES:
            return False
        # 原子创建权威取消标记；绝不把 Worker 的终态写回为过渡态。
        cancel_path.touch(exist_ok=True)
        latest_state = self.store.read_state(record)
        if latest_state.status not in ACTIVE_STATUSES:
            cancel_path.unlink(missing_ok=True)
        self.store.append_event(task_dir / "events.jsonl", TaskEvent(
            SCHEMA_VERSION,
            task_id,
            "cancel_requested",
            datetime.now().astimezone().isoformat(timespec="seconds"),
            {},
        ))
        return True

    def request_cancel_all(self) -> list[str]:
        """请求停止全部运行任务；无参数，返回已发出请求的任务编号。"""
        return [task_id for task_id in self._records() if self.request_cancel(task_id)]

    def _wait_for_process_exit(
        self,
        task_id: str,
        pid: int,
        timeout_seconds: float = 5.0,
    ) -> bool:
        """等待 Worker 退出；task_id/pid 为任务及进程号，timeout_seconds 为最长秒数。"""
        process = self._processes.get(task_id)
        if process is not None:
            try:
                process.wait(timeout=max(0.0, timeout_seconds))
            except subprocess.TimeoutExpired:
                return False
            return True
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while self.process_alive(pid):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.05, remaining))
        return True

    def force_stop(self, task_id: str, *, expected_pid: int) -> bool:
        """强制结束 Worker；task_id 为任务编号，expected_pid 为用户确认的进程号。"""
        record = self._records().get(task_id)
        if record is None:
            return False
        # 终止前必须重新读取并核对 PID，防止结束已经复用 PID 的无关进程。
        state = self.store.read_state(record)
        if state.worker_pid != expected_pid or not self.process_alive(expected_pid):
            return False
        if not self.process_terminator(expected_pid):
            return False
        if not self._wait_for_process_exit(task_id, expected_pid):
            return False
        latest_state = self.store.read_state(record)
        if latest_state.status not in ACTIVE_STATUSES:
            return True
        if latest_state.worker_pid not in {expected_pid, None}:
            return False
        stopped = latest_state.with_updates(
            status="cancelled",
            stage="cancelled",
            worker_pid=None,
            message="任务已被用户强制停止，已完成输出予以保留",
        )
        self.store.write_state(Path(record.state_path), stopped)
        return True

    def wait_for_all(self, timeout_ms: int) -> bool:
        """等待所有 Worker 进入终态；timeout_ms 为最大等待毫秒数。"""
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        while self.running_task_ids():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def running_task_ids(self) -> list[str]:
        """返回运行中或停止中的任务编号；无参数。"""
        return [
            task.record.task_id
            for task in self.snapshot().tasks
            if task.state.status in ACTIVE_STATUSES
        ]

    def stop_all_workers(self, timeout_seconds: float = 5.0) -> list[int]:
        """有界停止全部已登记 Worker 树；参数为单进程最长等待秒数。"""
        failed: list[int] = []
        active_processes = [
            process
            for process in self._processes.values()
            if process.poll() is None
        ]
        for process in active_processes:
            pid = int(process.pid)
            if not self.process_tree_terminator(pid, timeout_seconds):
                failed.append(pid)
        return failed
