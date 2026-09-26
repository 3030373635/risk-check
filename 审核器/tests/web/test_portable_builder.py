"""验证跨平台便携发布组装器。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import plistlib
import shutil
from types import SimpleNamespace

import pytest


def write_file(path: Path, content: bytes = b"x") -> Path:
    """创建测试文件；path 为目标，content 为文件内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def make_build_inputs(tmp_path: Path):
    """创建最小 Windows 组装输入；tmp_path 为测试根目录。"""
    from tools.portable_release import BuildInputs

    repository_root = tmp_path / "repository"
    auditor_root = repository_root / "审核器"
    write_file(auditor_root / "src/risk_audit/__init__.py", b'VERSION = "2.0.0"\n')
    write_file(auditor_root / "src/risk_audit/__pycache__/bad.pyc")
    write_file(auditor_root / "src/risk_audit_web/__init__.py")
    write_file(auditor_root / "src/risk_audit_web/app.py")
    write_file(auditor_root / "src/risk_audit_web/static/index.html", b"<main>offline</main>")
    write_file(auditor_root / "src/risk_audit_web/static/js/app.js", b"window.app = {};")
    write_file(auditor_root / "packaging/启动审核器.bat", b"@echo off\r\n")
    write_file(auditor_root / "packaging/启动审核器.command", b"#!/bin/zsh\n")
    write_file(auditor_root / "rulepacks/active.json", b'{"version":"2.0.0"}')
    write_file(auditor_root / "rulepacks/releases/2.0.0/manifest.json", b"{}")
    write_file(auditor_root / "rulepacks/releases/2.0.0/rules/R01.json", b"{}")
    write_file(repository_root / "audit-config.json", b"{}")
    write_file(repository_root / "审核/基准.xlsx")
    write_file(repository_root / "审核/会计主体清单20260907.xlsx")

    python_root = tmp_path / "python"
    write_file(python_root / "python.exe", b"python")
    write_file(python_root / "Lib/site-packages/package.py", b"package = True\n")
    write_file(python_root / "Lib/site-packages/__pycache__/package.pyc")
    libreoffice_root = tmp_path / "LibreOffice"
    write_file(libreoffice_root / "program/soffice.exe", b"office")
    licenses_root = tmp_path / "licenses"
    write_file(licenses_root / "Python.txt", b"license")
    usage_guide = write_file(tmp_path / "使用说明.md", b"guide")
    return BuildInputs(
        platform_id="windows-x64",
        project_root=repository_root,
        python_root=python_root,
        libreoffice_root=libreoffice_root,
        licenses_root=licenses_root,
        output_root=tmp_path / "风控矩阵审核器-v2.0.0",
        usage_guide=usage_guide,
    )


def test_assemble_distribution_uses_script_layout_and_single_web_copy(tmp_path: Path) -> None:
    """组装器必须产生脚本版目录，且前端只在 runtime 中保留一份。"""
    from tools.portable_release import assemble_distribution

    inputs = make_build_inputs(tmp_path)
    result = assemble_distribution(inputs)

    assert result == inputs.output_root
    assert (result / "启动审核器.bat").is_file()
    assert not (result / "启动审核器.command").exists()
    assert (result / "app/risk_audit/__init__.py").is_file()
    assert (result / "app/risk_audit_web/app.py").is_file()
    assert not (result / "app/risk_audit_web/static").exists()
    assert not list((result / "app").rglob("__pycache__"))
    assert not list((result / "app").rglob("*.pyc"))
    assert (result / "runtime/web/index.html").is_file()
    assert (result / "runtime/python/python.exe").is_file()
    assert not list((result / "runtime/python").rglob("__pycache__"))
    assert not list((result / "runtime/python").rglob("*.pyc"))
    assert (result / "runtime/libreoffice/program/soffice.exe").is_file()
    assert list((result / "data").iterdir()) == []
    assert list((result / "outputs").iterdir()) == []

    payload = json.loads((result / "runtime/manifest.json").read_text(encoding="utf-8"))
    paths = {item["path"] for item in payload["resources"]}
    assert "app/risk_audit/__init__.py" in paths
    assert "runtime/python/python.exe" in paths
    assert "启动审核器.bat" in paths
    assert "使用说明.md" in paths
    assert "runtime/manifest.json" not in paths
    assert not any(path.startswith(("data/", "outputs/")) for path in paths)


def test_assemble_distribution_refuses_overwrite_and_removes_partial_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已有目标必须拒绝覆盖，中途失败必须删除本次半成品。"""
    from tools import portable_release

    inputs = make_build_inputs(tmp_path)
    inputs.output_root.mkdir()
    with pytest.raises(portable_release.BuildError, match="已存在"):
        portable_release.assemble_distribution(inputs)

    inputs.output_root.rmdir()
    monkeypatch.setattr(
        portable_release,
        "build_release_manifest",
        lambda distribution_root: (_ for _ in ()).throw(RuntimeError("broken")),
    )
    with pytest.raises(RuntimeError, match="broken"):
        portable_release.assemble_distribution(inputs)
    assert not inputs.output_root.exists()


def test_install_runtime_dependencies_uses_hashed_binary_only_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """依赖安装必须使用目标 Python、哈希锁和二进制 wheel。"""
    from tools import portable_release

    python_executable = write_file(tmp_path / "python/bin/python3", b"python")
    lock_file = write_file(tmp_path / "requirements-runtime.lock", b"package==1 --hash=sha256:a\n")
    cache = write_file(tmp_path / "python/lib/python3.11/site-packages/pkg/__pycache__/x.pyc")
    wheel = write_file(tmp_path / "python/cache/pkg.whl")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        """记录安装命令；command 为参数，kwargs 为进程选项。"""
        calls.append(command)
        assert kwargs["check"] is True
        assert kwargs["shell"] is False
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(portable_release.subprocess, "run", fake_run)
    portable_release.install_runtime_dependencies(python_executable, lock_file)

    assert calls == [[
        str(python_executable), "-I", "-m", "pip", "install", "--require-hashes",
        "--only-binary=:all:", "--no-deps", "-r", str(lock_file),
    ]]
    assert not cache.exists()
    assert not wheel.exists()


def test_build_release_manifest_is_verified_from_distribution_root(tmp_path: Path) -> None:
    """发布清单必须覆盖不可变载荷，并报告后续篡改。"""
    from risk_audit_web.diagnostics import verify_release_manifest
    from tools.portable_release import assemble_distribution

    root = assemble_distribution(make_build_inputs(tmp_path))
    assert verify_release_manifest(root).can_start

    (root / "runtime/python/python.exe").write_bytes(b"changed")
    report = verify_release_manifest(root)
    assert not report.can_start
    assert any(item.code in {"size_mismatch", "hash_mismatch"} for item in report.items)


def test_macos_assembly_verifies_version_architecture_and_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macOS 组装必须在 ditto 前验证版本/架构，并在复制前后验签。"""
    from tools import portable_release

    base = make_build_inputs(tmp_path)
    write_file(
        base.project_root / "审核器/packaging/runtime-sources.json",
        json.dumps({
            "schema_version": "1.0",
            "platforms": {
                "macos-arm64": {
                    "libreoffice": {"version": "25.2.6.2"},
                },
            },
        }).encode(),
    )
    python_root = tmp_path / "mac-python"
    write_file(python_root / "bin/python3", b"python").chmod(0o755)
    application = tmp_path / "LibreOffice.app"
    soffice = write_file(application / "Contents/MacOS/soffice", b"office")
    soffice.chmod(0o755)
    info = application / "Contents/Info.plist"
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "25.2.6.2"}))
    inputs = replace(
        base,
        platform_id="macos-arm64",
        python_root=python_root,
        libreoffice_root=application,
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        """模拟 macOS 系统工具；command 为命令，kwargs 为进程选项。"""
        calls.append(command)
        if command[:2] == ["/usr/bin/lipo", "-archs"]:
            return SimpleNamespace(stdout="arm64\n")
        if command[0] == "/usr/bin/ditto":
            shutil.copytree(Path(command[-2]), Path(command[-1]), symlinks=True)
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(portable_release.subprocess, "run", fake_run)
    portable_release.assemble_distribution(inputs)

    assert calls[0][:2] == ["/usr/bin/lipo", "-archs"]
    assert [command[0] for command in calls].count("/usr/bin/codesign") == 2
    assert any(
        command[:4] == ["/usr/bin/ditto", "--noextattr", "--noqtn", "--noacl"]
        for command in calls
    )
    copied = inputs.output_root / "runtime/libreoffice/LibreOffice.app/Contents/MacOS/soffice"
    assert copied.is_file()
    assert copied.stat().st_mode & 0o111
    assert (inputs.output_root / "启动审核器.command").stat().st_mode & 0o111
