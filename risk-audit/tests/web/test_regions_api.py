"""验证地区集合 REST 资源及地区错误码。"""

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


def test_list_regions_exposes_availability(client) -> None:
    """地区集合必须公开可用状态和不可用原因；client 为测试客户端。"""
    response = client.get("/api/v1/regions", headers={"X-Local-Token": "secret"})

    assert response.status_code == 200
    payload = response.json()
    assert [item["region_code"] for item in payload["regions"]] == ["hunan", "chongqing"]
    assert payload["regions"][0]["available"] is True
    assert payload["regions"][1]["available"] is False
    assert payload["regions"][1]["unavailable_reasons"]


def test_create_task_rejects_unknown_region(client, input_root: Path) -> None:
    """未知地区必须返回稳定错误；参数为客户端和输入目录。"""
    response = client.post(
        "/api/v1/tasks",
        headers=_post_headers(client),
        json={"input_root": str(input_root), "region_code": "unknown"},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "UNKNOWN_REGION"


def test_create_task_rejects_region_without_entity_file(client, input_root: Path) -> None:
    """缺少会计主体清单时必须返回具体原因；参数为客户端和输入目录。"""
    response = client.post(
        "/api/v1/tasks",
        headers=_post_headers(client),
        json={"input_root": str(input_root), "region_code": "chongqing"},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "REGION_NOT_READY"
    assert response.json()["details"]["unavailable_reasons"][0].startswith(
        "会计主体清单文件缺失："
    )


def test_create_task_rechecks_entity_file_after_startup(
    client,
    web_services,
    input_root: Path,
) -> None:
    """服务启动后主体清单丢失时仍必须拒绝创建任务。

    Args:
        client: 已注册地区和任务路由的测试客户端。
        web_services: 启动时已缓存地区目录的应用服务。
        input_root: 用户选择的审核材料目录。
    """

    profile = web_services._region_catalog.require("hunan")
    profile.entity_file.unlink()

    response = client.post(
        "/api/v1/tasks",
        headers=_post_headers(client),
        json={"input_root": str(input_root), "region_code": "hunan"},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "REGION_NOT_READY"
    assert response.json()["details"]["unavailable_reasons"] == [
        f"会计主体清单文件缺失：{profile.entity_file}"
    ]


def test_create_task_allows_region_without_templates_and_config(
    client,
    web_services,
    input_root: Path,
) -> None:
    """只缺模板和审核配置时必须允许创建任务。

    Args:
        client: 已注册地区和任务路由的测试客户端。
        web_services: 使用真实地区加载和任务存储的应用服务。
        input_root: 用户选择的审核材料目录。
    """

    from risk_audit_web.region_profiles import load_region_catalog

    entity_file = web_services.paths.resources_root / "entities/chongqing/entities.xlsx"
    entity_file.parent.mkdir(parents=True, exist_ok=True)
    entity_file.write_bytes(b"xlsx")
    # 服务启动时会缓存地区目录，这里模拟补齐主体清单后重启应用。
    web_services._region_catalog = load_region_catalog(web_services.paths.resources_root)

    response = client.post(
        "/api/v1/tasks",
        headers=_post_headers(client),
        json={"input_root": str(input_root), "region_code": "chongqing"},
    )

    assert response.status_code == 201
    task_id = response.json()["task_id"]
    request = web_services.repository.read_request(task_id)
    assert request.config_file is None
    assert request.baseline_root.endswith("/baselines/chongqing/templates")


def test_create_task_rejects_disabled_region(client, web_services, input_root: Path) -> None:
    """禁用地区必须与资源缺失区分；参数为客户端、服务和输入目录。"""
    from risk_audit_web.region_profiles import RegionCatalog

    hunan = web_services._region_catalog.require("hunan")
    web_services._region_catalog = RegionCatalog({
        **web_services._region_catalog.profiles,
        "hunan": replace(hunan, enabled=False),
    })

    response = client.post(
        "/api/v1/tasks",
        headers=_post_headers(client),
        json={"input_root": str(input_root), "region_code": "hunan"},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "REGION_DISABLED"
