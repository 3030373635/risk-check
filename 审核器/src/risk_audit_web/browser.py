"""默认浏览器启动和本机页面 URL 生成。"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import quote
import webbrowser

from risk_audit_web.single_instance import ServerSession


def build_launch_url(session: ServerSession) -> str:
    """生成一次性启动 URL；session 为当前服务会话。"""
    return f"http://127.0.0.1:{session.port}/?token={quote(session.token, safe='')}"


def open_default_browser(
    session: ServerSession,
    opener: Callable[[str], bool] = webbrowser.open,
) -> bool:
    """打开当前会话页面；session 为服务会话，opener 为可替换浏览器函数。"""
    return bool(opener(build_launch_url(session)))

