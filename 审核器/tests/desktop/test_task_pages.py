"""验证桌面端主要页面的展示与交互契约。"""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt


def make_record(tmp_path: Path, task_id: str, name: str, created_at: str):
    """创建页面测试任务记录；参数为测试根、编号、名称和创建时间。"""
    from risk_audit_desktop.task_contracts import TaskRecord

    output_root = (tmp_path / task_id).resolve()
    return TaskRecord(
        schema_version="1.0",
        task_id=task_id,
        display_name=name,
        input_root=str((tmp_path / "input").resolve()),
        output_root=str(output_root),
        created_at=created_at,
        state_path=str((output_root / "_task/state.json").resolve()),
    )


def make_state(task_id: str, status: str, progress: int | None = None):
    """创建页面测试状态；参数为任务编号、状态和可选进度。"""
    from risk_audit_desktop.task_contracts import TaskState

    return TaskState(
        schema_version="1.0",
        task_id=task_id,
        status=status,
        stage="audit",
        completed_units=17 if progress is not None else 0,
        total_units=25 if progress is not None else None,
        progress_percent=progress,
        current_entity="长安汽车",
        current_business="采购业务",
        current_file="风控矩阵.xlsx",
        worker_pid=123 if status in {"running", "cancelling"} else None,
        started_at="2026-09-24T10:30:15+08:00",
        heartbeat_at="2026-09-24T10:31:15+08:00",
        message="正在审核",
        error_code="E100" if status == "failed" else None,
        result_summary={"passed": 20, "warnings": 3, "failed": 2}
        if status == "completed" else None,
    )


def test_task_list_renders_sorted_status_progress_and_actions(qtbot, tmp_path: Path) -> None:
    """列表必须倒序展示任务，并根据状态提供明确操作。"""
    from risk_audit_desktop.task_list_page import TaskListPage

    records = [
        make_record(tmp_path, "running-001", "第一批资料", "2026-09-24T10:00:00+08:00"),
        make_record(tmp_path, "completed-001", "第二批资料", "2026-09-24T11:00:00+08:00"),
        make_record(tmp_path, "failed-001", "第三批资料", "2026-09-24T12:00:00+08:00"),
    ]
    states = {
        "running-001": make_state("running-001", "running"),
        "completed-001": make_state("completed-001", "completed", 100),
        "failed-001": make_state("failed-001", "failed", 40),
    }
    page = TaskListPage()
    qtbot.addWidget(page)

    page.set_tasks(records, states)

    model = page.table.model()
    assert model.rowCount() == 3
    assert model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole) == "第三批资料"
    assert model.data(model.index(0, 3), Qt.ItemDataRole.DisplayRole) == "审核失败"
    assert model.data(model.index(0, 5), Qt.ItemDataRole.DisplayRole) == "重试"
    assert model.data(model.index(1, 5), Qt.ItemDataRole.DisplayRole) == "结果"
    assert model.data(model.index(2, 4), Qt.ItemDataRole.DisplayRole) == "正在扫描"
    assert model.data(model.index(2, 5), Qt.ItemDataRole.DisplayRole) == "查看"
    assert "running-001" in model.data(model.index(2, 1), Qt.ItemDataRole.DisplayRole)


def test_create_page_defaults_output_and_keeps_it_when_name_changes(qtbot, tmp_path: Path) -> None:
    """选择材料后自动生成名称与输出，改任务名不得改输出路径。"""
    from risk_audit_desktop.diagnostics import DiagnosticItem, DiagnosticReport
    from risk_audit_desktop.task_create_page import TaskCreatePage

    input_root = tmp_path / "第一批资料0924"
    input_root.mkdir()
    outputs_root = tmp_path / "outputs"
    report = DiagnosticReport([DiagnosticItem("ok", "runtime", "运行资源完整")])
    page = TaskCreatePage(
        outputs_root,
        report,
        clock=lambda: datetime(2026, 9, 24, 14, 5, 6),
    )
    qtbot.addWidget(page)

    page.set_input_directory(input_root)

    expected = outputs_root / "第一批资料0924-20260924-140506"
    assert page.name_edit.text() == "第一批资料0924"
    assert page.output_edit.text() == str(expected)
    page.name_edit.setText("九月首批审核")
    assert page.output_edit.text() == str(expected)

    with qtbot.waitSignal(page.create_requested, timeout=1000) as emitted:
        qtbot.mouseClick(page.create_button, Qt.MouseButton.LeftButton)
    form = emitted.args[0]
    assert form.display_name == "九月首批审核"
    assert form.input_root == input_root.resolve()
    assert form.output_root == expected.resolve()


def test_create_page_disables_creation_and_shows_failed_resource_path(qtbot, tmp_path: Path) -> None:
    """资源诊断失败时必须禁用创建并显示具体路径。"""
    from risk_audit_desktop.diagnostics import DiagnosticItem, DiagnosticReport
    from risk_audit_desktop.task_create_page import TaskCreatePage

    missing = tmp_path / "runtime/libreoffice/program/soffice.exe"
    report = DiagnosticReport([DiagnosticItem("missing", str(missing), f"资源缺失：{missing}")])
    page = TaskCreatePage(tmp_path / "outputs", report)
    qtbot.addWidget(page)

    assert not page.create_button.isEnabled()
    assert str(missing) in page.diagnostics_label.text()


def test_create_page_selects_output_parent_and_generates_new_task_directory(qtbot, tmp_path: Path, monkeypatch) -> None:
    """手动选择保存位置时必须生成未存在任务目录，不能直接使用已存在父目录。"""
    from PySide6.QtWidgets import QFileDialog
    from risk_audit_desktop.diagnostics import DiagnosticItem, DiagnosticReport
    from risk_audit_desktop.task_create_page import TaskCreatePage

    input_root = tmp_path / "材料"
    input_root.mkdir()
    selected_parent = tmp_path / "自定义输出位置"
    selected_parent.mkdir()
    report = DiagnosticReport([DiagnosticItem("ok", "runtime", "已就绪")])
    page = TaskCreatePage(tmp_path / "outputs", report, clock=lambda: datetime(2026, 9, 24, 16, 0, 0))
    qtbot.addWidget(page)
    page.set_input_directory(input_root)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(selected_parent))

    qtbot.mouseClick(page.output_button, Qt.MouseButton.LeftButton)

    assert page.output_edit.text() == str(selected_parent / "材料-20260924-160000")
    assert not Path(page.output_edit.text()).exists()


def test_detail_page_renders_progress_context_results_and_actions(qtbot, tmp_path: Path) -> None:
    """详情页必须展示真实进度、当前上下文、结果与入口。"""
    from risk_audit_desktop.task_detail_page import TaskDetailPage

    record = make_record(tmp_path, "detail-001", "明细审核", "2026-09-24T10:00:00+08:00")
    page = TaskDetailPage()
    qtbot.addWidget(page)

    page.set_task(record, make_state("detail-001", "running", 68))

    assert page.status_label.text() == "审核中"
    assert page.progress_bar.value() == 68
    assert page.progress_label.text() == "17 / 25（68%）"
    assert page.stage_label.text() == "规则审核"
    assert page.entity_label.text() == "长安汽车"
    assert page.business_label.text() == "采购业务"
    assert page.file_label.text() == "风控矩阵.xlsx"
    assert page.elapsed_label.text()
    assert page.stop_button.isVisibleTo(page) is False or page.stop_button.isEnabled()

    page.set_task(record, make_state("detail-001", "completed", 100))
    assert "通过 20" in page.summary_label.text()
    assert page.open_output_button.isEnabled()
    assert page.open_statistics_button.isEnabled()
    assert page.open_review_button.isEnabled()


def test_detail_page_uses_indeterminate_progress_and_safe_error_text(qtbot, tmp_path: Path) -> None:
    """未知总量显示扫描状态，失败只显示简要说明和错误编号。"""
    from risk_audit_desktop.task_detail_page import TaskDetailPage

    record = make_record(tmp_path, "failed-002", "错误审核", "2026-09-24T10:00:00+08:00")
    state = make_state("failed-002", "running")
    page = TaskDetailPage()
    qtbot.addWidget(page)
    page.set_task(record, state)
    assert page.progress_bar.maximum() == 0
    assert page.progress_label.text() == "正在扫描"

    failed = state.with_updates(
        status="failed",
        message="文件读取失败\nTraceback: 内部调用栈",
        error_code="E-XLS-01",
    )
    page.set_task(record, failed)
    assert "E-XLS-01" in page.error_label.text()
    assert "文件读取失败" in page.error_label.text()
    assert "Traceback" not in page.error_label.text()


def test_help_page_explains_portability_output_stop_and_offline(qtbot) -> None:
    """帮助页必须覆盖便携、输出、停止和离线特性。"""
    from risk_audit_desktop.help_page import HelpPage

    page = HelpPage()
    qtbot.addWidget(page)
    text = page.help_text.toPlainText()
    for phrase in ("整体移动", "outputs", "停止任务", "不联网"):
        assert phrase in text


def test_theme_applies_accessible_palette_and_stylesheet(qapp) -> None:
    """主题必须能根据系统配色生成完整样式。"""
    from risk_audit_desktop.theme import apply_theme

    mode = apply_theme(qapp)

    assert mode in {"light", "dark"}
    assert qapp.styleSheet()
    assert qapp.palette().color(qapp.palette().ColorRole.Window).isValid()
