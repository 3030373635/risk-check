"""FastAPI 应用组装和 Uvicorn 回环服务控制。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import signal
import socket
import sys
from threading import Lock
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from risk_audit_web.security import LocalSecurityMiddleware, SecuritySettings


class ServerController:
    """向 Uvicorn 暴露线程安全的退出请求。"""

    def __init__(self) -> None:
        """初始化空服务引用；无参数。"""
        self._server: Any | None = None
        self._lock = Lock()

    def attach(self, server: Any) -> None:
        """保存运行中的服务；server 为 Uvicorn 实例。"""
        with self._lock:
            self._server = server

    def request_exit(self) -> None:
        """请求服务在当前响应完成后退出；无参数。"""
        with self._lock:
            if self._server is not None:
                self._server.should_exit = True


def install_terminal_shutdown_handler(
    controller: ServerController,
    *,
    platform_name: str | None = None,
):
    """安装终端关闭处理器；参数为服务控制器和平台，返回恢复回调。"""
    selected_platform = platform_name or sys.platform
    if selected_platform != "darwin":
        return lambda: None
    previous = signal.getsignal(signal.SIGHUP)

    def request_exit(_signal_number: int, _frame: object) -> None:
        """把 macOS 终端挂断转换为 Uvicorn 退出请求；参数为信号信息。"""
        controller.request_exit()

    signal.signal(signal.SIGHUP, request_exit)

    def restore() -> None:
        """恢复原 SIGHUP 处理器；无参数。"""
        signal.signal(signal.SIGHUP, previous)

    return restore


def create_app(
    settings: SecuritySettings,
    controller: ServerController,
    static_root: Path,
    routers: Iterable[APIRouter] = (),
) -> FastAPI:
    """创建本机应用；参数为安全设置、控制器、静态根和业务路由。"""
    app = FastAPI(
        title="风控矩阵审核器",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.server_controller = controller
    from risk_audit_web.services import ApiProblem

    @app.exception_handler(ApiProblem)
    async def handle_api_problem(_request: Request, error: ApiProblem) -> JSONResponse:
        """返回已分类业务错误；_request 为请求，error 为安全错误。"""
        return JSONResponse(status_code=error.status_code, content=error.to_dict())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        """统一请求结构错误；_request 为请求，error 为校验详情。"""
        return JSONResponse(
            status_code=400,
            content={
                "error_code": "INVALID_REQUEST",
                "message": "请求字段格式不正确",
            },
        )
    for router in routers:
        app.include_router(router)

    @app.get("/healthz")
    def read_health() -> dict[str, str]:
        """返回最小健康状态；无参数。"""
        return {"status": "ok"}

    @app.get("/", response_class=FileResponse)
    def read_index() -> FileResponse:
        """返回离线启动页；无参数。"""
        return FileResponse(static_root / "index.html")

    app.mount("/static", StaticFiles(directory=static_root), name="static")
    app.add_middleware(LocalSecurityMiddleware, settings=settings)
    return app


def create_loopback_socket() -> socket.socket:
    """创建绑定随机 IPv4 回环端口的监听 socket；无参数。"""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        # 显式绑定回环 IPv4，禁止框架回退到所有网卡或 IPv6。
        listener.bind(("127.0.0.1", 0))
        listener.listen(socket.SOMAXCONN)
        return listener
    except BaseException:
        listener.close()
        raise


def run_uvicorn(
    app: FastAPI,
    listener: socket.socket,
    controller: ServerController,
) -> None:
    """用预绑定 socket 运行服务；参数为应用、监听器和退出控制器。"""
    config = uvicorn.Config(app, log_config=None, access_log=False)
    server = uvicorn.Server(config)
    controller.attach(server)
    server.run(sockets=[listener])
