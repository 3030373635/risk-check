"""验证审核核心的结构化进度与安全取消协议。"""

from pathlib import Path

import pytest


def test_progress_event_serializes_only_defined_fields() -> None:
    """未设置的进度字段不得写入任务状态，避免用空值覆盖已有上下文。"""
    from risk_audit.progress import AuditProgressEvent

    event = AuditProgressEvent(stage="audit", completed_units=2, total_units=5)

    assert event.to_dict() == {
        "stage": "audit",
        "completed_units": 2,
        "total_units": 5,
    }


def test_raise_if_cancelled_is_noop_without_check() -> None:
    """命令行未传取消检查器时，现有审核调用必须保持不变。"""
    from risk_audit.progress import raise_if_cancelled

    raise_if_cancelled(None)


def test_raise_if_cancelled_raises_only_for_requested_cancel() -> None:
    """只有取消检查器明确返回真时才在安全边界中止审核。"""
    from risk_audit.progress import AuditCancelled, raise_if_cancelled

    raise_if_cancelled(lambda: False)
    with pytest.raises(AuditCancelled, match="用户已请求停止审核"):
        raise_if_cancelled(lambda: True)


def test_audit_reports_startup_and_completed_events(monkeypatch, tmp_path: Path) -> None:
    """审核包装层必须报告确定的开始和完成终态。"""
    from risk_audit import runner

    events = []
    expected = {
        "entity_results": [],
        "findings": 0,
        "warnings": 0,
        "write_completed": True,
    }
    monkeypatch.setattr(runner, "_audit", lambda *args, **kwargs: expected)

    result = runner.audit(
        tmp_path / "input",
        tmp_path / "output",
        tmp_path / "pack",
        tmp_path / "entities.xlsx",
        tmp_path,
        tmp_path / "runs",
        run_id="progress-success",
        progress_callback=events.append,
    )

    assert result is expected
    assert [event.stage for event in events] == ["startup", "completed"]
    assert events[-1].completed_units == 0
    assert events[-1].total_units == 0


def test_audit_reports_failed_event_and_preserves_exception(monkeypatch, tmp_path: Path) -> None:
    """核心异常必须报告失败进度，同时保留原异常供 Worker 记录堆栈。"""
    from risk_audit import runner

    events = []

    def fail(*args, **kwargs):
        """模拟核心失败；参数与审核核心一致但本测试不使用。"""
        raise ValueError("测试失败")

    monkeypatch.setattr(runner, "_audit", fail)

    with pytest.raises(ValueError, match="测试失败"):
        runner.audit(
            tmp_path / "input",
            tmp_path / "output",
            tmp_path / "pack",
            tmp_path / "entities.xlsx",
            tmp_path,
            tmp_path / "runs",
            run_id="progress-failed",
            progress_callback=events.append,
        )

    assert [event.stage for event in events] == ["startup", "failed"]
    assert events[-1].message == "测试失败"


def test_explicit_model_root_is_forwarded_to_core(monkeypatch, tmp_path: Path) -> None:
    """桌面 Worker 指定的模型目录必须原样传给审核核心。"""
    from risk_audit import runner

    captured = {}

    def capture(*args, **kwargs):
        """记录包装层传入的关键字参数并返回最小成功结果。"""
        captured.update(kwargs)
        return {
            "entity_results": [],
            "findings": 0,
            "warnings": 0,
            "write_completed": True,
        }

    monkeypatch.setattr(runner, "_audit", capture)
    model_root = tmp_path / "runtime/resources/models/bge-small-zh-v1.5"

    runner.audit(
        tmp_path / "input",
        tmp_path / "output",
        tmp_path / "pack",
        tmp_path / "entities.xlsx",
        tmp_path,
        tmp_path / "runs",
        run_id="model-root",
        model_root=model_root,
    )

    assert captured["model_root"] == model_root
