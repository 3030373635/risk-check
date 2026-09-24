"""验证桌面进程管理所需的平台和恢复基础能力。"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace


def test_is_process_alive_detects_current_and_missing_pid() -> None:
    """平台适配必须能区分当前进程与不存在的 PID。"""
    from risk_audit_desktop.platform_windows import is_process_alive

    assert is_process_alive(os.getpid())
    assert not is_process_alive(2_147_483_647)


def create_managed_task(tmp_path: Path, task_id: str, status: str = "running"):
    """创建任务索引和状态；参数为测试根、编号和状态。"""
    from risk_audit_desktop.task_contracts import TaskRecord, TaskState

    output_root = (tmp_path / task_id).resolve()
    task_dir = output_root / "_task"
    task_dir.mkdir(parents=True)
    request_path = task_dir / "request.json"
    request_path.write_text("{}", encoding="utf-8")
    record = TaskRecord(
        schema_version="1.0",
        task_id=task_id,
        display_name=task_id,
        input_root=str((tmp_path / "input").resolve()),
        output_root=str(output_root),
        created_at="2026-09-24T10:30:15+08:00",
        state_path=str((task_dir / "state.json").resolve()),
    )
    state = TaskState(
        schema_version="1.0",
        task_id=task_id,
        status=status,
        stage="audit" if status == "running" else status,
        completed_units=1,
        total_units=2,
        progress_percent=50 if status == "running" else 100,
        current_entity=None,
        current_business=None,
        current_file=None,
        worker_pid=123 if status == "running" else None,
        started_at="2026-09-24T10:30:15+08:00",
        heartbeat_at="2026-09-24T10:30:16+08:00",
        message="状态",
        error_code=None,
        result_summary=None,
    )
    return record, state, request_path


def test_task_manager_starts_each_worker_immediately_with_argument_list(tmp_path: Path) -> None:
    """多个任务必须分别立即启动，不能进入应用级串行队列。"""
    from risk_audit_desktop.task_manager import TaskManager
    from risk_audit_desktop.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    records = []
    for task_id in ("a", "b"):
        record, state, _ = create_managed_task(tmp_path, task_id)
        store.add_task(record)
        store.write_state(Path(record.state_path), state.with_updates(worker_pid=None))
        records.append(record)
    calls = []

    def fake_popen(arguments, **kwargs):
        """记录子进程参数并返回不同 PID；参数与 subprocess.Popen 一致。"""
        calls.append((arguments, kwargs))
        return SimpleNamespace(pid=500 + len(calls), poll=lambda: None)

    executable = tmp_path / "风控矩阵审核器.exe"
    manager = TaskManager(store, executable, popen_factory=fake_popen, start_timer=False)

    manager.start_task(records[0])
    manager.start_task(records[1])

    assert len(calls) == 2
    assert calls[0][0] == [str(executable), "--worker", str(Path(records[0].output_root) / "_task/request.json")]
    assert calls[1][0] == [str(executable), "--worker", str(Path(records[1].output_root) / "_task/request.json")]
    assert all(call[1]["shell"] is False and "cwd" not in call[1] for call in calls)


def test_poll_once_isolates_corrupt_state_and_reports_running_count(tmp_path: Path) -> None:
    """一个损坏状态文件不能阻止其他任务刷新。"""
    from risk_audit_desktop.task_manager import TaskManager
    from risk_audit_desktop.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    valid_running, running_state, _ = create_managed_task(tmp_path, "running")
    broken, _, _ = create_managed_task(tmp_path, "broken")
    completed, completed_state, _ = create_managed_task(tmp_path, "completed", "completed")
    for record in (valid_running, broken, completed):
        store.add_task(record)
    store.write_state(Path(valid_running.state_path), running_state)
    Path(broken.state_path).write_text("{invalid", encoding="utf-8")
    store.write_state(Path(completed.state_path), completed_state)
    manager = TaskManager(store, tmp_path / "app.exe", start_timer=False)
    updates = []
    errors = []
    counts = []
    manager.task_updated.connect(lambda task_id, state: updates.append((task_id, state.status)))
    manager.task_error.connect(lambda task_id, message: errors.append((task_id, message)))
    manager.running_count_changed.connect(counts.append)

    manager.poll_once()

    assert updates == [("running", "running"), ("completed", "completed")]
    assert errors and errors[0][0] == "broken"
    assert counts == [1]


def test_cancel_one_task_does_not_modify_other_task(tmp_path: Path) -> None:
    """取消 A 只能创建 A 的标记并更新 A 状态。"""
    from risk_audit_desktop.task_manager import TaskManager
    from risk_audit_desktop.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    records = []
    for task_id in ("a", "b"):
        record, state, _ = create_managed_task(tmp_path, task_id)
        store.add_task(record)
        store.write_state(Path(record.state_path), state)
        records.append(record)
    manager = TaskManager(store, tmp_path / "app.exe", start_timer=False)

    manager.request_cancel("a")

    assert (Path(records[0].output_root) / "_task/cancel.requested").is_file()
    assert not (Path(records[1].output_root) / "_task/cancel.requested").exists()
    assert store.read_state(records[0]).status == "cancelling"
    assert store.read_state(records[1]).status == "running"


def test_force_stop_rechecks_state_pid_before_terminating(tmp_path: Path) -> None:
    """强制结束只能作用于状态文件当前记录的 PID。"""
    from risk_audit_desktop.task_manager import TaskManager
    from risk_audit_desktop.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record, state, _ = create_managed_task(tmp_path, "a")
    store.add_task(record)
    store.write_state(Path(record.state_path), state.with_updates(status="cancelling"))
    terminated = []
    manager = TaskManager(
        store,
        tmp_path / "app.exe",
        process_alive=lambda pid: pid == 123,
        process_terminator=lambda pid: terminated.append(pid) or True,
        start_timer=False,
    )

    assert manager.force_stop("a", expected_pid=123)

    assert terminated == [123]
    assert store.read_state(record).status == "cancelled"


def test_force_stop_rejects_stale_expected_pid(tmp_path: Path) -> None:
    """界面看到的旧 PID 与最新状态不一致时不得结束进程。"""
    from risk_audit_desktop.task_manager import TaskManager
    from risk_audit_desktop.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record, state, _ = create_managed_task(tmp_path, "a")
    store.add_task(record)
    store.write_state(Path(record.state_path), state.with_updates(worker_pid=456))
    terminated = []
    manager = TaskManager(
        store,
        tmp_path / "app.exe",
        process_alive=lambda pid: True,
        process_terminator=lambda pid: terminated.append(pid) or True,
        start_timer=False,
    )

    assert not manager.force_stop("a", expected_pid=123)
    assert terminated == []
