"""用于 Web 集成测试的可控 SQLite Worker 进程。"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time

# 独立子进程不继承 pytest 的 sys.path 修改，必须显式加载工作区源码。
SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from risk_audit_web import task_repository
from risk_audit_web.task_repository import TaskRepository


def now() -> str:
    """返回 UTC ISO 时间；无参数。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def main(database_path: Path, task_id: str, barrier: Path, mode: str) -> int:
    """执行可控任务；参数为数据库、任务编号、栅栏和执行模式。"""
    repository = TaskRepository(database_path)
    repository.initialize()
    request = repository.read_request(task_id)
    output_root = Path(request.output_root)
    task_dir = output_root / ".task"
    for relative in ("work", "lo-profile"):
        directory = task_dir / relative
        directory.mkdir(parents=True)
        (directory / "owner.txt").write_text(task_id, encoding="utf-8")
    (task_dir / "worker.log").write_text(
        f"task={task_id} pid={os.getpid()} module={Path(task_repository.__file__).resolve()}\n",
        encoding="utf-8",
    )
    repository.update_task_state(task_id, {
        "status": "running",
        "stage": "audit",
        "worker_pid": os.getpid(),
        "started_at": now(),
        "heartbeat_at": now(),
        "message": "fake worker running",
    })
    (task_dir / "ready").touch()
    deadline = time.monotonic() + 5
    while not barrier.exists() and time.monotonic() < deadline:
        if repository.is_cancel_requested(task_id):
            repository.update_task_state(task_id, {
                "status": "cancelled", "stage": "cancelled",
                "worker_pid": None, "heartbeat_at": now(),
            })
            return 0
        time.sleep(0.02)
    if mode == "crash":
        return 7
    for completed in range(1, 6):
        if repository.is_cancel_requested(task_id):
            repository.update_task_state(task_id, {
                "status": "cancelled", "stage": "cancelled",
                "worker_pid": None, "heartbeat_at": now(),
            })
            return 0
        repository.update_task_state(task_id, {
            "completed_units": completed,
            "total_units": 5,
            "progress_percent": completed * 20,
            "heartbeat_at": now(),
        })
        time.sleep(0.04)
    # 先完成结果落盘，再发布终态，保证观察到 completed 时结果已经可用。
    (output_root / "result.txt").write_text(task_id, encoding="utf-8")
    repository.update_task_state(task_id, {
        "status": "completed",
        "stage": "completed",
        "worker_pid": None,
        "heartbeat_at": now(),
        "message": "fake worker completed",
        "result_summary": {"passed": 5, "warnings": 0, "failed": 0},
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), sys.argv[4]))
