"""验证 FastAPI 与 Uvicorn 本机服务外壳。"""


def test_loopback_socket_uses_random_ipv4_port() -> None:
    """监听必须由系统分配 127.0.0.1 端口；无参数。"""
    from risk_audit_web.server import create_loopback_socket

    listener = create_loopback_socket()
    try:
        host, port = listener.getsockname()
        assert host == "127.0.0.1"
        assert 0 < port < 65536
    finally:
        listener.close()


def test_server_controller_requests_attached_server_exit() -> None:
    """控制器必须把退出请求转发给当前 Uvicorn 实例；无参数。"""
    from risk_audit_web.server import ServerController

    class StubServer:
        """保存 Uvicorn 退出标记。"""

        should_exit = False

    server = StubServer()
    controller = ServerController()
    controller.attach(server)
    controller.request_exit()

    assert server.should_exit
