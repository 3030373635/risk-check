"""验证 Worker 通过 SQLite 发布进度、终态、心跳和取消。"""

from pathlib import Path
from dataclasses import replace
import tempfile
import threading
import time

import pytest


def create_worker_task(tmp_path: Path, task_id: str = "task-1"):
    """创建可供 Worker 执行的数据库任务；tmp_path 为隔离根，task_id 为任务编号。"""
    from risk_audit_web.task_contracts import TaskEvent
    from risk_audit_web.task_repository import TaskRepository
    from tests.web.test_task_repository import make_request, make_state

    repository = TaskRepository(tmp_path / "data/tasks.sqlite3")
    repository.initialize()
    request = make_request(tmp_path, task_id)
    Path(request.output_root).mkdir(parents=True)
    repository.create_task(
        request,
        make_state(task_id),
        TaskEvent("1.0", task_id, "created", request.created_at, {}),
    )
    return repository, request


@pytest.mark.parametrize(
    ("write_completed", "expected_status"),
    [(True, "completed"), (False, "partial")],
)
def test_worker_maps_result_to_sqlite_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_completed: bool,
    expected_status: str,
) -> None:
    """审核结果必须写入对应终态；参数为隔离根、补丁工具和期望映射。"""
    from risk_audit.progress import AuditProgressEvent
    from risk_audit_web import worker

    repository, request = create_worker_task(tmp_path)
    monkeypatch.setattr(worker, "configure_conversion_runtime", lambda *args: None)

    def audit_func(*args, **kwargs):
        """模拟核心审核；args/kwargs 为审核入口参数。"""
        kwargs["progress_callback"](AuditProgressEvent(
            stage="audit",
            completed_units=1,
            total_units=2,
            current_entity="示例单位",
            current_business="设备管理",
            current_file="矩阵.xlsx",
            message="正在审核",
        ))
        report = kwargs["run_directory"] / "result.json"
        report.parent.mkdir(parents=True)
        report.write_text("{}", encoding="utf-8")
        incomplete_items_report = kwargs["run_directory"] / "_risk_audit/未完成项.json"
        incomplete_items_report.parent.mkdir(parents=True)
        incomplete_items_report.write_text(
            "[]" if write_completed else '[{"type":"file_parsing_failed"}]',
            encoding="utf-8",
        )
        excel_report = Path(request.output_root) / "审核统计表.xlsx"
        html_report = Path(request.output_root) / "审核统计表.html"
        excel_report.write_bytes(b"excel")
        html_report.write_text("<main>html</main>", encoding="utf-8")
        return {
            "write_completed": write_completed,
            "input_files": 2,
            "findings": 3,
            "warnings": 1,
            "limitations": 0,
            "incomplete_items": 0 if write_completed else 1,
            "incomplete_items_report": str(incomplete_items_report),
            "business_results": [{}, {}],
            "run_dir": str(kwargs["run_directory"]),
            "audit_statistics_report": str(excel_report),
            "audit_statistics_html_report": str(html_report),
        }

    code = worker.run_worker(repository.database_path, request.task_id, audit_func=audit_func)

    state = repository.read_state(request.task_id)
    assert code == 0
    assert state.status == expected_status
    assert state.result_summary["incomplete_items"] == (0 if write_completed else 1)
    assert state.result_summary["incomplete_items_report"].endswith("未完成项.json")
    assert state.result_summary["audit_statistics_report"].endswith("审核统计表.xlsx")
    assert state.result_summary["audit_statistics_html_report"].endswith("审核统计表.html")
    assert state.message == (
        "审核已完成"
        if write_completed
        else "审核已结束，存在 1 个未完成项"
    )
    assert state.progress_percent == 100
    assert state.worker_pid is None
    task_dir = Path(request.output_root) / ".task"
    assert (task_dir / "report/result.json").is_file()
    assert not (task_dir / "work").exists()
    for removed_name in ("request.json", "state.json", "events.jsonl", "cancel.requested"):
        assert not (task_dir / removed_name).exists()


def test_worker_failure_remains_visible_as_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """审核异常必须保留数据库任务并写失败态；参数为隔离根和补丁工具。"""
    from risk_audit_web import worker

    repository, request = create_worker_task(tmp_path)
    monkeypatch.setattr(worker, "configure_conversion_runtime", lambda *args: None)

    def fail(*args, **kwargs):
        """模拟审核异常；args/kwargs 为审核入口参数。"""
        raise RuntimeError("模拟审核崩溃")

    code = worker.run_worker(repository.database_path, request.task_id, audit_func=fail)

    state = repository.read_state(request.task_id)
    assert code == 1
    assert state.status == "failed"
    assert state.error_code == "AUDIT_FAILED"
    assert "模拟审核崩溃" in state.message
    assert (Path(request.output_root) / ".task/worker.log").is_file()


def test_worker_uses_system_temp_for_long_output_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超长结果目录不得参与 LibreOffice 运行路径。

    Args:
        tmp_path: pytest 提供的隔离目录。
        monkeypatch: pytest 提供的补丁工具。
    """
    from risk_audit_web import worker
    from risk_audit_web.task_contracts import TaskEvent
    from risk_audit_web.task_repository import TaskRepository
    from tests.web.test_task_repository import make_request, make_state

    task_id = "task-long-path"
    system_temp_root = tmp_path / "system-temp"
    system_temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp_root))

    repository = TaskRepository(tmp_path / "data/tasks.sqlite3")
    repository.initialize()
    request = replace(
        make_request(tmp_path, task_id),
        output_root=str((tmp_path / "outputs" / ("超长材料目录" * 20)).resolve()),
    )
    Path(request.output_root).mkdir(parents=True)
    repository.create_task(
        request,
        make_state(task_id),
        TaskEvent("1.0", task_id, "created", request.created_at, {}),
    )
    observed_paths: dict[str, Path] = {}

    def configure_runtime(_soffice_path: Path, profile_path: Path) -> None:
        """记录 LibreOffice profile 路径。

        Args:
            _soffice_path: 本用例不使用的可执行文件路径。
            profile_path: Worker 选择的 profile 路径。
        """
        observed_paths["profile"] = profile_path

    def audit_func(*_args, **kwargs):
        """记录核心审核使用的临时路径。

        Args:
            _args: 核心审核位置参数。
            kwargs: 核心审核命名参数。
        """
        observed_paths["work"] = kwargs["work_root"]
        return {"write_completed": True, "business_results": []}

    monkeypatch.setattr(worker, "configure_conversion_runtime", configure_runtime)

    code = worker.run_worker(repository.database_path, task_id, audit_func=audit_func)

    expected_runtime_root = system_temp_root / "risk-audit" / task_id
    assert code == 0
    assert observed_paths["work"] == expected_runtime_root / "work"
    assert observed_paths["profile"] == expected_runtime_root / "libreoffice-profile"
    assert Path(request.output_root) not in observed_paths["work"].parents
    assert not expected_runtime_root.exists()


def test_worker_cleans_partial_runtime_when_directory_creation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """临时目录初始化失败时必须发布失败终态并清理已创建内容。

    Args:
        tmp_path: pytest 提供的隔离目录。
        monkeypatch: pytest 提供的补丁工具。
    """
    from risk_audit_web import worker

    repository, request = create_worker_task(tmp_path)
    system_temp_root = tmp_path / "system-temp"
    system_temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp_root))
    runtime_dir = worker.task_runtime_directory(request.task_id)
    profile_dir = runtime_dir / "libreoffice-profile"
    original_mkdir = Path.mkdir

    def fail_profile_creation(path: Path, *args, **kwargs) -> None:
        """profile 目录创建时模拟磁盘错误。

        Args:
            path: 待创建目录。
            args: Path.mkdir 位置参数。
            kwargs: Path.mkdir 命名参数。
        """
        if path == profile_dir:
            raise OSError("无法创建 LibreOffice profile")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_profile_creation)

    code = worker.run_worker(repository.database_path, request.task_id)

    state = repository.read_state(request.task_id)
    assert code == 1
    assert state.status == "failed"
    assert "无法创建 LibreOffice profile" in state.message
    assert not runtime_dir.exists()


def test_worker_cancel_check_reads_database_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """核心取消检查必须读取当前任务数据库标志；参数为隔离根和补丁工具。"""
    from risk_audit.progress import AuditCancelled
    from risk_audit_web import worker

    repository, request = create_worker_task(tmp_path)
    monkeypatch.setattr(worker, "configure_conversion_runtime", lambda *args: None)

    def audit_func(*args, **kwargs):
        """请求取消并让核心抛出取消异常；args/kwargs 为审核入口参数。"""
        repository.request_cancel(request.task_id, "2026-09-30T10:02:00+08:00")
        assert kwargs["cancel_check"]() is True
        raise AuditCancelled("用户已取消")

    code = worker.run_worker(repository.database_path, request.task_id, audit_func=audit_func)

    assert code == 0
    assert repository.read_state(request.task_id).status == "cancelled"


def test_worker_refreshes_heartbeat_without_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """核心无进度事件时也必须刷新心跳；参数为隔离根和补丁工具。"""
    from risk_audit_web import worker

    repository, request = create_worker_task(tmp_path)
    monkeypatch.setattr(worker, "configure_conversion_runtime", lambda *args: None)
    initial_heartbeat = repository.read_state(request.task_id).heartbeat_at
    observed = threading.Event()

    def audit_func(*args, **kwargs):
        """等待心跳变化后返回；args/kwargs 为审核入口参数。"""
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if repository.read_state(request.task_id).heartbeat_at != initial_heartbeat:
                observed.set()
                break
            time.sleep(0.01)
        return {"write_completed": True, "business_results": []}

    code = worker.run_worker(
        repository.database_path,
        request.task_id,
        audit_func=audit_func,
        heartbeat_interval_seconds=0.02,
    )

    assert code == 0
    assert observed.is_set()
