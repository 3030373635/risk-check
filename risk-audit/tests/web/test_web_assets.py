"""验证离线前端资产完整性。"""

import json
from pathlib import Path
import re


def static_root() -> Path:
    """返回仓库内 Web 静态目录；无参数。"""
    return Path(__file__).resolve().parents[2] / "src/risk_audit_web/static"


def test_frontend_has_no_remote_runtime_resources() -> None:
    """页面和样式不得在运行时引用网络资源；无参数。"""
    root = static_root()
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".html", ".css", ".js"}
    )
    assert not re.search(r"(?:src|href)=[\"']https?://", text, re.IGNORECASE)
    assert not re.search(r"url\(\s*[\"']?https?://", text, re.IGNORECASE)
    owned_scripts = "\n".join(
        (root / f"js/{name}").read_text(encoding="utf-8")
        for name in ("api.js", "tasks.js", "app.js")
    )
    assert "innerHTML" not in owned_scripts


def test_vendor_manifest_matches_downloaded_assets() -> None:
    """固定版本资产必须与清单哈希一致；无参数。"""
    from tools.vendor_web_assets import verify_assets

    errors = verify_assets(Path(__file__).resolve().parents[2])

    assert errors == []
    manifest = json.loads((static_root() / "vendor-manifest.json").read_text(encoding="utf-8"))
    assert manifest["bootstrap_version"] == "5.3.8"
    assert manifest["bootstrap_icons_version"] == "1.13.1"
