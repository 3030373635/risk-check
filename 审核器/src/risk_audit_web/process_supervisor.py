"""把主服务及其子进程绑定到宿主终端生命周期。"""

from __future__ import annotations

import ctypes
import sys
from typing import Any


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


class IO_COUNTERS(ctypes.Structure):
    """对应 Win32 IO_COUNTERS 结构。"""

    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    """对应 Win32 JOBOBJECT_BASIC_LIMIT_INFORMATION 结构。"""

    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    """对应 Win32 JOBOBJECT_EXTENDED_LIMIT_INFORMATION 结构。"""

    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _load_job_kernel32() -> Any:
    """加载并声明 Windows Job Object API；无参数，返回 kernel32。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


class ProcessSupervisor:
    """定义主进程生命周期监管接口。"""

    def prepare(self) -> None:
        """在启动 Worker 前准备平台监管；无参数。"""

    def register(self, process: Any) -> None:
        """登记新 Worker；process 为刚启动的子进程。"""

    def close(self) -> None:
        """关闭平台监管资源；无参数。"""


class WindowsJobProcessSupervisor(ProcessSupervisor):
    """使用关闭即回收的 Windows Job Object 监管整个进程树。"""

    def __init__(self, *, kernel32: Any | None = None) -> None:
        """初始化监管器；kernel32 为测试可替换的 Win32 API。"""
        self.kernel32 = kernel32
        self._handle: int | None = None

    def prepare(self) -> None:
        """创建 Job、启用关闭回收并加入当前进程；无参数。"""
        if self._handle is not None:
            return
        kernel32 = self.kernel32 or _load_job_kernel32()
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "无法创建 Windows Job Object")
        numeric_handle = int(handle)
        information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        configured = kernel32.SetInformationJobObject(
            handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        assigned = configured and kernel32.AssignProcessToJobObject(
            handle,
            kernel32.GetCurrentProcess(),
        )
        if not configured or not assigned:
            error_code = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error_code, "无法配置 Windows Job Object")
        self.kernel32 = kernel32
        self._handle = numeric_handle

    def register(self, process: Any) -> None:
        """确认 Worker 由当前 Job 自动继承；process 为刚启动的子进程。"""
        if self._handle is None:
            raise RuntimeError(f"Worker {process.pid} 启动前尚未准备 Windows Job Object")

    def close(self) -> None:
        """关闭 Job 句柄并由系统回收仍存活的全部进程；无参数。"""
        if self._handle is None:
            return
        kernel32 = self.kernel32 or _load_job_kernel32()
        kernel32.CloseHandle(self._handle)
        self._handle = None


class MacOSProcessSupervisor(ProcessSupervisor):
    """保留 macOS Worker 在当前终端会话并登记其 PID。"""

    def __init__(self) -> None:
        """初始化 PID 集合；无参数。"""
        self.worker_pids: set[int] = set()

    def register(self, process: Any) -> None:
        """登记新 Worker；process 为刚启动的子进程。"""
        self.worker_pids.add(int(process.pid))


def create_process_supervisor(platform_name: str | None = None) -> ProcessSupervisor:
    """创建平台监管器；platform_name 为可选目标平台。"""
    selected_platform = platform_name or sys.platform
    if selected_platform == "win32":
        return WindowsJobProcessSupervisor()
    if selected_platform == "darwin":
        return MacOSProcessSupervisor()
    raise RuntimeError(f"不支持的运行平台：{selected_platform}")
