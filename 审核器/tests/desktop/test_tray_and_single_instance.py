"""验证系统托盘和单实例激活行为。"""

import sys
from uuid import uuid4


class FakeWindow:
    """记录托盘或单实例对窗口的激活调用。"""

    def __init__(self) -> None:
        """初始化调用计数；无参数。"""
        self.calls = []

    def showNormal(self) -> None:  # noqa: N802
        """记录恢复窗口；无参数。"""
        self.calls.append("show")

    def raise_(self) -> None:
        """记录窗口置顶；无参数。"""
        self.calls.append("raise")

    def activateWindow(self) -> None:  # noqa: N802
        """记录激活窗口；无参数。"""
        self.calls.append("activate")


def test_tray_updates_tooltip_restores_window_and_notifies_completion(qapp) -> None:
    """托盘必须更新数量、恢复窗口并在全部完成时通知。"""
    from PySide6.QtWidgets import QSystemTrayIcon
    from risk_audit_desktop.tray import TrayController

    class Channel:
        """提供托盘激活信号的测试替身。"""

        def connect(self, callback) -> None:
            """保存回调；callback 为托盘激活处理函数。"""
            self.callback = callback

    class FakeTrayIcon:
        """避免在无托盘的离屏平台创建真实系统资源。"""

        def __init__(self) -> None:
            """初始化提示和消息容器；无参数。"""
            self.activated = Channel()
            self.tooltip = ""
            self.messages = []

        def setContextMenu(self, menu) -> None:  # noqa: N802
            """保存菜单引用；menu 为 Qt 菜单。"""
            self.menu = menu

        def setToolTip(self, value: str) -> None:  # noqa: N802
            """设置提示；value 为文案。"""
            self.tooltip = value

        def toolTip(self) -> str:  # noqa: N802
            """返回提示；无参数。"""
            return self.tooltip

        def showMessage(self, title: str, body: str, *args) -> None:  # noqa: N802
            """记录通知；参数为标题、内容和可选 Qt 参数。"""
            self.messages.append((title, body))

        def show(self) -> None:
            """模拟显示托盘；无参数。"""

    window = FakeWindow()
    tray_icon = FakeTrayIcon()
    controller = TrayController(window, tray_icon=tray_icon)

    controller.update_running_count(2)
    assert "2" in controller.icon.toolTip()
    controller.handle_activation(QSystemTrayIcon.ActivationReason.DoubleClick)
    assert window.calls == ["show", "raise", "activate"]
    controller.update_running_count(0)
    assert tray_icon.messages == [("风控矩阵审核器", "所有审核任务已结束")]


def test_single_instance_second_client_activates_first(qtbot) -> None:
    """真实第二进程必须抢锁失败并激活第一个实例。"""
    from PySide6.QtCore import QProcess
    from risk_audit_desktop.single_instance import SingleInstanceCoordinator

    # macOS 的 Unix domain socket 路径长度有上限，跨进程测试标识保持短且唯一。
    server_name = f"rat-{uuid4().hex[:8]}"
    window = FakeWindow()
    first = SingleInstanceCoordinator(server_name)
    assert first.acquire()
    first.activated.connect(lambda: first.activate_window(window))

    child_code = (
        "import sys; "
        "from risk_audit_desktop.single_instance import SingleInstanceCoordinator; "
        "coordinator = SingleInstanceCoordinator(sys.argv[1]); "
        "acquired = coordinator.acquire(); "
        "raise SystemExit(2 if acquired else (0 if coordinator.notify_existing() else 3))"
    )
    process = QProcess()
    process.setProgram(sys.executable)
    process.setArguments(["-c", child_code, server_name])

    try:
        with qtbot.waitSignal(first.activated, timeout=5000):
            # QProcess 异步启动，保持主进程 Qt 事件循环可处理命名管道。
            process.start()

        assert process.waitForFinished(5000)
        assert process.exitStatus() == QProcess.ExitStatus.NormalExit
        assert process.exitCode() == 0
    finally:
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(1000)
        first.close()

    assert window.calls == ["show", "raise", "activate"]


def test_single_instance_uses_stable_application_server_name() -> None:
    """默认 server name 必须是稳定应用标识，不包含用户名或路径。"""
    from risk_audit_desktop.single_instance import APP_SERVER_NAME

    assert APP_SERVER_NAME == "openai.risk-audit-desktop.v2"
    assert "/" not in APP_SERVER_NAME and "\\" not in APP_SERVER_NAME
