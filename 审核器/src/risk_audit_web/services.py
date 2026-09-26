"""REST API 与任务、文件和系统操作之间的应用服务。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
from threading import Lock
from typing import Any

from risk_audit_web.api_models import OutputPathPreviewRequest, TaskCreateRequest
from risk_audit_web.diagnostics import DiagnosticReport, run_startup_diagnostics
from risk_audit_web.directory_picker import (
    DirectoryPicker,
    DirectoryPickerBusy,
    DirectoryPickerError,
)
from risk_audit_web.server import ServerController
from risk_audit_web.task_manager import ManagedTask, TaskManager
from risk_audit_web.task_paths import (
    PathValidationError,
    PortablePaths,
    default_output_path,
    validate_task_paths,
)
from risk_audit_web.task_store import TaskStore


class ApiProblem(Exception):
    """表示可安全返回给页面的稳定 API 错误。"""

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """初始化错误；参数为状态、错误码、中文消息和可选安全详情。"""
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """返回安全 JSON 响应；无参数。"""
        payload: dict[str, Any] = {"error_code": self.error_code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


def task_response(task: ManagedTask) -> dict[str, Any]:
    """构造任务响应；task 为已组合的任务记录和状态。"""
    payload = task.record.to_dict()
    payload.update(task.state.to_dict())
    return payload


def diagnostic_response(report: DiagnosticReport) -> list[dict[str, Any]]:
    """构造精简诊断响应；report 为启动时完整资源诊断。"""
    failed_items = [item for item in report.items if not item.ok]
    if failed_items:
        visible_items = failed_items
    else:
        # 全部成功时只返回汇总，避免浏览器渲染数万条文件明细。
        return [{
            "code": "ok",
            "path": "",
            "message": f"运行环境检查通过，共校验 {len(report.items)} 项资源",
            "ok": True,
        }]
    return [
        {
            "code": item.code,
            "path": item.relative_path,
            "message": item.message,
            "ok": item.ok,
        }
        for item in visible_items
    ]


@dataclass
class ApplicationServices:
    """组合 API 所需的任务、平台和退出能力。"""

    paths: PortablePaths
    store: TaskStore
    manager: TaskManager
    directory_picker: DirectoryPicker
    server_controller: ServerController
    open_path: Callable[[Path], None]
    background_runner: Callable[[Callable[[], None]], None]
    _creation_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _shutting_down: bool = field(default=False, init=False, repr=False)
    _startup_report: DiagnosticReport = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """在服务启动时缓存一次完整资源诊断；无参数。"""
        # LibreOffice 文件数量大，状态轮询不应重复读取和哈希。
        self._startup_report = run_startup_diagnostics(self.paths)

    def system_status(self) -> dict[str, Any]:
        """返回服务、资源和任务状态；无参数。"""
        report = self._startup_report
        with self._creation_lock:
            accepting_tasks = not self._shutting_down
        try:
            active = json.loads((self.paths.rulepacks / "active.json").read_text(encoding="utf-8"))
            rule_version = active.get("version") if isinstance(active, dict) else None
        except (OSError, json.JSONDecodeError):
            rule_version = None
        return {
            "service_status": "ok",
            "can_create_task": report.can_start and accepting_tasks,
            "rule_version": rule_version,
            "running_count": self.manager.snapshot().running_count,
            "diagnostics": diagnostic_response(report),
        }

    def select_input_directory(self) -> dict[str, Any]:
        """执行一次系统目录选择；无参数。"""
        try:
            selection = self.directory_picker.select_directory()
        except DirectoryPickerBusy as error:
            raise ApiProblem(409, "DIRECTORY_PICKER_BUSY", str(error)) from error
        except DirectoryPickerError as error:
            raise ApiProblem(500, error.error_code, str(error)) from error
        return {
            "selected": selection.selected,
            "path": str(selection.path) if selection.path is not None else None,
        }

    def preview_output_path(self, request: OutputPathPreviewRequest) -> dict[str, str]:
        """生成只读输出预览；request 为已验证输入目录。"""
        input_root = Path(request.input_root).resolve(strict=False)
        output_root = default_output_path(input_root, self.paths.outputs_root, datetime.now().astimezone())
        self._validate_paths(input_root, output_root)
        return {"output_root": str(output_root.resolve(strict=False))}

    def _validate_paths(self, input_root: Path, output_root: Path) -> None:
        """验证任务路径并转换错误；参数为输入和输出目录。"""
        try:
            validate_task_paths(input_root, output_root)
        except PathValidationError as error:
            error_code = "PATH_OVERLAP" if "相互包含" in str(error) else "INVALID_PATH"
            raise ApiProblem(422, error_code, str(error)) from error

    def create_task(self, request: TaskCreateRequest) -> ManagedTask:
        """验证并启动任务；request 为唯一允许的客户端字段。"""
        try:
            # 任务索引和输出预占共享一个临界区，避免并发覆盖 tasks.json。
            with self._creation_lock:
                if self._shutting_down:
                    raise ApiProblem(409, "SERVICE_SHUTTING_DOWN", "审核器正在退出，不能创建新任务")
                if not self._startup_report.can_start:
                    errors = "；".join(
                        item.message for item in self._startup_report.items if not item.ok
                    )
                    raise ApiProblem(
                        422,
                        "ENVIRONMENT_NOT_READY",
                        f"运行环境检查未通过：{errors}",
                    )
                record = self.store.create_task(
                    input_root=Path(request.input_root),
                    display_name=request.display_name,
                    paths=self.paths,
                )
                self.manager.start_task(record)
        except PathValidationError as error:
            error_code = "PATH_OVERLAP" if "相互包含" in str(error) else "INVALID_PATH"
            raise ApiProblem(422, error_code, str(error)) from error
        except ValueError as error:
            raise ApiProblem(422, "ENVIRONMENT_NOT_READY", str(error)) from error
        task = self.manager.get_task(record.task_id)
        if task is None:
            raise ApiProblem(500, "TASK_STATE_UNAVAILABLE", "任务已创建但状态暂时无法读取")
        return task

    def list_tasks(self) -> dict[str, Any]:
        """返回按创建时间降序排列的任务；无参数。"""
        snapshot = self.manager.snapshot()
        tasks = sorted(snapshot.tasks, key=lambda item: item.record.created_at, reverse=True)
        return {
            "tasks": [task_response(task) for task in tasks],
            "warnings": [warning.__dict__ for warning in snapshot.warnings],
            "running_count": snapshot.running_count,
        }

    def get_task(self, task_id: str) -> ManagedTask:
        """返回一个任务；task_id 为任务编号。"""
        task = self.manager.get_task(task_id)
        if task is None:
            raise ApiProblem(404, "TASK_NOT_FOUND", "任务不存在")
        return task

    def cancel_task(self, task_id: str) -> ManagedTask:
        """请求安全停止任务；task_id 为任务编号。"""
        if not self.manager.request_cancel(task_id):
            task = self.manager.get_task(task_id)
            if task is None:
                raise ApiProblem(404, "TASK_NOT_FOUND", "任务不存在")
            raise ApiProblem(409, "TASK_NOT_ACTIVE", "任务已结束，不能请求停止")
        return self.get_task(task_id)

    def terminate_task(self, task_id: str) -> ManagedTask:
        """强制终止任务；task_id 为任务编号。"""
        task = self.get_task(task_id)
        if task.state.worker_pid is None:
            raise ApiProblem(409, "WORKER_PID_UNAVAILABLE", "任务进程号不可用，无法强制终止")
        if not self.manager.force_stop(task_id, expected_pid=task.state.worker_pid):
            raise ApiProblem(409, "TASK_TERMINATION_FAILED", "任务状态已变化或进程无法终止")
        return self.get_task(task_id)

    def _safe_task_target(self, task_id: str, target: Path) -> Path:
        """验证任务输出内目标；task_id 为任务编号，target 为后端解析路径。"""
        task = self.get_task(task_id)
        output_root = Path(task.record.output_root).resolve()
        resolved = target.resolve()
        if resolved != output_root and output_root not in resolved.parents:
            raise ApiProblem(403, "PATH_OUTSIDE_TASK", "目标不属于当前任务输出目录")
        if not resolved.exists():
            raise ApiProblem(404, "RESULT_NOT_FOUND", "任务结果尚不存在")
        return resolved

    def open_output(self, task_id: str) -> dict[str, str]:
        """打开任务输出目录；task_id 为任务编号。"""
        task = self.get_task(task_id)
        target = self._safe_task_target(task_id, Path(task.record.output_root))
        self.open_path(target)
        return {"status": "opened"}

    def open_result(self, task_id: str, field_name: str) -> dict[str, str]:
        """打开结果文件；task_id 为任务编号，field_name 为后端摘要字段。"""
        task = self.get_task(task_id)
        summary = task.state.result_summary or {}
        raw_path = summary.get(field_name)
        if not isinstance(raw_path, str) or not raw_path:
            raise ApiProblem(404, "RESULT_NOT_FOUND", "任务结果尚不存在")
        target = self._safe_task_target(task_id, Path(raw_path))
        self.open_path(target)
        return {"status": "opened"}

    def request_shutdown(self, mode: str) -> dict[str, object]:
        """按模式请求退出；mode 为 immediate 或 cancel_active_tasks。"""
        with self._creation_lock:
            active = self.manager.running_task_ids()
            if mode == "immediate" and active:
                raise ApiProblem(
                    409,
                    "ACTIVE_TASKS_EXIST",
                    "仍有审核任务正在运行",
                    {"task_ids": active},
                )
            # 与任务创建共享锁，保证退出开始后不会再启动新 Worker。
            self._shutting_down = True
        if mode == "cancel_active_tasks":
            cancelled = self.manager.request_cancel_all()
            self.background_runner(self._wait_then_exit)
            return {"status": "stopping_tasks", "task_ids": cancelled}
        self.background_runner(self.server_controller.request_exit)
        return {"status": "shutting_down", "task_ids": []}

    def _wait_then_exit(self) -> None:
        """等待全部任务终态后退出；无参数。"""
        while not self.manager.wait_for_all(30_000):
            # 单次超时只用于分段观察，不能放弃用户的退出请求。
            continue
        self.server_controller.request_exit()
