"""验证任务资源 REST API。"""

from pathlib import Path


def authorized_post_headers(client) -> dict[str, str]:
    """返回修改型任务接口安全头；client 为测试客户端。"""
    return {
        "X-Local-Token": "secret",
        "Origin": str(client.base_url).rstrip("/"),
        "Content-Type": "application/json",
    }


def test_create_task_uses_fixed_outputs_and_rejects_client_output(client, input_root: Path) -> None:
    """客户端只能提交名称和输入目录；client/input_root 为测试依赖。"""
    response = client.post(
        "/api/v1/tasks",
        headers=authorized_post_headers(client),
        json={"display_name": "第一批审核", "input_root": str(input_root)},
    )

    assert response.status_code == 201
    payload = response.json()
    assert Path(payload["output_root"]).parent.name == "outputs"
    assert payload["status"] == "running"

    rejected = client.post(
        "/api/v1/tasks",
        headers=authorized_post_headers(client),
        json={
            "display_name": "越权任务",
            "input_root": str(input_root),
            "output_root": str(input_root.parent / "elsewhere"),
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["error_code"] == "INVALID_REQUEST"


def test_list_detail_cancel_and_open_output(client, web_services, input_root: Path) -> None:
    """任务主流程必须按资源接口工作；参数为客户端、服务和输入目录。"""
    created = client.post(
        "/api/v1/tasks",
        headers=authorized_post_headers(client),
        json={"display_name": "任务", "input_root": str(input_root)},
    ).json()
    task_id = created["task_id"]

    listed = client.get("/api/v1/tasks", headers={"X-Local-Token": "secret"})
    detail = client.get(f"/api/v1/tasks/{task_id}", headers={"X-Local-Token": "secret"})
    cancelled = client.post(
        f"/api/v1/tasks/{task_id}/cancellations",
        headers=authorized_post_headers(client),
        json={},
    )
    opened = client.post(
        f"/api/v1/tasks/{task_id}/output-openings",
        headers=authorized_post_headers(client),
        json={},
    )

    assert listed.status_code == 200 and listed.json()["tasks"][0]["task_id"] == task_id
    assert detail.status_code == 200 and detail.json()["task_id"] == task_id
    assert cancelled.status_code == 201 and cancelled.json()["status"] == "cancelling"
    assert opened.status_code == 201
    assert web_services.opened_paths == [Path(created["output_root"]).resolve()]


def test_output_opening_never_accepts_client_path(client, input_root: Path) -> None:
    """打开结果只能由任务编号解析目标；client/input_root 为测试依赖。"""
    created = client.post(
        "/api/v1/tasks",
        headers=authorized_post_headers(client),
        json={"display_name": "任务", "input_root": str(input_root)},
    ).json()
    response = client.post(
        f"/api/v1/tasks/{created['task_id']}/output-openings",
        headers=authorized_post_headers(client),
        json={"path": "C:\\Windows"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_REQUEST"
