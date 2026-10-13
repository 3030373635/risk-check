"""验证便携 runtime 资源清单和地区启动诊断。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from risk_audit.util import sha256_file, sha256_json


ROOT = Path(__file__).resolve().parents[2]


def write_manifest(app_root: Path, relative_path: str, content: bytes) -> Path:
    """创建单资源清单；参数为发布根、相对路径和期望内容，返回清单路径。"""
    manifest = {
        "schema_version": "1.0",
        "app_version": "2.0.0",
        "resources": [{
            "path": relative_path,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }],
    }
    path = app_root / "runtime/manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _write_region(
    resources_root: Path,
    region_code: str,
    *,
    ready: bool,
) -> dict[str, object]:
    """创建地区规则包及可选业务资源；参数为资源根、地区代码和就绪状态。"""
    version = "1.9.21" if region_code == "hunan" else "1.9.22"
    release = resources_root / "rulepacks" / "releases" / version
    shutil.copytree(ROOT / "rulepacks" / "releases" / version, release)
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    content_hash = str(manifest["content_hash"])
    if ready:
        if region_code == "hunan":
            entity_file = resources_root / "entities" / region_code / "entities.xlsx"
            entity_file.parent.mkdir(parents=True, exist_ok=True)
            entity_file.write_bytes(b"xlsx")
        (resources_root / "baselines" / region_code / "templates").mkdir(parents=True)
        config_file = resources_root / "configs" / region_code / "audit-config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text("{}", encoding="utf-8")
    profile: dict[str, object] = {
        "region_code": region_code,
        "region_name": "湖南" if region_code == "hunan" else "重庆",
        "rulepack_version": version,
        "rulepack_content_hash": content_hash,
        "entity_mode": "registry" if region_code == "hunan" else "parent_directory",
        "baseline_root": f"baselines/{region_code}/templates",
        "config_file": f"configs/{region_code}/audit-config.json",
        "enabled": True,
    }
    if region_code == "hunan":
        profile["entity_file"] = "entities/hunan/entities.xlsx"
    return profile


def _write_region_index(resources_root: Path, regions: list[dict[str, object]]) -> None:
    """写入地区索引；resources_root 为资源根，regions 为地区条目。"""
    index_path = resources_root / "regions" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps({"schema_version": "1.0", "regions": regions}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_verify_manifest_reports_ok_resource(tmp_path: Path) -> None:
    """内容、大小和哈希一致时资源应通过诊断；tmp_path 为测试根。"""
    from risk_audit_web.diagnostics import verify_release_manifest

    app_root = tmp_path / "release"
    content = b"portable-office"
    resource = app_root / "runtime/libreoffice/program/soffice.com"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(content)
    write_manifest(app_root, "runtime/libreoffice/program/soffice.com", content)

    report = verify_release_manifest(app_root)

    assert report.can_start
    assert [(item.code, item.relative_path) for item in report.items] == [
        ("ok", "runtime/libreoffice/program/soffice.com")
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
    """分别报告资源完整性错误；参数为测试根、实际内容和期望错误码。"""
    from risk_audit_web.diagnostics import verify_release_manifest

    app_root = tmp_path / "release"
    expected = b"expected-value"
    resource = app_root / "runtime/resources/file.bin"
    if actual is not None:
        if expected_code == "hash_mismatch":
            actual = b"changed-value!"
        resource.parent.mkdir(parents=True)
        resource.write_bytes(actual)
    write_manifest(app_root, "runtime/resources/file.bin", expected)

    report = verify_release_manifest(app_root)

    assert not report.can_start
    assert report.items[0].code == expected_code


@pytest.mark.parametrize("unsafe_path", ["../outside.bin", "/absolute.bin"])
def test_verify_manifest_rejects_path_escape(tmp_path: Path, unsafe_path: str) -> None:
    """资源清单不能读取发布根外文件；参数为测试根和非法路径。"""
    from risk_audit_web.diagnostics import verify_release_manifest

    app_root = tmp_path / "release"
    write_manifest(app_root, unsafe_path, b"outside")

    report = verify_release_manifest(app_root)

    assert not report.can_start
    assert report.items[0].code == "invalid_path"


def test_portable_paths_expose_region_resources(tmp_path: Path) -> None:
    """便携路径必须公开统一资源根和地区索引；tmp_path 为测试根。"""
    from risk_audit_web.task_paths import PortablePaths

    app_root = (tmp_path / "app").resolve()
    paths = PortablePaths.from_app_root(app_root, platform_name="win32")

    assert paths.soffice == app_root / "runtime/libreoffice/program/soffice.com"
    assert paths.resources_root == app_root / "runtime/resources"
    assert paths.region_index == app_root / "runtime/resources/regions/index.json"
    assert paths.rulepacks == app_root / "runtime/resources/rulepacks"


def test_startup_diagnostics_lists_missing_region_index(tmp_path: Path) -> None:
    """地区索引缺失时诊断必须阻止创建任务；tmp_path 为测试根。"""
    from risk_audit_web.diagnostics import run_startup_diagnostics
    from risk_audit_web.task_paths import PortablePaths

    paths = PortablePaths.from_app_root(tmp_path / "app", platform_name="win32")
    paths.soffice.parent.mkdir(parents=True)
    paths.soffice.write_bytes(b"exe")

    report = run_startup_diagnostics(paths)

    assert not report.can_start
    assert any(item.code == "invalid_region_catalog" for item in report.items)
    assert any(str(paths.region_index) in item.message for item in report.items)


def test_startup_diagnostics_rejects_non_executable_libreoffice(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """macOS 非执行文件必须被拒绝；参数为补丁工具和临时目录。"""
    from risk_audit_web.diagnostics import run_startup_diagnostics
    from risk_audit_web.task_paths import PortablePaths

    paths = PortablePaths.from_app_root(tmp_path / "app", platform_name="darwin")
    paths.soffice.parent.mkdir(parents=True)
    paths.soffice.write_bytes(b"not executable")
    paths.soffice.chmod(0o644)
    chongqing = _write_region(paths.resources_root, "chongqing", ready=True)
    _write_region_index(paths.resources_root, [chongqing])
    # Windows 的 X_OK 只反映文件存在，不能表达 macOS/POSIX 执行位。
    monkeypatch.setattr(os, "access", lambda *_args: True)

    report = run_startup_diagnostics(paths)

    assert not report.can_start
    assert any(item.code == "not_executable" for item in report.items)


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="仅非特权 POSIX 用户提供稳定的当前用户执行权限语义",
)
def test_startup_diagnostics_rejects_libreoffice_not_executable_by_current_user(
    tmp_path: Path,
) -> None:
    """当前用户无执行权限时必须拒绝；tmp_path 为临时发布目录。"""
    from risk_audit_web.diagnostics import run_startup_diagnostics
    from risk_audit_web.task_paths import PortablePaths

    paths = PortablePaths.from_app_root(tmp_path / "app", platform_name="darwin")
    paths.soffice.parent.mkdir(parents=True)
    paths.soffice.write_bytes(b"not executable by owner")
    # 文件所有者没有执行位，不能借用 other 执行位误判为当前进程可执行。
    paths.soffice.chmod(0o001)
    chongqing = _write_region(paths.resources_root, "chongqing", ready=True)
    _write_region_index(paths.resources_root, [chongqing])

    report = run_startup_diagnostics(paths)

    assert not report.can_start
    assert any(item.code == "not_executable" for item in report.items)


def test_startup_diagnostics_reports_unreadable_libreoffice(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """程序状态不可读时必须返回诊断；参数为补丁工具和临时目录。"""
    from risk_audit_web.diagnostics import run_startup_diagnostics
    from risk_audit_web.task_paths import PortablePaths

    paths = PortablePaths.from_app_root(tmp_path / "app", platform_name="darwin")
    paths.soffice.parent.mkdir(parents=True)
    paths.soffice.write_bytes(b"unreadable")
    chongqing = _write_region(paths.resources_root, "chongqing", ready=True)
    _write_region_index(paths.resources_root, [chongqing])
    original_stat = Path.stat

    def stat_with_denied_soffice(path: Path, *args, **kwargs):
        """模拟程序状态读取失败；path 为目标路径，args/kwargs 为 stat 参数。"""
        if path == paths.soffice:
            raise PermissionError("permission denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_with_denied_soffice)

    report = run_startup_diagnostics(paths)

    assert not report.can_start
    assert any(item.code == "unreadable" for item in report.items)


def test_startup_diagnostics_rejects_additional_region(tmp_path: Path) -> None:
    """重庆分支混入其他地区时必须阻止创建任务。"""
    from risk_audit_web.diagnostics import run_startup_diagnostics
    from risk_audit_web.task_paths import PortablePaths

    paths = PortablePaths.from_app_root(tmp_path / "app", platform_name="win32")
    paths.soffice.parent.mkdir(parents=True)
    paths.soffice.write_bytes(b"exe")
    hunan = _write_region(paths.resources_root, "hunan", ready=True)
    chongqing = _write_region(paths.resources_root, "chongqing", ready=False)
    _write_region_index(paths.resources_root, [hunan, chongqing])

    report = run_startup_diagnostics(paths)

    assert not report.can_start
    assert any(item.code == "invalid_region_catalog" for item in report.items)


def test_startup_diagnostics_rejects_catalog_without_available_region(tmp_path: Path) -> None:
    """没有任何就绪地区时必须阻止创建任务；tmp_path 为测试根。"""
    from risk_audit_web.diagnostics import run_startup_diagnostics
    from risk_audit_web.task_paths import PortablePaths

    paths = PortablePaths.from_app_root(tmp_path / "app", platform_name="win32")
    paths.soffice.parent.mkdir(parents=True)
    paths.soffice.write_bytes(b"exe")
    chongqing = _write_region(paths.resources_root, "chongqing", ready=False)
    chongqing["enabled"] = False
    _write_region_index(paths.resources_root, [chongqing])

    report = run_startup_diagnostics(paths)

    assert not report.can_start
    assert any(item.code == "no_available_region" for item in report.items)


def test_startup_diagnostics_rejects_structurally_invalid_rulepack(tmp_path: Path) -> None:
    """内容哈希正确但能力引用无效的规则包不得显示可用。"""

    from risk_audit.util import sha256_file, sha256_json
    from risk_audit_web.diagnostics import run_startup_diagnostics
    from risk_audit_web.task_paths import PortablePaths

    paths = PortablePaths.from_app_root(tmp_path / "app", platform_name="win32")
    paths.soffice.parent.mkdir(parents=True)
    paths.soffice.write_bytes(b"exe")
    chongqing = _write_region(paths.resources_root, "chongqing", ready=True)
    release = paths.resources_root / "rulepacks/releases/1.9.22"
    rule_path = next((release / "rules").glob("*.json"))
    rule = json.loads(rule_path.read_text(encoding="utf-8"))
    rule["checks"][0]["operator"] = "capability_does_not_exist"
    rule_path.write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")
    resource_hashes = {
        path.relative_to(release).as_posix(): sha256_file(path)
        for path in sorted(release.rglob("*.json"))
        if path.name != "manifest.json"
    }
    chongqing["rulepack_content_hash"] = sha256_json(resource_hashes)
    manifest_path = release / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["content_hash"] = chongqing["rulepack_content_hash"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    _write_region_index(paths.resources_root, [chongqing])

    report = run_startup_diagnostics(paths)

    assert not report.can_start
    assert any(item.code == "no_available_region" for item in report.items)
