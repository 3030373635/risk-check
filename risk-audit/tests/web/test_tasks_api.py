"""验证任务资源 REST API。"""

import json
from dataclasses import replace
from pathlib import Path
import re


def authorized_post_headers(client) -> dict[str, str]:
    """返回修改型任务接口安全头；client 为测试客户端。"""
    return {
        "X-Local-Token": "secret",
        "Origin": str(client.base_url).rstrip("/"),
        "Content-Type": "application/json",
    }


def test_create_task_uses_fixed_outputs_and_rejects_client_output(client, input_root: Path) -> None:
    """客户端只提交输入目录，服务端自动命名；client/input_root 为测试依赖。"""
    response = client.post(
        "/api/v1/tasks",
        headers=authorized_post_headers(client),
        json={"input_root": str(input_root)},
    )

    assert response.status_code == 201
    payload = response.json()
    assert Path(payload["output_root"]).parent.name == "outputs"
    assert re.fullmatch(rf"{re.escape(input_root.name)}-\d{{8}}-\d{{6}}", payload["display_name"])
    assert Path(payload["output_root"]).name == payload["display_name"]
    assert payload["status"] == "running"

    rejected = client.post(
        "/api/v1/tasks",
        headers=authorized_post_headers(client),
        json={
            "input_root": str(input_root),
            "output_root": str(input_root.parent / "elsewhere"),
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["error_code"] == "INVALID_REQUEST"

    legacy_name = client.post(
        "/api/v1/tasks",
        headers=authorized_post_headers(client),
        json={"display_name": "客户填写的名称", "input_root": str(input_root)},
    )
    assert legacy_name.status_code == 400
    assert legacy_name.json()["error_code"] == "INVALID_REQUEST"


def test_list_detail_cancel_and_open_output(client, web_services, input_root: Path) -> None:
    """任务主流程必须按资源接口工作；参数为客户端、服务和输入目录。"""
    created = client.post(
        "/api/v1/tasks",
        headers=authorized_post_headers(client),
        json={"input_root": str(input_root)},
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
        json={"input_root": str(input_root)},
    ).json()
    response = client.post(
        f"/api/v1/tasks/{created['task_id']}/output-openings",
        headers=authorized_post_headers(client),
        json={"path": "C:\\Windows"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_REQUEST"


def test_read_incomplete_items_returns_every_visible_reason(
    client,
    web_services,
    input_root: Path,
) -> None:
    """未完成项接口必须返回状态数量对应的结构化原因。

    Args:
        client: FastAPI 测试客户端。
        web_services: 使用真实任务存储的应用服务。
        input_root: 测试材料目录。
    """
    created = client.post(
        "/api/v1/tasks",
        headers=authorized_post_headers(client),
        json={"input_root": str(input_root)},
    ).json()
    task = web_services.get_task(created["task_id"])
    report_path = Path(task.record.output_root) / ".task/report/_risk_audit/未完成项.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    items = [{
        "type": "business_identification_failed",
        "title": "业务识别失败",
        "file": "本部/05配网/附件清单.xlsx",
        "stage": "业务识别",
        "message": "未找到业务模板映射",
    }]
    report_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    web_services.repository.update_task_state(
        task.record.task_id,
        {"result_summary": {
            "incomplete_items": 1,
            "incomplete_items_report": str(report_path),
        }},
    )

    response = client.get(
        f"/api/v1/tasks/{task.record.task_id}/incomplete-items",
        headers={"X-Local-Token": "secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"count": 1, "items": items}


def test_read_unaudited_files_returns_customer_friendly_json_resource(
    client,
    web_services,
    input_root: Path,
) -> None:
    """未审核文件接口必须返回结构化 JSON；参数为客户端、服务和输入目录。"""
    created = client.post(
        "/api/v1/tasks",
        headers=authorized_post_headers(client),
        json={"input_root": str(input_root)},
    ).json()
    task = web_services.get_task(created["task_id"])
    report_path = (
        Path(task.record.output_root)
        / ".task/report"
        / "_risk_audit/未审核文件.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    items = [{
        "type": "unparsed_business_file",
        "file": "待templates/台账.xlsx",
        "message": "文件无法解析",
        "parse_errors": ["缺少风险事件列", "缺少控制措施列"],
    }]
    report_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    web_services.repository.update_task_state(
        task.record.task_id,
        {"result_summary": {"unaudited_files_report": str(report_path)}},
    )

    response = client.get(
        f"/api/v1/tasks/{task.record.task_id}/unaudited-files",
        headers={"X-Local-Token": "secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"count": 1, "items": items}


def test_read_unaudited_files_reports_invalid_utf8(
    client,
    web_services,
    input_root: Path,
) -> None:
    """损坏的非 UTF-8 报告必须返回稳定错误；参数为客户端、服务和输入目录。"""
    created = client.post(
        "/api/v1/tasks",
        headers=authorized_post_headers(client),
        json={"input_root": str(input_root)},
    ).json()
    task = web_services.get_task(created["task_id"])
    report_path = Path(task.record.output_root) / ".task/未审核文件.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(b"\xff\xfe")
    web_services.repository.update_task_state(
        task.record.task_id,
        {"result_summary": {"unaudited_files_report": str(report_path)}},
    )

    response = client.get(
        f"/api/v1/tasks/{task.record.task_id}/unaudited-files",
        headers={"X-Local-Token": "secret"},
    )

    assert response.status_code == 500
    assert response.json()["error_code"] == "INVALID_RESULT"


def test_list_returns_every_created_task_status_without_internal_fields(
    client,
    web_services,
    tmp_path: Path,
) -> None:
    """列表必须返回全部终态且隐藏内部字段；参数为客户端、服务和隔离目录。"""
    from risk_audit_web.task_contracts import TaskEvent
    from tests.web.test_task_repository import make_request, make_state

    statuses = ["completed", "partial", "failed", "cancelled", "interrupted"]
    for index, status in enumerate(statuses):
        task_id = f"task-{index}"
        created_at = f"2026-09-30T10:0{index}:00+08:00"
        request = replace(make_request(tmp_path, task_id), created_at=created_at)
        state = make_state(task_id).with_updates(status=status, stage=status)
        web_services.repository.create_task(
            request,
            state,
            TaskEvent("1.0", task_id, "created", created_at, {}),
        )

    response = client.get("/api/v1/tasks", headers={"X-Local-Token": "secret"})

    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert [task["status"] for task in tasks] == list(reversed(statuses))
    assert all("state_path" not in task for task in tasks)
    assert all("database_path" not in task for task in tasks)
    assert all("cancel_requested" not in task for task in tasks)


def test_created_task_remains_in_rest_list_when_worker_start_fails(
    client,
    web_services,
    input_root: Path,
    monkeypatch,
) -> None:
    """Worker 启动失败后创建接口和列表仍须返回任务；参数为端到端测试依赖。"""
    monkeypatch.setattr(
        web_services.manager,
        "start_task",
        lambda record: (_ for _ in ()).throw(OSError("启动失败")),
    )

    created = client.post(
        "/api/v1/tasks",
        headers=authorized_post_headers(client),
        json={"input_root": str(input_root)},
    )
    listed = client.get("/api/v1/tasks", headers={"X-Local-Token": "secret"})

    assert created.status_code == 201
    assert created.json()["status"] == "failed"
    assert listed.json()["tasks"][0]["task_id"] == created.json()["task_id"]
    assert listed.json()["tasks"][0]["status"] == "failed"
