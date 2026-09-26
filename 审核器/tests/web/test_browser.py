"""验证本机页面启动 URL 和浏览器调用。"""


def test_build_launch_url_quotes_token() -> None:
    """启动 URL 必须固定回环地址并转义令牌；无外部参数。"""
    from risk_audit_web.browser import build_launch_url
    from risk_audit_web.single_instance import ServerSession

    session = ServerSession("1.0", 1, 49152, "a+b/c=", "now")

    assert build_launch_url(session) == "http://127.0.0.1:49152/?token=a%2Bb%2Fc%3D"


def test_open_default_browser_passes_only_launch_url() -> None:
    """浏览器打开器只接收生成后的启动 URL；无外部参数。"""
    from risk_audit_web.browser import open_default_browser
    from risk_audit_web.single_instance import ServerSession

    opened = []
    session = ServerSession("1.0", 1, 49152, "secret", "now")

    assert open_default_browser(session, opener=lambda url: opened.append(url) or True)
    assert opened == ["http://127.0.0.1:49152/?token=secret"]

