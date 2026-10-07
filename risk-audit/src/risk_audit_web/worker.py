"""同一主程序的独立审核 Worker 模式。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import os
from pathlib import Path
import shutil
import threading
import traceback
from typing import Any

from risk_audit.progress import AuditCancelled, AuditProgressEvent
from risk_audit.readers.xls import configure_conversion_runtime
from risk_audit_web.task_contracts import SCHEMA_VERSION, TaskEvent
from risk_audit_web.task_repository import TaskRepository


AuditFunction = Callable[..., dict[str, Any]]


def _now() -> str:
    """返回带本地时区的毫秒级 ISO 时间；无参数。"""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _progress_percent(completed_units: int, total_units: int | None) -> int | None:
    """计算真实工作单元进度；参数为已完成数和可选总数。"""
    if total_units is None or total_units <= 0:
        return None
    return max(0, min(100, completed_units * 100 // total_units))


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    """提取界面所需结果摘要；result 为审核核心完整结果。"""
    summary: dict[str, Any] = {
        key: int(result.get(key, 0) or 0)
        for key in ("input_files", "findings", "warnings", "limitations", "incomplete_items")
    }
    statistics_report = result.get("audit_statistics_report")
    run_dir = result.get("run_dir")
    log_reference = result.get("log_reference")
    incomplete_items_report = result.get("incomplete_items_report")
    if isinstance(statistics_report, str):
        summary["audit_statistics_report"] = statistics_report
    if isinstance(log_reference, str):
        # 只传递任务结果引用，不把数据库位置等内部资源暴露给界面。
        summary["log_reference"] = log_reference
    if isinstance(incomplete_items_report, str) and Path(incomplete_items_report).is_file():
        summary["incomplete_items_report"] = incomplete_items_report
    if isinstance(run_dir, str):
        summary["run_dir"] = run_dir
        unaudited_files_report = Path(run_dir) / "_risk_audit/未审核文件.json"
        if unaudited_files_report.is_file():
            summary["unaudited_files_report"] = str(unaudited_files_report)
    return summary


def cleanup_task_runtime(
    repository: TaskRepository,
    task_dir: Path,
    task_id: str,
) -> None:
    """清理任务临时资源；参数为仓储、任务目录和任务编号。"""
    for path in (task_dir / "work", task_dir / "libreoffice-profile"):
        try:
            if path.exists():
                shutil.rmtree(path)
        except OSError as error:
            repository.append_event(TaskEvent(
                SCHEMA_VERSION,
                task_id,
                "cleanup_warning",
                _now(),
                {"path": str(path), "message": str(error)},
            ))


def run_worker(
    database_path: Path,
    task_id: str,
    *,
    audit_func: AuditFunction | None = None,
    heartbeat_interval_seconds: float = 5.0,
) -> int:
    """执行审核任务；参数为数据库路径、任务编号、可替换核心入口和心跳间隔。"""
    from risk_audit.runner import audit

    selected_audit = audit if audit_func is None else audit_func
    repository = TaskRepository(database_path)
    try:
        repository.initialize()
        request = repository.read_request(task_id)
    except Exception:
        return 1

    output_root = Path(request.output_root)
    task_dir = output_root / ".task"
    log_path = task_dir / "worker.log"
    work_dir = task_dir / "work"
    profile_dir = task_dir / "libreoffice-profile"
    task_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    state_lock = threading.Lock()

    def update_state(changes: dict[str, Any]) -> None:
        """字段级发布状态；changes 为待更新状态字段。"""
        with state_lock:
            changes["heartbeat_at"] = _now()
            repository.update_task_state(task_id, changes)

    def refresh_heartbeat(stop_event: threading.Event) -> None:
        """周期刷新心跳；stop_event 为 Worker 结束通知。"""
        interval = max(0.01, heartbeat_interval_seconds)
        while not stop_event.wait(interval):
            changes: dict[str, Any] = {"heartbeat_at": _now()}
            if repository.is_cancel_requested(task_id):
                changes.update({
                    "status": "cancelling",
                    "message": "正在安全停止，请等待当前工作单元完成",
                })
            # 心跳只更新自身字段，不会覆盖并发提交的进度。
            repository.update_task_state(task_id, changes)

    def report_progress(event: AuditProgressEvent) -> None:
        """把核心进度映射为数据库字段；event 为核心事件。"""
        with state_lock:
            current = repository.read_state(task_id)
            completed = (
                event.completed_units
                if event.completed_units is not None
                else current.completed_units
            )
            total = event.total_units if event.total_units is not None else current.total_units
            changes: dict[str, Any] = {
                "stage": event.stage,
                "completed_units": completed,
                "total_units": total,
                "progress_percent": _progress_percent(completed, total),
                "heartbeat_at": _now(),
                "message": event.message or current.message,
            }
            if event.current_entity is not None:
                changes["current_entity"] = event.current_entity
            if event.current_business is not None:
                changes["current_business"] = event.current_business
            if event.current_file is not None:
                changes["current_file"] = event.current_file
            if repository.is_cancel_requested(task_id):
                changes["status"] = "cancelling"
            repository.update_task_state(task_id, changes)

    previous_environment: dict[str, str | None] = {}
    heartbeat_stop: threading.Event | None = None
    heartbeat_thread: threading.Thread | None = None
    exit_code = 0
    try:
        started_at = _now()
        update_state({
            "status": "running",
            "stage": "startup",
            "worker_pid": os.getpid(),
            "started_at": started_at,
            "message": "正在启动审核任务",
            "error_code": None,
        })
        repository.append_event(TaskEvent(SCHEMA_VERSION, task_id, "started", _now(), {}))
        with log_path.open("a", encoding="utf-8") as worker_log:
            worker_log.write(f"{_now()} Worker 启动，PID={os.getpid()}\n")
            worker_log.flush()
        configure_conversion_runtime(Path(request.soffice_path), profile_dir)
        previous_environment = {name: os.environ.get(name) for name in ("TMP", "TEMP", "TMPDIR")}
        for name in previous_environment:
            os.environ[name] = str(work_dir)
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=refresh_heartbeat,
            args=(heartbeat_stop,),
            name=f"task-heartbeat-{task_id}",
            daemon=True,
        )
        heartbeat_thread.start()
        result = selected_audit(
            input_root=Path(request.input_root),
            output_root=output_root,
            rulepack=Path(request.rulepack),
            entity_file=Path(request.entity_file),
            project_root=Path(request.baseline_root),
            # 报告属于任务结果，必须与可清理临时目录分离。
            runs_root=task_dir,
            write=True,
            run_id=task_id,
            run_directory=task_dir / "report",
            work_root=work_dir,
            config_file=Path(request.config_file) if request.config_file else None,
            progress_callback=report_progress,
            cancel_check=lambda: repository.is_cancel_requested(task_id),
        )
        terminal_status = "completed" if result.get("write_completed") else "partial"
        current = repository.read_state(task_id)
        total_units = (
            current.total_units
            if current.total_units is not None
            else len(result.get("business_results", []))
        )
        summary = _result_summary(result)
        incomplete_count = summary["incomplete_items"]
        terminal_message = (
            "审核已完成"
            if terminal_status == "completed"
            else f"审核已结束，存在 {incomplete_count} 个未完成项"
        )
        update_state({
            "status": terminal_status,
            "stage": "completed",
            "completed_units": total_units,
            "total_units": total_units,
            "progress_percent": 100,
            "message": terminal_message,
            "worker_pid": None,
            "result_summary": summary,
        })
        repository.append_event(TaskEvent(
            SCHEMA_VERSION, task_id, terminal_status, _now(), summary,
        ))
    except AuditCancelled as error:
        update_state({
            "status": "cancelled",
            "stage": "cancelled",
            "message": str(error),
            "worker_pid": None,
            "error_code": None,
        })
        repository.append_event(TaskEvent(
            SCHEMA_VERSION, task_id, "cancelled", _now(), {"message": str(error)},
        ))
    except Exception as error:
        exit_code = 1
        try:
            with log_path.open("a", encoding="utf-8") as worker_log:
                traceback.print_exc(file=worker_log)
                worker_log.flush()
        except OSError:
            pass
        try:
            update_state({
                "status": "failed",
                "stage": "failed",
                "message": str(error),
                "worker_pid": None,
                "error_code": "AUDIT_FAILED",
            })
            repository.append_event(TaskEvent(
                SCHEMA_VERSION,
                task_id,
                "failed",
                _now(),
                {"error_code": "AUDIT_FAILED", "message": str(error)},
            ))
        except Exception:
            # 数据库本身不可写时不能伪造成功；原始退出码仍明确失败。
            pass
    finally:
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=max(1.0, heartbeat_interval_seconds + 0.5))
        for name, previous_value in previous_environment.items():
            if previous_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous_value
        try:
            cleanup_task_runtime(repository, task_dir, task_id)
        except Exception:
            exit_code = 1
    return exit_code
