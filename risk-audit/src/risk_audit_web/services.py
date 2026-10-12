"""REST API 与任务、文件和系统操作之间的应用服务。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import secrets
from threading import Lock
from typing import Any

from risk_audit_web import __version__ as APP_VERSION
from risk_audit_web.api_models import OutputPathPreviewRequest, TaskCreateRequest
from risk_audit_web.diagnostics import (
    DiagnosticItem,
    DiagnosticReport,
    run_startup_diagnostics,
)
from risk_audit_web.directory_picker import (
    DirectoryPicker,
    DirectoryPickerBusy,
    DirectoryPickerError,
)
from risk_audit_web.server import ServerController
from risk_audit_web.region_profiles import (
    RegionCatalog,
    RegionProfile,
    RegionProfileError,
    load_region_catalog,
    verify_rulepack_release,
)
from risk_audit_web.task_contracts import (
    SCHEMA_VERSION,
    TaskEvent,
    TaskRequest,
    TaskState,
)
from risk_audit_web.task_manager import ManagedTask, TaskManager
from risk_audit_web.task_paths import (
    MAX_OUTPUT_DIRECTORY_STEM_LENGTH,
    PathValidationError,
    PortablePaths,
    sanitize_directory_name,
    validate_task_paths,
)
from risk_audit_web.task_repository import TaskDatabaseError, TaskRepository


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
    repository: TaskRepository
    manager: TaskManager
    directory_picker: DirectoryPicker
    server_controller: ServerController
    open_path: Callable[[Path], None]
    background_runner: Callable[[Callable[[], None]], None]
    _creation_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _shutting_down: bool = field(default=False, init=False, repr=False)
    _startup_report: DiagnosticReport = field(init=False, repr=False)
    _region_catalog: RegionCatalog = field(init=False, repr=False)
    _database_error: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """在服务启动时缓存一次必要资源诊断；无参数。"""
        # 状态轮询只复用诊断结果，不重复读取规则包或检查资源路径。
        resource_report = run_startup_diagnostics(self.paths)
        try:
            self._region_catalog = load_region_catalog(self.paths.resources_root)
        except RegionProfileError:
            # 详细错误已进入启动诊断；保留空目录让状态接口仍可工作。
            self._region_catalog = RegionCatalog({})
        database_items: list[DiagnosticItem] = []
        try:
            self.repository.initialize()
            self.repository.recover_tasks(is_process_alive=self.manager.process_alive)
        except TaskDatabaseError as error:
            self._database_error = str(error)
            database_items.append(DiagnosticItem(
                "database_unavailable",
                str(self.repository.database_path),
                f"任务数据库不可用：{error}",
            ))
        self._startup_report = DiagnosticReport([*resource_report.items, *database_items])

    def system_status(self) -> dict[str, Any]:
        """返回服务、资源和任务状态；无参数。"""
        report = self._startup_report
        with self._creation_lock:
            accepting_tasks = not self._shutting_down
        available_region_count = sum(
            profile.available for profile in self._region_catalog.profiles.values()
        )
        return {
            "service_status": "degraded" if self._database_error else "ok",
            "can_create_task": report.can_start and accepting_tasks,
            "app_version": APP_VERSION,
            "available_region_count": available_region_count,
            "running_count": self.manager.snapshot().running_count,
            "diagnostics": diagnostic_response(report),
        }

    def list_regions(self) -> dict[str, object]:
        """返回配置顺序中的公开地区集合；无参数。"""
        return {
            "regions": [
                profile.to_public_dict()
                for profile in self._region_catalog.profiles.values()
            ]
        }

    def _resolve_region(self, region_code: str) -> RegionProfile:
        """解析可创建任务的地区；region_code 为稳定地区代码，返回固定资源。"""
        try:
            profile = self._region_catalog.require(region_code)
        except RegionProfileError as error:
            raise ApiProblem(422, "UNKNOWN_REGION", f"未知审核地区：{region_code}") from error
        if not profile.enabled:
            raise ApiProblem(422, "REGION_DISABLED", f"审核地区已禁用：{profile.region_name}")
        if profile.unavailable_reasons:
            raise ApiProblem(
                422,
                "REGION_NOT_READY",
                f"审核地区资源未就绪：{profile.region_name}",
                {"unavailable_reasons": list(profile.unavailable_reasons)},
            )
        # 只有名册模式需在入库前复核文件，目录模式不依赖主体清单。
        if (
            profile.entity_mode == "registry"
            and (profile.entity_file is None or not profile.entity_file.is_file())
        ):
            raise ApiProblem(
                422,
                "REGION_NOT_READY",
                f"审核地区资源未就绪：{profile.region_name}",
                {"unavailable_reasons": [f"会计主体清单文件缺失：{profile.entity_file}"]},
            )
        return profile

    def _verify_pinned_rulepack(self, request: TaskRequest) -> None:
        """复核任务固定规则包；request 为完整任务请求，无返回值。"""
        reasons = verify_rulepack_release(
            Path(request.rulepack),
            request.rulepack_version,
            request.rulepack_content_hash,
            request.region_code,
            request.region_name,
        )
        if reasons:
            raise ApiProblem(
                422,
                "REGION_NOT_READY",
                f"地区规则包在任务创建前发生变化：{request.region_name}",
                {"unavailable_reasons": reasons},
            )

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
        """生成只读输出预览；request 含输入目录和地区代码。"""
        input_root = Path(request.input_root).resolve(strict=False)
        region = self._resolve_region(request.region_code)
        output_root = self._next_output_path(
            input_root,
            datetime.now().astimezone(),
            region.region_name,
        )
        self._validate_paths(input_root, output_root)
        return {"output_root": str(output_root.resolve(strict=False))}

    def _next_output_path(
        self,
        input_root: Path,
        created_at: datetime,
        region_name: str | None = None,
    ) -> Path:
        """选择未占用输出路径；参数为输入目录、创建时间和可选地区名称。"""
        prefix = f"{region_name} " if region_name else ""
        directory_name = sanitize_directory_name(input_root.name)[
            :MAX_OUTPUT_DIRECTORY_STEM_LENGTH - len(prefix)
        ]
        timestamp = created_at.strftime("%Y%m%d-%H%M%S")
        base_name = f"{prefix}{directory_name}-{timestamp}"
        candidate = self.paths.outputs_root / base_name
        suffix = 2
        while candidate.exists() or self.repository.output_root_exists(candidate):
            candidate = self.paths.outputs_root / f"{base_name}-{suffix}"
            suffix += 1
        return candidate

    def _validate_paths(self, input_root: Path, output_root: Path) -> None:
        """验证任务路径并转换错误；参数为输入和输出目录。"""
        managed_root = self.paths.outputs_root.resolve(strict=False)
        resolved_output = output_root.resolve(strict=False)
        if resolved_output == managed_root or managed_root not in resolved_output.parents:
            raise ApiProblem(422, "INVALID_PATH", "输出目录必须位于审核器托管输出根目录")
        try:
            validate_task_paths(input_root, output_root)
        except PathValidationError as error:
            error_code = "PATH_OVERLAP" if "相互包含" in str(error) else "INVALID_PATH"
            raise ApiProblem(422, error_code, str(error)) from error

    def create_task(self, request: TaskCreateRequest) -> ManagedTask:
        """验证并启动任务；request 为唯一允许的客户端字段。"""
        with self._creation_lock:
            if self._shutting_down:
                raise ApiProblem(409, "SERVICE_SHUTTING_DOWN", "审核器正在退出，不能创建新任务")
            if not self._startup_report.can_start:
                errors = "；".join(
                    item.message for item in self._startup_report.items if not item.ok
                )
                status_code = 500 if self._database_error else 422
                error_code = "DATABASE_UNAVAILABLE" if self._database_error else "ENVIRONMENT_NOT_READY"
                raise ApiProblem(status_code, error_code, f"运行环境检查未通过：{errors}")

            creation_time = datetime.now().astimezone()
            timestamp = creation_time.isoformat(timespec="seconds")
            task_id = f"{creation_time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
            input_root = Path(request.input_root).resolve(strict=False)
            region = self._resolve_region(request.region_code)
            output_root = self._next_output_path(input_root, creation_time, region.region_name)
            self._validate_paths(input_root, output_root)

            task_request = TaskRequest(
                schema_version=SCHEMA_VERSION,
                task_id=task_id,
                display_name=(
                    f"{region.region_name} {input_root.name or '审核任务'}-"
                    f"{creation_time.strftime('%Y%m%d-%H%M%S')}"
                ),
                region_code=region.region_code,
                region_name=region.region_name,
                rulepack_version=region.rulepack_version,
                rulepack_content_hash=region.rulepack_content_hash,
                input_root=str(input_root),
                output_root=str(output_root.resolve(strict=False)),
                rulepack=str(region.rulepack),
                entity_mode=region.entity_mode,
                entity_file=str(region.entity_file) if region.entity_file is not None else None,
                baseline_root=str(region.baseline_root),
                # 地区未配置禁用规则时传空，审核引擎使用全部规则的默认配置。
                config_file=(str(region.config_file) if region.config_file.is_file() else None),
                soffice_path=str(self.paths.soffice.resolve()),
                created_at=timestamp,
            )
            # 入库前重新读取发布包摘要，防止启动诊断后规则文件被替换。
            self._verify_pinned_rulepack(task_request)
            task_state = TaskState(
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
            try:
                # 入库是任务创建边界；提交后任何故障只能把该任务更新为失败。
                record = self.repository.create_task(
                    task_request,
                    task_state,
                    TaskEvent(SCHEMA_VERSION, task_id, "created", timestamp, {}),
                )
            except TaskDatabaseError as error:
                raise ApiProblem(500, "TASK_CREATE_FAILED", "任务数据库写入失败") from error

            try:
                output_root.parent.mkdir(parents=True, exist_ok=True)
                output_root.mkdir(parents=False, exist_ok=False)
                self.manager.start_task(record)
            except Exception as error:
                failure_time = datetime.now().astimezone().isoformat(timespec="seconds")
                self.repository.update_task_state(task_id, {
                    "status": "failed",
                    "stage": "failed",
                    "message": f"任务启动失败：{error}",
                    "worker_pid": None,
                    "error_code": "TASK_START_FAILED",
                    "heartbeat_at": failure_time,
                })
                self.repository.append_event(TaskEvent(
                    SCHEMA_VERSION,
                    task_id,
                    "failed",
                    failure_time,
                    {"error_code": "TASK_START_FAILED", "message": str(error)},
                ))
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

    def open_audit_statistics(self, task_id: str, report_format: str) -> dict[str, str]:
        """打开审核统计报告；task_id 为任务编号，report_format 为 excel 或 html。"""
        result_fields = {
            "excel": "audit_statistics_report",
            "html": "audit_statistics_html_report",
        }
        # 请求模型已经限制格式，这里仍使用显式映射，禁止字段名直接来自客户端。
        field_name = result_fields.get(report_format)
        if field_name is None:
            raise ApiProblem(400, "INVALID_REPORT_FORMAT", "不支持的统计报告格式")
        return self.open_result(task_id, field_name)

    def get_unaudited_files(self, task_id: str) -> dict[str, Any]:
        """读取未审核文件列表；task_id 为任务编号，返回客户可读的结构化数据。"""
        task = self.get_task(task_id)
        summary = task.state.result_summary or {}
        raw_path = summary.get("unaudited_files_report")
        if not isinstance(raw_path, str) or not raw_path:
            raise ApiProblem(404, "RESULT_NOT_FOUND", "未审核文件结果尚不存在")
        target = self._safe_task_target(task_id, Path(raw_path))
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ApiProblem(500, "INVALID_RESULT", "未审核文件结果无法读取") from error
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise ApiProblem(500, "INVALID_RESULT", "未审核文件结果格式错误")
        return {"count": len(payload), "items": payload}

    def get_incomplete_items(self, task_id: str) -> dict[str, Any]:
        """读取未完成项列表。

        Args:
            task_id: 待读取结果的任务编号。
        """

        task = self.get_task(task_id)
        summary = task.state.result_summary or {}
        raw_path = summary.get("incomplete_items_report")
        if not isinstance(raw_path, str) or not raw_path:
            raise ApiProblem(404, "RESULT_NOT_FOUND", "未完成项结果尚不存在")
        target = self._safe_task_target(task_id, Path(raw_path))
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ApiProblem(500, "INVALID_RESULT", "未完成项结果无法读取") from error
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise ApiProblem(500, "INVALID_RESULT", "未完成项结果格式错误")
        return {"count": len(payload), "items": payload}

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
