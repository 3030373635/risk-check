"""验证双平台 Web 便携版 GitHub Actions 构建契约。"""

from pathlib import Path
import re
import shutil
import subprocess

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/build-windows-web.yml"
MACOS_WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/build-macos-web.yml"


def read_workflow() -> str:
    """读取 Web 构建工作流；无参数，返回完整 YAML。"""
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def read_macos_workflow() -> str:
    """读取 macOS 构建工作流；无参数，返回完整 YAML。"""
    return MACOS_WORKFLOW_PATH.read_text(encoding="utf-8")


def test_web_build_workflow_is_manual_windows_job() -> None:
    """Web 便携构建必须只允许手动触发并使用 Windows x64。"""
    workflow = read_workflow()

    assert re.search(r"(?m)^on:\s*\n\s+workflow_dispatch:\s*$", workflow)
    assert not re.search(r"(?m)^\s+(push|pull_request|schedule):", workflow)
    assert "runs-on: windows-2022" in workflow
    assert "permissions:\n  contents: read" in workflow


def test_web_build_workflow_uses_runtime_lock_tests_and_portable_builder() -> None:
    """工作流必须分离构建依赖和目标运行锁，并调用通用构建器。"""
    workflow = read_workflow()

    assert "requirements-runtime.lock" in workflow
    assert "runtime-sources.json" in workflow
    assert "--require-hashes" in workflow
    assert "tests\\web" in workflow
    assert "build_portable.py" in workflow
    assert "--platform windows-x64" in workflow
    assert "verify_portable_distribution.py" in workflow
    assert "--platform windows-x64" in workflow
    assert "QT_QPA_PLATFORM" not in workflow
    assert "requirements-desktop.lock" not in workflow
    assert "build_windows_desktop.py" not in workflow
    assert "build_windows_web.py" not in workflow
    assert "PyInstaller" not in workflow


def test_web_build_workflow_pins_libreoffice_and_uploads_zip() -> None:
    """工作流必须校验固定 LibreOffice 并上传最终 ZIP。"""
    workflow = read_workflow()

    assert "LibreOffice_26.2.6_Win_x86-64.msi" in workflow
    assert "f9877032fd908beb9c0ddf06df4af5c2e85f419c42e14876c4cce5aae5fb2660" in workflow
    assert "Get-FileHash" in workflow
    assert "f86b3cbd425e1c446b56aa24e20a7be1223c1a8146e5e3a68c8e18d08b76e810" in workflow
    assert "风控矩阵审核器-v2.0.0-Windows-x64.zip" in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert "if-no-files-found: error" in workflow


def test_web_build_workflow_uploads_only_final_archive() -> None:
    """上传步骤必须只匹配最终 ZIP，不得上传半成品目录。"""
    workflow = read_workflow()
    upload_block = workflow.split("uses: actions/upload-artifact@v7", 1)[1]

    assert 'path: "release/风控矩阵审核器-v2.0.0-Windows-x64.zip"' in upload_block
    assert "release/**" not in upload_block


def test_macos_build_workflow_is_manual_arm64_job() -> None:
    """macOS 便携构建必须手动触发并在 Apple Silicon runner 上运行。"""
    workflow = read_macos_workflow()

    assert re.search(r"(?m)^on:\s*\n\s+workflow_dispatch:\s*$", workflow)
    assert not re.search(r"(?m)^\s+(push|pull_request|schedule):", workflow)
    assert "runs-on: macos-15" in workflow
    assert '[[ "$(uname -m)" == "arm64" ]]' in workflow
    assert "permissions:\n  contents: read" in workflow


def test_macos_build_workflow_is_valid_yaml() -> None:
    """macOS 工作流不得因命令行引号形成无法解析的 YAML。"""
    ruby = shutil.which("ruby")
    if ruby is None:
        pytest.skip("当前平台没有可用的 YAML 解析器")

    result = subprocess.run(
        [ruby, "-e", 'require "yaml"; YAML.load_file(ARGV.fetch(0))', str(MACOS_WORKFLOW_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_macos_build_workflow_pins_inputs_builds_and_verifies_archive() -> None:
    """macOS 工作流必须校验运行时和 DMG，再调用现有构建与发布校验入口。"""
    workflow = read_macos_workflow()

    assert "runtime-sources.json" in workflow
    assert "requirements-runtime.lock" in workflow
    assert "LibreOffice_25.2.6.2_MacOS_aarch64.dmg" in workflow
    assert "b1c4b78fdaea8bd42461ad3bee264d5d281827dfcfdc1a567c64bc512ccde8b3" in workflow
    assert "hdiutil attach" in workflow
    assert "hdiutil detach" in workflow
    assert "build_macos_portable.command" in workflow
    assert "verify_portable_distribution.py" in workflow
    assert "--platform macos-arm64" in workflow
    assert "风控矩阵审核器-v2.0.0-macOS-arm64.zip" in workflow


def test_macos_build_workflow_uploads_only_final_archive() -> None:
    """macOS 上传步骤必须只发布已验证的最终 ZIP。"""
    workflow = read_macos_workflow()
    upload_block = workflow.split("uses: actions/upload-artifact@v7", 1)[1]

    assert 'path: "release/风控矩阵审核器-v2.0.0-macOS-arm64.zip"' in upload_block
    assert "release/**" not in upload_block
    assert "if-no-files-found: error" in upload_block
    assert "compression-level: 0" in upload_block


@pytest.mark.parametrize("workflow_reader", [read_workflow, read_macos_workflow])
def test_web_test_step_runs_from_auditor_root(workflow_reader) -> None:
    """双平台测试步骤必须以审核器为工作目录，使 tools 与 src 同时可导入。"""
    workflow = workflow_reader()
    test_step = workflow.split("- name: 运行 Web 版测试", 1)[1].split("\n      - name:", 1)[0]

    assert "working-directory: 审核器" in test_step
