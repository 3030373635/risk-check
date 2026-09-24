"""验证主窗口导航、任务协调和关闭策略。"""

from pathlib import Path

from PySide6.QtGui import QCloseEvent


class FakeChannel:
    """为窗口测试提供最小信号通道。"""

    def __init__(self) -> None:
        """初始化回调集合；无参数。"""
        self.callbacks = []

    def connect(self, callback) -> None:
        """连接回调；callback 为接收函数。"""
        self.callbacks.append(callback)

    def emit(self, *args) -> None:
        """发出测试信号；args 为回调参数。"""
        for callback in self.callbacks:
            callback(*args)


class FakeManager:
    """记录主窗口对任务管理器的调用。"""

    def __init__(self, running_ids=()) -> None:
        """初始化管理器；running_ids 为运行任务编号。"""
        self.task_updated = FakeChannel()
        self.task_error = FakeChannel()
        self.running_count_changed = FakeChannel()
        self._running_ids = list(running_ids)
        self.cancel_all_calls = 0
        self.wait_result = True

    def running_task_ids(self):
        """返回运行编号；无参数。"""
        return list(self._running_ids)

    def request_cancel_all(self):
        """记录停止全部请求；无参数。"""
        self.cancel_all_calls += 1
        return list(self._running_ids)

    def request_cancel(self, task_id):
        """模拟停止单个任务；task_id 为任务编号。"""
        return task_id in self._running_ids

    def wait_for_all(self, timeout_ms):
        """返回预设等待结果；timeout_ms 为超时毫秒。"""
        return self.wait_result


class FakeStore:
    """提供空任务列表的窗口测试存储。"""

    class Result:
        """表示空任务查询结果。"""

        records = []
        warnings = []

    def list_tasks(self):
        """返回空索引；无参数。"""
        return self.Result()


def make_window(tmp_path: Path, manager: FakeManager, decision="tray"):
    """创建已注入测试依赖的主窗口；参数为路径、管理器和关闭决策。"""
    from risk_audit_desktop.diagnostics import DiagnosticItem, DiagnosticReport
    from risk_audit_desktop.main_window import MainWindow
    from risk_audit_desktop.task_paths import PortablePaths

    paths = PortablePaths(
        app_root=tmp_path,
        runtime_root=tmp_path / "runtime",
        data_root=tmp_path / "data",
        outputs_root=tmp_path / "outputs",
    )
    diagnostics = DiagnosticReport([DiagnosticItem("ok", "runtime", "已就绪")])
    return MainWindow(
        paths,
        FakeStore(),
        manager,
        diagnostics,
        close_decision_provider=lambda: decision,
    )


def test_main_window_navigation_and_running_count(qtbot, tmp_path: Path) -> None:
    """导航项必须齐全，运行数文案必须实时更新。"""
    manager = FakeManager()
    window = make_window(tmp_path, manager)
    qtbot.addWidget(window)

    labels = [window.navigation.item(index).text() for index in range(window.navigation.count())]
    assert labels == ["审核任务", "新建任务", "使用帮助"]
    assert window.running_count_label.text() == "0 个任务运行中"
    manager.running_count_changed.emit(2)
    assert window.running_count_label.text() == "2 个任务并行运行"


def test_close_without_running_tasks_accepts(qtbot, tmp_path: Path) -> None:
    """无运行任务时关闭窗口必须直接退出。"""
    window = make_window(tmp_path, FakeManager())
    qtbot.addWidget(window)
    event = QCloseEvent()

    window.closeEvent(event)

    assert event.isAccepted()


def test_close_running_default_hides_to_tray(qtbot, tmp_path: Path) -> None:
    """默认关闭决策必须隐藏窗口且保持任务运行。"""
    manager = FakeManager(["a"])
    window = make_window(tmp_path, manager, "tray")
    qtbot.addWidget(window)
    window.show()
    event = QCloseEvent()

    window.closeEvent(event)

    assert not event.isAccepted()
    assert window.isHidden()
    assert manager.cancel_all_calls == 0


def test_close_running_stop_all_waits_and_accepts(qtbot, tmp_path: Path) -> None:
    """停止全部后退出必须发出取消并等待终态。"""
    manager = FakeManager(["a", "b"])
    window = make_window(tmp_path, manager, "stop")
    qtbot.addWidget(window)
    event = QCloseEvent()

    window.closeEvent(event)

    assert event.isAccepted()
    assert manager.cancel_all_calls == 1


def test_close_running_cancel_keeps_window(qtbot, tmp_path: Path) -> None:
    """取消关闭必须忽略事件并保持窗口。"""
    window = make_window(tmp_path, FakeManager(["a"]), "cancel")
    qtbot.addWidget(window)
    window.show()
    event = QCloseEvent()

    window.closeEvent(event)

    assert not event.isAccepted()
    assert window.isVisible()


def test_startup_recovery_uses_real_process_probe() -> None:
    """界面启动必须先执行任务恢复，不能将上次崩溃任务一直显示为运行。"""
    from risk_audit_desktop.main_window import recover_startup_tasks

    captured = {}

    class RecoveryStore:
        """记录恢复调用的最小存储。"""

        def recover_tasks(self, **kwargs):
            """记录恢复参数；kwargs 为存活检查依赖。"""
            captured.update(kwargs)
            return object()

    result = recover_startup_tasks(RecoveryStore())

    assert result is not None
    assert callable(captured["is_process_alive"])
