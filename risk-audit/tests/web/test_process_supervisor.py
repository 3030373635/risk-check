"""验证终端生命周期和平台进程监管。"""

import ctypes
import signal
from types import SimpleNamespace


def test_descendant_pids_are_deepest_first() -> None:
    """进程树清理必须先结束最深后代，避免父进程退出后孤儿化。"""
    from risk_audit_web.platform_runtime import descendant_pids

    rows = [(100, 1), (110, 100), (120, 100), (111, 110), (999, 1)]

    assert descendant_pids(100, rows) == [111, 110, 120]


def test_windows_supervisor_sets_kill_on_close_and_preserves_handle() -> None:
    """Windows Job 必须启用关闭即回收并使用指针宽度句柄。"""
    from risk_audit_web.process_supervisor import (
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        WindowsJobProcessSupervisor,
    )

    large_handle = 0x1_0000_0001
    flags = []
    assigned = []
    closed = []

    class FakeKernel32:
        """记录 Job Object 配置和句柄操作。"""

        def CreateJobObjectW(self, _security, _name):
            """返回伪 Job 句柄；参数为安全属性和名称。"""
            return large_handle

        def SetInformationJobObject(self, handle, info_class, pointer, size):
            """读取扩展限制标志；参数与 Win32 API 一致。"""
            info = ctypes.cast(
                pointer,
                ctypes.POINTER(JOBOBJECT_EXTENDED_LIMIT_INFORMATION),
            ).contents
            flags.append((int(handle), info_class, info.BasicLimitInformation.LimitFlags, size))
            return 1

        def GetCurrentProcess(self):
            """返回当前进程伪句柄；无参数。"""
            return 0x2222

        def AssignProcessToJobObject(self, handle, process):
            """记录当前进程加入 Job；参数为 Job 和进程句柄。"""
            assigned.append((int(handle), int(process)))
            return 1

        def CloseHandle(self, handle):
            """记录关闭的 Job 句柄；handle 为目标句柄。"""
            closed.append(int(handle))
            return 1

    supervisor = WindowsJobProcessSupervisor(kernel32=FakeKernel32())

    supervisor.prepare()
    supervisor.register(SimpleNamespace(pid=123))
    supervisor.close()

    assert flags[0][0] == large_handle
    assert flags[0][2] == JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert assigned == [(large_handle, 0x2222)]
    assert closed == [large_handle]


def test_macos_sighup_requests_exit_and_restore_reinstates_handler(monkeypatch) -> None:
    """macOS 终端挂断必须请求退出，并可恢复原信号处理器。"""
    from risk_audit_web.server import ServerController, install_terminal_shutdown_handler

    installed = []
    original_handler = object()
    sighup = getattr(signal, "SIGHUP", 1)
    monkeypatch.setattr(signal, "SIGHUP", sighup, raising=False)
    monkeypatch.setattr(signal, "getsignal", lambda _signal: original_handler)
    monkeypatch.setattr(signal, "signal", lambda number, handler: installed.append((number, handler)))
    controller = ServerController()
    server = SimpleNamespace(should_exit=False)
    controller.attach(server)

    restore = install_terminal_shutdown_handler(controller, platform_name="darwin")
    installed[0][1](signal.SIGHUP, None)
    restore()

    assert server.should_exit
    assert installed[-1] == (signal.SIGHUP, original_handler)
