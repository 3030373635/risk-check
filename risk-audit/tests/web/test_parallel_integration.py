"""验证独立 SQLite Worker 的并行、取消、崩溃隔离和重启恢复。"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import time


def create_task(repository, tmp_path: Path, task_id: str):
    """创建真实进程任务；repository 为仓储，tmp_path 为根，task_id 为编号。"""
    from risk_audit_web.task_contracts import TaskEvent
    from tests.web.test_task_repository import make_request, make_state

    request = make_request(tmp_path, task_id)
    output_root = Path(request.output_root)
    (output_root / ".task").mkdir(parents=True)
    return repository.create_task(
        request,
        make_state(task_id),
        TaskEvent("1.0", task_id, "created", request.created_at, {}),
    )


def wait_until(predicate, timeout: float = 5.0) -> None:
    """有界等待条件成立；predicate 为检查函数，timeout 为最长秒数。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"等待条件超时（{timeout} 秒）")


def make_manager(repository, fake_worker: Path, barrier: Path, modes: dict[str, str]):
    """创建真实子进程管理器；参数为仓储、脚本、栅栏和任务模式。"""
    from risk_audit_web.task_manager import TaskManager

    def arguments(_executable: Path, database: Path, task_id: str) -> list[str]:
        """生成测试 Worker 参数；参数为解释器、数据库和任务编号。"""
        return [
            sys.executable,
            str(fake_worker),
            str(database),
            task_id,
            str(barrier),
            modes.get(task_id, "complete"),
        ]

    return TaskManager(
        repository,
        Path(sys.executable),
        worker_arguments_factory=arguments,
    )


def new_repository(tmp_path: Path):
    """创建已初始化仓储；tmp_path 为隔离根。"""
    from risk_audit_web.task_repository import TaskRepository

    repository = TaskRepository(tmp_path / "data/tasks.sqlite3")
    repository.initialize()
    return repository


def test_two_workers_overlap_and_keep_directories_isolated(tmp_path: Path) -> None:
    """两个 Worker 必须同时运行且工作目录互不混用；tmp_path 为隔离根。"""
    repository = new_repository(tmp_path)
    barrier = tmp_path / "release"
    records = [create_task(repository, tmp_path, task_id) for task_id in ("task-a", "task-b")]
    manager = make_manager(repository, Path(__file__).with_name("fake_worker.py"), barrier, {})
    for record in records:
        manager.start_task(record)
    wait_until(lambda: all((Path(item.output_root) / ".task/ready").exists() for item in records))

    states = [repository.read_state(item.task_id) for item in records]
    assert all(state.status == "running" for state in states)
    assert states[0].worker_pid != states[1].worker_pid
    barrier.touch()
    wait_until(lambda: all(repository.read_state(item.task_id).status == "completed" for item in records))
    for record in records:
        task_dir = Path(record.output_root) / ".task"
        assert (task_dir / "work/owner.txt").read_text(encoding="utf-8") == record.task_id
        assert (task_dir / "lo-profile/owner.txt").read_text(encoding="utf-8") == record.task_id
        assert (Path(record.output_root) / "result.txt").read_text(encoding="utf-8") == record.task_id
        for removed_name in ("request.json", "state.json", "events.jsonl", "cancel.requested"):
            assert not (task_dir / removed_name).exists()


def test_cancelling_one_worker_does_not_affect_other(tmp_path: Path) -> None:
    """取消 A 必须只作用于 A，B 仍正常完成；tmp_path 为隔离根。"""
    repository = new_repository(tmp_path)
    barrier = tmp_path / "release"
    first = create_task(repository, tmp_path, "task-a")
    second = create_task(repository, tmp_path, "task-b")
    manager = make_manager(repository, Path(__file__).with_name("fake_worker.py"), barrier, {})
    manager.start_task(first)
    manager.start_task(second)
    wait_until(lambda: all(
        (Path(item.output_root) / ".task/ready").exists() for item in (first, second)
    ))

    assert manager.request_cancel("task-a")
    barrier.touch()
    wait_until(lambda: repository.read_state("task-a").status == "cancelled")
    wait_until(lambda: repository.read_state("task-b").status == "completed")
    assert repository.is_cancel_requested("task-b") is False


def test_crashed_worker_is_interrupted_while_other_completes(tmp_path: Path) -> None:
    """单个 Worker 崩溃必须中断，另一个仍完成；tmp_path 为隔离根。"""
    repository = new_repository(tmp_path)
    barrier = tmp_path / "release"
    crashed = create_task(repository, tmp_path, "crashed")
    healthy = create_task(repository, tmp_path, "healthy")
    manager = make_manager(
        repository,
        Path(__file__).with_name("fake_worker.py"),
        barrier,
        {"crashed": "crash"},
    )
    manager.start_task(crashed)
    manager.start_task(healthy)
    wait_until(lambda: all(
        (Path(item.output_root) / ".task/ready").exists() for item in (crashed, healthy)
    ))
    barrier.touch()

    def both_terminal() -> bool:
        """驱动进程稳定化并判断两任务终态；无参数。"""
        manager.snapshot()
        return (
            repository.read_state("crashed").status == "interrupted"
            and repository.read_state("healthy").status == "completed"
        )

    wait_until(both_terminal)


def test_new_repository_recovers_live_dead_and_completed_tasks(tmp_path: Path) -> None:
    """新仓储必须保留存活和终态任务，将死进程标为中断；tmp_path 为隔离根。"""
    repository = new_repository(tmp_path)
    for task_id in ("live", "dead", "completed"):
        create_task(repository, tmp_path, task_id)
    fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
    repository.update_task_state("live", {"worker_pid": os.getpid(), "heartbeat_at": fresh})
    repository.update_task_state("dead", {"worker_pid": 2_147_483_647, "heartbeat_at": old})
    repository.update_task_state("completed", {
        "status": "completed", "stage": "completed", "progress_percent": 100,
    })

    restarted = new_repository(tmp_path)
    recovery = restarted.recover_tasks(is_process_alive=lambda pid: pid == os.getpid())
    states = {task.record.task_id: task.state.status for task in recovery.tasks}

    assert states == {"live": "running", "dead": "interrupted", "completed": "completed"}
