"""系统状态、目录选择、输出预览和退出路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from risk_audit_web.api_models import EmptyRequest, OutputPathPreviewRequest, ShutdownRequest
from risk_audit_web.services import ApplicationServices


router = APIRouter(prefix="/api/v1")


def get_services(request: Request) -> ApplicationServices:
    """读取应用服务；request 为当前 HTTP 请求。"""
    return request.app.state.services


@router.get("/system/status")
def read_system_status(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, object]:
    """返回本机系统状态；services 为应用服务。"""
    return services.system_status()


@router.post("/input-directory-selections")
def select_input_directory(
    _request: EmptyRequest,
    services: ApplicationServices = Depends(get_services),
):
    """创建一次目录选择；_request 禁止额外字段，services 为应用服务。"""
    result = services.select_input_directory()
    return result


@router.post("/output-path-previews")
def preview_output_path(
    request: OutputPathPreviewRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, str]:
    """返回输出路径预览；request 为输入目录，services 为应用服务。"""
    return services.preview_output_path(request)


@router.post("/shutdown-requests", status_code=status.HTTP_201_CREATED)
def request_shutdown(
    request: ShutdownRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, object]:
    """创建退出请求；request 为退出模式，services 为应用服务。"""
    return services.request_shutdown(request.mode)

