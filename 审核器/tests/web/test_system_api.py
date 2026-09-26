"""验证系统状态、目录选择、输出预览和退出 API。"""

from pathlib import Path

import pytest


def post_headers(client) -> dict[str, str]:
    """返回修改型接口安全头；client 为测试客户端。"""
    return {
        "X-Local-Token": "secret",
        "Origin": str(client.base_url).rstrip("/"),
        "Content-Type": "application/json",
    }


@pytest.mark.parametrize("name", ["中文 材料", "含.合法-标点", "目录末尾空格 "])
def test_output_preview_sanitizes_windows_names(client, tmp_path: Path, name: str) -> None:
    """常见 Windows 目录名应得到安全预览；name 为原目录名。"""
    input_path = tmp_path / name
    input_path.mkdir()
    response = client.post(
        "/api/v1/output-path-previews",
        headers=post_headers(client),
        json={"input_root": str(input_path)},
    )

    assert response.status_code == 200
    assert Path(response.json()["output_root"]).parent.name == "outputs"


def test_system_status_reports_diagnostics_and_running_count(client) -> None:
    """系统状态必须提供诊断与运行数量；client 为测试客户端。"""
    response = client.get("/api/v1/system/status", headers={"X-Local-Token": "secret"})

    assert response.status_code == 200
    assert response.json()["service_status"] == "ok"
    assert response.json()["diagnostics"][0]["ok"]


def test_directory_selection_returns_stable_platform_error(client, web_services) -> None:
    """系统目录框启动失败时 API 必须返回稳定错误码。"""
    from risk_audit_web.directory_picker import DirectoryPicker, DirectoryPickerError

    def fail_dialog() -> str:
        """模拟平台目录框无法启动；无参数。"""
        raise DirectoryPickerError("DIRECTORY_PICKER_FAILED", "无法打开系统目录选择器")

    web_services.directory_picker = DirectoryPicker(dialog=fail_dialog)
    response = client.post(
        "/api/v1/input-directory-selections",
        headers=post_headers(client),
        json={},
    )

    assert response.status_code == 500
    assert response.json() == {
        "error_code": "DIRECTORY_PICKER_FAILED",
        "message": "无法打开系统目录选择器",
    }


def test_output_preview_rejects_input_symlink_inside_outputs(client, portable_paths) -> None:
    """解析后位于 outputs 内的输入必须拒绝；client/paths 为测试依赖。"""
    nested = portable_paths.outputs_root / "nested-input"
    nested.mkdir(parents=True)
    link = portable_paths.app_root.parent / "linked-input"
    link.symlink_to(nested, target_is_directory=True)

    response = client.post(
        "/api/v1/output-path-previews",
        headers=post_headers(client),
        json={"input_root": str(link)},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "PATH_OVERLAP"


def test_output_preview_limits_very_long_directory_name(client, tmp_path: Path) -> None:
    """超长中文目录仍应生成 Windows 可用输出名；client/tmp_path 为测试依赖。"""
    input_path = tmp_path / ("长" * 121)
    input_path.mkdir()

    response = client.post(
        "/api/v1/output-path-previews",
        headers=post_headers(client),
        json={"input_root": str(input_path)},
    )

    assert response.status_code == 200
    assert len(Path(response.json()["output_root"]).name) <= 136


def test_shutdown_invalid_mode_is_stable_bad_request(client) -> None:
    """未声明退出模式必须返回稳定 400；client 为测试客户端。"""
    response = client.post(
        "/api/v1/shutdown-requests",
        headers=post_headers(client),
        json={"mode": "force"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_REQUEST"
