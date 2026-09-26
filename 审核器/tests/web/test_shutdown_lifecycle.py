"""验证退出等待和服务日志生命周期。"""

from pathlib import Path


class StubManager:
    """记录全部任务停止与等待调用。"""

    def __init__(self, active: list[str], wait_results: list[bool] | None = None) -> None:
        """初始化活动任务；active 为任务编号，wait_results 为分段等待结果。"""
        self.active = active
        self.cancelled = []
        self.waited = False
        self.wait_results = list(wait_results or [True])
        self.wait_calls = 0

    def running_task_ids(self) -> list[str]:
        """返回活动任务；无参数。"""
        return list(self.active)

    def request_cancel_all(self) -> list[str]:
        """记录全部取消；无参数。"""
        self.cancelled = list(self.active)
        return list(self.active)

    def wait_for_all(self, timeout_ms: int) -> bool:
        """记录等待；timeout_ms 为等待上限。"""
        self.waited = timeout_ms > 0
        self.wait_calls += 1
        return self.wait_results.pop(0) if self.wait_results else True


class StubServerController:
    """记录服务退出请求。"""

    def __init__(self) -> None:
        """初始化退出状态；无参数。"""
        self.exit_requested = False

    def request_exit(self) -> None:
        """记录退出请求；无参数。"""
        self.exit_requested = True


class StubDirectoryPicker:
    """占位目录选择器。"""


def test_cancel_active_tasks_shutdown_waits_before_server_exit(tmp_path: Path) -> None:
    """全部安全停止后退出必须等待任务终态；tmp_path 为任务存储。"""
    from risk_audit_web.services import ApplicationServices
    from risk_audit_web.task_paths import PortablePaths
    from risk_audit_web.task_store import TaskStore

    manager = StubManager(active=["a", "b"])
    controller = StubServerController()
    service = ApplicationServices(
        paths=PortablePaths.from_app_root(tmp_path),
        store=TaskStore(tmp_path / "data"),
        manager=manager,
        directory_picker=StubDirectoryPicker(),
        server_controller=controller,
        open_path=lambda path: None,
        background_runner=lambda callback: callback(),
    )

    result = service.request_shutdown("cancel_active_tasks")

    assert manager.cancelled == ["a", "b"]
    assert manager.waited
    assert controller.exit_requested
    assert result["status"] == "stopping_tasks"


def test_shutdown_keeps_waiting_after_a_single_timeout(tmp_path: Path) -> None:
    """安全退出必须持续等到全部任务终态；tmp_path 为任务存储。"""
    from risk_audit_web.services import ApplicationServices
    from risk_audit_web.task_paths import PortablePaths
    from risk_audit_web.task_store import TaskStore

    manager = StubManager(active=["a"], wait_results=[False, True])
    controller = StubServerController()
    service = ApplicationServices(
        paths=PortablePaths.from_app_root(tmp_path),
        store=TaskStore(tmp_path / "data"),
        manager=manager,
        directory_picker=StubDirectoryPicker(),
        server_controller=controller,
        open_path=lambda path: None,
        background_runner=lambda callback: callback(),
    )

    service.request_shutdown("cancel_active_tasks")

    assert manager.wait_calls == 2
    assert controller.exit_requested


def test_server_log_rotates_and_does_not_add_token(tmp_path: Path) -> None:
    """服务日志必须大小轮转且格式不附加令牌；tmp_path 为 data 根。"""
    from risk_audit_web.logging_setup import configure_server_logging

    logger = configure_server_logging(tmp_path, max_bytes=128, backup_count=2)
    for index in range(30):
        logger.info("line-%s %s", index, "x" * 24)
    for handler in logger.handlers:
        handler.flush()

    assert (tmp_path / "logs/server.log.1").is_file()
    assert "secret-token" not in (tmp_path / "logs/server.log").read_text(encoding="utf-8")


def test_primary_cleanup_stops_workers_before_closing_supervisor() -> None:
    """主实例退出必须先清理 Worker，再关闭进程监管器。"""
    from risk_audit_web.app import cleanup_primary_runtime

    events = []
    manager = type("Manager", (), {
        "stop_all_workers": lambda self: events.append("stop") or [],
    })()
    supervisor = type("Supervisor", (), {
        "close": lambda self: events.append("close"),
    })()

    cleanup_primary_runtime(manager, supervisor, lambda: events.append("restore"))

    assert events == ["stop", "close", "restore"]
