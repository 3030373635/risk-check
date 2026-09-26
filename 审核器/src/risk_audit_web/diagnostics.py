"""便携 runtime 资源完整性和启动前诊断。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any

from risk_audit.util import sha256_file, sha256_json
from risk_audit_web.task_paths import PortablePaths


@dataclass(frozen=True)
class DiagnosticItem:
    """描述一项资源诊断结果。"""

    code: str
    relative_path: str
    message: str

    @property
    def ok(self) -> bool:
        """返回该诊断项是否通过；无参数。"""
        return self.code == "ok"


@dataclass(frozen=True)
class DiagnosticReport:
    """汇总资源诊断项及是否允许创建任务。"""

    items: list[DiagnosticItem]

    @property
    def can_start(self) -> bool:
        """返回全部诊断是否通过；无参数。"""
        return bool(self.items) and all(item.ok for item in self.items)


def _manifest_error(message: str) -> DiagnosticReport:
    """构造清单级错误；message 为用户可读原因。"""
    return DiagnosticReport([DiagnosticItem("invalid_manifest", "runtime/manifest.json", message)])


def _safe_resource_path(app_root: Path, relative_path: str) -> Path | None:
    """解析清单资源路径；参数为发布根和 POSIX 相对路径。"""
    pure_path = PurePosixPath(relative_path)
    if pure_path.is_absolute() or ".." in pure_path.parts or not pure_path.parts:
        return None
    candidate = (app_root / Path(*pure_path.parts)).resolve(strict=False)
    resolved_root = app_root.resolve(strict=False)
    if candidate != resolved_root and resolved_root not in candidate.parents:
        return None
    return candidate


def _immutable_release_paths(app_root: Path) -> set[str]:
    """列出发布根的不可变文件；app_root 为发布根。"""
    manifest_path = app_root / "runtime/manifest.json"
    paths: set[str] = set()
    for root_name in ("app", "runtime"):
        root = app_root / root_name
        if not root.is_dir():
            continue
        paths.update(
            path.relative_to(app_root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path != manifest_path
        )
    for name in ("启动审核器.bat", "启动审核器.command", "使用说明.md"):
        if (app_root / name).is_file():
            paths.add(name)
    return paths


def verify_release_manifest(app_root: Path) -> DiagnosticReport:
    """校验发布根清单；app_root 为完整便携发布目录。"""
    manifest_path = app_root / "runtime/manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _manifest_error(f"资源清单不存在：{manifest_path}")
    except (OSError, json.JSONDecodeError) as error:
        return _manifest_error(f"资源清单无法读取：{manifest_path}；{error}")
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "1.0"
        or not isinstance(payload.get("resources"), list)
    ):
        return _manifest_error(f"资源清单格式或版本错误：{manifest_path}")

    items: list[DiagnosticItem] = []
    listed: set[str] = set()
    for raw_item in payload["resources"]:
        if not isinstance(raw_item, dict):
            items.append(DiagnosticItem("invalid_manifest", "", "资源清单条目必须是对象"))
            continue
        relative_path = str(raw_item.get("path", ""))
        if relative_path in listed:
            items.append(DiagnosticItem(
                "invalid_manifest", relative_path, f"资源清单路径重复：{relative_path}",
            ))
            continue
        listed.add(relative_path)
        resource_path = _safe_resource_path(app_root, relative_path)
        if resource_path is None:
            items.append(DiagnosticItem(
                "invalid_path", relative_path, f"资源路径越过发布根边界：{relative_path}",
            ))
            continue
        if not resource_path.is_file():
            items.append(DiagnosticItem(
                "missing", relative_path, f"资源文件缺失：{relative_path}",
            ))
            continue
        expected_size = raw_item.get("size")
        if not isinstance(expected_size, int) or resource_path.stat().st_size != expected_size:
            items.append(DiagnosticItem(
                "size_mismatch", relative_path, f"资源文件大小不一致：{relative_path}",
            ))
            continue
        expected_hash = raw_item.get("sha256")
        if not isinstance(expected_hash, str) or sha256_file(resource_path) != expected_hash:
            items.append(DiagnosticItem(
                "hash_mismatch", relative_path, f"资源文件哈希不一致：{relative_path}",
            ))
            continue
        items.append(DiagnosticItem("ok", relative_path, f"资源校验通过：{relative_path}"))
    actual = _immutable_release_paths(app_root)
    for relative_path in sorted(actual - listed):
        items.append(DiagnosticItem(
            "unlisted", relative_path, f"不可变文件未进入发布清单：{relative_path}",
        ))
    for relative_path in sorted(listed - actual):
        if not any(item.relative_path == relative_path and item.code == "missing" for item in items):
            items.append(DiagnosticItem(
                "missing", relative_path, f"清单记录的不可变文件缺失：{relative_path}",
            ))
    return DiagnosticReport(
        items or [DiagnosticItem("ok", "runtime/manifest.json", "空发布清单格式有效")]
    )


def _required_path_item(path: Path, *, expect_directory: bool = False) -> DiagnosticItem:
    """检查固定资源路径；path 为目标，expect_directory 指定是否必须为目录。"""
    exists = path.is_dir() if expect_directory else path.is_file()
    return DiagnosticItem(
        "ok" if exists else "missing",
        str(path),
        f"资源存在：{path}" if exists else f"必需运行资源缺失：{path}",
    )


def _is_release_version(value: Any) -> bool:
    """判断发布版本是否为单一安全目录名；value 为待检查版本值。"""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
    )


def verify_active_rulepack(rulepacks_root: Path) -> DiagnosticItem:
    """校验活动发布规则包；rulepacks_root 为发布规则根目录。"""
    active_path = rulepacks_root / "active.json"
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return DiagnosticItem(
            "missing", str(active_path), f"活动规则包不可用：{active_path}；{error}",
        )
    if not isinstance(active, dict):
        return DiagnosticItem(
            "invalid_manifest", str(active_path), f"活动规则配置必须是 JSON 对象：{active_path}",
        )
    version = active.get("version")
    if not _is_release_version(version):
        return DiagnosticItem(
            "invalid_path", str(active_path), f"活动规则版本路径无效：{version!r}",
        )

    try:
        releases_root = (rulepacks_root / "releases").resolve(strict=False)
        release_path = (releases_root / version).resolve(strict=False)
    except (OSError, RuntimeError) as error:
        return DiagnosticItem(
            "invalid_path",
            str(rulepacks_root / "releases" / version),
            f"活动规则版本路径无法解析：{version!r}；{error}",
        )
    # 解析后必须仍是 releases 的直接子目录，同时防止符号链接越界。
    if release_path.parent != releases_root:
        return DiagnosticItem(
            "invalid_path", str(release_path), f"活动规则版本路径越过 releases 边界：{release_path}",
        )
    if not release_path.is_dir():
        return DiagnosticItem(
            "missing", str(release_path), f"活动规则发布目录缺失：{release_path}",
        )

    manifest_path = release_path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return DiagnosticItem(
            "invalid_manifest", str(manifest_path), f"规则包发布清单无法读取：{manifest_path}；{error}",
        )
    if not isinstance(manifest, dict):
        return DiagnosticItem(
            "invalid_manifest", str(manifest_path), f"规则包发布清单必须是 JSON 对象：{manifest_path}",
        )
    if manifest.get("version") != version or manifest.get("status") != "released":
        return DiagnosticItem(
            "invalid_manifest", str(manifest_path), f"规则包发布版本或状态无效：{manifest_path}",
        )

    expected_hash = manifest.get("content_hash")
    active_hash = active.get("content_hash")
    if not isinstance(expected_hash, str) or not expected_hash:
        return DiagnosticItem(
            "invalid_manifest", str(manifest_path), f"规则包发布清单缺少内容哈希：{manifest_path}",
        )
    if not isinstance(active_hash, str) or active_hash != expected_hash:
        return DiagnosticItem(
            "hash_mismatch", str(active_path), f"活动规则包指针哈希不一致：{active_path}",
        )

    # 与审核核心保持同一发布摘要算法，但不加载业务能力注册表。
    try:
        resource_hashes = {
            path.relative_to(release_path).as_posix(): sha256_file(path)
            for path in sorted(release_path.rglob("*.json"), key=lambda item: item.as_posix())
            if path != manifest_path
        }
    except OSError as error:
        return DiagnosticItem(
            "unreadable", str(release_path), f"规则包资源无法读取：{release_path}；{error}",
        )
    actual_hash = sha256_json(resource_hashes)
    if actual_hash != expected_hash:
        return DiagnosticItem(
            "hash_mismatch",
            str(release_path),
            f"规则包内容哈希不一致：{release_path}；请重新构建完整便携包",
        )
    return DiagnosticItem("ok", str(release_path), f"活动规则包可用：{version}")


def run_startup_diagnostics(paths: PortablePaths) -> DiagnosticReport:
    """执行创建任务前资源诊断；paths 为便携程序路径集合。"""
    manifest_report = verify_release_manifest(paths.app_root)
    required = [
        _required_path_item(paths.soffice),
        verify_active_rulepack(paths.rulepacks),
        _required_path_item(paths.config_file),
        _required_path_item(paths.entity_file),
        _required_path_item(paths.baseline_root, expect_directory=True),
    ]
    return DiagnosticReport([*manifest_report.items, *required])


def load_active_rulepack(rulepacks_root: Path) -> Path:
    """返回活动发布规则包目录；rulepacks_root 为规则包根目录。"""
    payload: Any = json.loads((rulepacks_root / "active.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("version"), str):
        raise ValueError(f"活动规则包配置格式错误：{rulepacks_root / 'active.json'}")
    release_path = rulepacks_root / "releases" / payload["version"]
    if not release_path.is_dir():
        raise FileNotFoundError(f"活动规则包不存在：{release_path}")
    return release_path
