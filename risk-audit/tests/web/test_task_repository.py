"""验证 SQLite 任务仓储的建库、写入、查询和版本保护。"""

from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest


def make_request(tmp_path: Path, task_id: str = "task-1"):
    """创建测试任务请求；tmp_path 为测试根目录，task_id 为任务编号。"""
    from risk_audit_web.task_contracts import TaskRequest

    return TaskRequest(
        schema_version="1.0",
        task_id=task_id,
        display_name="资料",
        input_root=str((tmp_path / "input").resolve()),
        output_root=str((tmp_path / "outputs" / task_id).resolve()),
        rulepack=str((tmp_path / "rulepack").resolve()),
        entity_file=str((tmp_path / "entities.xlsx").resolve()),
        baseline_root=str((tmp_path / "baselines").resolve()),
        config_file=None,
        soffice_path=str((tmp_path / "soffice").resolve()),
        created_at="2026-09-30T10:00:00+08:00",
    )


def make_state(task_id: str = "task-1"):
    """创建测试任务状态；task_id 为任务编号。"""
    from risk_audit_web.task_contracts import TaskState

    return TaskState(
        schema_version="1.0",
        task_id=task_id,
        status="running",
        stage="startup",
        completed_units=0,
        total_units=None,
        progress_percent=None,
        current_entity=None,
        current_business=None,
        current_file=None,
        worker_pid=None,
        started_at=None,
        heartbeat_at="2026-09-30T10:00:00+08:00",
        message="任务已创建",
        error_code=None,
        result_summary=None,
    )


def make_event(task_id: str = "task-1"):
    """创建测试任务事件；task_id 为任务编号。"""
    from risk_audit_web.task_contracts import TaskEvent

    return TaskEvent(
        schema_version="1.0",
        task_id=task_id,
        event="created",
        occurred_at="2026-09-30T10:00:00+08:00",
        details={},
    )


def test_initialize_creates_versioned_wal_database(tmp_path: Path) -> None:
    """初始化必须创建版本化数据库并启用 WAL；tmp_path 为隔离目录。"""
    from risk_audit_web.task_repository import TaskRepository

    database_path = tmp_path / "data/tasks.sqlite3"
    repository = TaskRepository(database_path)

    repository.initialize()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"tasks", "task_events"}.issubset(tables)
    with repository._connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000


def test_create_task_is_immediately_visible_with_request_and_state(tmp_path: Path) -> None:
    """创建事务提交后必须立即可查询；tmp_path 为隔离目录。"""
    from risk_audit_web.task_repository import TaskRepository

    repository = TaskRepository(tmp_path / "data/tasks.sqlite3")
    repository.initialize()
    request = make_request(tmp_path)
    state = make_state()

    repository.create_task(request, state, make_event())

    stored = repository.get_task("task-1")
    assert stored is not None
    assert stored.request == request
    assert stored.state == state
    assert stored.record.task_id == "task-1"
    assert stored.cancel_requested is False
    assert [task.task_id for task in repository.list_tasks().tasks] == ["task-1"]


def test_initialize_rejects_unknown_database_version(tmp_path: Path) -> None:
    """未知数据库版本必须显式失败；tmp_path 为隔离目录。"""
    from risk_audit_web.task_repository import TaskDatabaseError, TaskRepository

    database_path = tmp_path / "tasks.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(TaskDatabaseError, match="数据库版本"):
        TaskRepository(database_path).initialize()


def test_initialize_rejects_incomplete_versioned_schema(tmp_path: Path) -> None:
    """声明为当前版本但缺少字段的数据库必须失败；tmp_path 为隔离目录。"""
    from risk_audit_web.task_repository import TaskDatabaseError, TaskRepository

    database_path = tmp_path / "tasks.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY)")
        connection.execute("PRAGMA user_version = 1")

    with pytest.raises(TaskDatabaseError, match="缺少字段"):
        TaskRepository(database_path).initialize()


def test_list_tasks_isolates_invalid_result_summary(tmp_path: Path) -> None:
    """单条非法摘要不能阻止其他任务展示；tmp_path 为隔离目录。"""
    from risk_audit_web.task_repository import TaskRepository

    repository = TaskRepository(tmp_path / "data/tasks.sqlite3")
    repository.initialize()
    repository.create_task(make_request(tmp_path, "valid"), make_state("valid"), make_event("valid"))
    repository.create_task(make_request(tmp_path, "broken"), make_state("broken"), make_event("broken"))
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE tasks SET result_summary_json = '[]' WHERE task_id = 'broken'"
        )

    result = repository.list_tasks()

    assert [task.task_id for task in result.tasks] == ["valid"]
    assert len(result.warnings) == 1
    assert "broken" in result.warnings[0]


def test_cancel_and_field_updates_do_not_overwrite_each_other(tmp_path: Path) -> None:
    """进度、心跳和取消并发写入不得互相覆盖；tmp_path 为隔离目录。"""
    from risk_audit_web.task_contracts import TaskEvent
    from risk_audit_web.task_repository import TaskRepository

    repository = TaskRepository(tmp_path / "data/tasks.sqlite3")
    repository.initialize()
    repository.create_task(make_request(tmp_path), make_state(), make_event())

    def update_progress() -> None:
        """写入最终进度；无参数。"""
        for completed in range(1, 21):
            repository.update_task_state("task-1", {
                "completed_units": completed,
                "total_units": 20,
                "progress_percent": completed * 5,
            })

    def update_heartbeat() -> None:
        """连续写入心跳；无参数。"""
        for index in range(20):
            repository.update_task_state(
                "task-1",
                {"heartbeat_at": f"2026-09-30T10:00:{index:02d}+08:00"},
            )

    def append_events() -> None:
        """连续追加事件；无参数。"""
        for index in range(20):
            repository.append_event(TaskEvent(
                "1.0",
                "task-1",
                "progress",
                f"2026-09-30T10:01:{index:02d}+08:00",
                {"index": index},
            ))

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(update_progress),
            executor.submit(update_heartbeat),
            executor.submit(append_events),
            executor.submit(
                repository.request_cancel,
                "task-1",
                "2026-09-30T10:02:00+08:00",
            ),
        ]
        for future in futures:
            future.result()

    stored = repository.get_task("task-1")
    assert stored is not None
    assert stored.state.completed_units == 20
    assert stored.state.progress_percent == 100
    assert stored.cancel_requested is True
    events = repository.list_events("task-1")
    assert [event.event for event in events].count("cancel_requested") == 1
    assert [event.event for event in events].count("progress") == 20


def test_request_cancel_is_idempotent_and_ignores_terminal_task(tmp_path: Path) -> None:
    """取消请求只能记录一次且不能修改终态；tmp_path 为隔离目录。"""
    from risk_audit_web.task_repository import TaskRepository

    repository = TaskRepository(tmp_path / "data/tasks.sqlite3")
    repository.initialize()
    repository.create_task(make_request(tmp_path), make_state(), make_event())

    assert repository.request_cancel("task-1", "2026-09-30T10:01:00+08:00") is True
    assert repository.request_cancel("task-1", "2026-09-30T10:01:01+08:00") is False
    repository.update_task_state("task-1", {"status": "completed", "stage": "completed"})
    assert repository.request_cancel("task-1", "2026-09-30T10:01:02+08:00") is False
    assert [event.event for event in repository.list_events("task-1")].count(
        "cancel_requested"
    ) == 1
