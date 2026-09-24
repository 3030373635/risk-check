"""使用 Qt 本地通信实现唯一桌面实例。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


APP_SERVER_NAME = "openai.risk-audit-desktop.v2"


class SingleInstanceCoordinator(QObject):
    """监听稳定应用标识，并将重复启动转为激活信号。"""

    activated = Signal()

    def __init__(self, server_name: str = APP_SERVER_NAME, parent: QObject | None = None) -> None:
        """初始化协调器；server_name 为稳定标识，parent 为 Qt 父对象。"""
        super().__init__(parent)
        self.server_name = server_name
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._accept_connections)

    def acquire(self) -> bool:
        """尝试成为主实例；返回是否监听成功。"""
        if self.server.listen(self.server_name):
            return True
        probe = QLocalSocket()
        probe.connectToServer(self.server_name)
        if probe.waitForConnected(250):
            probe.disconnectFromServer()
            return False
        # 只有无法连接现有服务时才清理陈旧 endpoint。
        QLocalServer.removeServer(self.server_name)
        return self.server.listen(self.server_name)

    def notify_existing(self) -> bool:
        """向已运行实例发送激活命令；返回是否成功写入。"""
        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(1000):
            return False
        socket.write(b"activate\n")
        success = socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return success

    def _accept_connections(self) -> None:
        """接收全部待处理客户端；无参数。"""
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda current=socket: self._read_command(current))
            if socket.bytesAvailable():
                self._read_command(socket)

    def _read_command(self, socket: QLocalSocket) -> None:
        """解析客户端命令；socket 为当前本地连接。"""
        command = bytes(socket.readAll()).decode("utf-8", errors="replace").strip()
        if command == "activate":
            self.activated.emit()
        socket.disconnectFromServer()

    @staticmethod
    def activate_window(window: object) -> None:
        """恢复并激活主窗口；window 为具有 Qt 窗口方法的对象。"""
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def close(self) -> None:
        """关闭监听并清理当前 endpoint；无参数。"""
        if self.server.isListening():
            self.server.close()
            QLocalServer.removeServer(self.server_name)
