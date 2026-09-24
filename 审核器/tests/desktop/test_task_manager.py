"""验证桌面进程管理所需的平台和恢复基础能力。"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
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


def test_start_task_does_not_overwrite_worker_terminal_state(tmp_path: Path) -> None:
    """Worker 启动后抢先写入的终态不得被 Manager 覆盖。"""
    from risk_audit_desktop.task_manager import TaskManager
    from risk_audit_desktop.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record, state, _ = create_managed_task(tmp_path, "a")
    store.add_task(record)
    store.write_state(Path(record.state_path), state.with_updates(worker_pid=None))

    def fake_popen(arguments, **kwargs):
        """模拟 Worker 启动即完成；arguments/kwargs 为 Popen 透传参数。"""
        current = store.read_state(record)
        store.write_state(Path(record.state_path), current.with_updates(
            status="completed",
            stage="completed",
            progress_percent=100,
            worker_pid=None,
        ))
        return SimpleNamespace(pid=501, poll=lambda: 0)

    manager = TaskManager(
        store,
        tmp_path / "app.exe",
        popen_factory=fake_popen,
        start_timer=False,
    )

    manager.start_task(record)

    assert store.read_state(record).status == "completed"


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


def test_poll_once_rereads_terminal_state_after_process_exit(tmp_path: Path) -> None:
    """进程退出时 Worker 刚发布的终态不得被标记为中断。"""
    from risk_audit_desktop.task_manager import TaskManager
    from risk_audit_desktop.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record, state, _ = create_managed_task(tmp_path, "a")
    store.add_task(record)
    store.write_state(Path(record.state_path), state)

    def finish_worker_and_report_exit() -> int:
        """Worker 写入完成终态后返回进程退出码；无参数。"""
        current = store.read_state(record)
        store.write_state(Path(record.state_path), current.with_updates(
            status="completed",
            stage="completed",
            progress_percent=100,
            worker_pid=None,
        ))
        return 0

    manager = TaskManager(store, tmp_path / "app.exe", start_timer=False)
    manager._processes[record.task_id] = SimpleNamespace(poll=finish_worker_and_report_exit)

    manager.poll_once()

    assert store.read_state(record).status == "completed"


def test_cancel_one_task_does_not_modify_persisted_states(tmp_path: Path) -> None:
    """取消 A 只创建 A 的标记，界面投影停止中但不改持久化状态。"""
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
    updates = []
    manager.task_updated.connect(lambda task_id, state: updates.append((task_id, state.status)))

    manager.request_cancel("a")
    manager.poll_once()

    assert (Path(records[0].output_root) / "_task/cancel.requested").is_file()
    assert not (Path(records[1].output_root) / "_task/cancel.requested").exists()
    assert store.read_state(records[0]).status == "running"
    assert store.read_state(records[1]).status == "running"
    assert updates == [("a", "cancelling"), ("b", "running")]


def test_cancel_request_does_not_overwrite_natural_completion(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """取消读取运行态时 Worker 恰好完成，Manager 不得覆盖完成终态。"""
    from risk_audit_desktop.task_manager import TaskManager
    from risk_audit_desktop.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record, state, _ = create_managed_task(tmp_path, "a")
    store.add_task(record)
    store.write_state(Path(record.state_path), state)
    cancel_path = Path(record.output_root) / "_task/cancel.requested"
    original_read_state = store.read_state
    original_write_state = store.write_state

    def complete_worker_after_state_read(record_to_read):
        """模拟状态读取后 Worker 自然完成；record_to_read 为待读任务。"""
        current = original_read_state(record_to_read)
        if current.status == "running":
            original_write_state(
                Path(record.state_path),
                current.with_updates(
                    status="completed",
                    stage="completed",
                    progress_percent=100,
                    worker_pid=None,
                ),
            )
            cancel_path.unlink(missing_ok=True)
        return current

    monkeypatch.setattr(store, "read_state", complete_worker_after_state_read)
    manager = TaskManager(store, tmp_path / "app.exe", start_timer=False)

    assert manager.request_cancel("a")
    assert original_read_state(record).status == "completed"
    assert not cancel_path.exists()


def test_cancel_request_does_not_overwrite_worker_terminal_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """取消标记触发 Worker 终态后，主进程不得覆盖该终态。"""
    from risk_audit_desktop.task_manager import TaskManager
    from risk_audit_desktop.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record, state, _ = create_managed_task(tmp_path, "a")
    store.add_task(record)
    store.write_state(Path(record.state_path), state)
    cancel_path = Path(record.output_root) / "_task/cancel.requested"
    original_touch = Path.touch

    def finish_worker_when_cancel_is_visible(path: Path, *args, **kwargs) -> None:
        """模拟 Worker 立即写入终态。

        path 为标记路径，args 和 kwargs 为 Path.touch 的透传参数。
        """
        original_touch(path, *args, **kwargs)
        if path == cancel_path:
            current = store.read_state(record)
            store.write_state(
                Path(record.state_path),
                current.with_updates(
                    status="cancelled",
                    stage="cancelled",
                    worker_pid=None,
                ),
            )

    monkeypatch.setattr(Path, "touch", finish_worker_when_cancel_is_visible)
    manager = TaskManager(store, tmp_path / "app.exe", start_timer=False)

    assert manager.request_cancel("a")
    assert store.read_state(record).status == "cancelled"


def test_repeated_cancel_request_preserves_worker_terminal_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """重复取消与 Worker 终态交错时，不得把终态覆盖回过渡态。"""
    from risk_audit_desktop.task_manager import TaskManager
    from risk_audit_desktop.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record, state, _ = create_managed_task(tmp_path, "a")
    store.add_task(record)
    store.write_state(Path(record.state_path), state.with_updates(status="cancelling"))
    original_read_state = store.read_state
    original_write_state = store.write_state

    def finish_worker_after_manager_reads(record_to_read):
        """模拟读取后 Worker 写终态；record_to_read 为待读取任务。"""
        current = original_read_state(record_to_read)
        if current.status == "cancelling":
            original_write_state(
                Path(record.state_path),
                current.with_updates(
                    status="cancelled",
                    stage="cancelled",
                    worker_pid=None,
                ),
            )
        return current

    monkeypatch.setattr(store, "read_state", finish_worker_after_manager_reads)
    manager = TaskManager(store, tmp_path / "app.exe", start_timer=False)

    assert manager.request_cancel("a")
    assert original_read_state(record).status == "cancelled"


def test_existing_cancel_marker_prevents_stale_running_state_write(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """心跳写回 running 后，已存在的取消标记必须阻止陈旧状态写入。"""
    from risk_audit_desktop.task_manager import TaskManager
    from risk_audit_desktop.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record, state, _ = create_managed_task(tmp_path, "a")
    store.add_task(record)
    store.write_state(Path(record.state_path), state)
    cancel_path = Path(record.output_root) / "_task/cancel.requested"
    cancel_path.touch()
    original_is_file = Path.is_file

    def finish_worker_when_marker_is_checked(path: Path) -> bool:
        """模拟检查标记时 Worker 写终态并清理；path 为待检查路径。"""
        if path == cancel_path:
            current = store.read_state(record)
            store.write_state(
                Path(record.state_path),
                current.with_updates(
                    status="cancelled",
                    stage="cancelled",
                    worker_pid=None,
                ),
            )
            path.unlink()
            return False
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", finish_worker_when_marker_is_checked)
    manager = TaskManager(store, tmp_path / "app.exe", start_timer=False)

    assert not manager.request_cancel("a")
    assert store.read_state(record).status == "cancelled"


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
        process_alive=lambda pid: pid == 123 and not terminated,
        process_terminator=lambda pid: terminated.append(pid) or True,
        start_timer=False,
    )

    assert manager.force_stop("a", expected_pid=123)

    assert terminated == [123]
    assert store.read_state(record).status == "cancelled"


def test_force_stop_preserves_terminal_state_published_during_exit(tmp_path: Path) -> None:
    """强制停止等待期间 Worker 发布的终态必须保留。"""
    from risk_audit_desktop.task_manager import TaskManager
    from risk_audit_desktop.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record, state, _ = create_managed_task(tmp_path, "a")
    store.add_task(record)
    store.write_state(Path(record.state_path), state.with_updates(status="cancelling"))
    alive_checks = 0

    def process_alive(pid: int) -> bool:
        """首次确认存活，再次检查时发布终态；pid 为 Worker 进程号。"""
        nonlocal alive_checks
        alive_checks += 1
        if alive_checks == 1:
            return pid == 123
        current = store.read_state(record)
        store.write_state(Path(record.state_path), current.with_updates(
            status="completed",
            stage="completed",
            progress_percent=100,
            worker_pid=None,
        ))
        return False

    manager = TaskManager(
        store,
        tmp_path / "app.exe",
        process_alive=process_alive,
        process_terminator=lambda _pid: True,
        start_timer=False,
    )

    assert manager.force_stop("a", expected_pid=123)
    assert alive_checks >= 2
    assert store.read_state(record).status == "completed"


def test_force_stop_does_not_publish_cancelled_before_process_exit(tmp_path: Path) -> None:
    """进程未退出时强制停止不得提前写入 cancelled。"""
    from risk_audit_desktop.task_manager import TaskManager
    from risk_audit_desktop.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record, state, _ = create_managed_task(tmp_path, "a")
    store.add_task(record)
    store.write_state(Path(record.state_path), state.with_updates(status="cancelling"))

    def wait_until_exit(*, timeout: float) -> None:
        """模拟在 timeout 秒内未退出的 Worker；timeout 为最长等待秒数。"""
        raise subprocess.TimeoutExpired("worker", timeout)

    process = SimpleNamespace(wait=wait_until_exit)
    manager = TaskManager(
        store,
        tmp_path / "app.exe",
        process_alive=lambda _pid: True,
        process_terminator=lambda _pid: True,
        start_timer=False,
    )
    manager._processes[record.task_id] = process

    assert not manager.force_stop("a", expected_pid=123)
    assert store.read_state(record).status == "cancelling"


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
