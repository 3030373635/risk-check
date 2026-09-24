"""验证系统托盘和单实例激活行为。"""

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
    """第二个实例必须发送 activate，由第一个恢复窗口。"""
    from risk_audit_desktop.single_instance import SingleInstanceCoordinator

    # macOS 的 Unix domain socket 路径长度有上限，测试标识保持短且唯一。
    server_name = f"rat-{uuid4().hex[:8]}"
    window = FakeWindow()
    first = SingleInstanceCoordinator(server_name)
    second = SingleInstanceCoordinator(server_name)
    assert first.acquire()
    first.activated.connect(lambda: first.activate_window(window))

    with qtbot.waitSignal(first.activated, timeout=2000):
        assert second.notify_existing()

    assert window.calls == ["show", "raise", "activate"]
    first.close()


def test_single_instance_uses_stable_application_server_name() -> None:
    """默认 server name 必须是稳定应用标识，不包含用户名或路径。"""
    from risk_audit_desktop.single_instance import APP_SERVER_NAME

    assert APP_SERVER_NAME == "openai.risk-audit-desktop.v2"
    assert "/" not in APP_SERVER_NAME and "\\" not in APP_SERVER_NAME
