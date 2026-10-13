"""验证重庆单地区任务资源边界。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path


def _post_headers(client) -> dict[str, str]:
    """返回修改型接口安全头；client 为测试客户端。"""
    return {
        "X-Local-Token": "secret",
        "Origin": str(client.base_url).rstrip("/"),
        "Content-Type": "application/json",
    }


def test_regions_collection_is_not_exposed(client) -> None:
    """单地区审核器不得暴露可选地区集合。"""
    response = client.get("/api/v1/regions", headers={"X-Local-Token": "secret"})

    assert response.status_code == 404


def test_create_task_uses_parent_directory_without_entity_file(
    client,
    web_services,
    input_root: Path,
) -> None:
    """重庆目录识别模式无主体清单也必须可创建任务。"""
    response = client.post(
        "/api/v1/tasks",
        headers=_post_headers(client),
        json={"input_root": str(input_root)},
    )

    assert response.status_code == 201
    request = web_services.repository.read_request(response.json()["task_id"])
    assert request.region_code == "chongqing"
    assert request.entity_mode == "parent_directory"
    assert request.entity_file is None


def test_create_task_allows_fixed_region_without_templates_and_config(
    client,
    web_services,
    input_root: Path,
) -> None:
    """重庆只缺模板和审核配置时必须允许创建任务。"""
    response = client.post(
        "/api/v1/tasks",
        headers=_post_headers(client),
        json={"input_root": str(input_root)},
    )

    assert response.status_code == 201
    request = web_services.repository.read_request(response.json()["task_id"])
    assert request.config_file is None
    assert request.baseline_root.endswith("/baselines/chongqing/templates")


def test_create_task_rejects_disabled_fixed_region(
    client,
    web_services,
    input_root: Path,
) -> None:
    """重庆地区被禁用时必须返回稳定错误。"""
    from risk_audit_web.region_profiles import RegionCatalog

    chongqing = web_services._region_catalog.require("chongqing")
    web_services._region_catalog = RegionCatalog({
        "chongqing": replace(chongqing, enabled=False),
    })

    response = client.post(
        "/api/v1/tasks",
        headers=_post_headers(client),
        json={"input_root": str(input_root)},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "REGION_DISABLED"
