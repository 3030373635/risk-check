"""验证 Web 任务隐藏目录的白名单清理与路径安全。"""

from pathlib import Path

import pytest


ALLOWLIST = {
    "worker.log",
    "report/audit.log",
    "report/_risk_audit/未审核文件.json",
    "report/_risk_audit/未完成项.json",
}


def _create_task_files(output_root: Path) -> Path:
    """创建包含保留文件和冗余文件的任务目录。

    Args:
        output_root: 任务输出根目录。

    Returns:
        新建的 ``.task`` 目录。
    """
    task_root = output_root / ".task"
    for relative_path in ALLOWLIST:
        path = task_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[]" if path.suffix == ".json" else "log", encoding="utf-8")
    for relative_path in (
        "report/findings.json",
        "report/review_tasks.json",
        "report/_risk_audit/ownership.json",
        "report/entities/0001/result.json",
    ):
        path = task_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    return task_root


def _task_files(task_root: Path) -> set[str]:
    """返回任务目录内的普通文件相对路径；task_root 为任务目录。"""
    return {
        path.relative_to(task_root).as_posix()
        for path in task_root.rglob("*")
        if path.is_file()
    }


def test_compact_task_artifacts_keeps_only_allowlist(tmp_path: Path) -> None:
    """白名单清理后只能保留四个用户需要的隐藏文件。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """
    from risk_audit_web.task_artifacts import compact_task_artifacts

    task_root = _create_task_files(tmp_path)

    assert compact_task_artifacts(tmp_path) == ()
    assert _task_files(task_root) == ALLOWLIST


def test_compact_task_artifacts_is_idempotent(tmp_path: Path) -> None:
    """重复清理不得删除白名单文件或报错。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """
    from risk_audit_web.task_artifacts import compact_task_artifacts

    task_root = _create_task_files(tmp_path)
    compact_task_artifacts(tmp_path)

    assert compact_task_artifacts(tmp_path) == ()
    assert _task_files(task_root) == ALLOWLIST


def test_compact_task_artifacts_removes_empty_directories(tmp_path: Path) -> None:
    """清理冗余文件后必须移除无用空目录。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """
    from risk_audit_web.task_artifacts import compact_task_artifacts

    task_root = _create_task_files(tmp_path)
    (task_root / "report/empty/deep").mkdir(parents=True)

    compact_task_artifacts(tmp_path)

    assert not (task_root / "report/empty").exists()
    assert not (task_root / "report/entities").exists()


def test_compact_task_artifacts_rejects_task_root_symlink(tmp_path: Path) -> None:
    """任务根为符号链接时必须拒绝清理。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """
    from risk_audit_web.task_artifacts import TaskArtifactCleanupError, compact_task_artifacts

    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / ".task").symlink_to(external, target_is_directory=True)

    with pytest.raises(TaskArtifactCleanupError, match="链接"):
        compact_task_artifacts(tmp_path)


def test_compact_task_artifacts_does_not_follow_inner_symlink(tmp_path: Path) -> None:
    """内部链接只能删除链接本身，不得修改外部目标。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """
    from risk_audit_web.task_artifacts import compact_task_artifacts

    task_root = _create_task_files(tmp_path)
    external = tmp_path / "external.txt"
    external.write_text("sentinel", encoding="utf-8")
    link = task_root / "report/entities/external-link"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(external)

    compact_task_artifacts(tmp_path)

    assert external.read_text(encoding="utf-8") == "sentinel"
    assert not link.exists()
