"""验证本机 Web 服务的请求安全边界。"""

from pathlib import Path


def create_probe_app(tmp_path: Path):
    """创建带读写探针的测试应用；tmp_path 为静态目录。"""
    from fastapi import APIRouter
    from risk_audit_web.security import SecuritySettings
    from risk_audit_web.server import ServerController, create_app

    tmp_path.joinpath("index.html").write_text("<main>ok</main>", encoding="utf-8")
    router = APIRouter()

    @router.get("/api/v1/probe")
    def read_probe() -> dict[str, bool]:
        """提供只读安全探针；无参数。"""
        return {"ok": True}

    @router.post("/api/v1/probe")
    def write_probe() -> dict[str, bool]:
        """提供修改型安全探针；无参数。"""
        return {"ok": True}

    return create_app(
        SecuritySettings(token="secret", port=49152),
        ServerController(),
        tmp_path,
        routers=(router,),
    )


def test_api_requires_exact_host_origin_and_token(tmp_path: Path) -> None:
    """API 必须同时限制 Host、Origin 和令牌；tmp_path 为静态目录。"""
    from fastapi.testclient import TestClient

    client = TestClient(create_probe_app(tmp_path), base_url="http://127.0.0.1:49152")

    assert client.get("/api/v1/probe").status_code == 401
    assert client.get(
        "/api/v1/probe",
        headers={"X-Local-Token": "secret", "Host": "evil.example"},
    ).status_code == 403
    assert client.post(
        "/api/v1/probe",
        headers={"X-Local-Token": "secret", "Origin": "http://evil.example"},
    ).status_code == 403
    assert client.post(
        "/api/v1/probe",
        headers={"X-Local-Token": "secret", "Origin": "http://127.0.0.1:49152"},
    ).json() == {"ok": True}


def test_health_requires_token_and_security_headers(tmp_path: Path) -> None:
    """健康检查不得泄露信息且必须带安全响应头；tmp_path 为静态目录。"""
    from fastapi.testclient import TestClient

    client = TestClient(create_probe_app(tmp_path), base_url="http://127.0.0.1:49152")
    response = client.get("/healthz", headers={"X-Local-Token": "secret"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_static_page_uses_exact_host_without_api_token(tmp_path: Path) -> None:
    """启动页可读取但仍受 Host 限制；tmp_path 为静态目录。"""
    from fastapi.testclient import TestClient

    client = TestClient(create_probe_app(tmp_path), base_url="http://127.0.0.1:49152")

    assert client.get("/").text == "<main>ok</main>"
    assert client.get("/", headers={"Host": "localhost:49152"}).status_code == 403

