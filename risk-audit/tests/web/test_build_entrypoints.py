"""验证本地 macOS Apple Silicon 构建入口。"""

import os
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = PROJECT_ROOT / "tools/build_macos_portable.command"
BUILD_SCRIPT = PROJECT_ROOT / "tools/build_portable.py"
VERIFIER_SCRIPT = PROJECT_ROOT / "tools/verify_portable_distribution.py"


def test_macos_build_entrypoint_uses_native_platform_and_project_python() -> None:
    """本地入口必须拒绝非 arm64 macOS，并只用项目虚拟环境。"""
    source = ENTRYPOINT.read_text(encoding="utf-8")

    assert source.startswith("#!/bin/zsh\n")
    assert "uname -s" in source and '"Darwin"' in source
    assert "uname -m" in source and '"arm64"' in source
    assert ".venv/bin/python" in source
    assert "python3 " not in source
    assert "pip install" not in source
    assert "sudo" not in source


def test_macos_build_entrypoint_passes_locked_builder_arguments() -> None:
    """本地入口必须把锁定平台、LibreOffice 和输出传给通用构建器。"""
    source = ENTRYPOINT.read_text(encoding="utf-8")

    assert "/Applications/LibreOffice.app" in source
    assert "tools/build_portable.py" in source
    assert "--platform" in source and "macos-arm64" in source
    assert "--libreoffice" in source
    assert "--output" in source
    assert "--usage-guide" in source
    assert "风控矩阵审核器-v2.0.0-macOS-arm64.zip" in source


def test_portable_builder_direct_entrypoint_loads_project_modules(tmp_path: Path) -> None:
    """直接执行构建器必须能加载 tools 包；tmp_path 模拟任意当前目录。"""
    result = subprocess.run(
        [str(PROJECT_ROOT / ".venv/bin/python"), str(BUILD_SCRIPT), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--platform" in result.stdout


def test_portable_verifier_direct_entrypoint_loads_source_modules(tmp_path: Path) -> None:
    """直接执行校验器必须能加载 src 包；tmp_path 模拟任意当前目录。"""
    result = subprocess.run(
        [str(PROJECT_ROOT / ".venv/bin/python"), str(VERIFIER_SCRIPT), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--platform" in result.stdout


def test_macos_build_entrypoint_stages_bundle_then_publishes_zip(tmp_path: Path) -> None:
    """macOS 构建必须在仓库外暂存应用包，再只发布最终 ZIP；tmp_path 为假仓库。"""
    repository_root = tmp_path / "repository"
    auditor_root = repository_root / "risk-audit"
    tools_root = auditor_root / "tools"
    tools_root.mkdir(parents=True)
    entrypoint = tools_root / "build_macos_portable.command"
    entrypoint.write_text(ENTRYPOINT.read_text(encoding="utf-8"), encoding="utf-8")
    entrypoint.chmod(0o755)

    argument_log = tmp_path / "output-argument.txt"
    fake_python = auditor_root / ".venv/bin/python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        """#!/bin/zsh
output_path=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--output" ]]; then
    output_path="$2"
    break
  fi
  shift
done
print -r -- "$output_path" > "$ARGUMENT_LOG"
mkdir -p "${output_path:h}"
print -n -- "portable-zip" > "${output_path}-macOS-arm64.zip"
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uname = fake_bin / "uname"
    fake_uname.write_text(
        "#!/bin/zsh\n[[ \"$1\" == \"-s\" ]] && print Darwin || print arm64\n",
        encoding="utf-8",
    )
    fake_uname.chmod(0o755)
    libreoffice = tmp_path / "LibreOffice.app"
    libreoffice.mkdir()
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["ARGUMENT_LOG"] = str(argument_log)

    result = subprocess.run(
        ["/bin/zsh", str(entrypoint), str(libreoffice), str(tmp_path / "python.tar.gz")],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    output_path = Path(argument_log.read_text(encoding="utf-8").strip())
    final_archive = repository_root / "dist/风控矩阵审核器-v2.0.0-macOS-arm64.zip"
    assert result.returncode == 0, result.stderr
    assert repository_root not in output_path.parents
    assert final_archive.read_bytes() == b"portable-zip"
    assert not output_path.exists()
