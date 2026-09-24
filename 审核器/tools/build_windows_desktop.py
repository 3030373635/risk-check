"""构建风控矩阵审核器 Windows x64 便携发布包。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


APP_NAME = "风控矩阵审核器"
RELEASE_NAME = f"{APP_NAME}-v2.0.0"


class BuildError(RuntimeError):
    """表示发布资源不完整或构建过程失败。"""


@dataclass(frozen=True)
class DistributionSources:
    """列出发布包所需的所有外部资源。"""

    rulepacks_root: Path
    config_file: Path
    baselines_root: Path
    entity_file: Path
    model_root: Path
    libreoffice_root: Path
    licenses_root: Path


def _copy_tree(source: Path, destination: Path) -> None:
    """复制资源树并排除开发缓存；参数为源与目标。"""
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=False,
        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__", "*.pyc", ".git"),
    )


def _active_version(rulepacks_root: Path) -> str:
    """读取活动规则版本；rulepacks_root 为规则包根目录。"""
    try:
        payload = json.loads((rulepacks_root / "active.json").read_text(encoding="utf-8"))
        version = payload["version"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise BuildError(f"活动规则配置无法读取：{rulepacks_root / 'active.json'}；{error}") from error
    if not isinstance(version, str) or not version:
        raise BuildError(f"活动规则版本无效：{rulepacks_root / 'active.json'}")
    return version


def validate_sources(sources: DistributionSources) -> list[str]:
    """检查必需资源；sources 为源路径集合，返回错误列表。"""
    required_files = [
        sources.rulepacks_root / "active.json",
        sources.config_file,
        sources.entity_file,
        sources.libreoffice_root / "program/soffice.exe",
        sources.licenses_root / "LibreOffice.txt",
    ]
    required_directories = [
        sources.baselines_root,
        sources.model_root,
        sources.libreoffice_root,
        sources.licenses_root,
    ]
    errors = [f"必需文件缺失：{path}" for path in required_files if not path.is_file()]
    errors.extend(f"必需目录缺失：{path}" for path in required_directories if not path.is_dir())
    if (sources.rulepacks_root / "active.json").is_file():
        try:
            version = _active_version(sources.rulepacks_root)
            release = sources.rulepacks_root / "releases" / version
            if not release.is_dir():
                errors.append(f"活动规则发布目录缺失：{release}")
        except BuildError as error:
            errors.append(str(error))
    return errors


def _manifest(runtime_root: Path) -> dict[str, object]:
    """生成 runtime 全量文件清单；runtime_root 为已收集资源根。"""
    resources = []
    for path in sorted(runtime_root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        resources.append({
            "path": path.relative_to(runtime_root).as_posix(),
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return {"schema_version": "1.0", "resources": resources}


def collect_runtime(sources: DistributionSources, distribution_root: Path) -> Path:
    """收集外部 runtime 并写清单；参数为资源源和发布根。"""
    errors = validate_sources(sources)
    if errors:
        raise BuildError("\n".join(errors))
    runtime_root = distribution_root / "runtime"
    if runtime_root.exists():
        raise BuildError(f"目标 runtime 已存在，拒绝覆盖：{runtime_root}")
    version = _active_version(sources.rulepacks_root)
    rulepacks_target = runtime_root / "resources/rulepacks"
    _copy_tree(sources.libreoffice_root, runtime_root / "libreoffice")
    _copy_tree(sources.rulepacks_root / "releases" / version, rulepacks_target / "releases" / version)
    rulepacks_target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sources.rulepacks_root / "active.json", rulepacks_target / "active.json")
    shutil.copy2(sources.config_file, rulepacks_target / "audit-config.json")
    # 规则包的基准路径以“审核/...”开头，发布包必须保留这一层目录。
    _copy_tree(sources.baselines_root, runtime_root / "resources/baselines/审核")
    entity_target = runtime_root / "resources/entities" / sources.entity_file.name
    entity_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sources.entity_file, entity_target)
    _copy_tree(sources.model_root, runtime_root / "resources/models/bge-small-zh-v1.5")
    _copy_tree(sources.licenses_root, runtime_root / "licenses")
    manifest_path = runtime_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(_manifest(runtime_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def pyinstaller_command(python_executable: Path, app_entry: Path, icon_path: Path) -> list[str]:
    """生成 PyInstaller 参数列表；参数为 Python、入口和图标路径。"""
    return [
        str(python_executable), "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed", "--name", APP_NAME, "--icon", str(icon_path),
        "--add-data", f"{icon_path}{os.pathsep}.",
        "--collect-all", "PySide6", "--paths", str(app_entry.parent.parent), str(app_entry),
    ]


def _create_zip(distribution_root: Path) -> Path:
    """创建唯一顶层目录的 ZIP；distribution_root 为发布目录。"""
    archive = distribution_root.parent / f"{distribution_root.name}-Windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        # 明确写入两个空可写目录，避免解压工具将它们丢失。
        handle.writestr(f"{distribution_root.name}/data/", b"")
        handle.writestr(f"{distribution_root.name}/outputs/", b"")
        for path in sorted(distribution_root.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_file():
                handle.write(path, (Path(distribution_root.name) / path.relative_to(distribution_root)).as_posix())
    return archive


def build_distribution(
    sources: DistributionSources,
    distribution_root: Path,
    *,
    executable_source: Path,
    usage_guide: Path | None = None,
    create_zip: bool = True,
) -> Path | None:
    """组装已生成 EXE 的便携包；参数为资源、目标、EXE、说明和 ZIP 开关。"""
    errors = validate_sources(sources)
    if not executable_source.is_file():
        errors.append(f"必需文件缺失：{executable_source}")
    if usage_guide is not None and not usage_guide.is_file():
        errors.append(f"使用说明缺失：{usage_guide}")
    if errors:
        raise BuildError("\n".join(errors))
    if distribution_root.exists():
        raise BuildError(f"发布目录已存在，拒绝覆盖：{distribution_root}")
    distribution_root.mkdir(parents=True)
    try:
        shutil.copy2(executable_source, distribution_root / f"{APP_NAME}.exe")
        if usage_guide is not None:
            shutil.copy2(usage_guide, distribution_root / usage_guide.name)
        (distribution_root / "data").mkdir()
        (distribution_root / "outputs").mkdir()
        collect_runtime(sources, distribution_root)
        return _create_zip(distribution_root) if create_zip else None
    except BaseException:
        # 失败产物不能伪装成完整发布包。
        shutil.rmtree(distribution_root, ignore_errors=True)
        raise


def _default_sources(project_root: Path, libreoffice_root: Path, licenses_root: Path) -> DistributionSources:
    """按仓库结构生成默认资源；参数为根目录、LibreOffice 和许可证。"""
    auditor_root = project_root / "审核器"
    return DistributionSources(
        rulepacks_root=auditor_root / "rulepacks",
        config_file=project_root / "audit-config.json",
        baselines_root=project_root / "审核",
        entity_file=project_root / "审核/会计主体清单20260907.xlsx",
        model_root=auditor_root / "models/bge-small-zh-v1.5",
        libreoffice_root=libreoffice_root,
        licenses_root=licenses_root,
    )


def main(argv: list[str] | None = None) -> int:
    """执行 Windows 构建；argv 为可选命令行参数，返回退出码。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="项目根目录，默认由脚本位置推导",
    )
    parser.add_argument("--libreoffice", "--libreoffice-root", dest="libreoffice_root", type=Path, required=True)
    parser.add_argument("--licenses", "--licenses-root", dest="licenses_root", type=Path, required=True)
    parser.add_argument("--output", "--dist-root", dest="dist_root", type=Path, default=Path("dist"))
    arguments = parser.parse_args(argv)
    if sys.platform != "win32":
        print("错误：EXE 必须在 Windows x64 上构建。", file=sys.stderr)
        return 2
    project_root = arguments.project_root.resolve()
    auditor_root = project_root / "审核器"
    command = pyinstaller_command(
        Path(sys.executable),
        auditor_root / "src/risk_audit_desktop/app.py",
        auditor_root / "src/risk_audit_desktop/assets/app.ico",
    )
    try:
        subprocess.run(command, cwd=project_root, check=True, shell=False)
        executable = project_root / f"dist/{APP_NAME}.exe"
        distribution_root = arguments.dist_root / RELEASE_NAME
        build_distribution(
            _default_sources(project_root, arguments.libreoffice_root, arguments.licenses_root),
            distribution_root,
            executable_source=executable,
            usage_guide=auditor_root / "Windows桌面版使用说明.md",
        )
    except (BuildError, subprocess.CalledProcessError) as error:
        print(f"构建失败：{error}", file=sys.stderr)
        return 1
    archive = distribution_root.parent / f"{distribution_root.name}-Windows-x64.zip"
    print(f"构建完成：{archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
