"""验证 REST 应用服务边界。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest


def create_request(input_root: Path):
    """创建严格任务请求；input_root 为用户选择的输入目录。"""
    from risk_audit_web.api_models import TaskCreateRequest

    return TaskCreateRequest(input_root=str(input_root))


def test_concurrent_tasks_reserve_distinct_output_directories(web_services, input_root: Path) -> None:
    """同秒并发任务必须获得不同输出目录；services/input_root 为真实边界。"""
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

    calls: list[Path] = []
    report = DiagnosticReport([DiagnosticItem("ok", "runtime/manifest.json", "ok")])
    monkeypatch.setattr(
        services,
        "run_startup_diagnostics",
        lambda paths: calls.append(paths.app_root) or report,
    )
    service = services.ApplicationServices(
        paths=portable_paths,
        store=SimpleNamespace(),
        manager=SimpleNamespace(snapshot=lambda: SimpleNamespace(running_count=0)),
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

    report = DiagnosticReport([
        DiagnosticItem("ok", "runtime/a", "资源校验通过：runtime/a"),
        DiagnosticItem("ok", "runtime/b", "资源校验通过：runtime/b"),
        DiagnosticItem("ok", "rulepacks", "活动规则包可用"),
    ])
    monkeypatch.setattr(services, "run_startup_diagnostics", lambda paths: report)
    service = services.ApplicationServices(
        paths=portable_paths,
        store=SimpleNamespace(),
        manager=SimpleNamespace(snapshot=lambda: SimpleNamespace(running_count=0)),
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

    report = DiagnosticReport([
        DiagnosticItem("ok", "runtime/a", "资源校验通过：runtime/a"),
        DiagnosticItem("missing", "runtime/b", "资源文件缺失：runtime/b"),
        DiagnosticItem("ok", "rulepacks", "活动规则包可用"),
    ])
    monkeypatch.setattr(services, "run_startup_diagnostics", lambda paths: report)
    service = services.ApplicationServices(
        paths=portable_paths,
        store=SimpleNamespace(),
        manager=SimpleNamespace(snapshot=lambda: SimpleNamespace(running_count=0)),
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
