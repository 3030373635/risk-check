"""Windows 与 macOS 的进程和文件操作适配。"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any
from collections.abc import Callable, Sequence


def _load_kernel32() -> Any:
    """加载并声明 Windows 进程 API；无参数，返回 kernel32 库。"""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateProcess.restype = ctypes.c_int
    # HANDLE 是指针宽度，必须显式声明以避免 64 位句柄截断。
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def is_process_alive(pid: int) -> bool:
    """检查指定 PID 是否存活；pid 为任务状态中的工作进程编号。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = _load_kernel32()
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
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
        process_terminate = 0x0001
        kernel32 = _load_kernel32()
        handle = kernel32.OpenProcess(process_terminate, False, pid)
        if not handle:
            return False
        try:
            return bool(kernel32.TerminateProcess(handle, 1))
        finally:
            kernel32.CloseHandle(handle)
    os.kill(pid, 15)
    return True


def descendant_pids(
    parent_pid: int,
    process_rows: Sequence[tuple[int, int]],
) -> list[int]:
    """返回最深后代优先的 PID；参数为父 PID 和 `(pid, ppid)` 列表。"""
    children: dict[int, list[int]] = {}
    for pid, ppid in process_rows:
        children.setdefault(ppid, []).append(pid)
    ordered: list[int] = []

    def visit(pid: int) -> None:
        """按后序遍历子树；pid 为当前进程。"""
        for child_pid in children.get(pid, []):
            visit(child_pid)
            ordered.append(child_pid)

    visit(parent_pid)
    return ordered


def read_process_rows(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[tuple[int, int]]:
    """读取系统进程父子表；runner 为可替换命令执行器。"""
    completed = runner(
        ["/bin/ps", "-axo", "pid=,ppid="],
        check=True,
        capture_output=True,
        text=True,
    )
    rows: list[tuple[int, int]] = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2:
            rows.append((int(fields[0]), int(fields[1])))
    return rows


def terminate_process_tree(pid: int, timeout_seconds: float = 5.0) -> bool:
    """有界结束 Worker 进程树；参数为根 PID 和最长等待秒数。"""
    if sys.platform == "win32":
        return terminate_process(pid)
    if sys.platform != "darwin":
        raise RuntimeError(f"不支持的运行平台：{sys.platform}")
    try:
        targets = [*descendant_pids(pid, read_process_rows()), pid]
    except (OSError, subprocess.SubprocessError, ValueError):
        targets = [pid]
    for target_pid in targets:
        try:
            os.kill(target_pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            continue
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() < deadline:
        if all(not is_process_alive(target_pid) for target_pid in targets):
            return True
        time.sleep(0.05)
    try:
        remaining_tree = set(descendant_pids(pid, read_process_rows()))
    except (OSError, subprocess.SubprocessError, ValueError):
        remaining_tree = set()
    if is_process_alive(pid):
        remaining_tree.add(pid)
    for target_pid in targets:
        if target_pid not in remaining_tree or not is_process_alive(target_pid):
            continue
        try:
            # 再次核对树关系后才强制结束，降低 PID 复用误杀风险。
            os.kill(target_pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            continue
    return all(not is_process_alive(target_pid) for target_pid in remaining_tree)


def open_path(path: Path) -> None:
    """使用系统默认程序打开路径；path 为文件或目录。"""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/open", str(path)])
        return
    raise RuntimeError(f"不支持的运行平台：{sys.platform}")
