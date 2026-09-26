"""验证便携 runtime 资源清单和创建任务前诊断。"""

import hashlib
import json
from pathlib import Path

import pytest


def write_manifest(app_root: Path, relative_path: str, content: bytes) -> Path:
    """创建单资源清单；参数为发布根、相对路径和期望内容。"""
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


def test_verify_manifest_reports_ok_resource(tmp_path: Path) -> None:
    """内容、大小和哈希一致时资源应通过诊断。"""
    from risk_audit_web.diagnostics import verify_release_manifest

    app_root = tmp_path / "release"
    content = b"portable-office"
    resource = app_root / "runtime/libreoffice/program/soffice.exe"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(content)
    write_manifest(app_root, "runtime/libreoffice/program/soffice.exe", content)

    report = verify_release_manifest(app_root)

    assert report.can_start
    assert [(item.code, item.relative_path) for item in report.items] == [
        ("ok", "runtime/libreoffice/program/soffice.exe")
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
    from risk_audit_web.diagnostics import verify_release_manifest

    app_root = tmp_path / "release"
    expected = b"expected-value"
    resource = app_root / "runtime/resources/file.bin"
    if actual is not None:
        if expected_code == "hash_mismatch":
            actual = b"changed-value!"
            assert len(actual) == len(expected)
        resource.parent.mkdir(parents=True)
        resource.write_bytes(actual)
    write_manifest(app_root, "runtime/resources/file.bin", expected)

    report = verify_release_manifest(app_root)

    assert not report.can_start
    assert report.items[0].code == expected_code
    assert "resources/file.bin" in report.items[0].message


@pytest.mark.parametrize("unsafe_path", ["../outside.bin", "/absolute.bin"])
def test_verify_manifest_rejects_path_escape(tmp_path: Path, unsafe_path: str) -> None:
    """资源清单不能读取 runtime 外部文件。"""
    from risk_audit_web.diagnostics import verify_release_manifest

    app_root = tmp_path / "release"
    write_manifest(app_root, unsafe_path, b"outside")

    report = verify_release_manifest(app_root)

    assert not report.can_start
    assert report.items[0].code == "invalid_path"


def test_portable_paths_expose_fixed_runtime_resources(tmp_path: Path) -> None:
    """桌面端必须只使用包内固定资源，不搜索系统安装。"""
    from risk_audit_web.task_paths import PortablePaths

    app_root = (tmp_path / "app").resolve()
    paths = PortablePaths.from_app_root(app_root, platform_name="win32")

    assert paths.soffice == app_root / "runtime/libreoffice/program/soffice.exe"
    assert paths.rulepacks == app_root / "runtime/resources/rulepacks"
    assert paths.baseline_root == app_root / "runtime/resources/baselines"
    assert paths.entity_file == app_root / "runtime/resources/entities/会计主体清单20260907.xlsx"
    assert paths.config_file == app_root / "runtime/resources/rulepacks/audit-config.json"


def test_startup_diagnostics_lists_missing_required_resource(tmp_path: Path) -> None:
    """固定资源缺失时诊断必须阻止创建任务并指出具体路径。"""
    from risk_audit_web.diagnostics import run_startup_diagnostics
    from risk_audit_web.task_paths import PortablePaths

    paths = PortablePaths.from_app_root(tmp_path / "app", platform_name="win32")
    paths.runtime_root.mkdir(parents=True)
    (paths.runtime_root / "manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "app_version": "2.0.0", "resources": []}),
        encoding="utf-8",
    )

    report = run_startup_diagnostics(paths)

    assert not report.can_start
    assert any(str(paths.soffice) in item.message for item in report.items)
    assert any(str(paths.entity_file) in item.message for item in report.items)


def test_startup_diagnostics_succeeds_without_removed_semantic_model(tmp_path: Path) -> None:
    """本地语义模型已停用并删除时，其他运行资源完整即可创建任务。"""
    from risk_audit_web.diagnostics import run_startup_diagnostics
    from risk_audit_web.task_paths import PortablePaths

    paths = PortablePaths.from_app_root(tmp_path / "app", platform_name="win32")
    paths.runtime_root.mkdir(parents=True)
    (paths.runtime_root / "manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "app_version": "2.0.0", "resources": []}),
        encoding="utf-8",
    )
    paths.soffice.parent.mkdir(parents=True)
    paths.soffice.write_bytes(b"exe")
    release_root = paths.rulepacks / "releases/2.0.0"
    release_root.mkdir(parents=True)
    rule_path = release_root / "rules/R01.json"
    rule_path.parent.mkdir()
    rule_path.write_text('{"id":"R01"}\n', encoding="utf-8")
    rule_hash = hashlib.sha256(rule_path.read_bytes()).hexdigest()
    content_hash = hashlib.sha256(
        json.dumps(
            {"rules/R01.json": rule_hash},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    (release_root / "manifest.json").write_text(json.dumps({
        "version": "2.0.0",
        "status": "released",
        "content_hash": content_hash,
    }), encoding="utf-8")
    (paths.rulepacks / "active.json").write_text(json.dumps({
        "version": "2.0.0",
        "content_hash": content_hash,
    }), encoding="utf-8")
    paths.config_file.write_text('{"mode":"desktop"}', encoding="utf-8")
    paths.entity_file.parent.mkdir(parents=True)
    paths.entity_file.write_bytes(b"entity")
    paths.baseline_root.mkdir(parents=True)

    from tools.portable_release import build_release_manifest

    (paths.runtime_root / "manifest.json").write_text(
        json.dumps(build_release_manifest(paths.app_root), ensure_ascii=False),
        encoding="utf-8",
    )

    report = run_startup_diagnostics(paths)

    assert report.can_start
    assert all("bge-small-zh-v1.5" not in item.relative_path for item in report.items)


def test_startup_diagnostics_rejects_rulepack_with_changed_line_endings(tmp_path: Path) -> None:
    """规则 JSON 被 Windows 换行转换后，启动诊断必须在创建任务前拦截。"""
    from risk_audit_web.diagnostics import verify_active_rulepack

    rulepacks = tmp_path / "rulepacks"
    release = rulepacks / "releases/2.0.0"
    rule_path = release / "rules/R01.json"
    rule_path.parent.mkdir(parents=True)
    original = b'{"id":"R01"}\n'
    rule_path.write_bytes(original)
    rule_hash = hashlib.sha256(original).hexdigest()
    content_hash = hashlib.sha256(
        json.dumps(
            {"rules/R01.json": rule_hash},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    (release / "manifest.json").write_text(json.dumps({
        "version": "2.0.0",
        "status": "released",
        "content_hash": content_hash,
    }), encoding="utf-8")
    (rulepacks / "active.json").write_text(json.dumps({
        "version": "2.0.0",
        "content_hash": content_hash,
    }), encoding="utf-8")
    rule_path.write_bytes(original.replace(b"\n", b"\r\n"))

    item = verify_active_rulepack(rulepacks)

    assert item.code == "hash_mismatch"
    assert "规则包内容哈希不一致" in item.message


def test_verify_active_rulepack_rejects_non_object_manifest(tmp_path: Path) -> None:
    """发布清单不是 JSON 对象时必须返回诊断，不得抛出内部异常。"""
    from risk_audit_web.diagnostics import verify_active_rulepack

    rulepacks = tmp_path / "rulepacks"
    release = rulepacks / "releases/2.0.0"
    release.mkdir(parents=True)
    (release / "manifest.json").write_text("[]", encoding="utf-8")
    (rulepacks / "active.json").write_text(json.dumps({
        "version": "2.0.0",
        "content_hash": "0" * 64,
    }), encoding="utf-8")

    item = verify_active_rulepack(rulepacks)

    assert item.code == "invalid_manifest"


def test_verify_active_rulepack_rejects_version_path_escape(tmp_path: Path) -> None:
    """active.json 中的版本必须是单一目录名，不得越过 releases 边界。"""
    from risk_audit_web.diagnostics import verify_active_rulepack

    rulepacks = tmp_path / "rulepacks"
    rulepacks.mkdir()
    (rulepacks / "active.json").write_text(json.dumps({
        "version": "../../external-release",
        "content_hash": "0" * 64,
    }), encoding="utf-8")

    item = verify_active_rulepack(rulepacks)

    assert item.code == "invalid_path"
    assert "版本路径无效" in item.message


def test_verify_active_rulepack_rejects_symlink_loop(tmp_path: Path) -> None:
    """发布目录形成循环符号链接时必须返回路径诊断。"""
    from risk_audit_web.diagnostics import verify_active_rulepack

    rulepacks = tmp_path / "rulepacks"
    releases = rulepacks / "releases"
    releases.mkdir(parents=True)
    (releases / "2.0.0").symlink_to("2.0.0", target_is_directory=True)
    (rulepacks / "active.json").write_text(json.dumps({
        "version": "2.0.0",
        "content_hash": "0" * 64,
    }), encoding="utf-8")

    item = verify_active_rulepack(rulepacks)

    assert item.code == "invalid_path"
    assert "无法解析" in item.message


def test_verify_active_rulepack_reports_unreadable_resource(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """JSON 资源在哈希期间无法读取时必须返回可诊断错误。"""
    from risk_audit_web import diagnostics

    rulepacks = tmp_path / "rulepacks"
    release = rulepacks / "releases/2.0.0"
    rule_path = release / "rules/R01.json"
    rule_path.parent.mkdir(parents=True)
    rule_path.write_text('{"id":"R01"}\n', encoding="utf-8")
    (release / "manifest.json").write_text(json.dumps({
        "version": "2.0.0",
        "status": "released",
        "content_hash": "0" * 64,
    }), encoding="utf-8")
    (rulepacks / "active.json").write_text(json.dumps({
        "version": "2.0.0",
        "content_hash": "0" * 64,
    }), encoding="utf-8")

    def deny_read(path: Path) -> str:
        """模拟资源在校验时被系统拒绝读取；path 为当前资源。"""
        raise PermissionError(f"access denied: {path}")

    monkeypatch.setattr(diagnostics, "sha256_file", deny_read)

    item = diagnostics.verify_active_rulepack(rulepacks)

    assert item.code == "unreadable"
    assert "无法读取" in item.message


def test_repository_active_rulepack_integrity_is_valid() -> None:
    """仓库当前活动发布包必须符合桌面端完整性校验。"""
    from risk_audit_web.diagnostics import verify_active_rulepack

    rulepacks = Path(__file__).resolve().parents[2] / "rulepacks"

    item = verify_active_rulepack(rulepacks)

    assert item.code == "ok"
