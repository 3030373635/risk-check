"""验证 SQLite 任务管理器的快照、启动、取消和强制停止。"""

from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace


def create_task(tmp_path: Path, task_id: str = "task-1", status: str = "running"):
    """创建真实数据库任务；tmp_path 为隔离根，task_id/status 为任务属性。"""
    from risk_audit_web.task_contracts import TaskEvent
    from risk_audit_web.task_repository import TaskRepository
    from tests.web.test_task_repository import make_request, make_state

    repository = TaskRepository(tmp_path / "data/tasks.sqlite3")
    repository.initialize()
    request = make_request(tmp_path, task_id)
    state = make_state(task_id).with_updates(status=status, worker_pid=321)
    record = repository.create_task(
        request,
        state,
        TaskEvent("1.0", task_id, "created", request.created_at, {}),
    )
    return repository, record


def test_snapshot_counts_active_tasks_and_projects_database_cancel(tmp_path: Path) -> None:
    """快照必须统计活动任务并投影取消状态；tmp_path 为隔离根。"""
    from risk_audit_web.task_manager import TaskManager

    repository, _record = create_task(tmp_path)
    repository.request_cancel("task-1", "2026-09-30T10:01:00+08:00")
    manager = TaskManager(repository, Path("python"), process_alive=lambda pid: True)

    snapshot = manager.snapshot()

    assert snapshot.running_count == 1
    assert snapshot.tasks[0].state.status == "cancelling"
    assert repository.read_state("task-1").status == "running"


def test_start_task_uses_database_and_task_id_without_shell(tmp_path: Path) -> None:
    """启动参数必须携带数据库和任务编号；tmp_path 为隔离根。"""
    from risk_audit_web.task_manager import TaskManager

    repository, record = create_task(tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def popen(arguments, **options):
        """记录启动调用；arguments 为参数列表，options 为进程选项。"""
        calls.append((arguments, options))
        return SimpleNamespace(pid=700, poll=lambda: None)

    executable = tmp_path / "python"
    manager = TaskManager(repository, executable, popen_factory=popen)

    assert manager.start_task(record) == 700
    assert calls[0][0] == [
        str(executable), "--worker", str(repository.database_path), "task-1",
    ]
    assert calls[0][1]["shell"] is False


def test_exited_worker_is_marked_interrupted(tmp_path: Path) -> None:
    """退出且未发布终态的 Worker 必须变为中断；tmp_path 为隔离根。"""
    from risk_audit_web.task_manager import TaskManager

    repository, _record = create_task(tmp_path)
    manager = TaskManager(repository, Path("python"), process_alive=lambda pid: False)

    task = manager.get_task("task-1")

    assert task is not None
    assert task.state.status == "interrupted"


def test_force_stop_waits_for_exit_then_publishes_cancelled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """强制停止必须等待退出、清理临时目录后再发布取消终态。

    Args:
        tmp_path: pytest 提供的隔离目录。
        monkeypatch: pytest 提供的补丁工具。
    """
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.worker import task_runtime_directory

    repository, _record = create_task(tmp_path, status="cancelling")
    system_temp_root = tmp_path / "system-temp"
    system_temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp_root))
    runtime_dir = task_runtime_directory("task-1")
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "conversion.tmp").write_bytes(b"temporary")
    alive = {321: True}

    def terminate_tree(pid: int, _timeout_seconds: float) -> bool:
        """模拟结束进程树；pid 为根进程号，_timeout_seconds 为等待时限。"""
        alive[pid] = False
        return True

    manager = TaskManager(
        repository,
        Path("python"),
        process_alive=lambda pid: alive.get(pid, False),
        process_tree_terminator=terminate_tree,
    )

    assert manager.force_stop("task-1", expected_pid=321) is True
    assert repository.read_state("task-1").status == "cancelled"
    assert not runtime_dir.exists()


def test_force_stop_does_not_publish_before_process_exit(tmp_path: Path) -> None:
    """进程未退出时不得提前发布取消终态；tmp_path 为隔离根。"""
    from risk_audit_web.task_manager import TaskManager

    repository, record = create_task(tmp_path, status="cancelling")
    process = SimpleNamespace(
        pid=321,
        poll=lambda: None,
        wait=lambda timeout: (_ for _ in ()).throw(subprocess.TimeoutExpired("worker", timeout)),
    )
    manager = TaskManager(
        repository,
        Path("python"),
        process_alive=lambda pid: True,
        process_tree_terminator=lambda _pid, _timeout_seconds: True,
    )
    manager._processes[record.task_id] = process

    assert manager.force_stop("task-1", expected_pid=321) is False
    assert repository.read_state("task-1").status == "cancelling"
