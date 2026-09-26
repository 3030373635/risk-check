"""验证 Windows x64 和 macOS arm64 便携发布目录或 ZIP。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import zipfile

# 支持从任意当前目录按文件路径直接执行发布校验器。
if __package__ in (None, ""):
    auditor_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(auditor_root / "src"))

from risk_audit_web.diagnostics import verify_active_rulepack, verify_release_manifest


PLATFORM_LAYOUTS = {
    "windows-x64": {
        "launcher": "启动审核器.bat",
        "python": "runtime/python/python.exe",
        "soffice": "runtime/libreoffice/program/soffice.exe",
        "architecture": "x86_64",
    },
    "macos-arm64": {
        "launcher": "启动审核器.command",
        "python": "runtime/python/bin/python3",
        "soffice": "runtime/libreoffice/LibreOffice.app/Contents/MacOS/soffice",
        "architecture": "arm64",
    },
}
COMMON_ROOT_ITEMS = {"app", "runtime", "data", "outputs", "使用说明.md"}
REQUIRED_WEB_FILES = {
    "index.html",
    "css/bootstrap.min.css",
    "css/bootstrap-icons.min.css",
    "css/theme.css",
    "js/bootstrap.bundle.min.js",
    "js/api.js",
    "js/tasks.js",
    "js/app.js",
    "fonts/bootstrap-icons.woff2",
    "vendor-manifest.json",
}
REQUIRED_LICENSES = {"Python.txt", "LibreOffice.txt", "Bootstrap.txt", "Bootstrap-Icons.txt"}
EXTERNAL_WEB_REFERENCE = re.compile(
    rb"(?:src\s*=\s*[\"']https?://|href\s*=\s*[\"']https?://|url\(\s*[\"']?https?://|fetch\(\s*[\"']https?://)",
    re.IGNORECASE,
)
DEVELOPMENT_PATH = re.compile(rb"(?:/Users/[^/\s]+/|[A-Za-z]:\\+Users\\+[^\\\s]+\\+)")
TEXT_SUFFIXES = {".bat", ".command", ".css", ".html", ".js", ".json", ".md", ".py", ".txt"}


def _verify_vendor_manifest(runtime_root: Path) -> list[str]:
    """验证前端供应商文件；runtime_root 为运行时根。"""
    manifest_path = runtime_root / "web/vendor-manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        assets = payload["assets"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        return [f"Web 资产清单无法读取：{manifest_path}；{error}"]
    if not isinstance(assets, list):
        return [f"Web 资产清单格式无效：{manifest_path}"]
    errors: list[str] = []
    for item in assets:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            errors.append("Web 资产清单条目无效")
            continue
        relative_path = item["path"]
        if relative_path.startswith("src/risk_audit_web/static/"):
            path = runtime_root / "web" / relative_path.removeprefix("src/risk_audit_web/static/")
        elif relative_path.startswith("licenses/web/"):
            path = runtime_root / "licenses" / relative_path.removeprefix("licenses/web/")
        else:
            errors.append(f"Web 资产清单路径无效：{relative_path}")
            continue
        try:
            content = path.read_bytes()
        except OSError:
            errors.append(f"Web 资产缺失：{path.relative_to(runtime_root)}")
            continue
        if len(content) != item.get("size") or hashlib.sha256(content).hexdigest() != item.get("sha256"):
            errors.append(f"Web 资产哈希不一致：{path.relative_to(runtime_root)}")
    return errors


def _verify_web_runtime(runtime_root: Path) -> list[str]:
    """验证离线前端完整且无外网引用；runtime_root 为运行时根。"""
    web_root = runtime_root / "web"
    errors = [
        f"Web 运行资源缺失：{relative_path}"
        for relative_path in sorted(REQUIRED_WEB_FILES)
        if not (web_root / relative_path).is_file()
    ]
    errors.extend(_verify_vendor_manifest(runtime_root))
    for path in web_root.rglob("*") if web_root.is_dir() else ():
        if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js"}:
            continue
        try:
            content = path.read_bytes()
        except OSError as error:
            errors.append(f"Web 文件无法读取：{path.relative_to(runtime_root)}；{error}")
            continue
        if EXTERNAL_WEB_REFERENCE.search(content):
            errors.append(f"Web 文件包含外部资源引用：{path.relative_to(runtime_root)}")
    return errors


def binary_architectures(path: Path) -> set[str]:
    """读取 PE 或 Mach-O 架构；path 为平台可执行文件。"""
    if path.suffix.lower() == ".exe":
        try:
            content = path.read_bytes()
            if content[:2] != b"MZ" or len(content) < 0x40:
                return set()
            offset = int.from_bytes(content[0x3C:0x40], "little")
            if content[offset:offset + 4] != b"PE\0\0":
                return set()
            machine = int.from_bytes(content[offset + 4:offset + 6], "little")
        except OSError:
            return set()
        return {{0x8664: "x86_64", 0xAA64: "arm64"}.get(machine, "unknown")}
    try:
        result = subprocess.run(
            ["/usr/bin/lipo", "-archs", str(path)],
            check=True,
            shell=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return set()
    return set(result.stdout.split())


def _verify_platform_layout(distribution_root: Path, platform_id: str) -> list[str]:
    """验证平台启动器和二进制；参数为发布根和平台。"""
    layout = PLATFORM_LAYOUTS[platform_id]
    launcher = distribution_root / layout["launcher"]
    python_executable = distribution_root / layout["python"]
    soffice = distribution_root / layout["soffice"]
    errors: list[str] = []
    if not launcher.is_file():
        errors.append(f"平台启动器缺失：{layout['launcher']}")
    wrong_launcher = "启动审核器.command" if platform_id == "windows-x64" else "启动审核器.bat"
    if (distribution_root / wrong_launcher).exists():
        errors.append(f"发布包含含错误平台启动器：{wrong_launcher}")
    for label, path in (("Python", python_executable), ("LibreOffice", soffice)):
        if not path.is_file():
            errors.append(f"{label} 可执行文件缺失：{path.relative_to(distribution_root)}")
            continue
        expected = str(layout["architecture"])
        if expected not in binary_architectures(path):
            errors.append(f"{label} 架构不匹配：需要 {expected}；{path.relative_to(distribution_root)}")
        if platform_id == "macos-arm64" and not path.stat().st_mode & 0o111:
            errors.append(f"{label} 缺少可执行权限：{path.relative_to(distribution_root)}")
    if platform_id == "macos-arm64" and launcher.is_file() and not launcher.stat().st_mode & 0o111:
        errors.append(f"平台启动器缺少可执行权限：{launcher.name}")
    return errors


def _verify_launcher(distribution_root: Path, platform_id: str) -> list[str]:
    """验证启动器只使用包内前台 Python；参数为发布根和平台。"""
    launcher_name = str(PLATFORM_LAYOUTS[platform_id]["launcher"])
    launcher = distribution_root / launcher_name
    try:
        source = launcher.read_text(encoding="utf-8")
    except OSError as error:
        return [f"平台启动器无法读取：{launcher_name}；{error}"]
    lower = source.lower()
    errors: list[str] = []
    if "runtime/python" not in lower.replace("\\", "/"):
        errors.append(f"平台启动器未使用包内 Python：{launcher_name}")
    if re.search(r"https?://|\bcurl\b|invoke-webrequest", lower):
        errors.append(f"平台启动器包含网络下载：{launcher_name}")
    if platform_id == "windows-x64":
        if "%~dp0" not in source or re.search(r"(?im)^\s*start\b", source):
            errors.append(f"Windows 启动器未遵守前台脚本契约：{launcher_name}")
    elif "${0:a:h}" not in lower or "exec " not in lower or "nohup" in lower:
        errors.append(f"macOS 启动器未遵守前台脚本契约：{launcher_name}")
    return errors


def _verify_licenses(runtime_root: Path) -> list[str]:
    """验证基础和 Python 发行版许可证；runtime_root 为运行时根。"""
    licenses_root = runtime_root / "licenses"
    errors = [
        f"必需许可证缺失：{name}"
        for name in sorted(REQUIRED_LICENSES)
        if not (licenses_root / name).is_file()
    ]
    package_licenses = [
        path
        for path in licenses_root.glob("*/*")
        if path.is_file()
    ] if licenses_root.is_dir() else []
    if not package_licenses:
        errors.append("Python 发行版许可证缺失")
    return errors


def _verify_api_output_contract(distribution_root: Path) -> list[str]:
    """验证创建任务 API 不接收外部输出根；distribution_root 为发布根。"""
    model_path = distribution_root / "app/risk_audit_web/api_models.py"
    try:
        source = model_path.read_text(encoding="utf-8")
    except OSError as error:
        return [f"API 请求模型无法读取：{model_path}；{error}"]
    match = re.search(
        r"^class\s+TaskCreateRequest\b(?P<body>.*?)(?=^class\s+|\Z)",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        return ["API 缺少 TaskCreateRequest 请求模型"]
    if re.search(r"^\s*output_root\s*[:=]", match.group("body"), flags=re.MULTILINE):
        return ["创建任务 API 不得允许用户指定外部输出根"]
    return []


def _verify_forbidden_payloads(distribution_root: Path) -> list[str]:
    """检查发布中的开发、冻结和 Qt 残留；distribution_root 为发布根。"""
    errors: list[str] = []
    for path in distribution_root.rglob("*"):
        relative_path = path.relative_to(distribution_root)
        relative = relative_path.as_posix()
        relative_lower = relative.lower()
        lower_parts = [part.lower() for part in relative_path.parts]
        first_part = lower_parts[0] if lower_parts else ""
        managed_payload = (
            first_part == "app"
            or relative_lower.startswith("runtime/python/")
            or relative_lower.startswith("runtime/web/")
            or relative_lower.startswith("runtime/resources/")
        )
        owned_text = first_part == "app" or relative_lower.startswith("runtime/web/") or len(lower_parts) == 1

        # PyInstaller 的 `_internal` 只会位于发布根；pip 自身的同名实现属于正常运行时。
        if first_part == "_internal" or (
            first_part == "app" and any("pyinstaller" in part for part in lower_parts)
        ):
            errors.append(f"发布包含 PyInstaller 残留：{relative}")
        if managed_payload and any(part in {"test", "tests"} for part in lower_parts):
            errors.append(f"发布包含测试目录：{relative}")
        if managed_payload and (
            any(part in {".venv", "__pycache__", ".pytest_cache"} for part in lower_parts)
            or path.suffix.lower() == ".pyc"
        ):
            errors.append(f"发布包含开发缓存：{relative}")
        if managed_payload and (
            any("pyside" in part or "qt6" in part for part in lower_parts)
            or path.suffix.lower() == ".qml"
        ):
            errors.append(f"发布包含 Qt 运行文件：{relative}")
        if owned_text and path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            try:
                content = path.read_bytes()
            except OSError as error:
                errors.append(f"文件无法读取：{relative}；{error}")
                continue
            if DEVELOPMENT_PATH.search(content):
                errors.append(f"文件包含开发机绝对路径：{relative}")
    return errors


def verify_distribution(distribution_root: Path, platform_id: str) -> list[str]:
    """验证已解压发布目录；参数为发布根和平台，返回错误列表。"""
    if platform_id not in PLATFORM_LAYOUTS:
        return [f"不支持的发布平台：{platform_id}"]
    if not distribution_root.is_dir():
        return [f"发布目录不存在：{distribution_root}"]
    layout = PLATFORM_LAYOUTS[platform_id]
    allowed_root_items = COMMON_ROOT_ITEMS | {str(layout["launcher"])}
    root_items = {path.name for path in distribution_root.iterdir()}
    errors: list[str] = []
    unexpected = sorted(root_items - allowed_root_items)
    if unexpected:
        errors.append(f"根目录包含未允许内容：{', '.join(unexpected)}")
    for name in sorted(COMMON_ROOT_ITEMS | {str(layout["launcher"])}):
        if not (distribution_root / name).exists():
            errors.append(f"发布根项缺失：{name}")
    for package_name in ("risk_audit", "risk_audit_web"):
        if not (distribution_root / "app" / package_name).is_dir():
            errors.append(f"产品源码包缺失：app/{package_name}")
    for writable_name in ("data", "outputs"):
        directory = distribution_root / writable_name
        if directory.is_dir() and any(directory.iterdir()):
            errors.append(f"发布可写目录必须为空：{writable_name}")

    runtime_root = distribution_root / "runtime"
    errors.extend(_verify_platform_layout(distribution_root, platform_id))
    errors.extend(_verify_launcher(distribution_root, platform_id))
    report = verify_release_manifest(distribution_root)
    errors.extend(item.message for item in report.items if not item.ok)
    rulepack_item = verify_active_rulepack(runtime_root / "resources/rulepacks")
    if not rulepack_item.ok:
        errors.append(rulepack_item.message)
    errors.extend(_verify_web_runtime(runtime_root))
    errors.extend(_verify_licenses(runtime_root))
    errors.extend(_verify_api_output_contract(distribution_root))
    errors.extend(_verify_forbidden_payloads(distribution_root))
    return errors


def _archive_top_level(handle: zipfile.ZipFile) -> tuple[str | None, list[str]]:
    """读取 ZIP 唯一顶层目录；handle 为已打开归档。"""
    names = handle.namelist()
    top_levels: set[str] = set()
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            return None, [f"ZIP 包含不安全路径：{name}"]
        if path.parts:
            top_levels.add(path.parts[0])
    if len(top_levels) != 1:
        return None, [f"ZIP 必须只包含一个顶层目录，实际为：{', '.join(sorted(top_levels))}"]
    return next(iter(top_levels)), []


def verify_archive(archive_path: Path, platform_id: str) -> list[str]:
    """解压并验证发布 ZIP；参数为归档和平台，返回错误列表。"""
    if platform_id not in PLATFORM_LAYOUTS:
        return [f"不支持的发布平台：{platform_id}"]
    try:
        with zipfile.ZipFile(archive_path) as handle:
            top_level, errors = _archive_top_level(handle)
            if errors:
                return errors
            names = set(handle.namelist())
            assert top_level is not None
            for writable_name in ("data", "outputs"):
                if f"{top_level}/{writable_name}/" not in names:
                    errors.append(f"ZIP 未显式保留空目录：{writable_name}")
            if errors:
                return errors
            with tempfile.TemporaryDirectory(prefix="risk-audit-archive-") as temporary:
                extraction_root = Path(temporary)
                if platform_id == "macos-arm64":
                    subprocess.run(
                        ["/usr/bin/ditto", "-x", "-k", str(archive_path), str(extraction_root)],
                        check=True,
                        shell=False,
                    )
                    # ditto 用额外元数据恢复中文名称，可能与 zipfile 的 CP437 解码结果不同。
                    extracted_items = list(extraction_root.iterdir())
                    if len(extracted_items) != 1 or not extracted_items[0].is_dir():
                        return ["macOS ZIP 解压后必须只包含一个顶层目录"]
                    distribution_root = extracted_items[0]
                else:
                    handle.extractall(extraction_root)
                    distribution_root = extraction_root / top_level
                return verify_distribution(distribution_root, platform_id)
    except (OSError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        return [f"ZIP 无法验证：{archive_path}；{error}"]


def main(argv: list[str] | None = None) -> int:
    """执行发布目录或 ZIP 校验；argv 为可选命令行参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("location", type=Path)
    parser.add_argument("--platform", choices=tuple(PLATFORM_LAYOUTS), required=True)
    arguments = parser.parse_args(argv)
    errors = (
        verify_distribution(arguments.location, arguments.platform)
        if arguments.location.is_dir()
        else verify_archive(arguments.location, arguments.platform)
    )
    if errors:
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("便携发布包验证通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
