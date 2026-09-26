"""验证便携发布归档的单根结构和 macOS 保真属性。"""

from __future__ import annotations

from pathlib import Path
import stat
import subprocess
import sys
import zipfile

import pytest


def write_file(path: Path, content: bytes) -> Path:
    """创建归档测试文件；path/content 为路径和字节内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def make_archive_root(tmp_path: Path, platform_id: str) -> Path:
    """创建最小归档根；tmp_path/platform_id 为测试根和平台。"""
    root = tmp_path / "风控矩阵审核器-v2.0.0"
    (root / "data").mkdir(parents=True)
    (root / "outputs").mkdir()
    if platform_id == "windows-x64":
        write_file(root / "启动审核器.bat", b"@echo off\r\n")
    else:
        write_file(root / "启动审核器.command", b"#!/bin/zsh\n").chmod(0o755)
        python = write_file(root / "runtime/python/bin/python3", b"python")
        python.chmod(0o755)
        soffice = write_file(
            root / "runtime/libreoffice/LibreOffice.app/Contents/MacOS/soffice",
            b"office",
        )
        soffice.chmod(0o755)
        link = root / "runtime/libreoffice/LibreOffice.app/Contents/MacOS/soffice-link"
        link.symlink_to("soffice")
    return root


def test_windows_archive_has_one_root_and_explicit_writable_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows ZIP 必须只有一个顶层目录并显式保留空可写目录。"""
    from tools import verify_portable_distribution
    from tools.portable_release import create_distribution_archive

    root = make_archive_root(tmp_path, "windows-x64")
    monkeypatch.setattr(verify_portable_distribution, "verify_distribution", lambda root, platform: [])
    archive = create_distribution_archive(root, "windows-x64")

    with zipfile.ZipFile(archive) as handle:
        names = set(handle.namelist())
    assert {Path(name).parts[0] for name in names if Path(name).parts} == {root.name}
    assert f"{root.name}/data/" in names
    assert f"{root.name}/outputs/" in names
    assert verify_portable_distribution.verify_archive(archive, "windows-x64") == []


def test_archive_rejects_multiple_top_level_directories(tmp_path: Path) -> None:
    """归档不得同时包含多个顶层目录。"""
    from tools.verify_portable_distribution import verify_archive

    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("one/file.txt", "1")
        handle.writestr("two/file.txt", "2")

    assert any("顶层目录" in error for error in verify_archive(archive, "windows-x64"))


@pytest.mark.skipif(sys.platform != "darwin", reason="ditto 保真归档只能在 macOS 验证")
def test_macos_archive_round_trip_preserves_modes_and_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macOS ditto 往返必须保留启动器、二进制权限和应用包链接。"""
    from tools import verify_portable_distribution
    from tools.portable_release import create_distribution_archive

    root = make_archive_root(tmp_path, "macos-arm64")
    verified_roots: list[Path] = []

    def verify_extracted_root(distribution_root: Path, platform_id: str) -> list[str]:
        """记录 ditto 解压根；distribution_root/platform_id 为待验证目录和平台。"""
        verified_roots.append(distribution_root)
        return [] if distribution_root.is_dir() else [f"发布目录不存在：{distribution_root}"]

    monkeypatch.setattr(
        verify_portable_distribution,
        "verify_distribution",
        verify_extracted_root,
    )
    archive = create_distribution_archive(root, "macos-arm64")
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    subprocess.run(
        ["/usr/bin/ditto", "-x", "-k", str(archive), str(extracted)],
        check=True,
        shell=False,
    )
    unpacked = extracted / root.name

    for relative_path in (
        "启动审核器.command",
        "runtime/python/bin/python3",
        "runtime/libreoffice/LibreOffice.app/Contents/MacOS/soffice",
    ):
        assert stat.S_IMODE((unpacked / relative_path).stat().st_mode) & 0o111
    assert (unpacked / "runtime/libreoffice/LibreOffice.app/Contents/MacOS/soffice-link").is_symlink()
    assert verify_portable_distribution.verify_archive(archive, "macos-arm64") == []
    assert verified_roots and all(path.name == root.name for path in verified_roots)
