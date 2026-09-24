"""多个审核 Worker 的启动、轮询和停止管理。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from risk_audit_desktop.platform_windows import is_process_alive, terminate_process
from risk_audit_desktop.task_contracts import SCHEMA_VERSION, TaskEvent, TaskRecord, TaskState
from risk_audit_desktop.task_store import TaskStore


class SignalChannel:
    """提供轻量信号连接；Qt 页面可像普通信号一样 connect。"""

    def __init__(self) -> None:
        """初始化空订阅列表；无参数。"""
        self._callbacks: list[Callable[..., None]] = []

    def connect(self, callback: Callable[..., None]) -> None:
        """连接回调；callback 为状态接收函数。"""
        self._callbacks.append(callback)

    def emit(self, *args: Any) -> None:
        """同步发送信号；args 为回调参数。"""
        for callback in list(self._callbacks):
            callback(*args)


class TaskManager:
    """管理不排队的独立 Worker 进程和文件状态轮询。"""

    def __init__(
        self,
        store: TaskStore,
        executable: Path,
        *,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        process_alive: Callable[[int], bool] = is_process_alive,
        process_terminator: Callable[[int], bool] = terminate_process,
        worker_arguments_factory: Callable[[Path, Path], list[str]] | None = None,
        start_timer: bool = True,
    ) -> None:
        """初始化管理器；参数为存储、主程序、进程依赖、Worker 参数工厂和 Qt 轮询开关。"""
        self.store = store
        self.executable = executable
        self.popen_factory = popen_factory
        self.process_alive = process_alive
        self.process_terminator = process_terminator
        self.worker_arguments_factory = worker_arguments_factory or (
            lambda executable, request: [str(executable), "--worker", str(request)]
        )
        self.task_updated = SignalChannel()
        self.task_error = SignalChannel()
        self.running_count_changed = SignalChannel()
        self._processes: dict[str, Any] = {}
        self._timer: Any | None = None
        if start_timer:
            # Qt 仅在 GUI 模式延迟导入，Worker 和核心测试不会加载界面运行时。
            from PySide6.QtCore import QTimer

            self._timer = QTimer()
            self._timer.setInterval(500)
            self._timer.timeout.connect(self.poll_once)
            self._timer.start()

    def _records(self) -> dict[str, TaskRecord]:
        """返回按任务编号索引的有效记录；无参数。"""
        return {record.task_id: record for record in self.store.list_tasks().records}

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
        self._processes[record.task_id] = process
        state = self.store.read_state(record)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        self.store.write_state(Path(record.state_path), state.with_updates(
            status="running",
            stage="startup",
            worker_pid=process.pid,
            started_at=state.started_at or now,
            heartbeat_at=now,
            message="审核任务已启动",
        ))
        return int(process.pid)

    def poll_once(self) -> None:
        """轮询全部任务一次；单任务错误通过信号隔离。"""
        running_count = 0
        for record in self.store.list_tasks().records:
            try:
                state = self.store.read_state(record)
                process = self._processes.get(record.task_id)
                if (
                    process is not None
                    and process.poll() is not None
                    and state.status in {"running", "cancelling"}
                ):
                    state = self.store.mark_interrupted(
                        record, state, "任务进程已中断，可重新创建任务",
                    )
                if state.status in {"running", "cancelling"}:
                    running_count += 1
                self.task_updated.emit(record.task_id, state)
            except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
                self.task_error.emit(record.task_id, str(error))
        self.running_count_changed.emit(running_count)

    def request_cancel(self, task_id: str) -> bool:
        """请求安全停止一个任务；task_id 为任务编号，返回是否已发出请求。"""
        record = self._records().get(task_id)
        if record is None:
            return False
        state = self.store.read_state(record)
        if state.status not in {"running", "cancelling"}:
            return False
        task_dir = Path(record.output_root) / "_task"
        (task_dir / "cancel.requested").touch(exist_ok=True)
        cancelling = state.with_updates(
            status="cancelling",
            message="正在安全停止，请等待当前工作单元完成",
        )
        self.store.write_state(Path(record.state_path), cancelling)
        self.store.append_event(task_dir / "events.jsonl", TaskEvent(
            SCHEMA_VERSION, task_id, "cancel_requested",
            datetime.now().astimezone().isoformat(timespec="seconds"), {},
        ))
        return True

    def request_cancel_all(self) -> list[str]:
        """请求停止全部运行任务；返回已发出请求的任务编号。"""
        cancelled: list[str] = []
        for task_id in self._records():
            if self.request_cancel(task_id):
                cancelled.append(task_id)
        return cancelled

    def force_stop(self, task_id: str, *, expected_pid: int) -> bool:
        """强制结束一个 Worker；参数为任务编号和界面最后确认的 PID。"""
        record = self._records().get(task_id)
        if record is None:
            return False
        # 强制结束前重新读取状态并比较 PID，禁止结束已经复用 PID 的无关进程。
        state = self.store.read_state(record)
        if state.worker_pid != expected_pid or not self.process_alive(expected_pid):
            return False
        if not self.process_terminator(expected_pid):
            return False
        stopped = state.with_updates(
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
        while True:
            active = False
            for record in self.store.list_tasks().records:
                try:
                    if self.store.read_state(record).status in {"running", "cancelling"}:
                        active = True
                        break
                except (OSError, json.JSONDecodeError, ValueError, TypeError):
                    continue
            if not active:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def running_task_ids(self) -> list[str]:
        """返回运行中或停止中的任务编号；无参数。"""
        active: list[str] = []
        for record in self.store.list_tasks().records:
            try:
                if self.store.read_state(record).status in {"running", "cancelling"}:
                    active.append(record.task_id)
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                continue
        return active
