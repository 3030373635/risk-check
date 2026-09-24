"""同一主程序的独立审核 Worker 模式。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import threading
import traceback
from typing import Any

from risk_audit.progress import AuditCancelled, AuditProgressEvent
from risk_audit.readers.xls import configure_conversion_runtime
from risk_audit_desktop.task_contracts import (
    SCHEMA_VERSION,
    TaskEvent,
    TaskRequest,
    TaskState,
)
from risk_audit_desktop.task_store import append_event, atomic_write_json


AuditFunction = Callable[..., dict[str, Any]]


def _now() -> str:
    """返回带本地时区的毫秒级 ISO 时间；无参数。"""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def is_cancel_requested(task_dir: Path) -> bool:
    """检查安全停止标记；task_dir 为当前任务的 `_task` 目录。"""
    return (task_dir / "cancel.requested").is_file()


def _progress_percent(completed_units: int, total_units: int | None) -> int | None:
    """计算真实工作单元进度；参数为已完成数和可选总数。"""
    if total_units is None or total_units <= 0:
        return None
    return max(0, min(100, completed_units * 100 // total_units))


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    """提取界面所需结果摘要；result 为审核核心完整结果。"""
    summary: dict[str, Any] = {
        key: int(result.get(key, 0) or 0)
        for key in ("input_files", "findings", "warnings", "limitations")
    }
    statistics_report = result.get("audit_statistics_report")
    run_dir = result.get("run_dir")
    if isinstance(statistics_report, str):
        summary["audit_statistics_report"] = statistics_report
    if isinstance(run_dir, str):
        summary["run_dir"] = run_dir
        summary["review_report"] = str(Path(run_dir) / "_risk_audit/集中复核事项.md")
    return summary


def _cleanup_task_runtime(task_dir: Path, events_path: Path, task_id: str) -> None:
    """清理任务临时资源；参数为任务目录、事件文件和任务编号。"""
    for path in (task_dir / "work", task_dir / "libreoffice-profile"):
        try:
            if path.exists():
                shutil.rmtree(path)
        except OSError as error:
            append_event(events_path, TaskEvent(
                SCHEMA_VERSION,
                task_id,
                "cleanup_warning",
                _now(),
                {"path": str(path), "message": str(error)},
            ))
    try:
        (task_dir / "cancel.requested").unlink(missing_ok=True)
    except OSError as error:
        append_event(events_path, TaskEvent(
            SCHEMA_VERSION,
            task_id,
            "cleanup_warning",
            _now(),
            {"path": str(task_dir / 'cancel.requested'), "message": str(error)},
        ))


def run_worker(
    request_path: Path,
    *,
    audit_func: AuditFunction | None = None,
    heartbeat_interval_seconds: float = 5.0,
) -> int:
    """执行审核任务；参数为请求 JSON、可替换核心入口和心跳间隔。"""
    from risk_audit.runner import audit

    selected_audit = audit if audit_func is None else audit_func
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    request = TaskRequest.from_dict(payload)
    output_root = Path(request.output_root)
    task_dir = output_root / "_task"
    state_path = task_dir / "state.json"
    events_path = task_dir / "events.jsonl"
    log_path = task_dir / "worker.log"
    work_dir = task_dir / "work"
    profile_dir = task_dir / "libreoffice-profile"
    task_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    started_at = _now()
    state = TaskState(
        schema_version=SCHEMA_VERSION,
        task_id=request.task_id,
        status="running",
        stage="startup",
        completed_units=0,
        total_units=None,
        progress_percent=None,
        current_entity=None,
        current_business=None,
        current_file=None,
        worker_pid=os.getpid(),
        started_at=started_at,
        heartbeat_at=started_at,
        message="正在启动审核任务",
        error_code=None,
        result_summary=None,
    )
    state_lock = threading.Lock()

    def write_state(next_state: TaskState) -> None:
        """发布 Worker 状态；next_state 为完整的新状态。"""
        nonlocal state
        with state_lock:
            state = next_state.with_updates(heartbeat_at=_now())
            atomic_write_json(state_path, state.to_dict())

    def refresh_heartbeat(stop_event: threading.Event) -> None:
        """周期刷新心跳；stop_event 为 Worker 结束通知。"""
        nonlocal state
        interval = max(0.01, heartbeat_interval_seconds)
        while not stop_event.wait(interval):
            with state_lock:
                status = "cancelling" if is_cancel_requested(task_dir) else state.status
                state = state.with_updates(status=status, heartbeat_at=_now())
                atomic_write_json(state_path, state.to_dict())

    def report_progress(event: AuditProgressEvent) -> None:
        """把核心进度映射为桌面状态；event 为核心事件。"""
        completed = event.completed_units if event.completed_units is not None else state.completed_units
        total = event.total_units if event.total_units is not None else state.total_units
        write_state(state.with_updates(
            status="cancelling" if is_cancel_requested(task_dir) else state.status,
            stage=event.stage,
            completed_units=completed,
            total_units=total,
            progress_percent=_progress_percent(completed, total),
            current_entity=event.current_entity if event.current_entity is not None else state.current_entity,
            current_business=event.current_business if event.current_business is not None else state.current_business,
            current_file=event.current_file if event.current_file is not None else state.current_file,
            message=event.message or state.message,
        ))

    write_state(state)
    append_event(events_path, TaskEvent(SCHEMA_VERSION, request.task_id, "started", _now(), {}))
    configure_conversion_runtime(Path(request.soffice_path), profile_dir)
    previous_environment = {name: os.environ.get(name) for name in ("TMP", "TEMP", "TMPDIR")}
    for name in previous_environment:
        os.environ[name] = str(work_dir)
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=refresh_heartbeat,
        args=(heartbeat_stop,),
        name=f"task-heartbeat-{request.task_id}",
        daemon=True,
    )
    heartbeat_thread.start()

    exit_code = 0
    with log_path.open("a", encoding="utf-8") as worker_log:
        worker_log.write(f"{_now()} Worker 启动，PID={os.getpid()}\n")
        worker_log.flush()
        try:
            result = selected_audit(
                input_root=Path(request.input_root),
                output_root=output_root,
                rulepack=Path(request.rulepack),
                entity_file=Path(request.entity_file),
                project_root=Path(request.baseline_root),
                # 诊断与集中复核报告是任务结果，必须与可清理临时目录分离。
                runs_root=task_dir / "reports",
                write=True,
                run_id=request.task_id,
                config_file=Path(request.config_file) if request.config_file else None,
                progress_callback=report_progress,
                cancel_check=lambda: is_cancel_requested(task_dir),
            )
            terminal_status = "completed" if result.get("write_completed") else "partial"
            terminal_message = "审核已完成" if terminal_status == "completed" else "审核已结束，存在未完成项"
            total_units = state.total_units if state.total_units is not None else len(result.get("business_results", []))
            write_state(state.with_updates(
                status=terminal_status,
                stage="completed",
                completed_units=total_units,
                total_units=total_units,
                progress_percent=100,
                message=terminal_message,
                worker_pid=None,
                result_summary=_result_summary(result),
            ))
            append_event(events_path, TaskEvent(
                SCHEMA_VERSION, request.task_id, terminal_status, _now(), _result_summary(result),
            ))
        except AuditCancelled as error:
            write_state(state.with_updates(
                status="cancelled",
                stage="cancelled",
                message=str(error),
                worker_pid=None,
                error_code=None,
            ))
            append_event(events_path, TaskEvent(
                SCHEMA_VERSION, request.task_id, "cancelled", _now(), {"message": str(error)},
            ))
        except BaseException as error:
            exit_code = 1
            traceback.print_exc(file=worker_log)
            worker_log.flush()
            write_state(state.with_updates(
                status="failed",
                stage="failed",
                message=str(error),
                worker_pid=None,
                error_code="AUDIT_FAILED",
            ))
            append_event(events_path, TaskEvent(
                SCHEMA_VERSION, request.task_id, "failed", _now(),
                {"error_code": "AUDIT_FAILED", "message": str(error)},
            ))
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=max(1.0, heartbeat_interval_seconds + 0.5))
            for name, previous_value in previous_environment.items():
                if previous_value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous_value
            _cleanup_task_runtime(task_dir, events_path, request.task_id)
    return exit_code
