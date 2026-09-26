"""验证纯 Python 任务管理器的快照和进程控制。"""

from pathlib import Path
import subprocess
from types import SimpleNamespace


def write_task(store, tmp_path: Path, task_id: str, status: str):
    """写入一个完整测试任务；store/tmp_path/task_id/status 为任务测试参数。"""
    from risk_audit_web.task_contracts import TaskRecord, TaskState

    output_root = (tmp_path / task_id).resolve()
    task_dir = output_root / ".task"
    task_dir.mkdir(parents=True)
    (task_dir / "request.json").write_text("{}", encoding="utf-8")
    state_path = task_dir / "state.json"
    record = TaskRecord(
        "1.0", task_id, task_id, str((tmp_path / "input").resolve()),
        str(output_root), "2026-09-25T10:00:00+08:00", str(state_path.resolve()),
    )
    state = TaskState(
        "1.0", task_id, status, status, 1, 2, 50, None, None, None,
        123 if status in {"running", "cancelling"} else None,
        "2026-09-25T10:00:00+08:00", "2026-09-25T10:00:01+08:00",
        "状态", None, None,
    )
    store.add_task(record)
    store.write_state(state_path, state)
    return record


def test_snapshot_isolates_corrupt_state_and_counts_running(tmp_path: Path) -> None:
    """损坏任务不能阻止有效任务形成快照；tmp_path 为隔离任务根。"""
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    running = write_task(store, tmp_path, "running", "running")
    completed = write_task(store, tmp_path, "completed", "completed")
    broken = write_task(store, tmp_path, "broken", "running")
    Path(broken.state_path).write_text("{invalid", encoding="utf-8")
    manager = TaskManager(
        store,
        tmp_path / "app.exe",
        popen_factory=lambda *args, **kwargs: None,
        process_alive=lambda pid: pid == 123,
    )

    snapshot = manager.snapshot()

    assert [(item.record.task_id, item.state.status) for item in snapshot.tasks] == [
        (running.task_id, "running"),
        (completed.task_id, "completed"),
    ]
    assert snapshot.running_count == 1
    assert snapshot.warnings[0].task_id == broken.task_id


def test_cancel_does_not_replace_worker_terminal_state(monkeypatch, tmp_path: Path) -> None:
    """Worker 同时完成时必须保留完成态；monkeypatch/tmp_path 为测试隔离依赖。"""
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record = write_task(store, tmp_path, "running", "running")
    original_touch = Path.touch

    def finish_when_cancel_appears(path: Path, *args, **kwargs) -> None:
        """创建取消标记后模拟 Worker 自然完成；参数透传给 Path.touch。"""
        original_touch(path, *args, **kwargs)
        if path.name == "cancel.requested":
            current = store.read_state(record)
            store.write_state(Path(record.state_path), current.with_updates(
                status="completed", stage="completed", progress_percent=100, worker_pid=None,
            ))

    monkeypatch.setattr(Path, "touch", finish_when_cancel_appears)
    manager = TaskManager(store, tmp_path / "app.exe")

    assert manager.request_cancel(record.task_id)
    assert store.read_state(record).status == "completed"
    assert not (Path(record.output_root) / ".task/cancel.requested").exists()


def test_start_task_uses_argument_list_without_shell(tmp_path: Path) -> None:
    """Worker 必须立即以参数列表启动；tmp_path 为隔离任务根。"""
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record = write_task(store, tmp_path, "a", "running")
    calls = []

    def fake_popen(arguments, **options):
        """记录启动参数；arguments/options 为 Popen 参数。"""
        calls.append((arguments, options))
        return SimpleNamespace(pid=501, poll=lambda: None)

    executable = tmp_path / "app.exe"
    manager = TaskManager(store, executable, popen_factory=fake_popen)

    assert manager.start_task(record) == 501
    assert calls[0][0] == [str(executable), "--worker", str(Path(record.output_root) / ".task/request.json")]
    assert calls[0][1]["shell"] is False


def test_snapshot_preserves_terminal_state_published_on_exit(tmp_path: Path) -> None:
    """进程退出时 Worker 刚发布的终态不得改为中断；tmp_path 为任务根。"""
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record = write_task(store, tmp_path, "a", "running")

    def finish_and_exit() -> int:
        """发布完成态并返回退出码；无参数。"""
        current = store.read_state(record)
        store.write_state(Path(record.state_path), current.with_updates(
            status="completed", stage="completed", progress_percent=100, worker_pid=None,
        ))
        return 0

    manager = TaskManager(store, tmp_path / "app.exe")
    manager._processes[record.task_id] = SimpleNamespace(poll=finish_and_exit)

    snapshot = manager.snapshot()

    assert snapshot.tasks[0].state.status == "completed"
    assert store.read_state(record).status == "completed"


def test_snapshot_projects_cancel_marker_without_persisting_transition(tmp_path: Path) -> None:
    """取消标记只投影为停止中，不覆盖 Worker 状态；tmp_path 为任务根。"""
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record = write_task(store, tmp_path, "a", "running")
    (Path(record.output_root) / ".task/cancel.requested").touch()

    state = TaskManager(store, tmp_path / "app.exe").snapshot().tasks[0].state

    assert state.status == "cancelling"
    assert store.read_state(record).status == "running"


def test_force_stop_rechecks_pid_and_waits_for_exit(tmp_path: Path) -> None:
    """强制停止必须复核 PID 并在退出后才写终态；tmp_path 为任务根。"""
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record = write_task(store, tmp_path, "a", "cancelling")
    terminated = []
    alive_checks = 0

    def process_alive(pid: int) -> bool:
        """首次返回存活，终止后返回退出；pid 为待检查进程号。"""
        nonlocal alive_checks
        alive_checks += 1
        return pid == 123 and not terminated

    manager = TaskManager(
        store,
        tmp_path / "app.exe",
        process_alive=process_alive,
        process_terminator=lambda pid: terminated.append(pid) or True,
    )

    assert manager.force_stop("a", expected_pid=123)
    assert terminated == [123]
    assert alive_checks >= 2
    assert store.read_state(record).status == "cancelled"


def test_force_stop_does_not_publish_cancelled_before_process_exit(tmp_path: Path) -> None:
    """未退出的 Worker 不得被提前标记为 cancelled；tmp_path 为任务根。"""
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record = write_task(store, tmp_path, "a", "cancelling")

    def wait_until_exit(*, timeout: float) -> None:
        """模拟进程等待超时；timeout 为最长等待秒数。"""
        raise subprocess.TimeoutExpired("worker", timeout)

    manager = TaskManager(
        store,
        tmp_path / "app.exe",
        process_alive=lambda _pid: True,
        process_terminator=lambda _pid: True,
    )
    manager._processes[record.task_id] = SimpleNamespace(wait=wait_until_exit)

    assert not manager.force_stop("a", expected_pid=123)
    assert store.read_state(record).status == "cancelling"


def test_get_task_running_ids_and_wait_for_all(tmp_path: Path) -> None:
    """查询和等待接口必须使用同一任务状态来源；tmp_path 为任务根。"""
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    running = write_task(store, tmp_path, "running", "running")
    write_task(store, tmp_path, "completed", "completed")
    manager = TaskManager(store, tmp_path / "app.exe")

    assert manager.get_task("running").record == running
    assert manager.get_task("missing") is None
    assert manager.running_task_ids() == ["running"]
    assert not manager.wait_for_all(0)


def test_stop_all_workers_terminates_each_registered_process_tree(tmp_path: Path) -> None:
    """主服务退出时必须按 Worker 根 PID 清理各自完整进程树。"""
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    terminated = []
    manager = TaskManager(
        store,
        tmp_path / "python",
        process_tree_terminator=lambda pid, timeout: terminated.append((pid, timeout)) or pid != 502,
    )
    manager._processes = {
        "a": SimpleNamespace(pid=501, poll=lambda: None),
        "b": SimpleNamespace(pid=502, poll=lambda: None),
        "done": SimpleNamespace(pid=503, poll=lambda: 0),
    }

    remaining = manager.stop_all_workers(timeout_seconds=2.5)

    assert terminated == [(501, 2.5), (502, 2.5)]
    assert remaining == [502]


def test_start_task_registers_worker_with_process_supervisor(tmp_path: Path) -> None:
    """新 Worker 必须立即登记到平台监管器。"""
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record = write_task(store, tmp_path, "a", "running")
    process = SimpleNamespace(pid=501, poll=lambda: None)
    registered = []
    supervisor = SimpleNamespace(register=lambda current: registered.append(current))
    manager = TaskManager(
        store,
        tmp_path / "python",
        popen_factory=lambda *_args, **_kwargs: process,
        process_supervisor=supervisor,
    )

    manager.start_task(record)

    assert registered == [process]
