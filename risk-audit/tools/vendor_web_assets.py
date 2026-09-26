"""下载并验证固定版本的离线 Web 第三方资产。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.request import urlopen


BOOTSTRAP_VERSION = "5.3.8"
BOOTSTRAP_ICONS_VERSION = "1.13.1"
ASSET_URLS = {
    "src/risk_audit_web/static/css/bootstrap.min.css": (
        "https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css"
    ),
    "src/risk_audit_web/static/js/bootstrap.bundle.min.js": (
        "https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/js/bootstrap.bundle.min.js"
    ),
    "src/risk_audit_web/static/css/bootstrap-icons.min.css": (
        "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.13.1/font/bootstrap-icons.min.css"
    ),
    "src/risk_audit_web/static/fonts/bootstrap-icons.woff2": (
        "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.13.1/font/fonts/bootstrap-icons.woff2"
    ),
    "licenses/web/Bootstrap.txt": "https://raw.githubusercontent.com/twbs/bootstrap/v5.3.8/LICENSE",
    "licenses/web/Bootstrap-Icons.txt": (
        "https://raw.githubusercontent.com/twbs/icons/v1.13.1/LICENSE"
    ),
}


def _sha256(data: bytes) -> str:
    """计算字节内容 SHA-256；data 为文件内容。"""
    return hashlib.sha256(data).hexdigest()


def _normalize_asset(relative_path: str, content: bytes) -> bytes:
    """调整下载资产的本地引用；relative_path/content 为目标和原始内容。"""
    if relative_path.endswith("bootstrap-icons.min.css"):
        return content.replace(b"./fonts/bootstrap-icons.woff2", b"../fonts/bootstrap-icons.woff2")
    return content


def _atomic_write(path: Path, content: bytes) -> None:
    """原子写入下载内容；path 为目标，content 为完整字节。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
    temporary.replace(path)


def download_assets(project_root: Path) -> None:
    """下载全部固定资产并写清单；project_root 为审核器工程根。"""
    entries = []
    for relative_path, url in ASSET_URLS.items():
        with urlopen(url, timeout=60) as response:
            content = _normalize_asset(relative_path, response.read())
        _atomic_write(project_root / relative_path, content)
        entries.append({
            "path": relative_path,
            "url": url,
            "size": len(content),
            "sha256": _sha256(content),
        })
    manifest = {
        "schema_version": "1.0",
        "bootstrap_version": BOOTSTRAP_VERSION,
        "bootstrap_icons_version": BOOTSTRAP_ICONS_VERSION,
        "assets": entries,
    }
    manifest_path = project_root / "src/risk_audit_web/static/vendor-manifest.json"
    _atomic_write(
        manifest_path,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def verify_assets(project_root: Path) -> list[str]:
    """验证资产清单；project_root 为审核器工程根，返回错误列表。"""
    manifest_path = project_root / "src/risk_audit_web/static/vendor-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"Web 资产清单无法读取：{error}"]
    errors: list[str] = []
    if manifest.get("bootstrap_version") != BOOTSTRAP_VERSION:
        errors.append("Bootstrap 版本不匹配")
    if manifest.get("bootstrap_icons_version") != BOOTSTRAP_ICONS_VERSION:
        errors.append("Bootstrap Icons 版本不匹配")
    entries = manifest.get("assets")
    if not isinstance(entries, list):
        return [*errors, "Web 资产清单 entries 无效"]
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            errors.append("Web 资产清单条目无效")
            continue
        path = project_root / entry["path"]
        try:
            content = path.read_bytes()
        except OSError:
            errors.append(f"Web 资产缺失：{entry['path']}")
            continue
        if len(content) != entry.get("size"):
            errors.append(f"Web 资产大小不一致：{entry['path']}")
        if _sha256(content) != entry.get("sha256"):
            errors.append(f"Web 资产哈希不一致：{entry['path']}")
    expected_paths = set(ASSET_URLS)
    actual_paths = {entry.get("path") for entry in entries if isinstance(entry, dict)}
    if actual_paths != expected_paths:
        errors.append("Web 资产清单文件集合不完整")
    return errors


def main(argv: list[str] | None = None) -> int:
    """下载或验证资产；argv 为命令行参数，返回退出码。"""
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--download", action="store_true")
    action.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]
    if args.download:
        download_assets(project_root)
    errors = verify_assets(project_root)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("Web 静态资源校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
