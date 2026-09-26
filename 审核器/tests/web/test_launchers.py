"""验证双平台启动器的前台运行契约。"""

from pathlib import Path
import shutil
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGING_ROOT = PROJECT_ROOT / "packaging"


def test_macos_launcher_runs_packaged_python_in_foreground(tmp_path: Path) -> None:
    """macOS 启动器必须在中文空格路径中把发布环境原样交给包内 Python。"""
    app_root = tmp_path / "中文 发布目录"
    python_path = app_root / "runtime/python/bin/python3"
    python_path.parent.mkdir(parents=True)
    python_path.write_text(
        "#!/bin/zsh\n"
        "print -r -- \"ROOT=$RISK_AUDIT_APP_ROOT\"\n"
        "print -r -- \"PATH=$PYTHONPATH\"\n"
        "print -r -- \"NOUSER=$PYTHONNOUSERSITE\"\n"
        "print -r -- \"NOBYTECODE=$PYTHONDONTWRITEBYTECODE\"\n"
        "print -r -- \"UTF8=$PYTHONUTF8\"\n"
        "print -r -- \"ARGS=$*\"\n",
        encoding="utf-8",
    )
    python_path.chmod(0o755)
    launcher = app_root / "启动审核器.command"
    shutil.copy2(PACKAGING_ROOT / launcher.name, launcher)
    launcher.chmod(0o755)
    (app_root / "app").mkdir()

    completed = subprocess.run(
        ["/bin/zsh", str(launcher)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert f"ROOT={app_root.resolve()}" in completed.stdout
    assert f"PATH={app_root.resolve() / 'app'}" in completed.stdout
    assert "NOUSER=1" in completed.stdout
    assert "NOBYTECODE=1" in completed.stdout
    assert "UTF8=1" in completed.stdout
    assert "ARGS=-m risk_audit_web.app" in completed.stdout


def test_windows_launcher_has_foreground_offline_contract() -> None:
    """Windows 启动器必须定位自身目录并直接等待包内 Python。"""
    content = (PACKAGING_ROOT / "启动审核器.bat").read_text(encoding="utf-8")
    normalized = content.lower()
    executable_lines = "\n".join(
        line.strip()
        for line in normalized.splitlines()
        if line.strip() and not line.lstrip().startswith("rem ")
    )

    assert "%~dp0" in content
    assert "runtime\\python\\python.exe" in normalized
    assert "risk_audit_app_root" in normalized
    assert "pythonpath" in normalized
    assert "pythonnoUsersite".lower() in normalized
    assert "pythondontwritebytecode" in normalized
    assert "pythonutf8" in normalized
    assert "-m risk_audit_web.app" in normalized
    assert " start " not in f" {executable_lines} "
    assert "pythonw.exe" not in executable_lines
    assert "http://" not in executable_lines and "https://" not in executable_lines
