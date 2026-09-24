"""用于桌面集成测试的可控独立 Worker 进程。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def atomic_write(path: Path, payload: dict) -> None:
    """原子写入状态；path 为目标，payload 为 JSON 数据。"""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(temporary, path)


def now() -> str:
    """返回 UTC ISO 时间；无参数。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def main(request_path: Path) -> int:
    """执行可控任务；request_path 为测试请求路径，返回退出码。"""
    request = json.loads(request_path.read_text(encoding="utf-8"))
    task_dir = request_path.parent
    output_root = task_dir.parent
    state_path = task_dir / "state.json"
    barrier = Path(request["barrier"])
    for relative in ("work", "lo-profile"):
        directory = task_dir / relative
        directory.mkdir()
        (directory / "owner.txt").write_text(request["task_id"], encoding="utf-8")
    (task_dir / "worker.log").write_text(f"task={request['task_id']} pid={os.getpid()}\n", encoding="utf-8")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({
        "status": "running", "stage": "audit", "worker_pid": os.getpid(),
        "started_at": now(), "heartbeat_at": now(), "message": "fake worker running",
    })
    atomic_write(state_path, state)
    (task_dir / "ready").touch()
    deadline = time.monotonic() + 5
    while not barrier.exists() and time.monotonic() < deadline:
        if (task_dir / "cancel.requested").exists():
            state.update({"status": "cancelled", "stage": "cancelled", "worker_pid": None, "heartbeat_at": now()})
            atomic_write(state_path, state)
            return 0
        time.sleep(0.02)
    if request.get("mode") == "crash":
        return 7
    for completed in range(1, 6):
        if (task_dir / "cancel.requested").exists():
            state.update({"status": "cancelled", "stage": "cancelled", "worker_pid": None, "heartbeat_at": now()})
            atomic_write(state_path, state)
            return 0
        state.update({
            "completed_units": completed,
            "total_units": 5,
            "progress_percent": completed * 20,
            "heartbeat_at": now(),
        })
        atomic_write(state_path, state)
        time.sleep(0.04)
    state.update({
        "status": "completed", "stage": "completed", "worker_pid": None,
        "heartbeat_at": now(), "message": "fake worker completed",
        "result_summary": {"passed": 5, "warnings": 0, "failed": 0},
    })
    atomic_write(state_path, state)
    (output_root / "result.txt").write_text(request["task_id"], encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
