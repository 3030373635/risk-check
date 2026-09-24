"""验证单实例协调器的跨进程独占策略。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType


class FakeSignal:
    """为协调器提供最小 Qt 信号行为。"""

    def connect(self, callback) -> None:
        """保存回调；callback 为待连接的槽函数。"""
        self.callback = callback


class FakeQObject:
    """接收 Qt 父对象参数的测试替身。"""

    def __init__(self, parent=None) -> None:
        """保存父对象；parent 为可选 Qt 父对象。"""
        self.parent = parent


class FakeLockFile:
    """提供可配置抢锁结果的长期锁替身。"""

    try_lock_result = False

    def __init__(self, file_name: str) -> None:
        """初始化锁状态；file_name 为锁文件路径。"""
        self.file_name = file_name
        self.stale_lock_time = None
        self.try_lock_timeout = None
        self.unlock_count = 0

    def setStaleLockTime(self, timeout: int) -> None:  # noqa: N802
        """记录陈旧锁时间；timeout 单位为毫秒。"""
        self.stale_lock_time = timeout

    def tryLock(self, timeout: int) -> bool:  # noqa: N802
        """返回配置的抢锁结果；timeout 为最长等待毫秒数。"""
        self.try_lock_timeout = timeout
        return self.try_lock_result

    def unlock(self) -> None:
        """记录解锁次数；无参数。"""
        self.unlock_count += 1


class FakeLocalServer:
    """模拟 Windows 允许重复监听的本地服务器。"""

    removed_server_names: list[str] = []
    listen_results = [True]

    def __init__(self, parent=None) -> None:
        """初始化监听记录；parent 为可选 Qt 父对象。"""
        self.parent = parent
        self.newConnection = FakeSignal()
        self.listen_count = 0
        self.listening = False

    def listen(self, server_name: str) -> bool:
        """模拟监听成功；server_name 为本地服务标识。"""
        _ = server_name
        self.listen_count += 1
        result_index = min(self.listen_count - 1, len(self.listen_results) - 1)
        result = self.listen_results[result_index]
        self.listening = result
        return result

    def isListening(self) -> bool:  # noqa: N802
        """返回监听状态；无参数。"""
        return self.listening

    def close(self) -> None:
        """关闭模拟监听；无参数。"""
        self.listening = False

    @classmethod
    def removeServer(cls, server_name: str) -> bool:  # noqa: N802
        """记录端点清理；server_name 为本地服务标识。"""
        cls.removed_server_names.append(server_name)
        return True


class FakeLocalSocket:
    """占位的本地套接字类型。"""

    def connectToServer(self, server_name: str) -> None:  # noqa: N802
        """记录连接目标；server_name 为本地服务标识。"""
        self.server_name = server_name

    def waitForConnected(self, timeout: int) -> bool:  # noqa: N802
        """模拟无现存端点；timeout 为最长等待毫秒数。"""
        _ = timeout
        return False

    def disconnectFromServer(self) -> None:  # noqa: N802
        """模拟断开连接；无参数。"""


def _load_single_instance_module(monkeypatch):
    """使用可控 Qt 替身加载模块；monkeypatch 用于隔离模块注册。"""
    FakeLockFile.try_lock_result = False
    FakeLocalServer.listen_results = [True]
    FakeLocalServer.removed_server_names = []
    qt_core = ModuleType("PySide6.QtCore")
    qt_core.QLockFile = FakeLockFile
    qt_core.QObject = FakeQObject
    qt_core.Signal = lambda: FakeSignal()

    qt_network = ModuleType("PySide6.QtNetwork")
    qt_network.QLocalServer = FakeLocalServer
    qt_network.QLocalSocket = FakeLocalSocket

    pyside = ModuleType("PySide6")
    monkeypatch.setitem(sys.modules, "PySide6", pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qt_core)
    monkeypatch.setitem(sys.modules, "PySide6.QtNetwork", qt_network)

    module_path = Path(__file__).resolve().parents[2] / "src" / "risk_audit_desktop" / "single_instance.py"
    spec = importlib.util.spec_from_file_location("single_instance_locking_under_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_acquire_rejects_secondary_instance_before_listening(monkeypatch) -> None:
    """锁已被占用时必须直接拒绝，不得在 Windows 上再次监听。"""
    module = _load_single_instance_module(monkeypatch)
    coordinator = module.SingleInstanceCoordinator("risk-audit-lock-test")

    assert not coordinator.acquire()
    assert coordinator.server.listen_count == 0
    assert coordinator.instance_lock.stale_lock_time == 0
    assert coordinator.instance_lock.try_lock_timeout == 0


def test_acquire_releases_lock_when_server_cannot_listen(monkeypatch) -> None:
    """主实例端点两次监听均失败时必须释放进程锁。"""
    module = _load_single_instance_module(monkeypatch)
    FakeLockFile.try_lock_result = True
    FakeLocalServer.listen_results = [False, False]
    coordinator = module.SingleInstanceCoordinator("risk-audit-listen-failure")

    assert not coordinator.acquire()
    assert coordinator.server.listen_count == 2
    assert coordinator.instance_lock.unlock_count == 1


def test_close_releases_primary_instance_lock(monkeypatch) -> None:
    """主实例关闭时必须同时释放监听端点和进程锁。"""
    module = _load_single_instance_module(monkeypatch)
    FakeLockFile.try_lock_result = True
    coordinator = module.SingleInstanceCoordinator("risk-audit-close")
    assert coordinator.acquire()

    coordinator.close()

    assert not coordinator.server.isListening()
    assert coordinator.instance_lock.unlock_count == 1
