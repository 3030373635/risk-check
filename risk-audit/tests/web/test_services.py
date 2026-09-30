"""验证 REST 应用服务边界。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def create_request(input_root: Path):
    """创建严格任务请求；input_root 为用户选择的输入目录。"""
    from risk_audit_web.api_models import TaskCreateRequest

    return TaskCreateRequest(input_root=str(input_root))


def test_concurrent_tasks_reserve_distinct_output_directories(
    web_services,
    input_root: Path,
    monkeypatch,
) -> None:
    """同秒并发任务必须获得不同输出目录；参数为真实服务、输入目录和补丁工具。"""
    from risk_audit_web import services

    fixed_created_at = datetime(2026, 9, 27, 10, 30, 15, tzinfo=timezone.utc)
    # 固定任务时间，确保测试真正覆盖同秒目录冲突，不受 CI 跨秒调度影响。
    monkeypatch.setattr(
        services,
        "datetime",
        SimpleNamespace(now=Mock(return_value=fixed_created_at)),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(web_services.create_task, create_request(input_root))
            for _ in range(2)
        ]
    tasks = [future.result() for future in futures]

    roots = [Path(task.record.output_root) for task in tasks]
    assert len(set(roots)) == 2
    assert all(path.is_dir() for path in roots)
    assert any(path.name.endswith("-2") for path in roots)


def test_shutdown_requires_explicit_active_task_policy(web_services, input_root: Path) -> None:
    """存在活动任务时 immediate 必须拒绝；services/input_root 为真实边界。"""
    from risk_audit_web.services import ApiProblem

    task = web_services.create_task(create_request(input_root))
    try:
        web_services.request_shutdown("immediate")
    except ApiProblem as error:
        assert error.status_code == 409
        assert error.error_code == "ACTIVE_TASKS_EXIST"
        assert task.record.task_id in error.details["task_ids"]
    else:
        raise AssertionError("存在活动任务时不应直接退出")


def test_shutdown_rejects_new_tasks_while_waiting(web_services, input_root: Path) -> None:
    """退出流程开始后不得再接收新任务；services/input_root 为真实边界。"""
    from risk_audit_web.services import ApiProblem

    web_services.create_task(create_request(input_root))
    callbacks = []
    web_services.background_runner = callbacks.append
    web_services.request_shutdown("cancel_active_tasks")

    try:
        web_services.create_task(create_request(input_root))
    except ApiProblem as error:
        assert error.status_code == 409
        assert error.error_code == "SERVICE_SHUTTING_DOWN"
    else:
        raise AssertionError("退出期间不应创建新任务")

    assert len(callbacks) == 1


def test_system_status_reuses_startup_diagnostics(
    portable_paths,
    monkeypatch,
) -> None:
    """状态轮询必须复用启动诊断，不得重复哈希全部运行文件。"""
    from risk_audit_web import services
    from risk_audit_web.diagnostics import DiagnosticItem, DiagnosticReport
    from risk_audit_web.task_repository import TaskRepository

    calls: list[Path] = []
    report = DiagnosticReport([DiagnosticItem("ok", "runtime/manifest.json", "ok")])
    monkeypatch.setattr(
        services,
        "run_startup_diagnostics",
        lambda paths: calls.append(paths.app_root) or report,
    )
    repository = TaskRepository(portable_paths.task_database)
    service = services.ApplicationServices(
        paths=portable_paths,
        repository=repository,
        manager=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(running_count=0),
            process_alive=lambda pid: False,
        ),
        directory_picker=SimpleNamespace(),
        server_controller=SimpleNamespace(),
        open_path=lambda path: None,
        background_runner=lambda callback: None,
    )

    first = service.system_status()
    second = service.system_status()

    assert calls == [portable_paths.app_root]
    assert first["diagnostics"] == second["diagnostics"]


def test_system_status_summarizes_successful_diagnostics(
    portable_paths,
    monkeypatch,
) -> None:
    """全部通过时只返回一条汇总；paths/monkeypatch 为测试依赖。"""
    from risk_audit_web import services
    from risk_audit_web.diagnostics import DiagnosticItem, DiagnosticReport
    from risk_audit_web.task_repository import TaskRepository

    report = DiagnosticReport([
        DiagnosticItem("ok", "runtime/a", "资源校验通过：runtime/a"),
        DiagnosticItem("ok", "runtime/b", "资源校验通过：runtime/b"),
        DiagnosticItem("ok", "rulepacks", "活动规则包可用"),
    ])
    monkeypatch.setattr(services, "run_startup_diagnostics", lambda paths: report)
    repository = TaskRepository(portable_paths.task_database)
    service = services.ApplicationServices(
        paths=portable_paths,
        repository=repository,
        manager=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(running_count=0),
            process_alive=lambda pid: False,
        ),
        directory_picker=SimpleNamespace(),
        server_controller=SimpleNamespace(),
        open_path=lambda path: None,
        background_runner=lambda callback: None,
    )

    status = service.system_status()

    assert status["diagnostics"] == [{
        "code": "ok",
        "path": "",
        "message": "运行环境检查通过，共校验 3 项资源",
        "ok": True,
    }]


def test_system_status_only_exposes_failed_diagnostics(
    portable_paths,
    monkeypatch,
) -> None:
    """存在失败时只返回失败明细；paths/monkeypatch 为测试依赖。"""
    from risk_audit_web import services
    from risk_audit_web.diagnostics import DiagnosticItem, DiagnosticReport
    from risk_audit_web.task_repository import TaskRepository

    report = DiagnosticReport([
        DiagnosticItem("ok", "runtime/a", "资源校验通过：runtime/a"),
        DiagnosticItem("missing", "runtime/b", "资源文件缺失：runtime/b"),
        DiagnosticItem("ok", "rulepacks", "活动规则包可用"),
    ])
    monkeypatch.setattr(services, "run_startup_diagnostics", lambda paths: report)
    repository = TaskRepository(portable_paths.task_database)
    service = services.ApplicationServices(
        paths=portable_paths,
        repository=repository,
        manager=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(running_count=0),
            process_alive=lambda pid: False,
        ),
        directory_picker=SimpleNamespace(),
        server_controller=SimpleNamespace(),
        open_path=lambda path: None,
        background_runner=lambda callback: None,
    )

    status = service.system_status()

    assert status["can_create_task"] is False
    assert status["diagnostics"] == [{
        "code": "missing",
        "path": "runtime/b",
        "message": "资源文件缺失：runtime/b",
        "ok": False,
    }]


def test_create_task_does_not_repeat_release_manifest_verification(
    web_services,
    input_root: Path,
    monkeypatch,
) -> None:
    """创建任务必须复用启动诊断；services/input_root/monkeypatch 为测试依赖。"""
    from risk_audit_web import diagnostics

    calls: list[Path] = []
    original_verify = diagnostics.verify_release_manifest

    def track_verification(app_root: Path):
        """记录完整发布清单校验；app_root 为发布根目录。"""
        calls.append(app_root)
        return original_verify(app_root)

    monkeypatch.setattr(diagnostics, "verify_release_manifest", track_verification)

    web_services.create_task(create_request(input_root))

    assert calls == []


def test_create_task_rejects_cached_failed_diagnostics(
    web_services,
    input_root: Path,
) -> None:
    """创建任务仍必须拦截启动诊断失败；services/input_root 为测试依赖。"""
    from risk_audit_web.diagnostics import DiagnosticItem, DiagnosticReport
    from risk_audit_web.services import ApiProblem

    web_services._startup_report = DiagnosticReport([
        DiagnosticItem("missing", "runtime/b", "资源文件缺失：runtime/b"),
    ])

    with pytest.raises(ApiProblem) as raised:
        web_services.create_task(create_request(input_root))

    assert raised.value.status_code == 422
    assert raised.value.error_code == "ENVIRONMENT_NOT_READY"
    assert "runtime/b" in raised.value.message


def test_worker_start_failure_keeps_failed_task_visible(
    web_services,
    input_root: Path,
    monkeypatch,
) -> None:
    """入库后 Worker 启动失败仍须展示任务；参数为服务、输入和补丁工具。"""
    monkeypatch.setattr(
        web_services.manager,
        "start_task",
        lambda record: (_ for _ in ()).throw(OSError("无法启动进程")),
    )

    task = web_services.create_task(create_request(input_root))
    listed = web_services.list_tasks()["tasks"]

    assert task.state.status == "failed"
    assert task.state.error_code == "TASK_START_FAILED"
    assert [item["task_id"] for item in listed] == [task.record.task_id]


def test_output_directory_failure_keeps_failed_task_visible(
    web_services,
    input_root: Path,
    monkeypatch,
) -> None:
    """入库后输出目录创建失败仍须展示任务；参数为服务、输入和补丁工具。"""
    from risk_audit_web import services

    original_mkdir = Path.mkdir
    fixed_created_at = datetime(2026, 9, 30, 10, 30, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(
        services,
        "datetime",
        SimpleNamespace(now=Mock(return_value=fixed_created_at)),
    )
    failure_count = 0

    def fail_output_directory(path: Path, *args, **kwargs) -> None:
        """拒绝创建任务输出目录；path 为目标，args/kwargs 为原始参数。"""
        nonlocal failure_count
        if path.parent == web_services.paths.outputs_root and failure_count == 0:
            failure_count += 1
            raise OSError("输出目录不可写")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_output_directory)

    task = web_services.create_task(create_request(input_root))
    retried = web_services.create_task(create_request(input_root))

    assert task.state.status == "failed"
    assert web_services.get_task(task.record.task_id).state.status == "failed"
    assert retried.state.status == "running"
    assert retried.record.output_root.endswith("-2")


def test_database_failure_keeps_diagnostics_available_and_disables_creation(
    portable_paths,
    input_root: Path,
) -> None:
    """数据库版本异常时诊断仍可访问并禁止创建；参数为便携路径和输入目录。"""
    from risk_audit_web.directory_picker import DirectoryPicker
    from risk_audit_web.server import ServerController
    from risk_audit_web.services import ApiProblem, ApplicationServices
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.task_repository import TaskRepository

    portable_paths.task_database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(portable_paths.task_database) as connection:
        connection.execute("PRAGMA user_version = 99")
    repository = TaskRepository(portable_paths.task_database)
    manager = TaskManager(repository, portable_paths.app_root / "审核器")
    service = ApplicationServices(
        paths=portable_paths,
        repository=repository,
        manager=manager,
        directory_picker=DirectoryPicker(dialog=lambda: ""),
        server_controller=ServerController(),
        open_path=lambda path: None,
        background_runner=lambda callback: None,
    )

    status = service.system_status()

    assert status["service_status"] == "degraded"
    assert status["can_create_task"] is False
    assert status["diagnostics"][0]["code"] == "database_unavailable"
    with pytest.raises(ApiProblem) as raised:
        service.create_task(create_request(input_root))
    assert raised.value.error_code == "DATABASE_UNAVAILABLE"
