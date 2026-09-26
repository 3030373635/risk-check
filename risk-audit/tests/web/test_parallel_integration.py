"""验证独立 Worker 的并行、取消、崩溃隔离和重启恢复。"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time


def create_task(store, tmp_path: Path, task_id: str, barrier: Path, mode: str = "complete"):
    """创建可供真实子进程使用的任务；参数为存储、路径、编号、栅栏和模式。"""
    from risk_audit_web.task_contracts import TaskRecord, TaskState

    output_root = (tmp_path / "outputs" / task_id).resolve()
    task_dir = output_root / ".task"
    task_dir.mkdir(parents=True)
    request_path = task_dir / "request.json"
    request_path.write_text(json.dumps({
        "task_id": task_id,
        "barrier": str(barrier.resolve()),
        "mode": mode,
    }), encoding="utf-8")
    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    record = TaskRecord(
        "1.0", task_id, task_id, str((tmp_path / "input").resolve()),
        str(output_root), timestamp, str((task_dir / "state.json").resolve()),
    )
    state = TaskState(
        "1.0", task_id, "running", "startup", 0, None, None,
        None, None, None, None, None, timestamp, "created", None, None,
    )
    store.add_task(record)
    store.write_state(Path(record.state_path), state)
    return record


def wait_until(predicate, timeout: float = 5.0) -> None:
    """有界等待条件成立；predicate 为检查函数，timeout 为最长秒数。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"等待条件超时（{timeout} 秒）")


def make_manager(store, fake_worker: Path):
    """创建使用真实 Python 子进程的管理器；参数为存储和脚本。"""
    from risk_audit_web.task_manager import TaskManager

    return TaskManager(
        store,
        Path(sys.executable),
        worker_arguments_factory=lambda _executable, request: [sys.executable, str(fake_worker), str(request)],
    )


def test_two_workers_overlap_and_keep_directories_isolated(tmp_path: Path) -> None:
    """两个 Worker 必须在同一栅栏前同时就绪，且工作目录互不混用。"""
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    barrier = tmp_path / "release"
    a = create_task(store, tmp_path, "task-a", barrier)
    b = create_task(store, tmp_path, "task-b", barrier)
    manager = make_manager(store, Path(__file__).with_name("fake_worker.py"))
    manager.start_task(a)
    manager.start_task(b)
    wait_until(lambda: all((Path(record.output_root) / ".task/ready").exists() for record in (a, b)))

    state_a, state_b = store.read_state(a), store.read_state(b)
    assert state_a.status == state_b.status == "running"
    assert state_a.worker_pid != state_b.worker_pid
    barrier.touch()
    wait_until(lambda: store.read_state(a).status == store.read_state(b).status == "completed")
    for record in (a, b):
        task_dir = Path(record.output_root) / ".task"
        assert (task_dir / "work/owner.txt").read_text(encoding="utf-8") == record.task_id
        assert (task_dir / "lo-profile/owner.txt").read_text(encoding="utf-8") == record.task_id
        assert record.task_id in (task_dir / "worker.log").read_text(encoding="utf-8")
        assert (Path(record.output_root) / "result.txt").read_text(encoding="utf-8") == record.task_id


def test_cancelling_one_worker_does_not_affect_the_other(tmp_path: Path) -> None:
    """取消 A 必须只作用于 A，B 仍然正常完成。"""
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    barrier = tmp_path / "release"
    a = create_task(store, tmp_path, "task-a", barrier)
    b = create_task(store, tmp_path, "task-b", barrier)
    manager = make_manager(store, Path(__file__).with_name("fake_worker.py"))
    manager.start_task(a)
    manager.start_task(b)
    wait_until(lambda: all((Path(record.output_root) / ".task/ready").exists() for record in (a, b)))
    assert manager.request_cancel("task-a")
    barrier.touch()
    wait_until(lambda: store.read_state(a).status == "cancelled")
    wait_until(lambda: store.read_state(b).status == "completed")

    assert not (Path(b.output_root) / ".task/cancel.requested").exists()
    assert "task-a" not in (Path(b.output_root) / ".task/worker.log").read_text(encoding="utf-8")


def test_crashed_worker_is_interrupted_while_other_completes(tmp_path: Path) -> None:
    """单个 Worker 无终态退出后必须中断，另一个仍可更新至完成。"""
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    barrier = tmp_path / "release"
    crashed = create_task(store, tmp_path, "crashed", barrier, "crash")
    healthy = create_task(store, tmp_path, "healthy", barrier)
    manager = make_manager(store, Path(__file__).with_name("fake_worker.py"))
    manager.start_task(crashed)
    manager.start_task(healthy)
    wait_until(lambda: all((Path(record.output_root) / ".task/ready").exists() for record in (crashed, healthy)))
    barrier.touch()

    def poll_to_terminal() -> bool:
        """轮询直至两任务终态；无参数。"""
        manager.snapshot()
        return store.read_state(crashed).status == "interrupted" and store.read_state(healthy).status == "completed"

    wait_until(poll_to_terminal)
    states = {item.record.task_id: item.state.status for item in manager.snapshot().tasks}
    assert states["healthy"] == "completed"


def test_new_store_recovers_live_dead_and_completed_tasks(tmp_path: Path) -> None:
    """新实例必须保留存活和已完成任务，将死进程标为中断。"""
    from risk_audit_web.task_store import TaskStore

    original = TaskStore(tmp_path / "data")
    barrier = tmp_path / "unused"
    live = create_task(original, tmp_path, "live", barrier)
    dead = create_task(original, tmp_path, "dead", barrier)
    completed = create_task(original, tmp_path, "completed", barrier)
    fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
    original.write_state(Path(live.state_path), original.read_state(live).with_updates(worker_pid=os.getpid(), heartbeat_at=fresh))
    original.write_state(Path(dead.state_path), original.read_state(dead).with_updates(worker_pid=2_147_483_647, heartbeat_at=old))
    original.write_state(Path(completed.state_path), original.read_state(completed).with_updates(status="completed", stage="completed", progress_percent=100))

    restarted = TaskStore(tmp_path / "data")
    recovery = restarted.recover_tasks(is_process_alive=lambda pid: pid == os.getpid())
    states = {task.record.task_id: task.state.status for task in recovery.tasks}

    assert states == {"live": "running", "dead": "interrupted", "completed": "completed"}
