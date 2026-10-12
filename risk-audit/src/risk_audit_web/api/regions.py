"""审核地区集合资源路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from risk_audit_web.api.system import get_services
from risk_audit_web.services import ApplicationServices


router = APIRouter(prefix="/api/v1/regions")


@router.get("")
def list_regions(services: ApplicationServices = Depends(get_services)) -> dict[str, object]:
    """列出审核地区；services 为应用服务，返回公开地区状态。"""
    return services.list_regions()
