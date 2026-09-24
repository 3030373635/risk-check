"""验证桌面任务请求、状态、索引和事件契约。"""

from pathlib import Path
import sys
from types import ModuleType

import pytest


def request_payload(tmp_path: Path) -> dict:
    """构造完整请求字典；tmp_path 为绝对路径根。"""
    return {
        "schema_version": "1.0",
        "task_id": "20260924-103015-a1b2",
        "display_name": "第一批资料",
        "input_root": str(tmp_path / "input"),
        "output_root": str(tmp_path / "output"),
        "rulepack": str(tmp_path / "runtime/resources/rulepacks/releases/1.9.19"),
        "entity_file": str(tmp_path / "runtime/resources/entities/entities.xlsx"),
        "baseline_root": str(tmp_path / "runtime/resources/baselines"),
        "model_root": str(tmp_path / "runtime/resources/models/model"),
        "config_file": str(tmp_path / "runtime/resources/rulepacks/audit-config.json"),
        "soffice_path": str(tmp_path / "runtime/libreoffice/program/soffice.exe"),
        "created_at": "2026-09-24T10:30:15+08:00",
    }


def test_task_request_round_trip_ignores_unknown_fields(tmp_path: Path) -> None:
    """协议升级附带的未知字段不能破坏当前 Worker。"""
    from risk_audit_desktop.task_contracts import TaskRequest

    payload = {**request_payload(tmp_path), "future_field": "ignored"}
    request = TaskRequest.from_dict(payload)

    assert request.to_dict() == request_payload(tmp_path)


def test_task_request_reports_missing_required_field(tmp_path: Path) -> None:
    """缺失必填路径时错误必须指出字段名。"""
    from risk_audit_desktop.task_contracts import ContractError, TaskRequest

    payload = request_payload(tmp_path)
    payload.pop("input_root")

    with pytest.raises(ContractError, match="input_root"):
        TaskRequest.from_dict(payload)


def test_task_request_rejects_relative_paths(tmp_path: Path) -> None:
    """Worker 请求使用相对路径会依赖 cwd，必须在解析时拒绝。"""
    from risk_audit_desktop.task_contracts import ContractError, TaskRequest

    payload = request_payload(tmp_path)
    payload["output_root"] = "outputs/task"

    with pytest.raises(ContractError, match="output_root"):
        TaskRequest.from_dict(payload)


def test_task_state_accepts_only_documented_statuses() -> None:
    """拼写错误的状态不能进入任务列表。"""
    from risk_audit_desktop.task_contracts import ContractError, TaskState

    payload = {
        "schema_version": "1.0",
        "task_id": "task-1",
        "status": "queued",
        "stage": "startup",
        "completed_units": 0,
        "total_units": None,
        "progress_percent": None,
        "current_entity": None,
        "current_business": None,
        "current_file": None,
        "worker_pid": None,
        "started_at": None,
        "heartbeat_at": "2026-09-24T10:30:15+08:00",
        "message": "等待",
        "error_code": None,
        "result_summary": None,
    }

    with pytest.raises(ContractError, match="queued"):
        TaskState.from_dict(payload)


def test_task_state_round_trip_keeps_progress_context() -> None:
    """界面需要的阶段、当前业务和结果摘要必须完整往返。"""
    from risk_audit_desktop.task_contracts import TaskState

    state = TaskState(
        schema_version="1.0",
        task_id="task-1",
        status="running",
        stage="audit",
        completed_units=17,
        total_units=25,
        progress_percent=68,
        current_entity="示例单位",
        current_business="设备管理",
        current_file="矩阵.xlsx",
        worker_pid=123,
        started_at="2026-09-24T10:30:15+08:00",
        heartbeat_at="2026-09-24T10:33:57+08:00",
        message="正在执行审核规则",
        error_code=None,
        result_summary={"findings": 8},
    )

    assert TaskState.from_dict({**state.to_dict(), "future": True}) == state


def test_worker_dispatch_does_not_import_qt_widgets(monkeypatch, tmp_path: Path) -> None:
    """Worker 参数必须在任何 Qt Widgets 页面导入前完成分流。"""
    from risk_audit_desktop import app

    request_path = tmp_path / "request.json"
    request_path.write_text("{}", encoding="utf-8")
    fake_worker = ModuleType("risk_audit_desktop.worker")
    captured = []

    def run_worker(path: Path) -> int:
        """记录分流路径；path 为命令行请求文件。"""
        captured.append(path)
        return 7

    fake_worker.run_worker = run_worker
    monkeypatch.setitem(sys.modules, "risk_audit_desktop.worker", fake_worker)
    qt_widgets_before = sys.modules.get("PySide6.QtWidgets")

    code = app.main(["--worker", str(request_path)])

    assert code == 7
    assert captured == [request_path]
    # pytest-qt 可能已在收集阶段加载 QtWidgets；Worker 分流不得改变它的加载状态。
    assert sys.modules.get("PySide6.QtWidgets") is qt_widgets_before


def test_desktop_worker_arguments_support_source_and_frozen_modes(tmp_path: Path) -> None:
    """源码运行必须通过模块启动 Worker，打包后则复用同一 EXE。"""
    from risk_audit_desktop.main_window import desktop_worker_arguments

    executable = tmp_path / "python.exe"
    request = tmp_path / "request.json"

    assert desktop_worker_arguments(executable, request, frozen=True) == [
        str(executable), "--worker", str(request),
    ]
    assert desktop_worker_arguments(executable, request, frozen=False) == [
        str(executable), "-m", "risk_audit_desktop.app", "--worker", str(request),
    ]
