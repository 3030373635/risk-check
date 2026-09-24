"""验证 Windows 便携发布目录和 ZIP 的安全性与完整性。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import zipfile

from risk_audit_desktop.diagnostics import verify_runtime_manifest


ALLOWED_ROOT_ITEMS = {
    "风控矩阵审核器.exe",
    "runtime",
    "data",
    "outputs",
    "Windows桌面版使用说明.md",
}
DEVELOPMENT_PATH = re.compile(rb"(?:/Users/[^/\s]+/|[A-Za-z]:\\Users\\[^\\\s]+\\)")


def verify_distribution(distribution_root: Path) -> list[str]:
    """验证已解压发布目录；distribution_root 为交付根，返回错误列表。"""
    errors: list[str] = []
    if not distribution_root.is_dir():
        return [f"发布目录不存在：{distribution_root}"]
    root_items = {path.name for path in distribution_root.iterdir()}
    unexpected = sorted(root_items - ALLOWED_ROOT_ITEMS)
    if unexpected:
        errors.append(f"根目录包含未允许内容：{', '.join(unexpected)}")
    executable = distribution_root / "风控矩阵审核器.exe"
    if not executable.is_file():
        errors.append(f"EXE 缺失：{executable}")
    runtime = distribution_root / "runtime"
    report = verify_runtime_manifest(runtime)
    errors.extend(item.message for item in report.items if not item.ok)

    manifest_path = runtime / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        listed = {item["path"] for item in payload["resources"]}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        listed = set()
    actual = {
        path.relative_to(runtime).as_posix()
        for path in runtime.rglob("*")
        if path.is_file() and path != manifest_path
    } if runtime.is_dir() else set()
    extra = sorted(actual - listed)
    missing = sorted(listed - actual)
    if extra:
        errors.append(f"清单未记录资源：{', '.join(extra)}")
    if missing:
        errors.append(f"清单资源缺失：{', '.join(missing)}")

    for path in distribution_root.rglob("*"):
        relative = path.relative_to(distribution_root).as_posix()
        if "__pycache__" in path.parts or path.suffix.lower() == ".pyc":
            errors.append(f"发布包含开发缓存：{relative}")
        if path.is_file():
            try:
                content = path.read_bytes()
            except OSError as error:
                errors.append(f"文件无法读取：{relative}；{error}")
                continue
            if DEVELOPMENT_PATH.search(content):
                errors.append(f"文件包含开发机绝对路径：{relative}")
    return errors


def verify_zip(archive: Path) -> list[str]:
    """验证 ZIP 只有一个顶层目录；archive 为压缩包路径。"""
    try:
        with zipfile.ZipFile(archive) as handle:
            top_levels = {Path(name).parts[0] for name in handle.namelist() if Path(name).parts}
    except (OSError, zipfile.BadZipFile) as error:
        return [f"ZIP 无法读取：{archive}；{error}"]
    if len(top_levels) != 1:
        return [f"ZIP 必须只包含一个顶层目录，实际为：{', '.join(sorted(top_levels))}"]
    return []


def main(argv: list[str] | None = None) -> int:
    """执行交付验证；argv 为可选参数，返回退出码。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distribution", type=Path)
    parser.add_argument("--zip", dest="archive", type=Path)
    arguments = parser.parse_args(argv)
    errors = verify_distribution(arguments.distribution)
    if arguments.archive:
        errors.extend(verify_zip(arguments.archive))
    if errors:
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("便携发布包验证通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
