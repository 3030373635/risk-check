"""本机 Web 服务单实例锁和短期会话文件。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from tempfile import gettempdir
import time
from typing import Any

from risk_audit_web.task_store import atomic_write_json


SESSION_SCHEMA_VERSION = "1.0"
WINDOWS_MUTEX_NAME = r"Local\OpenAI.RiskAudit.Web.v2"
ERROR_ALREADY_EXISTS = 183


def _load_kernel32() -> Any:
    """加载并声明 Windows mutex API；无参数，返回 kernel32 库。"""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    # HANDLE 在 64 位 Windows 上是指针，必须显式声明以防默认 c_int 截断。
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


@dataclass(frozen=True)
class ServerSession:
    """描述当前本机 Web 服务会话。"""

    schema_version: str
    pid: int
    port: int
    token: str
    started_at: str

    def to_dict(self) -> dict[str, object]:
        """转换为 JSON 兼容字典；无参数。"""
        return asdict(self)


def write_server_session(path: Path, session: ServerSession) -> None:
    """原子写入服务会话；path 为文件路径，session 为会话数据。"""
    atomic_write_json(path, session.to_dict())


def read_server_session(path: Path) -> ServerSession | None:
    """读取并严格验证服务会话；path 为会话文件，无效时返回 None。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    required = {"schema_version", "pid", "port", "token", "started_at"}
    if set(value) != required:
        return None
    if value["schema_version"] != SESSION_SCHEMA_VERSION:
        return None
    if type(value["pid"]) is not int or value["pid"] <= 0:
        return None
    if type(value["port"]) is not int or not 0 < value["port"] < 65536:
        return None
    if not isinstance(value["token"], str) or not value["token"]:
        return None
    if not isinstance(value["started_at"], str) or not value["started_at"]:
        return None
    return ServerSession(**value)


def wait_for_live_session(
    session_path: Path,
    *,
    is_process_alive: Callable[[int], bool],
    health_check: Callable[[ServerSession], bool],
    attempts: int = 20,
    retry_seconds: float = 0.1,
) -> ServerSession | None:
    """等待已验证会话；参数为文件、PID 探测、健康检查、次数和间隔。"""
    for attempt in range(max(0, attempts)):
        session = read_server_session(session_path)
        if (
            session is not None
            and is_process_alive(session.pid)
            and health_check(session)
        ):
            return session
        if attempt + 1 < attempts:
            time.sleep(max(0.0, retry_seconds))
    return None


class SingleInstanceLock:
    """在主进程生命周期内持有 Windows 命名 mutex。"""

    def __init__(
        self,
        name: str = WINDOWS_MUTEX_NAME,
        *,
        lock_root: Path | None = None,
        force_file_lock: bool = False,
    ) -> None:
        """初始化锁；name 为名称，lock_root/force_file_lock 用于非 Windows 替身。"""
        self.name = name
        self.lock_root = lock_root or Path(gettempdir())
        self.force_file_lock = force_file_lock
        self._handle: int | None = None
        self._kernel32: Any | None = None
        self._file_descriptor: int | None = None
        digest = sha256(name.encode("utf-8")).hexdigest()[:16]
        self._lock_path = self.lock_root / f"risk-audit-web-{digest}.lock"

    def acquire(self) -> bool:
        """尝试获取实例锁；无参数，返回当前进程是否为主实例。"""
        if sys.platform == "win32" and not self.force_file_lock:
            return self._acquire_windows_mutex()
        self.lock_root.mkdir(parents=True, exist_ok=True)
        import fcntl

        descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            # flock 由内核绑定到打开的文件描述符，进程异常结束也会自动释放。
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return False
        os.ftruncate(descriptor, 0)
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        self._file_descriptor = descriptor
        return True

    def _acquire_windows_mutex(self) -> bool:
        """获取 Windows 命名 mutex；无参数，返回是否为首个持有者。"""
        import ctypes

        kernel32 = _load_kernel32()
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            return False
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            # Windows 会为重复创建返回一个新句柄，次实例必须立即关闭它。
            kernel32.CloseHandle(handle)
            return False
        self._handle = int(handle)
        self._kernel32 = kernel32
        return True

    def close(self) -> None:
        """释放当前实例锁；无参数。"""
        if self._handle is not None:
            kernel32 = self._kernel32 or _load_kernel32()
            kernel32.CloseHandle(self._handle)
            self._handle = None
            self._kernel32 = None
        if self._file_descriptor is not None:
            import fcntl

            fcntl.flock(self._file_descriptor, fcntl.LOCK_UN)
            os.close(self._file_descriptor)
            self._file_descriptor = None

    def __enter__(self) -> "SingleInstanceLock":
        """进入锁上下文；无参数，返回当前锁。"""
        return self

    def __exit__(self, *_error: object) -> None:
        """退出锁上下文；_error 为异常信息，无返回值。"""
        self.close()
