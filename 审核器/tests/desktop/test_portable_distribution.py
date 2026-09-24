"""验证 Windows 便携包资源收集、清单和交付检查。"""

import hashlib
import json
from pathlib import Path
import zipfile

import pytest


def write_file(path: Path, content: bytes) -> Path:
    """创建测试文件；path 为路径，content 为字节内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def make_sources(tmp_path: Path):
    """创建最小发布资源；tmp_path 为测试根目录。"""
    from tools.build_windows_desktop import DistributionSources

    rulepacks = tmp_path / "source/rulepacks"
    write_file(rulepacks / "active.json", b'{"version":"2.0.0"}')
    write_file(rulepacks / "releases/2.0.0/rules/R01.json", b'{"id":"R01"}')
    config = write_file(tmp_path / "source/audit-config.json", b'{"mode":"desktop"}')
    baselines = tmp_path / "source/审核"
    write_file(baselines / "基准.xlsx", b"baseline")
    entity = write_file(tmp_path / "source/会计主体清单20260907.xlsx", b"entity")
    libreoffice = tmp_path / "source/libreoffice"
    write_file(libreoffice / "program/soffice.exe", b"exe")
    write_file(libreoffice / "program/fundamental.ini", b"ini")
    licenses = tmp_path / "source/licenses"
    write_file(licenses / "LibreOffice.txt", b"license")
    return DistributionSources(
        rulepacks_root=rulepacks,
        config_file=config,
        baselines_root=baselines,
        entity_file=entity,
        libreoffice_root=libreoffice,
        licenses_root=licenses,
    )


def test_collect_runtime_copies_only_active_release_and_writes_exact_manifest(tmp_path: Path) -> None:
    """收集器必须保留目标结构，并为每个资源记录精确摘要。"""
    from tools.build_windows_desktop import collect_runtime

    sources = make_sources(tmp_path)
    distribution_root = tmp_path / "dist/风控矩阵审核器-v2.0.0"

    manifest_path = collect_runtime(sources, distribution_root)

    runtime = distribution_root / "runtime"
    assert (runtime / "libreoffice/program/soffice.exe").read_bytes() == b"exe"
    assert (runtime / "resources/rulepacks/active.json").is_file()
    assert (runtime / "resources/rulepacks/releases/2.0.0/rules/R01.json").is_file()
    assert (runtime / "resources/rulepacks/audit-config.json").read_bytes() == b'{"mode":"desktop"}'
    assert (runtime / "resources/baselines/审核/基准.xlsx").is_file()
    assert (runtime / "resources/entities/会计主体清单20260907.xlsx").is_file()
    assert not (runtime / "resources/models").exists()
    assert not (runtime / "resources/rulepacks/drafts").exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = {entry["path"]: entry for entry in manifest["resources"]}
    expected_files = [path for path in runtime.rglob("*") if path.is_file() and path != manifest_path]
    assert set(entries) == {path.relative_to(runtime).as_posix() for path in expected_files}
    for path in expected_files:
        relative = path.relative_to(runtime).as_posix()
        assert entries[relative]["size"] == path.stat().st_size
        assert entries[relative]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert "\\" not in relative


def test_collect_runtime_succeeds_without_removed_semantic_model(tmp_path: Path) -> None:
    """本地语义模型已停用并删除时，runtime 收集仍必须成功。"""
    from tools.build_windows_desktop import collect_runtime

    sources = make_sources(tmp_path)
    distribution_root = tmp_path / "dist/风控矩阵审核器-v2.0.0"

    manifest_path = collect_runtime(sources, distribution_root)

    assert manifest_path.is_file()
    assert not (distribution_root / "runtime/resources/models").exists()


@pytest.mark.parametrize("missing", ["libreoffice", "licenses"])
def test_missing_soffice_or_license_blocks_build_without_zip(tmp_path: Path, missing: str) -> None:
    """缺 LibreOffice 或许可证必须阻止构建；missing 指定缺失项。"""
    from tools.build_windows_desktop import BuildError, build_distribution

    sources = make_sources(tmp_path)
    if missing == "libreoffice":
        (sources.libreoffice_root / "program/soffice.exe").unlink()
    else:
        (sources.licenses_root / "LibreOffice.txt").unlink()
    distribution_root = tmp_path / "dist/风控矩阵审核器-v2.0.0"

    with pytest.raises(BuildError) as raised:
        build_distribution(
            sources,
            distribution_root,
            executable_source=tmp_path / "风控矩阵审核器.exe",
            create_zip=True,
        )

    assert missing in str(raised.value).lower() or ("soffice.exe" if missing == "libreoffice" else "LibreOffice.txt") in str(raised.value)
    assert not list(distribution_root.parent.glob("*.zip"))


def test_pyinstaller_command_is_windowed_onefile_and_never_uses_shell() -> None:
    """构建命令必须产生无控制台单 EXE 参数列表。"""
    from tools.build_windows_desktop import pyinstaller_command

    command = pyinstaller_command(Path("python.exe"), Path("app.py"), Path("app.ico"))

    assert command[:3] == ["python.exe", "-m", "PyInstaller"]
    assert "--onefile" in command and "--windowed" in command
    assert "--add-data" in command
    assert command[-1] == "app.py"


def test_complete_distribution_uses_windows_x64_archive_name(tmp_path: Path) -> None:
    """完整发布包必须生成明确标注 Windows x64 的 ZIP。"""
    from tools.build_windows_desktop import build_distribution
    from tools.verify_portable_distribution import verify_distribution, verify_zip

    sources = make_sources(tmp_path)
    executable = write_file(tmp_path / "build/风控矩阵审核器.exe", b"exe")
    guide = write_file(tmp_path / "build/Windows桌面版使用说明.md", b"guide")
    distribution_root = tmp_path / "dist/风控矩阵审核器-v2.0.0"

    archive = build_distribution(
        sources,
        distribution_root,
        executable_source=executable,
        usage_guide=guide,
    )

    assert archive == tmp_path / "dist/风控矩阵审核器-v2.0.0-Windows-x64.zip"
    assert archive.is_file()
    assert verify_distribution(distribution_root) == []
    assert verify_zip(archive) == []
    with zipfile.ZipFile(archive) as handle:
        names = set(handle.namelist())
    assert f"{distribution_root.name}/data/" in names
    assert f"{distribution_root.name}/outputs/" in names


def test_verify_distribution_rejects_development_artifacts_and_bad_zip(tmp_path: Path) -> None:
    """交付验证必须拒绝缓存、开发机路径和多顶层 ZIP。"""
    from tools.verify_portable_distribution import verify_distribution, verify_zip

    root = tmp_path / "风控矩阵审核器-v2.0.0"
    write_file(root / "风控矩阵审核器.exe", b"exe")
    write_file(root / "runtime/manifest.json", b'{"schema_version":"1.0","resources":[]}')
    write_file(root / "runtime/leak.txt", b"/Users/developer/project")
    write_file(root / "runtime/__pycache__/bad.pyc", b"cache")
    errors = verify_distribution(root)
    assert any("__pycache__" in error for error in errors)
    assert any("开发机" in error for error in errors)

    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("one/file.txt", "1")
        handle.writestr("two/file.txt", "2")
    assert any("顶层目录" in error for error in verify_zip(archive))
