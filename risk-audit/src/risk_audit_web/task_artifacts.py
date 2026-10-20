"""安全压缩 Web 任务隐藏产物。"""

from __future__ import annotations

import os
from pathlib import Path
import stat


RETAINED_TASK_FILES = frozenset({
    "worker.log",
    "report/audit.log",
    "report/_risk_audit/未审核文件.json",
    "report/_risk_audit/未完成项.json",
})


class TaskArtifactCleanupError(RuntimeError):
    """表示任务产物根目录不安全或保留文件无效。"""


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    """判断目录项是否为 Windows 重解析点。

    Args:
        file_stat: 使用 ``follow_symlinks=False`` 取得的文件状态。
    """
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(file_stat, "st_file_attributes", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _unlink_entry(path: Path, file_stat: os.stat_result) -> None:
    """删除普通文件或链接本身，绝不遍历其目标。

    Args:
        path: 需要删除的目录项路径。
        file_stat: 不跟随链接取得的目录项状态。
    """
    if stat.S_ISDIR(file_stat.st_mode) and _is_reparse_point(file_stat):
        path.rmdir()
        return
    path.unlink()


def _compact_directory(
    task_root: Path,
    directory: Path,
    warnings: list[str],
) -> None:
    """逐项清理一个真实目录且不跟随链接。

    Args:
        task_root: 已验证的任务隐藏目录根。
        directory: 当前需要扫描的真实目录。
        warnings: 累计非致命清理警告。
    """
    try:
        entries = list(os.scandir(directory))
    except OSError as error:
        warnings.append(f"无法扫描任务产物目录：{directory}：{error}")
        return
    for entry in entries:
        path = Path(entry.path)
        relative_path = path.relative_to(task_root).as_posix()
        try:
            file_stat = entry.stat(follow_symlinks=False)
            is_link_like = stat.S_ISLNK(file_stat.st_mode) or _is_reparse_point(file_stat)
            if is_link_like:
                # 链接与重解析点只删除目录项本身，禁止访问其外部目标。
                _unlink_entry(path, file_stat)
                continue
            if stat.S_ISDIR(file_stat.st_mode):
                _compact_directory(task_root, path, warnings)
                try:
                    path.rmdir()
                except OSError:
                    # 白名单文件的父目录非空，或并发新增文件时保留目录。
                    pass
                continue
            if relative_path not in RETAINED_TASK_FILES:
                # 普通冗余文件可直接删除，不需要读取或解析其内容。
                path.unlink()
        except OSError as error:
            warnings.append(f"无法清理任务产物：{relative_path}：{error}")


def compact_task_artifacts(output_root: Path) -> tuple[str, ...]:
    """将 ``.task`` 压缩为固定白名单。

    Args:
        output_root: 用户选择的任务输出根目录。

    Returns:
        非致命清理警告；空元组表示全部清理成功。

    Raises:
        TaskArtifactCleanupError: ``.task`` 根不是安全的真实目录。
    """
    task_root = output_root / ".task"
    if not task_root.exists() and not task_root.is_symlink():
        return ()
    root_stat = task_root.lstat()
    if stat.S_ISLNK(root_stat.st_mode) or _is_reparse_point(root_stat):
        raise TaskArtifactCleanupError(f"任务产物根目录不能是链接或重解析点：{task_root}")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise TaskArtifactCleanupError(f"任务产物根路径不是目录：{task_root}")
    warnings: list[str] = []
    _compact_directory(task_root, task_root, warnings)
    return tuple(warnings)


def validate_retained_task_artifacts(
    output_root: Path,
    required_paths: tuple[Path, ...],
) -> None:
    """验证发布终态所需的实际结果文件。

    Args:
        output_root: 用户选择的任务输出根目录。
        required_paths: Runner 返回的必需结果文件路径。

    Raises:
        TaskArtifactCleanupError: 路径越界、为链接或缺失时抛出。
    """
    resolved_output_root = output_root.resolve()
    for path in required_paths:
        candidate = Path(path)
        try:
            candidate.relative_to(resolved_output_root)
        except ValueError as error:
            raise TaskArtifactCleanupError(f"任务结果路径超出输出目录：{candidate}") from error
        if candidate.is_symlink() or not candidate.is_file():
            raise TaskArtifactCleanupError(f"任务必需结果文件缺失或不安全：{candidate}")
