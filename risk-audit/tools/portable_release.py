"""组装 Windows 和 macOS 脚本启动的便携发布目录。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import zipfile

from packaging.requirements import InvalidRequirement, Requirement

from risk_audit_web.region_profiles import (
    RegionProfileError,
    load_region_catalog,
    verify_rulepack_release,
)


RELEASE_NAME = "风控矩阵审核器-v2.2.0"
PLATFORM_LAUNCHERS = {
    "windows-x64": "启动审核器.bat",
    "macos-arm64": "启动审核器.command",
}
WINDOWS_VC_RUNTIME_REQUIRED_DLLS = (
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
)


class BuildError(RuntimeError):
    """表示发布输入、目标平台或组装过程无效。"""


@dataclass(frozen=True)
class BuildInputs:
    """定义一次平台原生便携发布的全部输入。"""

    platform_id: str
    project_root: Path
    python_root: Path
    libreoffice_root: Path
    licenses_root: Path
    output_root: Path
    usage_guide: Path
    vc_runtime_root: Path | None = None


def _copy_tree(source: Path, destination: Path, *, ignored_names: set[str] | None = None) -> None:
    """复制目录并过滤开发产物；source/destination 为源和目标。"""
    ignored = ignored_names or set()

    def ignore(directory: str, names: list[str]) -> set[str]:
        """返回当前目录应忽略的名称；directory/names 由 copytree 传入。"""
        del directory
        return {
            name
            for name in names
            if name in ignored
            or name in {".DS_Store", "__pycache__", ".git", ".pytest_cache", ".venv", "tests"}
            or name.endswith((".pyc", ".pyo", ".whl"))
        }

    if not source.is_dir():
        raise BuildError(f"必需目录缺失：{source}")
    shutil.copytree(source, destination, symlinks=True, ignore=ignore)


def _load_region_index(index_path: Path) -> dict[str, object]:
    """读取待发布的地区索引根对象。

    Args:
        index_path: 仓库中的地区索引路径。
    """

    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildError(f"地区资源索引无法读取：{index_path}；{error}") from error
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "1.0"
        or not isinstance(payload.get("regions"), list)
        or not payload["regions"]
    ):
        raise BuildError(f"地区资源索引格式无效：{index_path}")
    return payload


def _existing_region_source(
    project_root: Path,
    auditor_root: Path,
    relative_path: str,
    region_code: str,
) -> Path | None:
    """定位地区索引引用的现有仓库资源。

    Args:
        project_root: 仓库根目录。
        auditor_root: risk-audit 产品目录。
        relative_path: 相对 runtime/resources 的 POSIX 路径。
        region_code: 稳定地区代码。
    """

    relative = Path(*relative_path.split("/"))
    candidates = [auditor_root / relative, project_root / relative]
    if region_code == "hunan":
        if relative_path.startswith("entities/hunan/"):
            candidates.append(project_root / "reference-data" / relative.name)
        elif relative_path == "baselines/hunan/templates":
            candidates.append(project_root / "templates")
        elif relative_path == "configs/hunan/audit-config.json":
            candidates.append(project_root / "audit-config.json")
    return next((path for path in candidates if path.exists()), None)


def _copy_optional_region_resource(source: Path | None, destination: Path) -> None:
    """复制已提供的地区业务资源，缺失时保持该地区未就绪。

    Args:
        source: 已定位的文件或目录，未提供时为空。
        destination: 便携发布中的目标路径。
    """

    if source is None:
        return
    if source.is_dir():
        _copy_tree(source, destination)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_project_sources(auditor_root: Path, distribution_root: Path) -> None:
    """复制两个产品包；auditor_root/distribution_root 为项目与发布根。"""
    source_root = auditor_root / "src"
    _copy_tree(source_root / "risk_audit", distribution_root / "app/risk_audit")
    # 前端资源有独立的 runtime 副本，不在 Python 源码树重复发布。
    _copy_tree(
        source_root / "risk_audit_web",
        distribution_root / "app/risk_audit_web",
        ignored_names={"static"},
    )
    _copy_tree(source_root / "risk_audit_web/static", distribution_root / "runtime/web")


def _copy_runtime_resources(project_root: Path, auditor_root: Path, distribution_root: Path) -> None:
    """按地区索引复制规则包及已提供的隔离业务资源。

    Args:
        project_root: 仓库根目录。
        auditor_root: risk-audit 产品目录。
        distribution_root: 便携发布根目录。
    """

    source_rulepacks = auditor_root / "rulepacks"
    source_index = auditor_root / "regions/index.json"
    index = _load_region_index(source_index)
    resources_root = distribution_root / "runtime/resources"
    target_index = resources_root / "regions/index.json"
    target_index.parent.mkdir(parents=True)
    shutil.copy2(source_index, target_index)

    try:
        # 复制任何索引资源前先执行严格字段和路径边界校验。
        source_catalog = load_region_catalog(resources_root)
    except RegionProfileError as error:
        raise BuildError(f"发布地区资源索引无效：{error}") from error

    copied_versions: set[str] = set()
    for profile in source_catalog.profiles.values():
        source_release = source_rulepacks / "releases" / profile.rulepack_version
        reasons = verify_rulepack_release(
            source_release,
            profile.rulepack_version,
            profile.rulepack_content_hash,
            profile.region_code,
            profile.region_name,
        )
        if reasons:
            raise BuildError(f"地区 {profile.region_code} 规则包不可发布：{'|'.join(reasons)}")
        if profile.rulepack_version not in copied_versions:
            _copy_tree(source_release, profile.rulepack)
            copied_versions.add(profile.rulepack_version)

        for destination in (profile.entity_file, profile.baseline_root, profile.config_file):
            relative_path = destination.relative_to(resources_root).as_posix()
            source = _existing_region_source(
                project_root,
                auditor_root,
                relative_path,
                profile.region_code,
            )
            _copy_optional_region_resource(source, destination)

    try:
        catalog = load_region_catalog(resources_root)
    except RegionProfileError as error:
        raise BuildError(f"发布地区资源索引无效：{error}") from error
    if not any(profile.available for profile in catalog.profiles.values()):
        raise BuildError("发布包中没有可用地区")


def _locked_libreoffice_version(inputs: BuildInputs) -> str:
    """读取目标平台 LibreOffice 版本；inputs 为构建输入。"""
    source_manifest = inputs.project_root / "risk-audit/packaging/runtime-sources.json"
    try:
        payload = json.loads(source_manifest.read_text(encoding="utf-8"))
        version = payload["platforms"][inputs.platform_id]["libreoffice"]["version"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise BuildError(f"LibreOffice 来源版本无法读取：{source_manifest}") from error
    if not isinstance(version, str) or not version:
        raise BuildError(f"LibreOffice 来源版本无效：{source_manifest}")
    return version


def _copy_macos_libreoffice(source: Path, destination: Path, expected_version: str) -> None:
    """保真复制 macOS LibreOffice；参数为应用包路径和锁定版本。"""
    soffice = source / "Contents/MacOS/soffice"
    if not soffice.is_file() or not os.access(soffice, os.X_OK):
        raise BuildError(f"macOS LibreOffice 可执行文件缺失：{soffice}")
    info_path = source / "Contents/Info.plist"
    try:
        info = plistlib.loads(info_path.read_bytes())
        actual_version = info["CFBundleShortVersionString"]
    except (OSError, plistlib.InvalidFileException, KeyError, TypeError) as error:
        raise BuildError(f"macOS LibreOffice 版本无法读取：{info_path}") from error
    if actual_version != expected_version:
        raise BuildError(
            f"macOS LibreOffice 版本不一致：{actual_version} != {expected_version}"
        )
    architecture = subprocess.run(
        ["/usr/bin/lipo", "-archs", str(soffice)],
        check=True,
        shell=False,
        capture_output=True,
        text=True,
    ).stdout.split()
    if "arm64" not in architecture:
        raise BuildError("macOS LibreOffice 不包含 arm64 架构")
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(source)],
        check=True,
        shell=False,
    )
    destination.parent.mkdir(parents=True)
    # 不复制安装来源的 Gatekeeper、ACL 等元数据；应用内容及代码签名保持不变。
    subprocess.run(
        [
            "/usr/bin/ditto",
            "--noextattr",
            "--noqtn",
            "--noacl",
            str(source),
            str(destination),
        ],
        check=True,
        shell=False,
    )
    # 复制后再验证完整签名，防止组装过程改写应用包。
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(destination)],
        check=True,
        shell=False,
    )


def _windows_pe_architecture(path: Path) -> str | None:
    """读取 Windows PE 文件架构。

    参数 path 为待检查的 EXE 或 DLL，返回架构名；无效 PE 返回 None。
    """
    try:
        content = path.read_bytes()
        if content[:2] != b"MZ" or len(content) < 0x40:
            return None
        pe_offset = int.from_bytes(content[0x3C:0x40], "little")
        if content[pe_offset:pe_offset + 4] != b"PE\0\0":
            return None
        machine = int.from_bytes(content[pe_offset + 4:pe_offset + 6], "little")
    except OSError:
        return None
    return {0x8664: "x86_64", 0xAA64: "arm64", 0x014C: "x86"}.get(machine, "unknown")


def _copy_windows_vc_runtime(source: Path | None, destination: Path) -> None:
    """复制 Windows x64 VC++ 应用本地运行库。

    参数 source 为 Microsoft.VC143.CRT x64 目录，destination 为
    LibreOffice program 目录；函数校验后复制全部 DLL。
    """
    if source is None or not source.is_dir():
        raise BuildError(f"Windows VC++ x64 运行库目录缺失：{source}")
    dll_paths = sorted(source.glob("*.dll"), key=lambda item: item.name.lower())
    dll_by_name = {path.name.lower(): path for path in dll_paths}
    for dll_name in WINDOWS_VC_RUNTIME_REQUIRED_DLLS:
        if dll_name not in dll_by_name:
            raise BuildError(f"Windows VC++ x64 运行库 {dll_name} 缺失：{source}")
    for path in dll_paths:
        architecture = _windows_pe_architecture(path)
        if architecture != "x86_64":
            raise BuildError(
                f"Windows VC++ 运行库 {path.name} 不是 x64：{architecture or '无效 PE'}"
            )
        # LibreOffice 必须从自身 program 目录加载 CRT，不依赖目标电脑 System32。
        shutil.copy2(path, destination / path.name)


def _copy_platform_runtime(inputs: BuildInputs, distribution_root: Path) -> None:
    """复制目标 Python 和 LibreOffice；inputs 为构建输入。"""
    _copy_tree(inputs.python_root, distribution_root / "runtime/python")
    if inputs.platform_id == "windows-x64":
        if not (inputs.libreoffice_root / "program/soffice.com").is_file():
            raise BuildError(f"Windows LibreOffice 命令行入口 soffice.com 缺失：{inputs.libreoffice_root}")
        libreoffice_target = distribution_root / "runtime/libreoffice"
        _copy_tree(inputs.libreoffice_root, libreoffice_target)
        _copy_windows_vc_runtime(
            inputs.vc_runtime_root,
            libreoffice_target / "program",
        )
    elif inputs.platform_id == "macos-arm64":
        _copy_macos_libreoffice(
            inputs.libreoffice_root,
            distribution_root / "runtime/libreoffice/LibreOffice.app",
            _locked_libreoffice_version(inputs),
        )
    else:
        raise BuildError(f"不支持的平台：{inputs.platform_id}")


def _immutable_files(distribution_root: Path) -> list[Path]:
    """列出应进入发布清单的文件；distribution_root 为发布根。"""
    manifest_path = distribution_root / "runtime/manifest.json"
    files: list[Path] = []
    for root_name in ("app", "runtime"):
        root = distribution_root / root_name
        if not root.is_dir():
            continue
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file() and path != manifest_path
        )
    for name in (*PLATFORM_LAUNCHERS.values(), "使用说明.md"):
        path = distribution_root / name
        if path.is_file():
            files.append(path)
    return sorted(set(files), key=lambda item: item.relative_to(distribution_root).as_posix())


def build_release_manifest(distribution_root: Path) -> dict[str, object]:
    """生成不可变载荷清单；distribution_root 为完整发布根。"""
    resources = []
    for path in _immutable_files(distribution_root):
        content = path.read_bytes()
        resources.append({
            "path": path.relative_to(distribution_root).as_posix(),
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    return {"schema_version": "1.0", "app_version": "2.2.0", "resources": resources}


def _remove_runtime_caches(python_root: Path) -> None:
    """删除目标 Python 的缓存和 wheel；python_root 为运行时根。"""
    for path in sorted(python_root.rglob("__pycache__"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            shutil.rmtree(path)
    for pattern in ("*.pyc", "*.pyo", "*.whl"):
        for path in python_root.rglob(pattern):
            if path.is_file():
                path.unlink()
    for name in (".cache", "pip-cache", "wheels"):
        cache_root = python_root / name
        if cache_root.is_dir():
            shutil.rmtree(cache_root)


def install_runtime_dependencies(python_executable: Path, lock_file: Path) -> None:
    """向目标 Python 安装锁定依赖；参数为 Python 和哈希锁。"""
    if not python_executable.is_file():
        raise BuildError(f"目标 Python 缺失：{python_executable}")
    if not lock_file.is_file():
        raise BuildError(f"运行依赖锁缺失：{lock_file}")
    subprocess.run(
        [
            str(python_executable), "-I", "-m", "pip", "install", "--require-hashes",
            "--only-binary=:all:", "--no-deps", "-r", str(lock_file),
        ],
        check=True,
        shell=False,
    )
    python_root = python_executable.parent.parent if python_executable.parent.name == "bin" else python_executable.parent
    _remove_runtime_caches(python_root)


def locked_distribution_names(
    lock_file: Path,
    *,
    marker_environment: Mapping[str, str] | None = None,
) -> list[str]:
    """读取当前目标平台的锁定发行名；参数为锁和 PEP 508 环境。"""
    names: list[str] = []
    for line_number, raw_line in enumerate(lock_file.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line or raw_line[0].isspace() or raw_line.startswith("#"):
            continue
        statement = raw_line.removesuffix("\\").strip()
        try:
            requirement = Requirement(statement)
        except InvalidRequirement as error:
            raise BuildError(f"锁文件第 {line_number} 行无效：{statement}") from error
        if requirement.marker is not None and not requirement.marker.evaluate(
            environment=dict(marker_environment) if marker_environment is not None else None,
        ):
            continue
        names.append(requirement.name)
    if not names:
        raise BuildError(f"锁文件没有发行版：{lock_file}")
    return names


def validate_native_platform(platform_id: str) -> None:
    """确认构建机与目标一致；platform_id 为发布平台。"""
    if platform_id == "windows-x64":
        if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
            raise BuildError("Windows x64 便携包必须在 Windows x64 上构建")
        return
    if platform_id == "macos-arm64":
        if sys.platform != "darwin" or platform.machine().lower() != "arm64":
            raise BuildError("macOS arm64 便携包必须在 Apple Silicon Mac 上构建")
        return
    raise BuildError(f"不支持的平台：{platform_id}")


def assemble_distribution(inputs: BuildInputs) -> Path:
    """组装完整发布目录；inputs 为已准备的本机平台资源。"""
    if inputs.platform_id not in PLATFORM_LAUNCHERS:
        raise BuildError(f"不支持的平台：{inputs.platform_id}")
    if inputs.output_root.exists():
        raise BuildError(f"发布目标已存在，拒绝覆盖：{inputs.output_root}")
    auditor_root = inputs.project_root / "risk-audit"
    launcher_name = PLATFORM_LAUNCHERS[inputs.platform_id]
    launcher_source = auditor_root / "packaging" / launcher_name
    if not launcher_source.is_file():
        raise BuildError(f"平台启动器缺失：{launcher_source}")
    if not inputs.usage_guide.is_file():
        raise BuildError(f"使用说明缺失：{inputs.usage_guide}")
    if not inputs.licenses_root.is_dir():
        raise BuildError(f"许可证目录缺失：{inputs.licenses_root}")

    inputs.output_root.mkdir(parents=True)
    try:
        _copy_project_sources(auditor_root, inputs.output_root)
        _copy_platform_runtime(inputs, inputs.output_root)
        _copy_runtime_resources(inputs.project_root, auditor_root, inputs.output_root)
        _copy_tree(inputs.licenses_root, inputs.output_root / "runtime/licenses")
        launcher_target = inputs.output_root / launcher_name
        shutil.copy2(launcher_source, launcher_target)
        if inputs.platform_id == "macos-arm64":
            # `.command` 必须可直接双击执行，不依赖源码仓库中的权限位。
            launcher_target.chmod(launcher_target.stat().st_mode | 0o111)
        shutil.copy2(inputs.usage_guide, inputs.output_root / "使用说明.md")
        (inputs.output_root / "data").mkdir()
        (inputs.output_root / "outputs").mkdir()
        manifest_path = inputs.output_root / "runtime/manifest.json"
        # 清单最后写入，保证覆盖所有已组装的不可变载荷。
        manifest_path.write_text(
            json.dumps(build_release_manifest(inputs.output_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return inputs.output_root
    except BaseException:
        shutil.rmtree(inputs.output_root, ignore_errors=True)
        raise


def _write_windows_archive(distribution_root: Path, archive_path: Path) -> None:
    """写入可重现 Windows ZIP；参数为发布根和归档路径。"""
    prefix = f"{distribution_root.name}/"
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as handle:
        for directory_name in ("data", "outputs"):
            info = zipfile.ZipInfo(f"{prefix}{directory_name}/", date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (0o40755 & 0xFFFF) << 16
            handle.writestr(info, b"")
        for path in sorted(distribution_root.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            relative_path = path.relative_to(distribution_root).as_posix()
            info = zipfile.ZipInfo(f"{prefix}{relative_path}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
            handle.writestr(info, path.read_bytes())


def create_distribution_archive(distribution_root: Path, platform_id: str) -> Path:
    """创建并往返验证发布 ZIP；参数为发布根和平台。"""
    from tools.verify_portable_distribution import verify_archive, verify_distribution

    suffixes = {"windows-x64": "Windows-x64", "macos-arm64": "macOS-arm64"}
    if platform_id not in suffixes:
        raise BuildError(f"不支持的平台：{platform_id}")
    errors = verify_distribution(distribution_root, platform_id)
    if errors:
        raise BuildError("发布目录验证失败：\n" + "\n".join(errors))
    archive_path = distribution_root.parent / f"{distribution_root.name}-{suffixes[platform_id]}.zip"
    if archive_path.exists():
        raise BuildError(f"发布归档已存在，拒绝覆盖：{archive_path}")
    try:
        if platform_id == "macos-arm64":
            subprocess.run(
                [
                    "/usr/bin/ditto", "-c", "-k", "--keepParent",
                    str(distribution_root), str(archive_path),
                ],
                check=True,
                shell=False,
            )
        else:
            _write_windows_archive(distribution_root, archive_path)
        archive_errors = verify_archive(archive_path, platform_id)
        if archive_errors:
            raise BuildError("发布归档验证失败：\n" + "\n".join(archive_errors))
        return archive_path
    except BaseException:
        archive_path.unlink(missing_ok=True)
        raise
