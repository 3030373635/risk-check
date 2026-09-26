"""验证 Web 主程序、次实例和 Worker 分流。"""

import builtins
import os
from pathlib import Path

import pytest


def test_worker_dispatch_does_not_import_fastapi_or_uvicorn(monkeypatch, tmp_path: Path) -> None:
    """Worker 模式必须在导入 Web 服务前分流；monkeypatch/tmp_path 隔离依赖。"""
    from risk_audit_web.app import main

    request = tmp_path / "request.json"
    imported = []
    original_import = builtins.__import__

    def record_import(name, *args, **kwargs):
        """记录导入并调用真实导入器；参数与内置导入器一致。"""
        imported.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", record_import)
    monkeypatch.setattr("risk_audit_web.worker.run_worker", lambda path: 7)

    assert main(["--worker", str(request)]) == 7
    assert not any(name.startswith(("fastapi", "uvicorn")) for name in imported)


def test_worker_arguments_always_use_packaged_python_module(tmp_path: Path) -> None:
    """脚本便携版 Worker 必须始终由当前 Python 模块启动。"""
    from risk_audit_web.app import worker_arguments

    executable = tmp_path / "python.exe"
    request = tmp_path / "request.json"

    assert worker_arguments(executable, request) == [
        str(executable), "-m", "risk_audit_web.app", "--worker", str(request),
    ]


def test_secondary_instance_only_reopens_verified_session(tmp_path: Path) -> None:
    """次实例只能重开通过令牌健康检查的会话；tmp_path 为会话根。"""
    from risk_audit_web.app import reopen_existing_instance
    from risk_audit_web.single_instance import ServerSession, write_server_session

    session_path = tmp_path / "data/server-session.json"
    session = ServerSession("1.0", 321, 49152, "secret", "2026-09-25T10:00:00+08:00")
    write_server_session(session_path, session)
    opened = []

    assert reopen_existing_instance(
        session_path,
        process_alive=lambda pid: pid == 321,
        health_check=lambda current: current.token == "secret",
        browser_opener=lambda current: opened.append(current) or True,
    )
    assert opened == [session]


def test_secondary_instance_rejects_stale_or_wrong_session(tmp_path: Path) -> None:
    """健康检查不匹配时不得打开浏览器；tmp_path 为会话根。"""
    from risk_audit_web.app import reopen_existing_instance
    from risk_audit_web.single_instance import ServerSession, write_server_session

    path = tmp_path / "server-session.json"
    write_server_session(path, ServerSession("1.0", 321, 49152, "secret", "now"))
    opened = []

    assert not reopen_existing_instance(
        path,
        process_alive=lambda _pid: True,
        health_check=lambda _session: False,
        browser_opener=lambda session: opened.append(session) or True,
        attempts=1,
        retry_seconds=0,
    )
    assert opened == []


def test_static_root_uses_source_or_portable_runtime(tmp_path: Path) -> None:
    """源码与便携模式必须解析到不同静态根；tmp_path 为便携根。"""
    from risk_audit_web.app import resolve_static_root
    from risk_audit_web.task_paths import PortablePaths

    paths = PortablePaths.from_app_root(tmp_path)

    assert resolve_static_root(paths, portable=True) == paths.runtime_root / "web"
    assert resolve_static_root(paths, portable=False).name == "static"


def test_application_paths_use_valid_absolute_environment_root(tmp_path: Path) -> None:
    """发布模式必须只接受结构完整的绝对发布根。"""
    from risk_audit_web.app import resolve_application_paths

    app_root = tmp_path / "含 空格的发布目录"
    for name in ("app", "runtime", "data", "outputs"):
        (app_root / name).mkdir(parents=True)

    paths, portable = resolve_application_paths(
        {"RISK_AUDIT_APP_ROOT": str(app_root)},
        tmp_path / "source",
        platform_name="darwin",
    )

    assert portable
    assert paths.app_root == app_root.resolve()
    assert paths.soffice == app_root.resolve() / "runtime/libreoffice/LibreOffice.app/Contents/MacOS/soffice"


@pytest.mark.parametrize("raw_root", ["relative/path", ""])
def test_application_paths_reject_invalid_portable_root(
    tmp_path: Path,
    raw_root: str,
) -> None:
    """相对或结构不完整的发布根不得被静默接受。"""
    from risk_audit_web.app import resolve_application_paths

    environment = {"RISK_AUDIT_APP_ROOT": raw_root} if raw_root else {}
    if not raw_root:
        environment = {"RISK_AUDIT_APP_ROOT": str(tmp_path / "missing")}

    with pytest.raises(ValueError, match="发布根"):
        resolve_application_paths(environment, tmp_path / "source")


def test_application_paths_keep_source_mode_without_environment(tmp_path: Path) -> None:
    """未设置发布变量时必须明确落入源码模式。"""
    from risk_audit_web.app import resolve_application_paths

    paths, portable = resolve_application_paths({}, tmp_path / "source", platform_name=os.sys.platform)

    assert not portable
    assert paths.app_root == (tmp_path / "source").resolve()
