"""验证串行本机目录选择。"""

import threading
from pathlib import Path
import subprocess

import pytest


def test_directory_picker_rejects_second_concurrent_request(tmp_path: Path) -> None:
    """同一时刻只能有一个系统目录框；tmp_path 提供选择结果。"""
    from risk_audit_web.directory_picker import DirectoryPicker, DirectoryPickerBusy

    entered = threading.Event()
    release = threading.Event()

    def blocking_dialog() -> str:
        """阻塞首个目录框直到测试允许返回；无参数。"""
        entered.set()
        assert release.wait(1)
        return str(tmp_path)

    picker = DirectoryPicker(dialog=blocking_dialog)
    first = threading.Thread(target=picker.select_directory)
    first.start()
    assert entered.wait(1)

    with pytest.raises(DirectoryPickerBusy):
        picker.select_directory()

    release.set()
    first.join(1)


def test_directory_picker_returns_cancel_and_resolved_selection(tmp_path: Path) -> None:
    """取消与成功选择必须使用稳定契约；tmp_path 为已存在目录。"""
    from risk_audit_web.directory_picker import DirectoryPicker

    cancelled = DirectoryPicker(dialog=lambda: "").select_directory()
    selected = DirectoryPicker(dialog=lambda: str(tmp_path / ".")).select_directory()

    assert not cancelled.selected and cancelled.path is None
    assert selected.selected and selected.path == tmp_path.resolve()


def test_windows_dialog_runs_fixed_powershell_script() -> None:
    """Windows 选择器必须用固定 STA PowerShell 脚本返回目录。"""
    from risk_audit_web.directory_picker import run_directory_dialog

    calls = []

    def runner(arguments, **options):
        """记录系统命令并返回包含特殊字符的用户选择；参数为命令和选项。"""
        calls.append((arguments, options))
        return subprocess.CompletedProcess(arguments, 0, "C:\\用户目录\\A&B\n", "")

    selected = run_directory_dialog("win32", runner=runner)

    assert selected == "C:\\用户目录\\A&B"
    arguments, options = calls[0]
    assert arguments[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA"]
    assert "FolderBrowserDialog" in arguments[-1]
    assert "C:\\用户目录" not in arguments[-1]
    assert options == {"check": False, "capture_output": True, "text": True}


def test_macos_dialog_returns_selection_and_treats_cancel_as_empty() -> None:
    """macOS 选择器必须调用 osascript，并把用户取消转换为空选择。"""
    from risk_audit_web.directory_picker import run_directory_dialog

    calls = []
    results = [
        subprocess.CompletedProcess([], 0, "/Users/example/中文 资料/\n", ""),
        subprocess.CompletedProcess([], 1, "", "execution error: User canceled. (-128)\n"),
    ]

    def runner(arguments, **options):
        """依次返回选择和取消结果；参数为命令和选项。"""
        calls.append((arguments, options))
        return results.pop(0)

    assert run_directory_dialog("darwin", runner=runner) == "/Users/example/中文 资料/"
    assert run_directory_dialog("darwin", runner=runner) == ""
    assert calls[0][0][0] == "/usr/bin/osascript"
    assert "choose folder" in calls[0][0][-1]


def test_directory_dialog_reports_stable_system_error() -> None:
    """系统命令异常必须转换为稳定错误码，不泄露原始 stderr。"""
    from risk_audit_web.directory_picker import DirectoryPickerError, run_directory_dialog

    def runner(arguments, **options):
        """返回系统脚本失败；参数为命令和选项。"""
        return subprocess.CompletedProcess(arguments, 2, "", "private system detail")

    with pytest.raises(DirectoryPickerError) as captured:
        run_directory_dialog("darwin", runner=runner)

    assert captured.value.error_code == "DIRECTORY_PICKER_FAILED"
    assert "private system detail" not in str(captured.value)
