"""Windows 进程和文件操作适配，并提供非 Windows 测试路径。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def is_process_alive(pid: int) -> bool:
    """检查指定 PID 是否存活；pid 为任务状态中的工作进程编号。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_process(pid: int) -> bool:
    """强制结束已核对的进程；pid 为目标 Worker 编号，返回是否成功。"""
    if not is_process_alive(pid):
        return True
    if sys.platform == "win32":
        import ctypes

        process_terminate = 0x0001
        handle = ctypes.windll.kernel32.OpenProcess(process_terminate, False, pid)
        if not handle:
            return False
        try:
            return bool(ctypes.windll.kernel32.TerminateProcess(handle, 1))
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    os.kill(pid, 15)
    return True


def open_path(path: Path) -> None:
    """使用系统默认程序打开路径；path 为文件或目录。"""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
