"""多个审核 Worker 的启动、快照读取和停止管理。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
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
from risk_audit_web.task_repository import ACTIVE_STATUSES, TaskDatabaseError, TaskRepository
from risk_audit_web.worker import cleanup_task_runtime


@dataclass(frozen=True)
class ManagedTask:
    """组合任务记录与当前状态。"""

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
        repository: TaskRepository,
        executable: Path,
        *,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        process_alive: Callable[[int], bool] = is_process_alive,
        process_terminator: Callable[[int], bool] = terminate_process,
        process_tree_terminator: Callable[[int, float], bool] = terminate_process_tree,
        process_supervisor: Any | None = None,
        worker_arguments_factory: Callable[[Path, Path, str], list[str]] | None = None,
    ) -> None:
        """初始化管理器；参数为仓储、主程序、进程依赖和 Worker 参数工厂。"""
        self.repository = repository
        self.executable = executable
        self.popen_factory = popen_factory
        self.process_alive = process_alive
        self.process_terminator = process_terminator
        self.process_tree_terminator = process_tree_terminator
        self.process_supervisor = process_supervisor
        self.worker_arguments_factory = worker_arguments_factory or (
            lambda executable, database, task_id: [
                str(executable), "--worker", str(database), task_id,
            ]
        )
        self._processes: dict[str, Any] = {}

    def _records(self) -> dict[str, TaskRecord]:
        """返回按任务编号索引的有效记录；无参数。"""
        return {record.task_id: record for record in self.repository.list_tasks().tasks}

    def _process_has_exited(self, record: TaskRecord, state: TaskState) -> bool:
        """判断 Worker 是否已退出；record/state 为任务记录和最新状态。"""
        process = self._processes.get(record.task_id)
        if process is not None:
            return process.poll() is not None
        if state.worker_pid is not None:
            return not self.process_alive(state.worker_pid)
        return False

    def _stable_state(self, record: TaskRecord, state: TaskState) -> TaskState:
        """稳定活动任务状态；record/state 为任务记录和首次读取状态。"""
        if state.status not in ACTIVE_STATUSES or not self._process_has_exited(record, state):
            return state
        # 进程退出边界必须重读一次，避免覆盖 Worker 刚提交的终态。
        latest_state = self.repository.read_state(record.task_id)
        if latest_state.status not in ACTIVE_STATUSES:
            return latest_state
        return self.repository.mark_interrupted(
            record.task_id,
            "任务进程已中断，可重新创建任务",
        )

    def snapshot(self) -> TaskSnapshot:
        """读取当前任务快照；无参数，返回隔离损坏记录后的稳定视图。"""
        try:
            query = self.repository.list_tasks()
        except TaskDatabaseError as error:
            return TaskSnapshot([], [TaskWarning("", str(error))], 0)
        tasks: list[ManagedTask] = []
        warnings = [TaskWarning("", message) for message in query.warnings]
        running_count = 0
        for record in query.tasks:
            try:
                state = self._stable_state(record, self.repository.read_state(record.task_id))
                if state.status == "running" and self.repository.is_cancel_requested(record.task_id):
                    # 取消标志由数据库持久化，页面立即投影为停止中。
                    state = state.with_updates(
                        status="cancelling",
                        message="正在安全停止，请等待当前工作单元完成",
                    )
                if state.status in ACTIVE_STATUSES:
                    running_count += 1
                tasks.append(ManagedTask(record, state))
            except (KeyError, TaskDatabaseError, ValueError, TypeError) as error:
                warnings.append(TaskWarning(record.task_id, str(error)))
        return TaskSnapshot(tasks, warnings, running_count)

    def get_task(self, task_id: str) -> ManagedTask | None:
        """读取一个任务；task_id 为任务编号，不存在或损坏时返回 None。"""
        try:
            stored = self.repository.get_task(task_id)
            if stored is None:
                return None
            state = self._stable_state(stored.record, stored.state)
            if state.status == "running" and stored.cancel_requested:
                state = state.with_updates(
                    status="cancelling",
                    message="正在安全停止，请等待当前工作单元完成",
                )
            return ManagedTask(stored.record, state)
        except (TaskDatabaseError, ValueError, TypeError):
            return None

    def start_task(self, record: TaskRecord) -> int:
        """立即启动独立 Worker；record 为已持久化任务，返回子进程 PID。"""
        arguments = self.worker_arguments_factory(
            self.executable,
            self.repository.database_path,
            record.task_id,
        )
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
        return int(process.pid)

    def request_cancel(self, task_id: str) -> bool:
        """请求安全停止任务；task_id 为任务编号，返回任务是否仍处于活动态。"""
        occurred_at = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            created = self.repository.request_cancel(task_id, occurred_at)
            if created:
                return True
            stored = self.repository.get_task(task_id)
            return bool(
                stored is not None
                and stored.state.status in ACTIVE_STATUSES
                and stored.cancel_requested
            )
        except (KeyError, TaskDatabaseError):
            return False

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
        state = self.repository.read_state(task_id)
        # 终止前重新核对 PID，防止结束已经复用 PID 的无关进程。
        if state.worker_pid != expected_pid or not self.process_alive(expected_pid):
            return False
        if not self.process_terminator(expected_pid):
            return False
        if not self._wait_for_process_exit(task_id, expected_pid):
            return False
        cleanup_task_runtime(self.repository, Path(record.output_root) / ".task", task_id)
        latest_state = self.repository.read_state(task_id)
        if latest_state.status not in ACTIVE_STATUSES:
            return True
        if latest_state.worker_pid not in {expected_pid, None}:
            return False
        self.repository.update_task_state(
            task_id,
            {
                "status": "cancelled",
                "stage": "cancelled",
                "worker_pid": None,
                "message": "任务已被用户强制停止，已完成输出予以保留",
            },
        )
        self.repository.append_event(TaskEvent(
            SCHEMA_VERSION,
            task_id,
            "cancelled",
            datetime.now().astimezone().isoformat(timespec="seconds"),
            {"forced": True},
        ))
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
        active_processes = [process for process in self._processes.values() if process.poll() is None]
        for process in active_processes:
            pid = int(process.pid)
            if not self.process_tree_terminator(pid, timeout_seconds):
                failed.append(pid)
        return failed
