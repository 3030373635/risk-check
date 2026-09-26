"""风控矩阵审核器本机 Web 主入口和 Worker 分流。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
import os
from pathlib import Path
import secrets
import sys
from threading import Thread
import time
from urllib.request import Request, urlopen


APP_ROOT_ENVIRONMENT = "RISK_AUDIT_APP_ROOT"
PORTABLE_ROOT_DIRECTORIES = ("app", "runtime", "data", "outputs")


def worker_arguments(python_executable: Path, request_path: Path) -> list[str]:
    """生成 Worker 参数；参数为当前 Python 和任务请求文件。"""
    return [
        str(python_executable),
        "-m",
        "risk_audit_web.app",
        "--worker",
        str(request_path),
    ]


def resolve_static_root(paths, *, portable: bool) -> Path:
    """解析静态资源根；paths 为应用路径，portable 表示是否为发布模式。"""
    if portable:
        return paths.runtime_root / "web"
    return Path(__file__).resolve().parent / "static"


def resolve_application_paths(
    environment: Mapping[str, str],
    source_root: Path,
    *,
    platform_name: str | None = None,
):
    """解析应用路径；参数为环境、源码根和可选平台，返回路径及发布模式。"""
    from risk_audit_web.task_paths import PortablePaths

    raw_root = environment.get(APP_ROOT_ENVIRONMENT)
    if raw_root is None:
        return PortablePaths.from_app_root(
            source_root,
            platform_name=platform_name,
        ), False
    candidate = Path(raw_root)
    if not candidate.is_absolute():
        raise ValueError(f"发布根必须是绝对路径：{raw_root!r}")
    app_root = candidate.resolve()
    missing = [name for name in PORTABLE_ROOT_DIRECTORIES if not (app_root / name).is_dir()]
    if missing:
        raise ValueError(f"发布根结构不完整，缺少目录：{', '.join(missing)}；{app_root}")
    return PortablePaths.from_app_root(
        app_root,
        platform_name=platform_name,
    ), True


def check_session_health(session) -> bool:
    """执行带令牌的本机健康检查；session 为待验证服务会话。"""
    url = f"http://127.0.0.1:{session.port}/healthz"
    request = Request(url, headers={"X-Local-Token": session.token})
    try:
        with urlopen(request, timeout=1) as response:
            return response.status == 200 and response.read() == b'{"status":"ok"}'
    except OSError:
        return False


def reopen_existing_instance(
    session_path: Path,
    *,
    process_alive: Callable[[int], bool],
    health_check: Callable[[object], bool],
    browser_opener: Callable[[object], bool],
    attempts: int = 20,
    retry_seconds: float = 0.1,
) -> bool:
    """重开已验证页面；参数为会话路径、验证依赖、尝试次数和间隔。"""
    from risk_audit_web.single_instance import wait_for_live_session

    session = wait_for_live_session(
        session_path,
        is_process_alive=process_alive,
        health_check=health_check,
        attempts=attempts,
        retry_seconds=retry_seconds,
    )
    return session is not None and browser_opener(session)


def _open_browser_when_ready(session) -> None:
    """健康检查通过后打开浏览器；session 为本次服务会话。"""
    from risk_audit_web.browser import open_default_browser

    # 当前会话尚未通过文件读取，直接有界探测其令牌健康接口。
    for _attempt in range(50):
        if check_session_health(session):
            open_default_browser(session)
            return
        time.sleep(0.1)


def cleanup_primary_runtime(manager, supervisor, restore_signal: Callable[[], None]) -> None:
    """清理主实例运行资源；参数为任务管理器、进程监管器和信号恢复回调。"""
    try:
        if manager is not None:
            manager.stop_all_workers()
    finally:
        try:
            supervisor.close()
        finally:
            restore_signal()


def run_primary_instance(
    paths,
    executable: Path,
    instance_lock,
    *,
    portable: bool,
) -> int:
    """运行主实例；参数为便携路径、Worker Python、实例锁和发布模式。"""
    from risk_audit_web.api.system import router as system_router
    from risk_audit_web.api.tasks import router as tasks_router
    from risk_audit_web.directory_picker import DirectoryPicker
    from risk_audit_web.logging_setup import configure_server_logging
    from risk_audit_web.platform_runtime import is_process_alive, open_path
    from risk_audit_web.process_supervisor import create_process_supervisor
    from risk_audit_web.security import SecuritySettings
    from risk_audit_web.server import (
        ServerController,
        create_app,
        create_loopback_socket,
        install_terminal_shutdown_handler,
        run_uvicorn,
    )
    from risk_audit_web.services import ApplicationServices
    from risk_audit_web.single_instance import (
        SESSION_SCHEMA_VERSION,
        ServerSession,
        read_server_session,
        write_server_session,
    )
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.task_store import TaskStore

    logger = None
    listener = None
    session_path = paths.data_root / "server-session.json"
    session = None
    manager = None
    supervisor = create_process_supervisor(paths.platform_name)
    restore_signal: Callable[[], None] = lambda: None
    try:
        supervisor.prepare()
        logger = configure_server_logging(paths.data_root)
        store = TaskStore(paths.data_root)
        store.recover_tasks(is_process_alive=is_process_alive)
        listener = create_loopback_socket()
        port = int(listener.getsockname()[1])
        session = ServerSession(
            SESSION_SCHEMA_VERSION,
            os.getpid(),
            port,
            secrets.token_urlsafe(32),
            datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        write_server_session(session_path, session)

        controller = ServerController()
        restore_signal = install_terminal_shutdown_handler(
            controller,
            platform_name=paths.platform_name,
        )
        manager = TaskManager(
            store,
            executable,
            process_supervisor=supervisor,
            worker_arguments_factory=worker_arguments,
        )

        def run_in_background(callback: Callable[[], None]) -> None:
            """在 daemon 线程运行回调；callback 为后台操作。"""
            Thread(target=callback, daemon=True).start()

        services = ApplicationServices(
            paths=paths,
            store=store,
            manager=manager,
            directory_picker=DirectoryPicker(),
            server_controller=controller,
            open_path=open_path,
            background_runner=run_in_background,
        )
        app = create_app(
            SecuritySettings(session.token, session.port),
            controller,
            resolve_static_root(paths, portable=portable),
            routers=(system_router, tasks_router),
        )
        app.state.services = services
        Thread(target=_open_browser_when_ready, args=(session,), daemon=True).start()
        logger.info("本机 Web 服务启动，端口=%s", port)
        run_uvicorn(app, listener, controller)
        return 0
    except KeyboardInterrupt:
        return 0
    except BaseException:
        if logger is not None:
            logger.exception("本机 Web 服务启动或运行失败")
        return 1
    finally:
        cleanup_primary_runtime(manager, supervisor, restore_signal)
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        if session is not None:
            current = read_server_session(session_path)
            # 只删除当前进程持有的令牌会话，禁止清理另一个新实例的状态。
            if current == session:
                session_path.unlink(missing_ok=True)
        instance_lock.close()


def run_web_application(argv: Sequence[str]) -> int:
    """启动主实例或重开现有页面；argv 为非 Worker 参数。"""
    from risk_audit_web.browser import open_default_browser
    from risk_audit_web.platform_runtime import is_process_alive
    from risk_audit_web.single_instance import SingleInstanceLock
    from risk_audit_web.task_paths import PortablePaths

    if argv:
        return 2
    executable = Path(sys.executable).resolve()
    project_root = Path(__file__).resolve().parents[2]
    paths, portable = resolve_application_paths(
        os.environ,
        project_root,
        platform_name=sys.platform,
    )
    instance_lock = SingleInstanceLock()
    if not instance_lock.acquire():
        reopened = reopen_existing_instance(
            paths.data_root / "server-session.json",
            process_alive=is_process_alive,
            health_check=check_session_health,
            browser_opener=open_default_browser,
        )
        return 0 if reopened else 1
    return run_primary_instance(
        paths,
        executable,
        instance_lock,
        portable=portable,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """启动 Web 主程序或 Worker；argv 为可选命令行参数，返回退出码。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--worker":
        from risk_audit_web.worker import run_worker

        return run_worker(Path(args[1]))
    return run_web_application(args)


if __name__ == "__main__":
    raise SystemExit(main())
