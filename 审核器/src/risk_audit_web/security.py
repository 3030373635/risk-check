"""本机 Web 服务的 Host、Origin、令牌和响应头约束。"""

from __future__ import annotations

from dataclasses import dataclass
import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


@dataclass(frozen=True)
class SecuritySettings:
    """保存当前会话安全参数。"""

    token: str
    port: int

    @property
    def origin(self) -> str:
        """返回唯一允许的浏览器来源；无参数。"""
        return f"http://127.0.0.1:{self.port}"

    @property
    def host(self) -> str:
        """返回唯一允许的 Host 请求头；无参数。"""
        return f"127.0.0.1:{self.port}"


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    """限制服务只能由本机会话页面调用。"""

    def __init__(self, app: ASGIApp, settings: SecuritySettings) -> None:
        """初始化中间件；app 为下游应用，settings 为会话安全参数。"""
        super().__init__(app)
        self.settings = settings

    def _secured_response(self, response: Response) -> Response:
        """添加浏览器安全响应头；response 为待返回响应。"""
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "font-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def _problem(self, status_code: int, error_code: str, message: str) -> Response:
        """构造安全错误；参数为 HTTP 状态、稳定错误码和中文提示。"""
        return self._secured_response(JSONResponse(
            status_code=status_code,
            content={"error_code": error_code, "message": message},
        ))

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """校验一个请求；request 为请求，call_next 为下游处理器。"""
        # Host 必须精确匹配当前随机端口，避免浏览器 DNS rebinding 调用本机接口。
        if request.headers.get("host") != self.settings.host:
            return self._problem(403, "INVALID_HOST", "请求来源主机不受信任")

        protected = request.url.path == "/healthz" or request.url.path.startswith("/api/v1")
        if protected:
            supplied_token = request.headers.get("x-local-token", "")
            if not secrets.compare_digest(supplied_token, self.settings.token):
                return self._problem(401, "INVALID_TOKEN", "本机会话令牌无效")

        if request.url.path.startswith("/api/v1"):
            origin = request.headers.get("origin")
            if origin is not None and origin != self.settings.origin:
                return self._problem(403, "INVALID_ORIGIN", "请求来源页面不受信任")
            if request.method not in {"GET", "HEAD", "OPTIONS"} and origin != self.settings.origin:
                return self._problem(403, "INVALID_ORIGIN", "修改操作必须来自当前本机页面")

        return self._secured_response(await call_next(request))

