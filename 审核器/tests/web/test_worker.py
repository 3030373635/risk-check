"""验证独立审核 Worker 的状态、取消、日志和清理行为。"""

import json
from pathlib import Path
import time

import pytest


def create_request(tmp_path: Path):
    """创建 Worker 请求和必需目录；tmp_path 为任务测试根。"""
    from risk_audit_web.task_contracts import TaskRequest
    from risk_audit_web.task_store import atomic_write_json

    output_root = tmp_path / "output"
    task_dir = output_root / "_task"
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
        report = kwargs["runs_root"] / kwargs["run_id"] / "_risk_audit/集中复核事项.md"
        report.parent.mkdir(parents=True)
        report.write_text("复核事项", encoding="utf-8")
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
            "run_dir": str(kwargs["runs_root"] / kwargs["run_id"]),
            "audit_statistics_report": str(statistics),
        }

    code = run_worker(request_path, audit_func=audit_func)
    state = TaskState.from_dict(json.loads(
        (Path(request.output_root) / "_task/state.json").read_text(encoding="utf-8")
    ))

    assert code == 0
    assert state.status == expected_status
    assert state.progress_percent == 100
    assert state.result_summary["input_files"] == 2
    assert state.result_summary["findings"] == 3
    assert state.result_summary["warnings"] == 1
    assert state.result_summary["limitations"] == 0
    assert Path(state.result_summary["audit_statistics_report"]).is_file()
    assert Path(state.result_summary["review_report"]).is_file()
    assert captured["runs_root"] == Path(request.output_root) / "_task/reports"
    assert "model_root" not in captured
    assert not (Path(request.output_root) / "_task/work").exists()
    assert not (Path(request.output_root) / "_task/libreoffice-profile").exists()
    assert (Path(request.output_root) / "_task/reports").is_dir()


def test_worker_maps_audit_cancel_to_cancelled(tmp_path: Path) -> None:
    """安全取消必须保留为 cancelled，不能被误报为失败或成功。"""
    from risk_audit.progress import AuditCancelled
    from risk_audit_web.task_contracts import TaskState
    from risk_audit_web.worker import run_worker

    request, request_path = create_request(tmp_path)

    def cancel(*args, **kwargs):
        """模拟核心在安全边界响应停止。"""
        raise AuditCancelled("用户已请求停止审核")

    code = run_worker(request_path, audit_func=cancel)
    state = TaskState.from_dict(json.loads(
        (Path(request.output_root) / "_task/state.json").read_text(encoding="utf-8")
    ))

    assert code == 0
    assert state.status == "cancelled"
    assert state.error_code is None


def test_worker_failure_writes_failed_state_and_traceback(tmp_path: Path) -> None:
    """致命异常只影响当前任务，并把完整堆栈写入 worker.log。"""
    from risk_audit_web.task_contracts import TaskState
    from risk_audit_web.worker import run_worker

    request, request_path = create_request(tmp_path)

    def fail(*args, **kwargs):
        """模拟不可恢复异常。"""
        raise RuntimeError("模拟审核崩溃")

    code = run_worker(request_path, audit_func=fail)
    task_dir = Path(request.output_root) / "_task"
    state = TaskState.from_dict(json.loads((task_dir / "state.json").read_text(encoding="utf-8")))

    assert code == 1
    assert state.status == "failed"
    assert state.error_code == "AUDIT_FAILED"
    log_text = (task_dir / "worker.log").read_text(encoding="utf-8")
    assert "RuntimeError: 模拟审核崩溃" in log_text


def test_worker_cancel_check_reads_only_own_marker(tmp_path: Path) -> None:
    """取消检查只能读取当前任务目录中的标记。"""
    from risk_audit_web.worker import is_cancel_requested

    own_task = tmp_path / "a/_task"
    other_task = tmp_path / "b/_task"
    own_task.mkdir(parents=True)
    other_task.mkdir(parents=True)
    (other_task / "cancel.requested").touch()

    assert not is_cancel_requested(own_task)
    (own_task / "cancel.requested").touch()
    assert is_cancel_requested(own_task)


def test_worker_refreshes_heartbeat_during_long_audit_unit(tmp_path: Path) -> None:
    """即使核心暂时没有进度事件，Worker 也必须周期刷新心跳。"""
    from risk_audit_web.worker import run_worker

    request, request_path = create_request(tmp_path)
    state_path = Path(request.output_root) / "_task/state.json"
    heartbeats = []

    def slow_audit(*args, **kwargs):
        """模拟单个耗时工作单元；参数与审核核心一致。"""
        heartbeats.append(json.loads(state_path.read_text(encoding="utf-8"))["heartbeat_at"])
        time.sleep(0.08)
        heartbeats.append(json.loads(state_path.read_text(encoding="utf-8"))["heartbeat_at"])
        return {
            "write_completed": True,
            "input_files": 0,
            "findings": 0,
            "warnings": 0,
            "limitations": 0,
            "business_results": [],
        }

    assert run_worker(request_path, audit_func=slow_audit, heartbeat_interval_seconds=0.02) == 0
    assert heartbeats[0] != heartbeats[1]
