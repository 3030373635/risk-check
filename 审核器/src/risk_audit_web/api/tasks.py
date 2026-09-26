"""审核任务资源路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from risk_audit_web.api.system import get_services
from risk_audit_web.api_models import EmptyRequest, TaskCreateRequest
from risk_audit_web.services import ApplicationServices, task_response


router = APIRouter(prefix="/api/v1/tasks")


@router.get("")
def list_tasks(services: ApplicationServices = Depends(get_services)) -> dict[str, object]:
    """列出任务；services 为应用服务。"""
    return services.list_tasks()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(
    request: TaskCreateRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, object]:
    """创建并启动任务；request 为用户字段，services 为应用服务。"""
    return task_response(services.create_task(request))


@router.get("/{task_id}")
def read_task(
    task_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, object]:
    """读取任务详情；task_id 为任务编号，services 为应用服务。"""
    return task_response(services.get_task(task_id))


@router.post("/{task_id}/cancellations", status_code=status.HTTP_201_CREATED)
def cancel_task(
    task_id: str,
    _request: EmptyRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, object]:
    """创建安全停止请求；task_id 为任务编号，_request 禁止额外字段。"""
    return task_response(services.cancel_task(task_id))


@router.post("/{task_id}/terminations", status_code=status.HTTP_201_CREATED)
def terminate_task(
    task_id: str,
    _request: EmptyRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, object]:
    """创建强制终止请求；task_id 为任务编号，_request 禁止额外字段。"""
    return task_response(services.terminate_task(task_id))


@router.post("/{task_id}/output-openings", status_code=status.HTTP_201_CREATED)
def open_output(
    task_id: str,
    _request: EmptyRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, str]:
    """打开任务输出目录；task_id 为任务编号，_request 禁止额外字段。"""
    return services.open_output(task_id)


@router.post("/{task_id}/statistics-openings", status_code=status.HTTP_201_CREATED)
def open_statistics(
    task_id: str,
    _request: EmptyRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, str]:
    """打开统计表；task_id 为任务编号，_request 禁止额外字段。"""
    return services.open_result(task_id, "audit_statistics_report")


@router.post("/{task_id}/review-openings", status_code=status.HTTP_201_CREATED)
def open_review(
    task_id: str,
    _request: EmptyRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, str]:
    """打开集中复核结果；task_id 为任务编号，_request 禁止额外字段。"""
    return services.open_result(task_id, "review_report")
