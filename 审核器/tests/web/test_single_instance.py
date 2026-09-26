"""验证单实例锁和本机服务会话。"""

import ctypes
from pathlib import Path

import pytest


def test_server_session_round_trip_and_validation(tmp_path: Path) -> None:
    """会话文件必须原子往返并拒绝非法端口；tmp_path 为会话目录。"""
    from risk_audit_web.single_instance import (
        ServerSession,
        read_server_session,
        write_server_session,
    )

    path = tmp_path / "server-session.json"
    session = ServerSession("1.0", 321, 49152, "secret", "2026-09-25T10:00:00+08:00")
    write_server_session(path, session)

    assert read_server_session(path) == session
    path.write_text('{"schema_version":"1.0","pid":1,"port":0,"token":"x","started_at":"now"}')
    assert read_server_session(path) is None


def test_wait_for_live_session_rejects_pid_reuse_and_wrong_token(tmp_path: Path) -> None:
    """存活 PID 但令牌不匹配时不得重开错误服务；tmp_path 为会话目录。"""
    from risk_audit_web.single_instance import ServerSession, wait_for_live_session, write_server_session

    session = ServerSession("1.0", 321, 49152, "expected-token", "2026-09-25T10:00:00+08:00")
    write_server_session(tmp_path / "server-session.json", session)

    found = wait_for_live_session(
        tmp_path / "server-session.json",
        is_process_alive=lambda pid: pid == 321,
        health_check=lambda candidate: candidate.token == "other-token",
        attempts=2,
        retry_seconds=0,
    )

    assert found is None


def test_wait_for_live_session_returns_verified_session(tmp_path: Path) -> None:
    """PID 和令牌健康检查都匹配时返回会话；tmp_path 为会话目录。"""
    from risk_audit_web.single_instance import ServerSession, wait_for_live_session, write_server_session

    path = tmp_path / "server-session.json"
    session = ServerSession("1.0", 321, 49152, "secret", "2026-09-25T10:00:00+08:00")
    write_server_session(path, session)

    assert wait_for_live_session(
        path,
        is_process_alive=lambda pid: pid == 321,
        health_check=lambda candidate: candidate.token == "secret",
        attempts=1,
        retry_seconds=0,
    ) == session


def test_non_windows_lock_allows_only_one_holder(tmp_path: Path) -> None:
    """POSIX flock 必须拒绝第二实例并由内核在关闭后释放。"""
    from risk_audit_web.single_instance import SingleInstanceLock

    first = SingleInstanceLock("test-lock", lock_root=tmp_path, force_file_lock=True)
    second = SingleInstanceLock("test-lock", lock_root=tmp_path, force_file_lock=True)
    assert first.acquire()
    assert not second.acquire()
    first.close()
    assert first._lock_path.is_file()
    assert second.acquire()
    second.close()


def test_windows_mutex_preserves_64_bit_handle(monkeypatch) -> None:
    """Windows mutex 关闭时不得截断 64 位句柄。"""
    from risk_audit_web import single_instance

    large_handle = 0x1_0000_0001
    closed_handles: list[int] = []

    class FakeFunction:
        """模拟 ctypes 函数并在缺少指针签名时复现句柄截断。"""

        def __init__(self, result: int, *, records: list[int] | None = None) -> None:
            """初始化伪函数；result 为返回值，records 为参数记录。"""
            self.result = result
            self.records = records
            self.argtypes = None
            self.restype = None

        def __call__(self, *arguments):
            """执行伪函数；arguments 为 Win32 API 参数。"""
            if self.records is not None:
                value = int(arguments[0])
                if self.argtypes != [ctypes.c_void_p]:
                    value = ctypes.c_int(value).value
                self.records.append(value)
            return self.result

    class FakeKernel32:
        """提供测试所需的 mutex API。"""

        def __init__(self) -> None:
            """初始化 CreateMutexW 和 CloseHandle 伪函数。"""
            self.CreateMutexW = FakeFunction(large_handle)
            self.CloseHandle = FakeFunction(1, records=closed_handles)

    kernel32 = FakeKernel32()
    monkeypatch.setattr(single_instance.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)
    lock = single_instance.SingleInstanceLock()

    assert lock.acquire()
    lock.close()

    assert closed_handles == [large_handle]
