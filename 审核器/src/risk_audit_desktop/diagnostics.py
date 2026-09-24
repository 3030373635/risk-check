"""便携 runtime 资源完整性和启动前诊断。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any

from risk_audit.util import sha256_file
from risk_audit_desktop.task_paths import PortablePaths


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
    return DiagnosticReport([DiagnosticItem("invalid_manifest", "manifest.json", message)])


def _safe_resource_path(runtime_root: Path, relative_path: str) -> Path | None:
    """解析清单资源路径；参数为 runtime 根和 POSIX 相对路径。"""
    pure_path = PurePosixPath(relative_path)
    if pure_path.is_absolute() or ".." in pure_path.parts or not pure_path.parts:
        return None
    candidate = (runtime_root / Path(*pure_path.parts)).resolve(strict=False)
    resolved_root = runtime_root.resolve(strict=False)
    if candidate != resolved_root and resolved_root not in candidate.parents:
        return None
    return candidate


def verify_runtime_manifest(runtime_root: Path) -> DiagnosticReport:
    """校验 runtime 清单；runtime_root 为外部运行资源根目录。"""
    manifest_path = runtime_root / "manifest.json"
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
    for raw_item in payload["resources"]:
        if not isinstance(raw_item, dict):
            items.append(DiagnosticItem("invalid_manifest", "", "资源清单条目必须是对象"))
            continue
        relative_path = str(raw_item.get("path", ""))
        resource_path = _safe_resource_path(runtime_root, relative_path)
        if resource_path is None:
            items.append(DiagnosticItem(
                "invalid_path", relative_path, f"资源路径越过 runtime 边界：{relative_path}",
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
    return DiagnosticReport(items or [DiagnosticItem("ok", "manifest.json", "空资源清单格式有效")])


def _required_path_item(path: Path, *, expect_directory: bool = False) -> DiagnosticItem:
    """检查固定资源路径；path 为目标，expect_directory 指定是否必须为目录。"""
    exists = path.is_dir() if expect_directory else path.is_file()
    return DiagnosticItem(
        "ok" if exists else "missing",
        str(path),
        f"资源存在：{path}" if exists else f"必需运行资源缺失：{path}",
    )


def _active_rulepack_item(rulepacks_root: Path) -> DiagnosticItem:
    """检查活动规则包；rulepacks_root 为发布规则根目录。"""
    active_path = rulepacks_root / "active.json"
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
        version = active["version"]
        release_path = rulepacks_root / "releases" / version
        if not release_path.is_dir():
            raise FileNotFoundError(release_path)
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        return DiagnosticItem(
            "missing", str(active_path), f"活动规则包不可用：{active_path}；{error}",
        )
    return DiagnosticItem("ok", str(release_path), f"活动规则包可用：{version}")


def run_startup_diagnostics(paths: PortablePaths) -> DiagnosticReport:
    """执行创建任务前资源诊断；paths 为便携程序路径集合。"""
    manifest_report = verify_runtime_manifest(paths.runtime_root)
    required = [
        _required_path_item(paths.soffice),
        _active_rulepack_item(paths.rulepacks),
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
