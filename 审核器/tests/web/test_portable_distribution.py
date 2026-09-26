"""验证便携发布许可证与目标 Python 边界。"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest


def write_file(path: Path, content: bytes) -> Path:
    """创建测试文件；path 为文件路径，content 为字节内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_collect_distribution_licenses_queries_target_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """许可证元数据必须由目标 Python 查询，不得读取构建器环境。"""
    from tools import collect_python_licenses

    python_executable = write_file(tmp_path / "runtime/python/bin/python3", b"python")
    package_license = write_file(tmp_path / "target/FastAPI.dist-info/LICENSE", b"fastapi")
    python_license = write_file(tmp_path / "runtime/python/LICENSE.txt", b"python")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        """返回目标环境许可证索引；command 为命令，kwargs 为进程选项。"""
        calls.append(command)
        assert kwargs["check"] is True
        assert kwargs["shell"] is False
        return SimpleNamespace(stdout=json.dumps({
            "python_license": str(python_license),
            "distributions": [{
                "name": "FastAPI",
                "licenses": [str(package_license)],
            }],
        }))

    monkeypatch.setattr(collect_python_licenses.subprocess, "run", fake_run)
    destination = tmp_path / "licenses"
    copied = collect_python_licenses.collect_distribution_licenses(
        python_executable,
        ["FastAPI"],
        destination,
    )

    assert calls[0][0] == str(python_executable)
    assert calls[0][1:3] == ["-I", "-c"]
    assert json.loads(calls[0][4]) == ["FastAPI"]
    assert (destination / "FastAPI/01-LICENSE.txt").read_bytes() == b"fastapi"
    assert (destination / "Python.txt").read_bytes() == b"python"
    assert len(copied) == 2


def test_collect_distribution_licenses_rejects_missing_license(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目标发行版未携带许可证时必须阻止发布。"""
    from tools import collect_python_licenses

    python_executable = write_file(tmp_path / "python", b"python")
    monkeypatch.setattr(
        collect_python_licenses.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps({
            "python_license": str(tmp_path / "LICENSE.txt"),
            "distributions": [{"name": "missing", "licenses": []}],
        })),
    )

    with pytest.raises(collect_python_licenses.LicenseCollectionError, match="许可证"):
        collect_python_licenses.collect_distribution_licenses(
            python_executable,
            ["missing"],
            tmp_path / "licenses",
        )


def test_target_license_query_accepts_british_licence_file_name() -> None:
    """目标环境查询必须识别 et-xmlfile 使用的 LICENCE 文件名；无参数。"""
    from tools import collect_python_licenses

    payload = collect_python_licenses._load_target_index(
        Path(__file__).resolve().parents[2] / ".venv/bin/python",
        ["et-xmlfile"],
    )

    assert payload["distributions"][0]["licenses"]


def test_target_license_query_finds_python_license_in_standard_library() -> None:
    """精简运行时必须从标准库目录找到 Python LICENSE.txt；无参数。"""
    from tools import collect_python_licenses

    payload = collect_python_licenses._load_target_index(
        Path(__file__).resolve().parents[2] / ".venv/bin/python",
        [],
    )

    assert payload["python_license"].endswith("/lib/python3.11/LICENSE.txt")


def pe_x64_bytes() -> bytes:
    """创建最小 x64 PE 头；无参数，返回测试字节。"""
    content = bytearray(0x88)
    content[:2] = b"MZ"
    content[0x3C:0x40] = (0x80).to_bytes(4, "little")
    content[0x80:0x84] = b"PE\0\0"
    content[0x84:0x86] = (0x8664).to_bytes(2, "little")
    return bytes(content)


def _write_rulepack(root: Path) -> None:
    """创建有效规则包；root 为发布根。"""
    rulepacks = root / "runtime/resources/rulepacks"
    rule = write_file(rulepacks / "releases/2.0.0/rules/R01.json", b'{"id":"R01"}\n')
    rule_hash = hashlib.sha256(rule.read_bytes()).hexdigest()
    content_hash = hashlib.sha256(json.dumps(
        {"rules/R01.json": rule_hash},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()).hexdigest()
    write_file(rulepacks / "active.json", json.dumps({
        "version": "2.0.0", "content_hash": content_hash,
    }).encode())
    write_file(rulepacks / "releases/2.0.0/manifest.json", json.dumps({
        "version": "2.0.0", "status": "released", "content_hash": content_hash,
    }).encode())
    write_file(rulepacks / "audit-config.json", b"{}")


def _write_web_runtime(root: Path) -> None:
    """创建离线前端和供应商清单；root 为发布根。"""
    files = {
        "index.html": b'<link href="css/bootstrap.min.css"><script src="js/app.js"></script>',
        "css/bootstrap.min.css": b".btn{display:block}",
        "css/bootstrap-icons.min.css": b".bi{display:inline-block}",
        "css/theme.css": b"body{color:#123}",
        "js/bootstrap.bundle.min.js": b"window.bootstrap={}",
        "js/api.js": b"window.api={}",
        "js/tasks.js": b"window.tasks={}",
        "js/app.js": b"window.app={}",
        "fonts/bootstrap-icons.woff2": b"font",
    }
    assets = []
    for relative_path, content in files.items():
        path = write_file(root / "runtime/web" / relative_path, content)
        if relative_path.startswith(("css/bootstrap", "js/bootstrap", "fonts/bootstrap")):
            assets.append({
                "path": f"src/risk_audit_web/static/{relative_path}",
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(content).hexdigest(),
            })
    for name in ("Bootstrap.txt", "Bootstrap-Icons.txt"):
        path = write_file(root / "runtime/licenses" / name, b"license")
        assets.append({
            "path": f"licenses/web/{name}",
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    write_file(root / "runtime/web/vendor-manifest.json", json.dumps({
        "schema_version": "1.0",
        "assets": assets,
    }).encode())


def refresh_release_manifest(root: Path) -> None:
    """根据当前发布文件重建清单；root 为发布根。"""
    from tools.portable_release import build_release_manifest

    (root / "runtime/manifest.json").write_text(
        json.dumps(build_release_manifest(root), ensure_ascii=False),
        encoding="utf-8",
    )


def make_valid_distribution(tmp_path: Path, platform_id: str) -> Path:
    """创建最小双平台发布；tmp_path/platform_id 为根目录和平台。"""
    root = tmp_path / f"release-{platform_id}"
    write_file(root / "app/risk_audit/__init__.py", b"")
    write_file(root / "app/risk_audit_web/__init__.py", b"")
    write_file(
        root / "app/risk_audit_web/api_models.py",
        b"class TaskCreateRequest:\n    input_root: str\n",
    )
    if platform_id == "windows-x64":
        write_file(
            root / "启动审核器.bat",
            b'@echo off\r\nset "APP_ROOT=%~dp0"\r\n"%APP_ROOT%runtime\\python\\python.exe" -m risk_audit_web.app\r\n',
        )
        write_file(root / "runtime/python/python.exe", pe_x64_bytes())
        write_file(root / "runtime/libreoffice/program/soffice.exe", pe_x64_bytes())
    else:
        write_file(
            root / "启动审核器.command",
            b'#!/bin/zsh\nAPP_ROOT="${0:A:h}"\nexec "$APP_ROOT/runtime/python/bin/python3" -m risk_audit_web.app\n',
        ).chmod(0o755)
        write_file(root / "runtime/python/bin/python3", b"mach-o").chmod(0o755)
        write_file(
            root / "runtime/libreoffice/LibreOffice.app/Contents/MacOS/soffice",
            b"mach-o",
        ).chmod(0o755)
    write_file(root / "使用说明.md", b"guide")
    (root / "data").mkdir(parents=True)
    (root / "outputs").mkdir()
    write_file(root / "runtime/resources/baselines/审核/基准.xlsx", b"baseline")
    write_file(root / "runtime/resources/entities/会计主体清单20260907.xlsx", b"entity")
    _write_rulepack(root)
    _write_web_runtime(root)
    write_file(root / "runtime/licenses/Python.txt", b"license")
    write_file(root / "runtime/licenses/LibreOffice.txt", b"license")
    write_file(root / "runtime/licenses/fastapi/01-LICENSE.txt", b"license")
    refresh_release_manifest(root)
    return root


@pytest.mark.parametrize("platform_id", ["windows-x64", "macos-arm64"])
def test_valid_distribution_passes_platform_contract(
    tmp_path: Path,
    platform_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """双平台最小完整发布必须通过平台感知校验。"""
    from tools import verify_portable_distribution

    monkeypatch.setattr(
        verify_portable_distribution,
        "binary_architectures",
        lambda path: {"x86_64" if platform_id == "windows-x64" else "arm64"},
    )
    root = make_valid_distribution(tmp_path, platform_id)

    assert verify_portable_distribution.verify_distribution(root, platform_id) == []


def test_distribution_allows_third_party_runtime_internal_metadata_and_caches(
    tmp_path: Path,
) -> None:
    """第三方运行时的内部目录、SBOM 路径和 LibreOffice 缓存不得误判为开发残留。"""
    from tools.verify_portable_distribution import verify_distribution

    root = make_valid_distribution(tmp_path, "windows-x64")
    write_file(root / "runtime/python/Lib/site-packages/pip/_internal/main.py", b"main = True\n")
    write_file(
        root / "runtime/python/Lib/site-packages/package.dist-info/sboms/package.json",
        b'{"source": "/Users/build/package"}',
    )
    write_file(
        root / "runtime/libreoffice/program/python/lib/__pycache__/vendor.pyc",
        b"vendor-cache",
    )
    refresh_release_manifest(root)

    assert verify_distribution(root, "windows-x64") == []


@pytest.mark.parametrize(
    ("platform_id", "relative_path", "content", "expected"),
    [
        ("windows-x64", "_internal/python311.dll", b"pyinstaller", "PyInstaller"),
        ("windows-x64", "app/tests/test_bad.py", b"", "测试目录"),
        ("windows-x64", "app/PySide6/Qt6Core.dll", b"qt", "Qt"),
        ("windows-x64", "runtime/web/js/app.js", b'fetch("https://api.example")', "外部资源"),
        ("windows-x64", "app/risk_audit/leak.py", b'C:\\Users\\developer\\project', "开发机绝对路径"),
        (
            "windows-x64",
            "app/risk_audit_web/api_models.py",
            b"class TaskCreateRequest:\n    input_root: str\n    output_root: str\n",
            "输出根",
        ),
    ],
)
def test_distribution_rejects_forbidden_payloads(
    tmp_path: Path,
    platform_id: str,
    relative_path: str,
    content: bytes,
    expected: str,
) -> None:
    """校验器必须拒绝冻结、Qt、测试、外网、开发路径和外部输出入口。"""
    from tools.verify_portable_distribution import verify_distribution

    root = make_valid_distribution(tmp_path, platform_id)
    write_file(root / relative_path, content)
    refresh_release_manifest(root)

    assert any(expected in error for error in verify_distribution(root, platform_id))


def test_distribution_requires_only_matching_launcher_and_licenses(tmp_path: Path) -> None:
    """平台启动器必须唯一，基础许可证必须完整。"""
    from tools.verify_portable_distribution import verify_distribution

    root = make_valid_distribution(tmp_path, "windows-x64")
    write_file(root / "启动审核器.command", b"#!/bin/zsh\n")
    (root / "runtime/licenses/Python.txt").unlink()
    refresh_release_manifest(root)

    errors = verify_distribution(root, "windows-x64")

    assert any("启动器" in error for error in errors)
    assert any("Python" in error and "许可证" in error for error in errors)
