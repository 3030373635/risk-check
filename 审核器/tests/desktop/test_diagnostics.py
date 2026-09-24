"""验证便携 runtime 资源清单和创建任务前诊断。"""

import hashlib
import json
from pathlib import Path

import pytest


def write_manifest(runtime_root: Path, relative_path: str, content: bytes) -> Path:
    """创建单资源清单；参数为 runtime 根、相对路径和期望内容。"""
    manifest = {
        "schema_version": "1.0",
        "app_version": "2.0.0",
        "resources": [{
            "path": relative_path,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }],
    }
    path = runtime_root / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_verify_manifest_reports_ok_resource(tmp_path: Path) -> None:
    """内容、大小和哈希一致时资源应通过诊断。"""
    from risk_audit_desktop.diagnostics import verify_runtime_manifest

    runtime_root = tmp_path / "runtime"
    content = b"portable-office"
    resource = runtime_root / "libreoffice/program/soffice.exe"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(content)
    write_manifest(runtime_root, "libreoffice/program/soffice.exe", content)

    report = verify_runtime_manifest(runtime_root)

    assert report.can_start
    assert [(item.code, item.relative_path) for item in report.items] == [
        ("ok", "libreoffice/program/soffice.exe")
    ]


@pytest.mark.parametrize(
    ("actual", "expected_code"),
    [(None, "missing"), (b"x", "size_mismatch"), (b"other-content", "hash_mismatch")],
)
def test_verify_manifest_reports_specific_integrity_error(
    tmp_path: Path,
    actual: bytes | None,
    expected_code: str,
) -> None:
    """缺失、大小变化和同大小内容变化必须分别报告。"""
    from risk_audit_desktop.diagnostics import verify_runtime_manifest

    runtime_root = tmp_path / "runtime"
    expected = b"expected-value"
    resource = runtime_root / "resources/file.bin"
    if actual is not None:
        if expected_code == "hash_mismatch":
            actual = b"changed-value!"
            assert len(actual) == len(expected)
        resource.parent.mkdir(parents=True)
        resource.write_bytes(actual)
    write_manifest(runtime_root, "resources/file.bin", expected)

    report = verify_runtime_manifest(runtime_root)

    assert not report.can_start
    assert report.items[0].code == expected_code
    assert "resources/file.bin" in report.items[0].message


@pytest.mark.parametrize("unsafe_path", ["../outside.bin", "/absolute.bin"])
def test_verify_manifest_rejects_path_escape(tmp_path: Path, unsafe_path: str) -> None:
    """资源清单不能读取 runtime 外部文件。"""
    from risk_audit_desktop.diagnostics import verify_runtime_manifest

    runtime_root = tmp_path / "runtime"
    write_manifest(runtime_root, unsafe_path, b"outside")

    report = verify_runtime_manifest(runtime_root)

    assert not report.can_start
    assert report.items[0].code == "invalid_path"


def test_portable_paths_expose_fixed_runtime_resources(tmp_path: Path) -> None:
    """桌面端必须只使用包内固定资源，不搜索系统安装。"""
    from risk_audit_desktop.task_paths import PortablePaths

    app_root = (tmp_path / "app").resolve()
    paths = PortablePaths.from_executable(app_root / "风控矩阵审核器.exe")

    assert paths.soffice == app_root / "runtime/libreoffice/program/soffice.exe"
    assert paths.rulepacks == app_root / "runtime/resources/rulepacks"
    assert paths.baseline_root == app_root / "runtime/resources/baselines"
    assert paths.entity_file == app_root / "runtime/resources/entities/会计主体清单20260907.xlsx"
    assert paths.model_root == app_root / "runtime/resources/models/bge-small-zh-v1.5"
    assert paths.config_file == app_root / "runtime/resources/rulepacks/audit-config.json"


def test_startup_diagnostics_lists_missing_required_resource(tmp_path: Path) -> None:
    """固定资源缺失时诊断必须阻止创建任务并指出具体路径。"""
    from risk_audit_desktop.diagnostics import run_startup_diagnostics
    from risk_audit_desktop.task_paths import PortablePaths

    paths = PortablePaths.from_executable(tmp_path / "app/风控矩阵审核器.exe")
    paths.runtime_root.mkdir(parents=True)
    (paths.runtime_root / "manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "app_version": "2.0.0", "resources": []}),
        encoding="utf-8",
    )

    report = run_startup_diagnostics(paths)

    assert not report.can_start
    assert any(str(paths.soffice) in item.message for item in report.items)
    assert any(str(paths.entity_file) in item.message for item in report.items)
