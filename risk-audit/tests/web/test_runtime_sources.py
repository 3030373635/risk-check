"""验证便携运行时来源、下载摘要和安全解压。"""

import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_MANIFEST = PROJECT_ROOT / "packaging/runtime-sources.json"


def test_runtime_manifest_pins_both_python_archives() -> None:
    """双平台 Python 必须固定到已核对的 3.11.16 资产和摘要。"""
    from tools.runtime_sources import load_runtime_sources

    sources = load_runtime_sources(SOURCE_MANIFEST)

    windows = sources["windows-x64"]["python"]
    macos = sources["macos-arm64"]["python"]
    assert windows.version == macos.version == "3.11.16+20260924"
    assert windows.url.endswith(
        "cpython-3.11.16%2B20260924-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
    )
    assert windows.size == 25273229
    assert windows.sha256 == "f86b3cbd425e1c446b56aa24e20a7be1223c1a8146e5e3a68c8e18d08b76e810"
    assert macos.url.endswith(
        "cpython-3.11.16%2B20260924-aarch64-apple-darwin-install_only_stripped.tar.gz"
    )
    assert macos.size == 26965707
    assert macos.sha256 == "e1d745b07b6acc0641dbb3237d3c5953deeeed182141bab2242684076fd86547"
    macos_libreoffice = sources["macos-arm64"]["libreoffice"]
    assert macos_libreoffice.version == "25.2.6.2"
    assert macos_libreoffice.kind == "dmg"
    assert macos_libreoffice.url.endswith("LibreOffice_25.2.6.2_MacOS_aarch64.dmg")
    assert macos_libreoffice.size == 291550461
    assert macos_libreoffice.sha256 == (
        "b1c4b78fdaea8bd42461ad3bee264d5d281827dfcfdc1a567c64bc512ccde8b3"
    )


def test_download_verified_accepts_exact_local_source(tmp_path: Path) -> None:
    """下载器必须只在大小和摘要都匹配后原子发布文件。"""
    from tools.runtime_sources import RuntimeSource, download_verified

    content = b"portable-python"
    source_path = tmp_path / "source.tar.gz"
    source_path.write_bytes(content)
    destination = tmp_path / "cache/python.tar.gz"
    source = RuntimeSource(
        "macos-arm64",
        "python",
        "3.11.16+20260924",
        source_path.as_uri(),
        hashlib.sha256(content).hexdigest(),
        len(content),
        "python",
    )

    assert download_verified(source, destination) == destination
    assert destination.read_bytes() == content


def test_download_verified_rejects_bad_digest_and_existing_target(tmp_path: Path) -> None:
    """错误摘要或已有目标都不得被覆盖为可信缓存。"""
    from tools.runtime_sources import RuntimeSource, download_verified

    source_path = tmp_path / "source.tar.gz"
    source_path.write_bytes(b"changed")
    source = RuntimeSource(
        "windows-x64", "python", "3.11.16+20260924",
        source_path.as_uri(), "0" * 64, 7, "python",
    )
    destination = tmp_path / "download.tar.gz"

    with pytest.raises(ValueError, match="SHA-256"):
        download_verified(source, destination)
    destination.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        download_verified(source, destination)


def _write_tar(path: Path, members: list[tuple[tarfile.TarInfo, bytes]]) -> None:
    """写测试 tar；参数为目标路径和成员/内容列表。"""
    with tarfile.open(path, "w:gz") as archive:
        for member, content in members:
            archive.addfile(member, io.BytesIO(content) if member.isfile() else None)


def test_extract_verified_archive_preserves_internal_symlink(tmp_path: Path) -> None:
    """安全解压必须保留归档根内的合法 Python 符号链接。"""
    from tools.runtime_sources import extract_verified_archive

    executable = tarfile.TarInfo("python/bin/python3.11")
    executable.size = 6
    executable.mode = 0o755
    link = tarfile.TarInfo("python/bin/python3")
    link.type = tarfile.SYMTYPE
    link.linkname = "python3.11"
    archive = tmp_path / "python.tar.gz"
    _write_tar(archive, [(executable, b"python"), (link, b"")])

    extracted = extract_verified_archive(archive, tmp_path / "runtime", archive_root="python")

    assert extracted == tmp_path / "runtime/python"
    assert (extracted / "bin/python3").is_symlink()
    assert (extracted / "bin/python3").readlink() == Path("python3.11")


@pytest.mark.parametrize(
    ("member_name", "link_name"),
    [("../outside", None), ("/absolute", None), ("python/link", "../../outside")],
)
def test_extract_verified_archive_rejects_path_escape(
    tmp_path: Path,
    member_name: str,
    link_name: str | None,
) -> None:
    """成员路径或符号链接不得越过解压目录。"""
    from tools.runtime_sources import extract_verified_archive

    member = tarfile.TarInfo(member_name)
    if link_name is None:
        member.size = 1
        content = b"x"
    else:
        member.type = tarfile.SYMTYPE
        member.linkname = link_name
        content = b""
    archive = tmp_path / "malicious.tar.gz"
    _write_tar(archive, [(member, content)])

    with pytest.raises(ValueError, match="不安全"):
        extract_verified_archive(archive, tmp_path / "runtime", archive_root="python")


def test_runtime_source_loader_rejects_unknown_schema(tmp_path: Path) -> None:
    """来源清单版本不受支持时必须拒绝构建。"""
    from tools.runtime_sources import load_runtime_sources

    manifest = tmp_path / "sources.json"
    manifest.write_text(json.dumps({"schema_version": "9.9", "platforms": {}}))

    with pytest.raises(ValueError, match="版本"):
        load_runtime_sources(manifest)
