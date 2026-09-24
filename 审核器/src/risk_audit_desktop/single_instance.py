"""使用 Qt 本地通信实现唯一桌面实例。"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import gettempdir

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


APP_SERVER_NAME = "openai.risk-audit-desktop.v2"


def _lock_path_for_server(server_name: str) -> Path:
    """生成稳定的进程锁路径；server_name 为本地服务标识。"""
    server_digest = sha256(server_name.encode("utf-8")).hexdigest()[:16]
    return Path(gettempdir()) / f"risk-audit-{server_digest}.lock"


class SingleInstanceCoordinator(QObject):
    """使用进程锁保证唯一性，并将重复启动转为激活信号。"""

    activated = Signal()

    def __init__(self, server_name: str = APP_SERVER_NAME, parent: QObject | None = None) -> None:
        """初始化协调器；server_name 为稳定标识，parent 为 Qt 父对象。"""
        super().__init__(parent)
        self.server_name = server_name
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._accept_connections)
        self.instance_lock = QLockFile(str(_lock_path_for_server(server_name)))
        # 应用整个生命周期持有锁，不能按默认 30 秒误判为陈旧锁。
        self.instance_lock.setStaleLockTime(0)

    def acquire(self) -> bool:
        """尝试成为主实例；返回是否监听成功。"""
        # Windows 允许多个 QLocalServer 监听同名管道，必须先用进程锁判定唯一性。
        if not self.instance_lock.tryLock(0):
            return False
        if self.server.listen(self.server_name):
            return True
        # 已持有独占锁，首次监听失败只可能是崩溃后遗留的 Unix 端点或系统错误。
        QLocalServer.removeServer(self.server_name)
        if self.server.listen(self.server_name):
            return True
        self.instance_lock.unlock()
        return False

    def notify_existing(self) -> bool:
        """向已运行实例发送激活命令；返回是否成功写入。"""
        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(1000):
            return False
        payload = b"activate\n"
        if socket.write(payload) != len(payload):
            socket.disconnectFromServer()
            return False
        if socket.bytesToWrite() > 0:
            socket.waitForBytesWritten(1000)
        # Windows 命名管道的对端收到命令后会立即断开，此时 waitForBytesWritten
        # 可能返回 False；写缓冲已清空才是本次短消息已交付的判据。
        success = socket.bytesToWrite() == 0
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
        """关闭监听、清理当前 endpoint 并释放进程锁；无参数。"""
        if self.server.isListening():
            self.server.close()
            QLocalServer.removeServer(self.server_name)
        self.instance_lock.unlock()
