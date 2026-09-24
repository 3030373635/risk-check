"""便携程序目录、默认输出命名和路径保护。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import re
from threading import Lock


_INVALID_WINDOWS_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_OUTPUT_RESERVATION_LOCK = Lock()


class PathValidationError(ValueError):
    """表示任务输入或输出路径不满足安全约束。"""


@dataclass(frozen=True)
class PortablePaths:
    """保存便携程序的固定目录；所有路径均锚定在主程序目录。"""

    app_root: Path
    runtime_root: Path
    data_root: Path
    outputs_root: Path

    @classmethod
    def from_executable(cls, executable: Path) -> "PortablePaths":
        """由主程序路径生成固定目录；executable 为 EXE 或源码入口。"""
        app_root = executable.resolve().parent
        return cls(
            app_root=app_root,
            runtime_root=app_root / "runtime",
            data_root=app_root / "data",
            outputs_root=app_root / "outputs",
        )

    @property
    def soffice(self) -> Path:
        """返回包内 LibreOffice 可执行文件路径；无参数。"""
        return self.runtime_root / "libreoffice/program/soffice.exe"

    @property
    def rulepacks(self) -> Path:
        """返回发布规则包根目录；无参数。"""
        return self.runtime_root / "resources/rulepacks"

    @property
    def baseline_root(self) -> Path:
        """返回冻结基准根目录；无参数。"""
        return self.runtime_root / "resources/baselines"

    @property
    def entity_file(self) -> Path:
        """返回会计主体名册路径；无参数。"""
        return self.runtime_root / "resources/entities/会计主体清单20260907.xlsx"

    @property
    def model_root(self) -> Path:
        """返回本地语义模型目录；无参数。"""
        return self.runtime_root / "resources/models/bge-small-zh-v1.5"

    @property
    def config_file(self) -> Path:
        """返回桌面审核运行配置路径；无参数。"""
        return self.rulepacks / "audit-config.json"


def sanitize_directory_name(raw_name: str) -> str:
    """生成合法 Windows 目录名；raw_name 为材料目录原名。"""
    sanitized = _INVALID_WINDOWS_CHARACTERS.sub("_", raw_name).rstrip(" .")
    return sanitized or "审核任务"


def default_output_path(
    input_root: Path,
    outputs_root: Path,
    created_at: datetime,
) -> Path:
    """计算未占用的默认输出路径；参数依次为材料目录、输出根和创建时间。"""
    directory_name = sanitize_directory_name(input_root.name)
    timestamp = created_at.strftime("%Y%m%d-%H%M%S")
    base_name = f"{directory_name}-{timestamp}"
    candidate = outputs_root / base_name
    suffix = 2
    while candidate.exists():
        candidate = outputs_root / f"{base_name}-{suffix}"
        suffix += 1
    return candidate


def reserve_output_path(
    input_root: Path,
    outputs_root: Path,
    created_at: datetime,
) -> Path:
    """独占创建默认输出目录；参数依次为材料目录、输出根和创建时间。"""
    outputs_root.mkdir(parents=True, exist_ok=True)
    with _OUTPUT_RESERVATION_LOCK:
        while True:
            candidate = default_output_path(input_root, outputs_root, created_at)
            try:
                # mkdir 的排他语义是最终防线，避免同秒创建的任务互相覆盖。
                candidate.mkdir(parents=False, exist_ok=False)
                return candidate
            except FileExistsError:
                continue


def _nearest_existing_parent(path: Path) -> Path:
    """返回最近的已存在父目录；path 为尚未创建的目标路径。"""
    current = path.parent
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def validate_task_paths(input_root: Path, output_root: Path) -> None:
    """验证任务路径；参数为材料目录和拟创建输出目录，无返回值。"""
    input_resolved = input_root.resolve(strict=False)
    output_resolved = output_root.resolve(strict=False)
    if not input_resolved.is_dir():
        raise PathValidationError(f"待审核材料目录不存在或不是目录：{input_resolved}")
    if (
        input_resolved == output_resolved
        or input_resolved in output_resolved.parents
        or output_resolved in input_resolved.parents
    ):
        raise PathValidationError(
            f"输入目录与输出目录相同或相互包含：{input_resolved}；{output_resolved}"
        )
    if output_resolved.exists():
        raise PathValidationError(f"输出目录已经存在，不能覆盖历史结果：{output_resolved}")
    writable_parent = _nearest_existing_parent(output_resolved)
    if not writable_parent.is_dir() or not os.access(writable_parent, os.W_OK):
        raise PathValidationError(f"输出上级目录不可写：{writable_parent}")
