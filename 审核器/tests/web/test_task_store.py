"""验证任务 JSON 原子存储、事件追加和损坏隔离。"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def make_record(tmp_path: Path, task_id: str = "task-1"):
    """创建任务索引记录；tmp_path 为便携目录，task_id 为任务编号。"""
    from risk_audit_web.task_contracts import TaskRecord

    return TaskRecord(
        schema_version="1.0",
        task_id=task_id,
        display_name="资料",
        input_root=str((tmp_path / "input").resolve()),
        output_root=str((tmp_path / task_id).resolve()),
        created_at="2026-09-24T10:30:15+08:00",
        state_path=str((tmp_path / task_id / "_task/state.json").resolve()),
    )


def make_state(task_id: str = "task-1"):
    """创建最小运行状态；task_id 为任务编号。"""
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
        worker_pid=123,
        started_at="2026-09-24T10:30:15+08:00",
        heartbeat_at="2026-09-24T10:30:16+08:00",
        message="正在启动",
        error_code=None,
        result_summary=None,
    )


def test_atomic_write_replaces_complete_json(tmp_path: Path) -> None:
    """状态更新必须产生完整 JSON，不能让轮询读到半个文件。"""
    from risk_audit_web.task_store import atomic_write_json

    target = tmp_path / "state.json"
    atomic_write_json(target, {"status": "running", "message": "中文"})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "status": "running",
        "message": "中文",
    }
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_atomic_write_failure_preserves_previous_state(monkeypatch, tmp_path: Path) -> None:
    """原子替换失败时旧状态仍须可读，并清理临时文件。"""
    from risk_audit_web import task_store

    target = tmp_path / "state.json"
    target.write_text('{"status":"completed"}\n', encoding="utf-8")

    def fail_replace(source, destination):
        """模拟操作系统拒绝替换；参数为临时文件和目标文件。"""
        raise OSError("replace failed")

    monkeypatch.setattr(task_store.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        task_store.atomic_write_json(target, {"status": "running"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "completed"}
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_task_store_skips_one_invalid_index_record(tmp_path: Path) -> None:
    """单条损坏记录不能阻止其他任务恢复。"""
    from risk_audit_web.task_store import TaskStore

    valid = make_record(tmp_path).to_dict()
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "tasks.json").write_text(
        json.dumps({"schema_version": "1.0", "tasks": [valid, {"task_id": "broken"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = TaskStore(data_root).list_tasks()

    assert [record.task_id for record in result.records] == ["task-1"]
    assert len(result.warnings) == 1
    assert "broken" in result.warnings[0]


def test_task_store_survives_invalid_index_json(tmp_path: Path) -> None:
    """整个索引 JSON 损坏时应用仍须启动为空列表。"""
    from risk_audit_web.task_store import TaskStore

    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "tasks.json").write_text("{invalid", encoding="utf-8")

    result = TaskStore(data_root).list_tasks()

    assert result.records == []
    assert len(result.warnings) == 1


def test_task_store_adds_reads_and_marks_interrupted(tmp_path: Path) -> None:
    """索引、状态和中断更新必须使用同一任务协议。"""
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record = make_record(tmp_path)
    state = make_state()
    state_path = Path(record.state_path)

    store.add_task(record)
    store.write_state(state_path, state)
    interrupted = store.mark_interrupted(record, state, "任务进程已中断")

    assert store.list_tasks().records == [record]
    assert store.read_state(record).status == "interrupted"
    assert interrupted.message == "任务进程已中断"


def test_read_state_retries_transient_windows_permission_error(monkeypatch, tmp_path: Path) -> None:
    """Windows 原子替换短暂拒绝读取时，状态读取必须重试后成功。"""
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record = make_record(tmp_path)
    state = make_state()
    state_path = Path(record.state_path)
    store.write_state(state_path, state)
    original_read_text = Path.read_text
    attempts = 0

    def transient_permission_error(path: Path, *args, **kwargs) -> str:
        """前两次模拟 Windows 共享冲突；path 为目标，args/kwargs 透传读取参数。"""
        nonlocal attempts
        if path == state_path:
            attempts += 1
            if attempts <= 2:
                raise PermissionError(13, "Permission denied", str(path))
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", transient_permission_error)

    assert store.read_state(record) == state
    assert attempts == 3


def test_read_state_reraises_persistent_permission_error(monkeypatch, tmp_path: Path) -> None:
    """Windows 持续拒绝读取时，有界重试后必须保留原异常。"""
    from risk_audit_web.task_store import STATE_READ_ATTEMPTS, TaskStore

    store = TaskStore(tmp_path / "data")
    record = make_record(tmp_path)
    state_path = Path(record.state_path)
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{}", encoding="utf-8")
    attempts = 0

    def persistent_permission_error(path: Path, *args, **kwargs) -> str:
        """模拟持续的 Windows 访问拒绝；path 为目标，args/kwargs 为读取参数。"""
        nonlocal attempts
        attempts += 1
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(Path, "read_text", persistent_permission_error)

    with pytest.raises(PermissionError, match="Permission denied") as raised:
        store.read_state(record)
    assert attempts == STATE_READ_ATTEMPTS
    assert raised.value.errno == 13


def test_append_event_writes_one_json_object_per_line(tmp_path: Path) -> None:
    """诊断事件必须追加为独立 JSON 行，不能改写历史。"""
    from risk_audit_web.task_contracts import TaskEvent
    from risk_audit_web.task_store import append_event

    path = tmp_path / "events.jsonl"
    first = TaskEvent("1.0", "task-1", "started", "2026-09-24T10:30:15+08:00", {})
    second = TaskEvent("1.0", "task-1", "completed", "2026-09-24T10:31:15+08:00", {"findings": 2})

    append_event(path, first)
    append_event(path, second)

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["event"] for line in lines] == ["started", "completed"]


def create_portable_resources(tmp_path: Path):
    """创建通过启动诊断的最小便携资源；tmp_path 为测试根。"""
    from risk_audit_web.task_paths import PortablePaths

    paths = PortablePaths.from_app_root(tmp_path / "app")
    paths.soffice.parent.mkdir(parents=True)
    paths.soffice.write_bytes(b"exe")
    paths.entity_file.parent.mkdir(parents=True)
    paths.entity_file.write_bytes(b"xlsx")
    paths.baseline_root.mkdir(parents=True)
    release = paths.rulepacks / "releases/1.9.19"
    release.mkdir(parents=True)
    rule_path = release / "rules/R01.json"
    rule_path.parent.mkdir()
    rule_path.write_text('{"id":"R01"}\n', encoding="utf-8")
    rule_hash = hashlib.sha256(rule_path.read_bytes()).hexdigest()
    content_hash = hashlib.sha256(
        json.dumps(
            {"rules/R01.json": rule_hash},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    (release / "manifest.json").write_text(json.dumps({
        "version": "1.9.19",
        "status": "released",
        "content_hash": content_hash,
    }), encoding="utf-8")
    (paths.rulepacks / "active.json").write_text(json.dumps({
        "version": "1.9.19",
        "content_hash": content_hash,
    }), encoding="utf-8")
    paths.config_file.write_text('{"disabled_rules":[]}', encoding="utf-8")
    from tools.portable_release import build_release_manifest

    (paths.runtime_root / "manifest.json").write_text(
        json.dumps(build_release_manifest(paths.app_root), ensure_ascii=False),
        encoding="utf-8",
    )
    return paths


def test_create_task_writes_request_state_and_index(tmp_path: Path) -> None:
    """任务创建成功后请求、初态和索引必须全部可读取。"""
    from risk_audit_web.task_contracts import TaskRequest
    from risk_audit_web.task_store import TaskStore

    paths = create_portable_resources(tmp_path)
    input_root = tmp_path / "第一批资料0924"
    input_root.mkdir()
    store = TaskStore(paths.data_root)
    created_at = datetime(2026, 9, 24, 10, 30, 15, tzinfo=timezone(timedelta(hours=8)))

    record = store.create_task(
        input_root=input_root,
        display_name="第一批审核",
        paths=paths,
        created_at=created_at,
        token_factory=lambda: "a1b2",
    )

    output_root = Path(record.output_root)
    request = TaskRequest.from_dict(json.loads(
        (output_root / "_task/request.json").read_text(encoding="utf-8")
    ))
    state = store.read_state(record)
    assert output_root.name == "第一批资料0924-20260924-103015"
    assert record.task_id == "20260924-103015-a1b2"
    assert record.display_name == "第一批审核"
    assert request.input_root == str(input_root.resolve())
    assert "model_root" not in request.to_dict()
    assert state.status == "running" and state.worker_pid is None
    assert store.list_tasks().records == [record]


def test_create_task_with_explicit_default_output_creates_missing_outputs_parent(tmp_path: Path) -> None:
    """便携包初次运行时 outputs 父目录即使不存在也必须可创建任务。"""
    from risk_audit_web.task_store import TaskStore

    paths = create_portable_resources(tmp_path)
    input_root = tmp_path / "材料"
    input_root.mkdir()
    output_root = paths.outputs_root / "材料-20260924-103015"
    store = TaskStore(paths.data_root)

    record = store.create_task(
        input_root=input_root,
        display_name="材料",
        paths=paths,
        output_root=output_root,
        created_at=datetime(2026, 9, 24, 10, 30, 15, tzinfo=timezone.utc),
        token_factory=lambda: "a1b2",
    )

    assert Path(record.output_root).is_dir()


def test_create_task_rolls_back_managed_directory_when_index_write_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """索引写入失败时只能清理本次新建且尚无业务产物的目录。"""
    from risk_audit_web.task_store import TaskStore

    paths = create_portable_resources(tmp_path)
    input_root = tmp_path / "资料"
    input_root.mkdir()
    store = TaskStore(paths.data_root)
    monkeypatch.setattr(store, "add_task", lambda record: (_ for _ in ()).throw(OSError("index failed")))

    with pytest.raises(OSError, match="index failed"):
        store.create_task(
            input_root=input_root,
            display_name="资料",
            paths=paths,
            created_at=datetime(2026, 9, 24, 10, 30, 15, tzinfo=timezone.utc),
            token_factory=lambda: "a1b2",
        )

    assert not (paths.outputs_root / "资料-20260924-103015").exists()


def test_recover_tasks_keeps_live_fresh_worker_and_terminal_state(tmp_path: Path) -> None:
    """存活且心跳新鲜的 Worker 以及终态任务必须原样恢复。"""
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    running_record = make_record(tmp_path, "running")
    completed_record = make_record(tmp_path, "completed")
    store.add_task(running_record)
    store.add_task(completed_record)
    now = datetime(2026, 9, 24, 10, 31, 0, tzinfo=timezone.utc)
    running = make_state("running").with_updates(
        worker_pid=321,
        heartbeat_at=(now - timedelta(seconds=5)).isoformat(),
    )
    completed = make_state("completed").with_updates(
        status="completed", stage="completed", worker_pid=None, progress_percent=100,
    )
    store.write_state(Path(running_record.state_path), running)
    store.write_state(Path(completed_record.state_path), completed)

    result = store.recover_tasks(is_process_alive=lambda pid: pid == 321, now=now)

    assert [item.state.status for item in result.tasks] == ["running", "completed"]
    assert result.warnings == []


@pytest.mark.parametrize(("heartbeat_age", "alive"), [(5, False), (31, True)])
def test_recover_tasks_marks_dead_or_stale_worker_interrupted(
    tmp_path: Path,
    heartbeat_age: int,
    alive: bool,
) -> None:
    """PID 消失或心跳超时都不能继续显示为运行中。"""
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    record = make_record(tmp_path)
    store.add_task(record)
    now = datetime(2026, 9, 24, 10, 31, 0, tzinfo=timezone.utc)
    state = make_state().with_updates(
        heartbeat_at=(now - timedelta(seconds=heartbeat_age)).isoformat(),
    )
    store.write_state(Path(record.state_path), state)

    result = store.recover_tasks(is_process_alive=lambda pid: alive, now=now)

    assert result.tasks[0].state.status == "interrupted"
    assert "可重新创建任务" in result.tasks[0].state.message


def test_recover_tasks_isolates_missing_state_file(tmp_path: Path) -> None:
    """一个任务状态缺失时其他任务仍应恢复。"""
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(tmp_path / "data")
    missing = make_record(tmp_path, "missing")
    valid = make_record(tmp_path, "valid")
    store.add_task(missing)
    store.add_task(valid)
    store.write_state(Path(valid.state_path), make_state("valid"))

    result = store.recover_tasks(is_process_alive=lambda pid: True)

    assert [item.record.task_id for item in result.tasks] == ["valid"]
    assert len(result.warnings) == 1 and "missing" in result.warnings[0]
