"""风控矩阵审核器系统托盘控制。"""

from __future__ import annotations

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from risk_audit_desktop.single_instance import SingleInstanceCoordinator


class TrayController:
    """管理托盘提示、恢复窗口和全部完成通知。"""

    def __init__(
        self,
        window: object,
        icon: QIcon | None = None,
        *,
        tray_icon: object | None = None,
    ) -> None:
        """初始化托盘；参数为主窗口、应用图标和可注入托盘对象。"""
        self.window = window
        self.icon = tray_icon or QSystemTrayIcon(icon or QIcon())
        self._running_count = 0
        menu = QMenu()
        show_action = QAction("打开风控矩阵审核器", menu)
        show_action.triggered.connect(self.restore_window)
        menu.addAction(show_action)
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self.handle_activation)
        self.update_running_count(0)

    def show(self) -> None:
        """显示系统托盘图标；无参数。"""
        self.icon.show()

    def restore_window(self) -> None:
        """恢复并激活主窗口；无参数。"""
        SingleInstanceCoordinator.activate_window(self.window)

    def handle_activation(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """处理托盘激活；reason 为 Qt 激活类型。"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore_window()

    def update_running_count(self, count: int) -> None:
        """更新托盘运行数；count 为当前活动任务数。"""
        previous = self._running_count
        self._running_count = count
        self.icon.setToolTip(f"风控矩阵审核器 · {count} 个任务运行中")
        if previous > 0 and count == 0:
            self.icon.showMessage("风控矩阵审核器", "所有审核任务已结束")
