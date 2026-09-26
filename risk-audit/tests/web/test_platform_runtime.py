"""验证跨平台进程与文件操作适配。"""

import ctypes
from pathlib import Path
from types import SimpleNamespace


def test_windows_process_apis_preserve_64_bit_handles(monkeypatch) -> None:
    """存活检查和终止进程不得截断 64 位 HANDLE。"""
    from risk_audit_web import platform_runtime

    large_handle = 0x1_0000_0001
    received_handles: list[int] = []

    class FakeFunction:
        """模拟 ctypes 函数并记录 HANDLE 参数。"""

        def __init__(self, callback) -> None:
            """初始化伪函数；callback 为行为回调。"""
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *arguments):
            """执行伪函数；arguments 为 Win32 API 参数。"""
            return self.callback(self, *arguments)

    def open_process(_function, _access, _inherit, _pid):
        """返回大于 32 位的伪进程句柄。"""
        return large_handle

    def record_handle(function, handle, *arguments):
        """记录按声明签名转换后的句柄；arguments 为其余参数。"""
        value = int(handle)
        if not function.argtypes or function.argtypes[0] is not ctypes.c_void_p:
            value = ctypes.c_int(value).value
        received_handles.append(value)
        return 1

    def get_exit_code(function, handle, exit_code_pointer):
        """记录句柄并返回进程存活码；exit_code_pointer 为输出指针。"""
        result = record_handle(function, handle)
        exit_code_pointer._obj.value = 259
        return result

    kernel32 = SimpleNamespace(
        OpenProcess=FakeFunction(open_process),
        GetExitCodeProcess=FakeFunction(get_exit_code),
        CloseHandle=FakeFunction(record_handle),
        TerminateProcess=FakeFunction(record_handle),
    )
    monkeypatch.setattr(platform_runtime.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)

    assert platform_runtime.is_process_alive(123)
    assert platform_runtime.terminate_process(123)
    assert received_handles == [large_handle] * 6


def test_macos_open_path_uses_absolute_system_open(monkeypatch, tmp_path: Path) -> None:
    """macOS 打开文件必须使用固定系统命令并保留完整路径参数。"""
    from risk_audit_web import platform_runtime

    calls = []
    monkeypatch.setattr(platform_runtime.sys, "platform", "darwin")
    monkeypatch.setattr(
        platform_runtime.subprocess,
        "Popen",
        lambda arguments: calls.append(arguments),
    )
    target = tmp_path / "中文 结果.xlsx"

    platform_runtime.open_path(target)

    assert calls == [["/usr/bin/open", str(target)]]
