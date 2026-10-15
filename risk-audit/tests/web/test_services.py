"""验证 REST 应用服务边界。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import subprocess
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


def test_delete_task_rejects_active_task_without_removing_data(
    web_services,
    input_root: Path,
) -> None:
    """运行中任务不得删除任何数据。

    Args:
        web_services: 使用真实任务存储的应用服务。
        input_root: 测试材料目录。
    """
    from risk_audit_web.services import ApiProblem

    task = web_services.create_task(create_request(input_root))
    output_root = Path(task.record.output_root)

    with pytest.raises(ApiProblem) as raised:
        web_services.delete_task(task.record.task_id)

    assert raised.value.status_code == 409
    assert raised.value.error_code == "TASK_NOT_TERMINAL"
    assert output_root.is_dir()
    assert web_services.repository.get_task(task.record.task_id) is not None


def test_delete_task_rejects_output_path_outside_managed_root(
    web_services,
    input_root: Path,
    tmp_path: Path,
) -> None:
    """任务输出路径越界时必须拒绝删除。

    Args:
        web_services: 使用真实任务存储的应用服务。
        input_root: 测试材料目录。
        tmp_path: 测试隔离目录。
    """
    from risk_audit_web.services import ApiProblem

    task = web_services.create_task(create_request(input_root))
    task_id = task.record.task_id
    external_root = tmp_path / "external-output"
    external_root.mkdir()
    (external_root / "keep.txt").write_text("keep", encoding="utf-8")
    web_services.repository.update_task_state(
        task_id,
        {"status": "completed", "stage": "completed"},
    )
    with sqlite3.connect(web_services.repository.database_path) as connection:
        connection.execute(
            "UPDATE tasks SET output_root = ? WHERE task_id = ?",
            (str(external_root.resolve()), task_id),
        )

    with pytest.raises(ApiProblem) as raised:
        web_services.delete_task(task_id)

    assert raised.value.status_code == 409
    assert raised.value.error_code == "TASK_OUTPUT_PATH_INVALID"
    assert (external_root / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert web_services.repository.get_task(task_id) is not None


def test_delete_task_rejects_symlinked_outputs_root(
    web_services,
    input_root: Path,
    tmp_path: Path,
) -> None:
    """outputs 根目录被替换为链接时必须拒绝删除外部数据。

    Args:
        web_services: 使用真实任务存储的应用服务。
        input_root: 测试材料目录。
        tmp_path: 测试隔离目录。
    """
    from risk_audit_web.services import ApiProblem

    task = web_services.create_task(create_request(input_root))
    task_id = task.record.task_id
    outputs_root = web_services.paths.outputs_root
    external_root = tmp_path / "external-outputs"
    outputs_root.rename(external_root)
    try:
        outputs_root.symlink_to(external_root, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"当前平台无法创建目录符号链接：{error}")
    external_task_root = external_root / Path(task.record.output_root).name
    (external_task_root / "keep.txt").write_text("keep", encoding="utf-8")
    web_services.repository.update_task_state(
        task_id,
        {"status": "completed", "stage": "completed"},
    )

    with pytest.raises(ApiProblem) as raised:
        web_services.delete_task(task_id)

    assert raised.value.error_code == "TASK_OUTPUT_PATH_INVALID"
    assert (external_task_root / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert web_services.repository.get_task(task_id) is not None


def test_delete_task_anchors_cleanup_when_outputs_root_is_swapped(
    web_services,
    input_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """删除期间根目录被替换时不得触碰外部同名目录。

    Args:
        web_services: 使用真实任务存储的应用服务。
        input_root: 测试材料目录。
        tmp_path: 测试隔离目录。
        monkeypatch: pytest 补丁工具。
    """
    import os

    if os.name != "posix":
        pytest.skip("目录句柄锚定竞态测试仅适用于 POSIX")

    from risk_audit_web import services

    task = web_services.create_task(create_request(input_root))
    task_id = task.record.task_id
    output_root = Path(task.record.output_root)
    outputs_root = web_services.paths.outputs_root
    original_outputs_root = tmp_path / "original-outputs"
    external_root = tmp_path / "external-outputs"
    external_task_root = external_root / output_root.name
    external_task_root.mkdir(parents=True)
    (external_task_root / "keep.txt").write_text("keep", encoding="utf-8")
    (output_root / "remove.txt").write_text("remove", encoding="utf-8")
    web_services.repository.update_task_state(
        task_id,
        {"status": "completed", "stage": "completed"},
    )
    web_services.manager._processes.pop(task_id)
    real_rmtree = services.shutil.rmtree
    swapped = False

    def swap_root_then_remove(path, *args, **kwargs) -> None:
        """替换 outputs 根后继续删除；path/args/kwargs 为 rmtree 参数。"""
        nonlocal swapped
        if not swapped:
            outputs_root.rename(original_outputs_root)
            outputs_root.symlink_to(external_root, target_is_directory=True)
            swapped = True
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(services.shutil, "rmtree", swap_root_then_remove)

    web_services.delete_task(task_id)

    assert swapped is True
    assert not (original_outputs_root / output_root.name).exists()
    assert (external_task_root / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert web_services.repository.get_task(task_id) is None


def test_delete_task_handles_windows_target_rename_during_recursive_cleanup(
    web_services,
    input_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Windows 递归枚举期间目标被移动时必须安全结束。

    Args:
        web_services: 使用真实任务存储的应用服务。
        input_root: 测试材料目录。
        tmp_path: 测试隔离目录。
        monkeypatch: pytest 补丁工具。
    """
    import os

    if os.name != "nt":
        pytest.skip("Windows 目录句柄竞态测试仅适用于 NTFS")

    from risk_audit_web import services
    from risk_audit_web.services import ApiProblem

    task = web_services.create_task(create_request(input_root))
    task_id = task.record.task_id
    output_root = Path(task.record.output_root)
    moved_root = tmp_path / "moved-task-output"
    (output_root / "remove.txt").write_text("remove", encoding="utf-8")
    web_services.repository.update_task_state(
        task_id,
        {"status": "completed", "stage": "completed"},
    )
    web_services.manager._processes.pop(task_id)
    real_scandir = services.os.scandir
    replacement_blocked = False

    def try_replace_locked_target(path):
        """在递归枚举边界尝试替换目标；path 为当前枚举目录。"""
        nonlocal replacement_blocked
        if Path(path) == output_root:
            try:
                output_root.rename(moved_root)
            except OSError:
                replacement_blocked = True
        return real_scandir(path)

    monkeypatch.setattr(services.os, "scandir", try_replace_locked_target)

    try:
        web_services.delete_task(task_id)
    except ApiProblem as error:
        # 若 Windows 运行环境允许移动已打开的目录，必须停止删除并保留记录。
        assert replacement_blocked is False
        assert error.status_code == 500
        assert error.error_code == "TASK_DATA_DELETE_FAILED"
        assert output_root.exists() is False
        assert (moved_root / "remove.txt").read_text(encoding="utf-8") == "remove"
        assert web_services.repository.get_task(task_id) is not None
    else:
        # 若 Windows 运行环境拒绝移动，原目录可以继续安全删除。
        assert replacement_blocked is True
        assert output_root.exists() is False
        assert moved_root.exists() is False
        assert web_services.repository.get_task(task_id) is None


def test_delete_terminal_task_removes_all_related_data(
    web_services,
    input_root: Path,
) -> None:
    """删除终态任务必须清理输出、临时数据、数据库和进程引用。

    Args:
        web_services: 使用真实任务存储的应用服务。
        input_root: 测试材料目录。
    """
    from risk_audit_web.worker import task_runtime_directory

    task = web_services.create_task(create_request(input_root))
    task_id = task.record.task_id
    output_root = Path(task.record.output_root)
    (output_root / "report.xlsx").write_bytes(b"report")
    runtime_root = task_runtime_directory(task_id)
    runtime_root.mkdir(parents=True)
    (runtime_root / "work.tmp").write_bytes(b"temporary")
    web_services.repository.update_task_state(
        task_id,
        {"status": "completed", "stage": "completed"},
    )
    web_services.manager._processes[task_id] = SimpleNamespace(
        poll=lambda: 0,
        wait=lambda timeout: 0,
    )

    web_services.delete_task(task_id)

    assert output_root.exists() is False
    assert runtime_root.exists() is False
    assert web_services.repository.get_task(task_id) is None
    assert web_services.repository.list_events(task_id) == []
    assert task_id not in web_services.manager._processes


def test_delete_task_waits_for_terminal_process_to_exit(
    web_services,
    input_root: Path,
) -> None:
    """终态 Worker 尚未退出时不得提前删除文件。

    Args:
        web_services: 使用真实任务存储的应用服务。
        input_root: 测试材料目录。
    """
    from risk_audit_web.services import ApiProblem

    task = web_services.create_task(create_request(input_root))
    task_id = task.record.task_id
    output_root = Path(task.record.output_root)
    web_services.repository.update_task_state(
        task_id,
        {"status": "completed", "stage": "completed"},
    )
    web_services.manager._processes[task_id] = SimpleNamespace(
        poll=lambda: None,
        wait=lambda timeout: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("worker", timeout)
        ),
    )

    with pytest.raises(ApiProblem) as raised:
        web_services.delete_task(task_id)

    assert raised.value.status_code == 409
    assert raised.value.error_code == "TASK_PROCESS_ACTIVE"
    assert output_root.is_dir()
    assert web_services.repository.get_task(task_id) is not None


def test_delete_task_keeps_record_when_output_cleanup_fails(
    web_services,
    input_root: Path,
    monkeypatch,
) -> None:
    """输出目录被占用时必须保留任务记录以便重试。

    Args:
        web_services: 使用真实任务存储的应用服务。
        input_root: 测试材料目录。
        monkeypatch: pytest 补丁工具。
    """
    from risk_audit_web.services import ApiProblem

    task = web_services.create_task(create_request(input_root))
    task_id = task.record.task_id
    output_root = Path(task.record.output_root)
    web_services.repository.update_task_state(
        task_id,
        {"status": "failed", "stage": "failed"},
    )
    web_services.manager._processes.pop(task_id)

    real_remove_task_path = web_services._remove_task_path

    def fail_output_cleanup(
        path: Path,
        managed_root: Path,
        error_code: str,
        message: str,
    ) -> None:
        """仅模拟输出目录被占用。

        Args:
            path: 当前待删除路径。
            managed_root: 当前路径所属的托管根目录。
            error_code: 路径安全校验错误码。
            message: 路径安全校验提示。
        """
        if path == output_root:
            raise PermissionError(13, "目录被占用", str(path))
        real_remove_task_path(path, managed_root, error_code, message)

    monkeypatch.setattr(web_services, "_remove_task_path", fail_output_cleanup)

    with pytest.raises(ApiProblem) as raised:
        web_services.delete_task(task_id)

    assert raised.value.status_code == 500
    assert raised.value.error_code == "TASK_DATA_DELETE_FAILED"
    assert output_root.is_dir()
    assert web_services.repository.get_task(task_id) is not None


def test_terminate_task_rejects_terminal_task_with_worker_pid(
    web_services,
    input_root: Path,
    monkeypatch,
) -> None:
    """终态任务即使残留 PID 也不得再次强制终止。

    Args:
        web_services: 使用真实任务存储的应用服务。
        input_root: 测试材料目录。
        monkeypatch: pytest 补丁工具。
    """
    from risk_audit_web.services import ApiProblem

    task = web_services.create_task(create_request(input_root))
    task_id = task.record.task_id
    web_services.repository.update_task_state(
        task_id,
        {"status": "completed", "stage": "completed", "worker_pid": 700},
    )
    calls: list[tuple[str, int]] = []

    def force_stop(target_task_id: str, *, expected_pid: int) -> bool:
        """记录意外强停调用；target_task_id/expected_pid 为任务和进程号。"""
        calls.append((target_task_id, expected_pid))
        return True

    monkeypatch.setattr(web_services.manager, "force_stop", force_stop)

    with pytest.raises(ApiProblem) as raised:
        web_services.terminate_task(task_id)

    assert raised.value.status_code == 409
    assert raised.value.error_code == "TASK_NOT_ACTIVE"
    assert calls == []


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


def test_system_status_reports_auditor_and_fixed_region(web_services) -> None:
    """系统状态必须显示审核器版本和固定地区；web_services 为真实服务。"""
    status = web_services.system_status()

    assert status["app_version"] == "2.2.2"
    assert status["region_code"] == "chongqing"
    assert status["region_name"] == "重庆"
    assert status["rulepack_version"] == "1.9.33"
    assert "rule_version" not in status


def test_system_status_remains_available_when_product_region_is_invalid(web_services) -> None:
    """地区配置损坏时状态接口仍必须返回诊断信息。"""
    from risk_audit_web.region_profiles import RegionCatalog

    web_services._region_catalog = RegionCatalog({})

    status = web_services.system_status()

    assert status["region_code"] is None
    assert status["region_name"] is None
    assert status["rulepack_version"] is None
    assert status["diagnostics"]


def test_created_task_keeps_pinned_region_after_index_changes(
    web_services,
    input_root: Path,
) -> None:
    """创建后的任务不得重新读取地区索引；参数为服务和输入目录。"""
    task = web_services.create_task(create_request(input_root))
    web_services.paths.region_index.write_text('{"schema_version":"broken"}', encoding="utf-8")

    request = web_services.repository.read_request(task.record.task_id)

    assert request.region_code == "chongqing"
    assert request.region_name == "重庆"
    assert request.rulepack_version == "1.9.33"
    assert request.rulepack == str(web_services._region_catalog.require("chongqing").rulepack)


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
