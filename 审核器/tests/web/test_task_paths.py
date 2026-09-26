"""验证便携目录、默认输出命名和路径保护。"""

from datetime import datetime
from pathlib import Path
import re

import pytest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("第一批资料0924", "第一批资料0924"),
        ("甲:乙*丙?", "甲_乙_丙_"),
        ("名称. ", "名称"),
        ("...", "审核任务"),
    ],
)
def test_sanitize_directory_name_replaces_only_windows_invalid_parts(
    raw: str,
    expected: str,
) -> None:
    """非法字符和尾部点空格不得生成 Windows 无效目录。"""
    from risk_audit_web.task_paths import sanitize_directory_name

    assert sanitize_directory_name(raw) == expected


def test_default_output_uses_input_name_and_creation_time(tmp_path: Path) -> None:
    """默认输出必须使用材料目录名，不能使用任务展示名。"""
    from risk_audit_web.task_paths import default_output_path

    output = default_output_path(
        Path("D:/报送材料/第一批资料0924"),
        tmp_path / "outputs",
        datetime(2026, 9, 24, 10, 30, 15),
    )

    assert output == tmp_path / "outputs/第一批资料0924-20260924-103015"


def test_default_output_increments_existing_name_without_overwrite(tmp_path: Path) -> None:
    """同秒同名目录存在时必须递增后缀，不能覆盖已有结果。"""
    from risk_audit_web.task_paths import default_output_path

    outputs_root = tmp_path / "outputs"
    existing = outputs_root / "第一批资料0924-20260924-103015"
    existing.mkdir(parents=True)

    output = default_output_path(
        Path("D:/报送材料/第一批资料0924"),
        outputs_root,
        datetime(2026, 9, 24, 10, 30, 15),
    )

    assert output == outputs_root / "第一批资料0924-20260924-103015-2"


@pytest.mark.parametrize(
    ("platform_name", "relative_soffice"),
    [
        ("win32", "runtime/libreoffice/program/soffice.exe"),
        ("darwin", "runtime/libreoffice/LibreOffice.app/Contents/MacOS/soffice"),
    ],
)
def test_portable_paths_are_anchored_to_app_root_for_each_platform(
    tmp_path: Path,
    platform_name: str,
    relative_soffice: str,
) -> None:
    """发布路径必须锚定显式根目录并选择对应平台的 LibreOffice。"""
    from risk_audit_web.task_paths import PortablePaths

    app_root = tmp_path / "含 空格的发布目录"
    paths = PortablePaths.from_app_root(app_root, platform_name=platform_name)

    assert paths.app_root == app_root.resolve()
    assert paths.runtime_root == paths.app_root / "runtime"
    assert paths.data_root == paths.app_root / "data"
    assert paths.outputs_root == paths.app_root / "outputs"
    assert paths.soffice == paths.app_root / relative_soffice


@pytest.mark.parametrize("relation", ["same", "output_inside_input", "input_inside_output"])
def test_validate_task_paths_rejects_overlap(tmp_path: Path, relation: str) -> None:
    """任何输入输出重叠都可能污染原材料，必须在创建目录前拒绝。"""
    from risk_audit_web.task_paths import PathValidationError, validate_task_paths

    if relation == "same":
        input_root = tmp_path / "materials"
        output_root = input_root
        input_root.mkdir()
    elif relation == "output_inside_input":
        input_root = tmp_path / "materials"
        input_root.mkdir()
        output_root = input_root / "result"
    else:
        output_root = tmp_path / "result"
        input_root = output_root / "materials"
        input_root.mkdir(parents=True)

    with pytest.raises(PathValidationError, match="相同或相互包含"):
        validate_task_paths(input_root, output_root)


def test_validate_task_paths_rejects_existing_output(tmp_path: Path) -> None:
    """已有输出目录不得被复用，避免覆盖历史审核结果。"""
    from risk_audit_web.task_paths import PathValidationError, validate_task_paths

    input_root = tmp_path / "materials"
    output_root = tmp_path / "outputs/task"
    input_root.mkdir()
    output_root.mkdir(parents=True)

    with pytest.raises(PathValidationError, match="已经存在"):
        validate_task_paths(input_root, output_root)


def test_validate_task_paths_rejects_unwritable_parent(monkeypatch, tmp_path: Path) -> None:
    """输出上级目录不可写时必须显示具体目录并阻止任务。"""
    from risk_audit_web.task_paths import PathValidationError, validate_task_paths

    input_root = tmp_path / "materials"
    output_parent = tmp_path / "outputs"
    input_root.mkdir()
    output_parent.mkdir()
    monkeypatch.setattr("risk_audit_web.task_paths.os.access", lambda *args: False)

    with pytest.raises(PathValidationError, match=re.escape(str(output_parent))):
        validate_task_paths(input_root, output_parent / "task")


def test_reserve_output_path_creates_unique_directory(tmp_path: Path) -> None:
    """正式创建必须独占保留目录，并在冲突时选择新名称。"""
    from risk_audit_web.task_paths import reserve_output_path

    outputs_root = tmp_path / "outputs"
    created_at = datetime(2026, 9, 24, 10, 30, 15)
    first = reserve_output_path(Path("D:/报送材料/资料"), outputs_root, created_at)
    second = reserve_output_path(Path("D:/报送材料/资料"), outputs_root, created_at)

    assert first.is_dir()
    assert second.is_dir()
    assert first.name == "资料-20260924-103015"
    assert second.name == "资料-20260924-103015-2"
