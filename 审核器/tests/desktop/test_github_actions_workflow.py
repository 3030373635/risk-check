"""验证 Windows 桌面版 GitHub Actions 构建契约。"""

from pathlib import Path
import re


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/build-windows-desktop.yml"


def read_workflow() -> str:
    """读取工作流文本；无参数，返回完整 YAML 内容。"""
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_windows_build_workflow_is_manual_only() -> None:
    """Windows 构建只能由 GitHub Actions 页面手动触发。"""
    workflow = read_workflow()

    assert re.search(r"(?m)^on:\s*\n\s+workflow_dispatch:\s*$", workflow)
    assert not re.search(r"(?m)^\s+(push|pull_request|schedule):", workflow)
    assert "runs-on: windows-2022" in workflow
    assert "permissions:\n  contents: read" in workflow


def test_windows_build_workflow_pins_and_verifies_libreoffice() -> None:
    """工作流必须固定 LibreOffice 版本并验证官方 SHA-256。"""
    workflow = read_workflow()

    assert "LibreOffice_26.2.6_Win_x86-64.msi" in workflow
    assert "f9877032fd908beb9c0ddf06df4af5c2e85f419c42e14876c4cce5aae5fb2660" in workflow
    assert "Get-FileHash" in workflow
    assert "msiexec.exe" in workflow
    assert "program\\soffice.exe" in workflow
    assert 'Join-Path $libreOfficeRoot "readmes\\readme_en-US.txt"' in workflow
    assert 'Join-Path $libreOfficeRoot "LICENSE"' not in workflow


def test_windows_build_workflow_builds_verifies_and_uploads_zip() -> None:
    """工作流必须安装锁定依赖、验证发布包并上传最终 ZIP。"""
    workflow = read_workflow()

    assert "actions/checkout@v7" in workflow
    assert "actions/setup-python@v7" in workflow
    assert "python-version: \"3.11\"" in workflow
    assert "--require-hashes" in workflow
    assert "requirements-desktop.lock" in workflow
    assert "build_windows_desktop.py" in workflow
    assert "verify_portable_distribution.py" in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert "风控矩阵审核器-v2.0.0-Windows-x64.zip" in workflow
    assert "if-no-files-found: error" in workflow
    assert "archive: false" in workflow
