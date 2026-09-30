"""验证非任务用途 JSON 文件的原子写入。"""

import json
from pathlib import Path

import pytest


def test_atomic_write_json_replaces_complete_document(tmp_path: Path) -> None:
    """原子写入必须发布完整文档；tmp_path 为隔离目录。"""
    from risk_audit_web.atomic_files import atomic_write_json

    target = tmp_path / "session.json"
    atomic_write_json(target, {"message": "中文", "status": "ready"})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "message": "中文",
        "status": "ready",
    }


def test_atomic_write_json_preserves_old_document_on_replace_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """替换失败时必须保留旧文档；monkeypatch 为补丁工具，tmp_path 为隔离目录。"""
    from risk_audit_web import atomic_files

    target = tmp_path / "session.json"
    target.write_text('{"status":"old"}\n', encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        """模拟替换失败；source 为临时文件，destination 为目标文件。"""
        raise OSError("replace failed")

    monkeypatch.setattr(atomic_files.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_files.atomic_write_json(target, {"status": "new"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "old"}
    assert list(tmp_path.glob(".session.json.*.tmp")) == []
