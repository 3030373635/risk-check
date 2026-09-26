"""串行调用系统目录选择框。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from threading import Lock


class DirectoryPickerBusy(RuntimeError):
    """表示已有目录选择框正在运行。"""


class DirectoryPickerError(RuntimeError):
    """表示系统目录选择器无法完成请求。"""

    def __init__(self, error_code: str, message: str) -> None:
        """初始化平台错误；参数为稳定错误码和用户可读消息。"""
        super().__init__(message)
        self.error_code = error_code


WINDOWS_DIRECTORY_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '请选择待审核材料目录'
$dialog.ShowNewFolderButton = $false
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::WriteLine($dialog.SelectedPath)
}
""".strip()

MACOS_DIRECTORY_SCRIPT = 'POSIX path of (choose folder with prompt "请选择待审核材料目录")'


@dataclass(frozen=True)
class DirectorySelection:
    """保存一次目录选择的结果。"""

    selected: bool
    path: Path | None


def run_directory_dialog(
    platform_name: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """运行系统目录框；参数为平台和命令执行器，返回选择路径或空字符串。"""
    if platform_name == "win32":
        arguments = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-STA",
            "-Command",
            WINDOWS_DIRECTORY_SCRIPT,
        ]
    elif platform_name == "darwin":
        arguments = ["/usr/bin/osascript", "-e", MACOS_DIRECTORY_SCRIPT]
    else:
        raise DirectoryPickerError(
            "DIRECTORY_PICKER_UNSUPPORTED",
            f"当前系统不支持目录选择：{platform_name}",
        )
    try:
        completed = runner(
            arguments,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise DirectoryPickerError(
            "DIRECTORY_PICKER_FAILED",
            "无法打开系统目录选择器",
        ) from error
    if completed.returncode == 0:
        return completed.stdout.strip()
    if platform_name == "darwin" and "-128" in completed.stderr:
        return ""
    raise DirectoryPickerError(
        "DIRECTORY_PICKER_FAILED",
        "无法打开系统目录选择器",
    )


class DirectoryPicker:
    """确保同一时间只有一个目录选择框。"""

    def __init__(self, dialog: Callable[[], str] | None = None) -> None:
        """初始化选择器；dialog 为可替换的同步系统对话框。"""
        self.dialog = dialog or (lambda: run_directory_dialog(sys.platform))
        self._lock = Lock()

    def select_directory(self) -> DirectorySelection:
        """执行一次目录选择；无参数，返回取消或绝对路径。"""
        if not self._lock.acquire(blocking=False):
            raise DirectoryPickerBusy("已有目录选择框正在等待用户操作")
        try:
            selected = self.dialog()
            if not selected:
                return DirectorySelection(False, None)
            return DirectorySelection(True, Path(selected).resolve())
        finally:
            self._lock.release()
