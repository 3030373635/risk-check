"""验证仓库只保留单机离线 Web 交互层。"""

from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = PROJECT_ROOT.parent


def test_repository_has_no_runtime_qt_or_desktop_entry() -> None:
    """最终仓库不得继续发布旧 GUI。"""
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert not (PROJECT_ROOT / "src/risk_audit_desktop").exists()
    assert "PySide6" not in pyproject
    assert "pytest-qt" not in pyproject
    assert "risk-audit-desktop" not in pyproject
    assert "risk-audit-web" in pyproject


def test_repository_has_no_obsolete_desktop_build_files() -> None:
    """最终仓库不得保留旧桌面构建入口和说明。"""
    obsolete_paths = [
        PROJECT_ROOT / "tests/desktop",
        PROJECT_ROOT / "requirements-desktop.lock",
        PROJECT_ROOT / "tools/build_windows_desktop.py",
        PROJECT_ROOT / "tools/build_windows_web.py",
        PROJECT_ROOT / "Windows桌面版使用说明.md",
        REPOSITORY_ROOT / ".github/workflows/build-windows-desktop.yml",
    ]

    assert [path for path in obsolete_paths if path.exists()] == []


def test_python_package_includes_offline_web_assets() -> None:
    """Python 发行包必须携带页面、样式、脚本和应用图标。"""
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]["risk_audit_web"]

    assert "assets/*" in package_data
    assert "static/*" in package_data
    assert "static/**/*" in package_data


def test_repository_uses_runtime_only_dependency_lock() -> None:
    """发布依赖锁不得继续混入测试或冻结构建工具。"""
    lock_path = PROJECT_ROOT / "requirements-runtime.lock"
    assert lock_path.is_file()
    assert not (PROJECT_ROOT / "requirements-web.lock").exists()
    normalized = lock_path.read_text(encoding="utf-8").lower()
    for forbidden in (
        "pytest==", "httpx==", "pyinstaller==", "altgraph==", "pefile==", "macholib==",
    ):
        assert forbidden not in normalized

    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "build" not in pyproject["project"]["optional-dependencies"]


def test_repository_has_both_script_guides_and_launchers() -> None:
    """仓库必须同时交付 Windows 和 Apple Silicon macOS 启动说明。"""
    required_paths = [
        PROJECT_ROOT / "packaging/启动审核器.bat",
        PROJECT_ROOT / "packaging/启动审核器.command",
        PROJECT_ROOT / "Windows Web版使用说明.md",
        PROJECT_ROOT / "macOS Apple Silicon Web版使用说明.md",
    ]

    assert [path for path in required_paths if not path.is_file()] == []
