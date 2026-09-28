"""验证独立审核 Worker 的状态、取消、日志和清理行为。"""

import json
from pathlib import Path
import threading

import pytest


def create_request(tmp_path: Path):
    """创建 Worker 请求和必需目录；tmp_path 为任务测试根。"""
    from risk_audit_web.task_contracts import TaskRequest
    from risk_audit_web.task_store import atomic_write_json

    output_root = tmp_path / "output"
    task_dir = output_root / ".task"
    task_dir.mkdir(parents=True)
    soffice = tmp_path / "runtime/libreoffice/program/soffice.exe"
    soffice.parent.mkdir(parents=True)
    soffice.write_bytes(b"exe")
    config = tmp_path / "runtime/resources/rulepacks/audit-config.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"disabled_rules": []}', encoding="utf-8")
    request = TaskRequest(
        schema_version="1.0",
        task_id="task-1",
        display_name="第一批资料",
        input_root=str((tmp_path / "input").resolve()),
        output_root=str(output_root.resolve()),
        rulepack=str((tmp_path / "runtime/resources/rulepacks/releases/1.9.19").resolve()),
        entity_file=str((tmp_path / "runtime/resources/entities/entities.xlsx").resolve()),
        baseline_root=str((tmp_path / "runtime/resources/baselines").resolve()),
        config_file=str(config.resolve()),
        soffice_path=str(soffice.resolve()),
        created_at="2026-09-24T10:30:15+08:00",
    )
    request_path = task_dir / "request.json"
    atomic_write_json(request_path, request.to_dict())
    return request, request_path


@pytest.mark.parametrize(
    ("write_completed", "expected_status"),
    [(True, "completed"), (False, "partial")],
)
def test_worker_maps_audit_result_to_terminal_state(
    tmp_path: Path,
    write_completed: bool,
    expected_status: str,
) -> None:
    """审核是否完整写出必须分别映射为完成或部分完成。"""
    from risk_audit_web.task_contracts import TaskState
    from risk_audit_web.worker import run_worker

    request, request_path = create_request(tmp_path)
    captured = {}

    def audit_func(*args, **kwargs):
        """模拟核心审核并发送真实进度事件。"""
        from risk_audit.progress import AuditProgressEvent

        captured.update(kwargs)
        report = kwargs["run_directory"] / "_risk_audit/未审核文件.json"
        report.parent.mkdir(parents=True)
        report.write_text("[]", encoding="utf-8")
        temporary_file = kwargs["work_root"] / "e0001-b0001/system_types/source.xlsx"
        temporary_file.parent.mkdir(parents=True)
        temporary_file.write_bytes(b"temporary")
        statistics = Path(request.output_root) / "审核统计表.xlsx"
        statistics.write_bytes(b"xlsx")
        kwargs["progress_callback"](AuditProgressEvent(
            stage="audit", completed_units=1, total_units=2,
            current_entity="示例单位", current_business="设备管理",
            current_file="矩阵.xlsx", message="正在审核",
        ))
        return {
            "write_completed": write_completed,
            "input_files": 2,
            "findings": 3,
            "warnings": 1,
            "limitations": 0,
            "business_results": [{}, {}],
            "run_dir": str(kwargs["run_directory"]),
            "log_reference": ".task/report/audit.log",
            "audit_statistics_report": str(statistics),
        }

    code = run_worker(request_path, audit_func=audit_func)
    state = TaskState.from_dict(json.loads(
        (Path(request.output_root) / ".task/state.json").read_text(encoding="utf-8")
    ))

    assert code == 0
    assert state.status == expected_status
    assert state.progress_percent == 100
    assert state.result_summary["input_files"] == 2
    assert state.result_summary["findings"] == 3
    assert state.result_summary["warnings"] == 1
    assert state.result_summary["limitations"] == 0
    assert state.result_summary["log_reference"] == ".task/report/audit.log"
    assert Path(state.result_summary["audit_statistics_report"]).is_file()
    assert Path(state.result_summary["unaudited_files_report"]).is_file()
    assert "review_report" not in state.result_summary
    task_directory = Path(request.output_root) / ".task"
    assert captured["runs_root"] == task_directory
    assert captured["run_directory"] == task_directory / "report"
    assert captured["work_root"] == task_directory / "work"
    assert captured["run_id"] == request.task_id
    assert "model_root" not in captured
    assert not (task_directory / "work").exists()
    assert not (task_directory / "libreoffice-profile").exists()
    assert (task_directory / "report").is_dir()


def test_worker_maps_audit_cancel_to_cancelled(tmp_path: Path) -> None:
    """安全取消必须保留为 cancelled，不能被误报为失败或成功。"""
    from risk_audit.progress import AuditCancelled
    from risk_audit_web.task_contracts import TaskState
    from risk_audit_web.worker import run_worker

    request, request_path = create_request(tmp_path)

    def cancel(*args, **kwargs):
        """模拟核心在安全边界响应停止。"""
        temporary_file = kwargs["work_root"] / "e0001-b0001/cancelled.xlsx"
        temporary_file.parent.mkdir(parents=True)
        temporary_file.write_bytes(b"temporary")
        report = kwargs["run_directory"] / "cancelled.json"
        report.parent.mkdir(parents=True)
        report.write_text("{}", encoding="utf-8")
        raise AuditCancelled("用户已请求停止审核")

    code = run_worker(request_path, audit_func=cancel)
    task_directory = Path(request.output_root) / ".task"
    state = TaskState.from_dict(json.loads(
        (task_directory / "state.json").read_text(encoding="utf-8")
    ))

    assert code == 0
    assert state.status == "cancelled"
    assert state.error_code is None
    assert not (task_directory / "work").exists()
    assert (task_directory / "report/cancelled.json").is_file()


def test_worker_failure_writes_failed_state_and_traceback(tmp_path: Path) -> None:
    """致命异常只影响当前任务，并把完整堆栈写入 worker.log。"""
    from risk_audit_web.task_contracts import TaskState
    from risk_audit_web.worker import run_worker

    request, request_path = create_request(tmp_path)

    def fail(*args, **kwargs):
        """模拟不可恢复异常。"""
        temporary_file = kwargs["work_root"] / "e0001-b0001/failed.xlsx"
        temporary_file.parent.mkdir(parents=True)
        temporary_file.write_bytes(b"temporary")
        report = kwargs["run_directory"] / "failed.json"
        report.parent.mkdir(parents=True)
        report.write_text("{}", encoding="utf-8")
        raise RuntimeError("模拟审核崩溃")

    code = run_worker(request_path, audit_func=fail)
    task_dir = Path(request.output_root) / ".task"
    state = TaskState.from_dict(json.loads((task_dir / "state.json").read_text(encoding="utf-8")))

    assert code == 1
    assert state.status == "failed"
    assert state.error_code == "AUDIT_FAILED"
    log_text = (task_dir / "worker.log").read_text(encoding="utf-8")
    assert "RuntimeError: 模拟审核崩溃" in log_text
    assert not (task_dir / "work").exists()
    assert (task_dir / "report/failed.json").is_file()


def test_worker_startup_failure_cleans_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LibreOffice 配置失败也必须发布失败态并清理临时资源。

    Args:
        tmp_path: pytest 提供的隔离目录。
        monkeypatch: pytest 提供的补丁工具。
    """

    from risk_audit_web import worker
    from risk_audit_web.task_contracts import TaskState

    request, request_path = create_request(tmp_path)

    def fail_configuration(*args, **kwargs) -> None:
        """模拟 Worker 进入核心审核前的转换运行时失败。

        Args:
            args: 转换运行时位置参数。
            kwargs: 转换运行时关键字参数。
        """

        raise RuntimeError("转换运行时配置失败")

    monkeypatch.setattr(worker, "configure_conversion_runtime", fail_configuration)

    code = worker.run_worker(request_path)
    task_dir = Path(request.output_root) / ".task"
    state = TaskState.from_dict(json.loads((task_dir / "state.json").read_text(encoding="utf-8")))

    assert code == 1
    assert state.status == "failed"
    assert "RuntimeError: 转换运行时配置失败" in (task_dir / "worker.log").read_text(encoding="utf-8")
    assert not (task_dir / "work").exists()
    assert not (task_dir / "libreoffice-profile").exists()


def test_worker_log_open_failure_cleans_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker 日志无法打开时仍必须发布失败态并清理临时资源。

    Args:
        tmp_path: pytest 提供的隔离目录。
        monkeypatch: pytest 提供的补丁工具。
    """

    from risk_audit_web import worker
    from risk_audit_web.task_contracts import TaskState

    request, request_path = create_request(tmp_path)
    task_dir = Path(request.output_root) / ".task"
    log_path = task_dir / "worker.log"
    original_open = Path.open

    def fail_worker_log(path: Path, *args, **kwargs):
        """仅拒绝 Worker 日志打开，其他文件操作保持真实行为。

        Args:
            path: 当前打开的路径对象。
            args: Path.open 位置参数。
            kwargs: Path.open 关键字参数。
        """

        if path == log_path:
            raise OSError("日志目录不可写")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_worker_log)

    code = worker.run_worker(request_path)
    state = TaskState.from_dict(json.loads((task_dir / "state.json").read_text(encoding="utf-8")))

    assert code == 1
    assert state.status == "failed"
    assert state.message == "日志目录不可写"
    assert not (task_dir / "work").exists()
    assert not (task_dir / "libreoffice-profile").exists()


def test_worker_cancel_check_reads_only_own_marker(tmp_path: Path) -> None:
    """取消检查只能读取当前任务目录中的标记。"""
    from risk_audit_web.worker import is_cancel_requested

    own_task = tmp_path / "a/.task"
    other_task = tmp_path / "b/.task"
    own_task.mkdir(parents=True)
    other_task.mkdir(parents=True)
    (other_task / "cancel.requested").touch()

    assert not is_cancel_requested(own_task)
    (own_task / "cancel.requested").touch()
    assert is_cancel_requested(own_task)


def test_worker_refreshes_heartbeat_during_long_audit_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使核心暂时没有进度事件，Worker 也必须周期刷新心跳。"""
    from risk_audit_web import worker

    request, request_path = create_request(tmp_path)
    state_path = Path(request.output_root) / ".task/state.json"
    heartbeat_written = threading.Event()
    heartbeat_values: list[str] = []
    heartbeat_observed: list[bool] = []
    original_atomic_write_json = worker.atomic_write_json

    def observe_state_write(path: Path, payload: dict) -> None:
        """记录状态写入；path 为目标文件，payload 为状态数据。"""
        original_atomic_write_json(path, payload)
        if path != state_path:
            return
        heartbeat_values.append(payload["heartbeat_at"])
        # 只有时间戳真正变化才能证明心跳已刷新。
        if heartbeat_values[-1] != heartbeat_values[0]:
            heartbeat_written.set()

    monkeypatch.setattr(worker, "atomic_write_json", observe_state_write)

    def slow_audit(*args, **kwargs):
        """模拟单个耗时工作单元；参数与审核核心一致。"""
        heartbeat_observed.append(heartbeat_written.wait(timeout=1.0))
        return {
            "write_completed": True,
            "input_files": 0,
            "findings": 0,
            "warnings": 0,
            "limitations": 0,
            "business_results": [],
        }

    assert worker.run_worker(
        request_path,
        audit_func=slow_audit,
        heartbeat_interval_seconds=0.02,
    ) == 0
    assert heartbeat_observed == [True]
