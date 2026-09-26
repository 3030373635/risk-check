"""使用本机平台运行时构建脚本启动的便携发布目录。"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

# 启动脚本按文件路径直接执行本模块，因此先加入工具包和产品源码路径。
if __package__ in (None, ""):
    auditor_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(auditor_root))
    sys.path.insert(0, str(auditor_root / "src"))

from tools.collect_python_licenses import LicenseCollectionError, collect_distribution_licenses
from tools.portable_release import (
    BuildError,
    BuildInputs,
    RELEASE_NAME,
    assemble_distribution,
    create_distribution_archive,
    install_runtime_dependencies,
    locked_distribution_names,
    validate_native_platform,
)
from tools.runtime_sources import download_verified, extract_verified_archive, load_runtime_sources


def target_python_executable(platform_id: str, python_root: Path) -> Path:
    """返回目标 Python 路径；platform_id/python_root 为平台和运行时根。"""
    if platform_id == "windows-x64":
        return python_root / "python.exe"
    if platform_id == "macos-arm64":
        return python_root / "bin/python3"
    raise BuildError(f"不支持的平台：{platform_id}")


def prepare_locked_python_runtime(
    auditor_root: Path,
    platform_id: str,
    working_root: Path,
    *,
    archive_override: Path | None = None,
) -> Path:
    """下载、校验并解压目标 Python；参数为项目、平台、临时根和可选归档。"""
    sources = load_runtime_sources(auditor_root / "packaging/runtime-sources.json")
    try:
        source = sources[platform_id]["python"]
    except KeyError as error:
        raise BuildError(f"目标 Python 来源缺失：{platform_id}") from error
    if archive_override is not None:
        if not archive_override.is_file():
            raise BuildError(f"目标 Python 归档缺失：{archive_override}")
        # 本地归档仍通过同一锁定大小和摘要校验，不因离线构建降级。
        source = replace(source, url=archive_override.resolve().as_uri())
    downloaded = download_verified(source, working_root / "python-runtime.tar.gz")
    return extract_verified_archive(
        downloaded,
        working_root / "python-extracted",
        archive_root=source.archive_root,
    )


def _prepare_static_licenses(inputs: BuildInputs, destination: Path) -> None:
    """收集非 Python 许可证；inputs/destination 为构建输入和临时目录。"""
    destination.mkdir(parents=True)
    for path in sorted(inputs.licenses_root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file():
            target = destination / path.name
            if target.exists() and target.read_bytes() != path.read_bytes():
                raise BuildError(f"静态许可证同名冲突：{path.name}")
            shutil.copy2(path, target)
    if (destination / "LibreOffice.txt").is_file():
        return
    candidates = (
        inputs.libreoffice_root / "readmes/readme_en-US.txt",
        inputs.libreoffice_root / "Contents/Resources/readmes/readme_en-US.txt",
        inputs.libreoffice_root / "Contents/Resources/LICENSE",
    )
    license_source = next((path for path in candidates if path.is_file()), None)
    if license_source is None:
        raise BuildError(f"LibreOffice 许可证缺失：{inputs.libreoffice_root}")
    shutil.copy2(license_source, destination / "LibreOffice.txt")


def build_portable_release(inputs: BuildInputs, lock_file: Path) -> Path:
    """安装依赖、收集许可证并组装发布；参数为输入和运行锁。"""
    validate_native_platform(inputs.platform_id)
    python_executable = target_python_executable(inputs.platform_id, inputs.python_root)
    install_runtime_dependencies(python_executable, lock_file)
    names = locked_distribution_names(lock_file)
    with tempfile.TemporaryDirectory(prefix="risk-audit-licenses-") as temporary:
        combined_licenses = Path(temporary) / "licenses"
        _prepare_static_licenses(inputs, combined_licenses)
        collect_distribution_licenses(python_executable, names, combined_licenses)
        combined_inputs = BuildInputs(
            platform_id=inputs.platform_id,
            project_root=inputs.project_root,
            python_root=inputs.python_root,
            libreoffice_root=inputs.libreoffice_root,
            licenses_root=combined_licenses,
            output_root=inputs.output_root,
            usage_guide=inputs.usage_guide,
        )
        return assemble_distribution(combined_inputs)


def main(argv: list[str] | None = None) -> int:
    """执行通用便携构建；argv 为可选命令行参数，返回退出码。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("windows-x64", "macos-arm64"), required=True)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--python-archive", type=Path)
    parser.add_argument("--libreoffice", type=Path, required=True)
    parser.add_argument("--licenses", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--usage-guide", type=Path, required=True)
    arguments = parser.parse_args(argv)
    project_root = arguments.project_root.resolve()
    auditor_root = project_root / "risk-audit"
    output_root = (arguments.output or project_root / "dist" / RELEASE_NAME).resolve()
    try:
        with tempfile.TemporaryDirectory(prefix="risk-audit-python-") as temporary:
            python_root = prepare_locked_python_runtime(
                auditor_root,
                arguments.platform,
                Path(temporary),
                archive_override=arguments.python_archive,
            )
            inputs = BuildInputs(
                platform_id=arguments.platform,
                project_root=project_root,
                python_root=python_root,
                libreoffice_root=arguments.libreoffice.resolve(),
                licenses_root=(arguments.licenses or auditor_root / "licenses").resolve(),
                output_root=output_root,
                usage_guide=arguments.usage_guide.resolve(),
            )
            result = build_portable_release(inputs, auditor_root / "requirements-runtime.lock")
            archive = create_distribution_archive(result, arguments.platform)
    except (BuildError, LicenseCollectionError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"构建失败：{error}", file=sys.stderr)
        return 1
    print(f"构建完成：{archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
