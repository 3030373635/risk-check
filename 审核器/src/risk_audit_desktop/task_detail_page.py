"""单个审核任务的进度与结果详情页。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from risk_audit_desktop.task_contracts import TaskRecord, TaskState
from risk_audit_desktop.task_list_page import STATUS_TEXT


STAGE_TEXT = {
    "startup": "启动审核",
    "prepare": "准备任务",
    "scan": "扫描材料",
    "validation": "主体与范围校验",
    "audit": "规则审核",
    "report": "生成报告",
    "output": "生成汇总结果",
    "completed": "已完成",
    "failed": "执行失败",
    "cancelled": "已停止",
}


def _elapsed_text(started_at: str | None, heartbeat_at: str) -> str:
    """计算用户可读运行时长；参数为开始与心跳时间。"""
    if not started_at:
        return "--"
    try:
        seconds = max(0, int((datetime.fromisoformat(heartbeat_at) - datetime.fromisoformat(started_at)).total_seconds()))
    except ValueError:
        return "--"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class TaskDetailPage(QWidget):
    """展示任务最新状态，所有外部操作都通过信号上报。"""

    stop_requested = Signal(str)
    open_output_requested = Signal(str)
    open_statistics_requested = Signal(str)
    open_review_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化详情页；parent 为 Qt 父窗口。"""
        super().__init__(parent)
        self._task_id: str | None = None
        self.title_label = QLabel("任务详情")
        self.title_label.setObjectName("pageTitle")
        self.status_label = QLabel("--")
        self.progress_bar = QProgressBar()
        self.progress_label = QLabel("--")
        self.stage_label = QLabel("--")
        self.entity_label = QLabel("--")
        self.business_label = QLabel("--")
        self.file_label = QLabel("--")
        self.path_label = QLabel("--")
        self.path_label.setWordWrap(True)
        self.elapsed_label = QLabel("--")
        self.summary_label = QLabel("任务完成后将显示结果摘要")
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.stop_button = QPushButton("停止任务")
        self.open_output_button = QPushButton("打开输出目录")
        self.open_statistics_button = QPushButton("查看统计表")
        self.open_review_button = QPushButton("进入集中复核")
        self.stop_button.clicked.connect(lambda: self._emit(self.stop_requested))
        self.open_output_button.clicked.connect(lambda: self._emit(self.open_output_requested))
        self.open_statistics_button.clicked.connect(lambda: self._emit(self.open_statistics_requested))
        self.open_review_button.clicked.connect(lambda: self._emit(self.open_review_requested))

        form = QFormLayout()
        for label, widget in (
            ("状态", self.status_label), ("当前阶段", self.stage_label),
            ("审核进度", self.progress_label), ("当前主体", self.entity_label),
            ("当前业务", self.business_label), ("当前文件", self.file_label),
            ("输出位置", self.path_label), ("运行时间", self.elapsed_label),
        ):
            form.addRow(label, widget)
        actions = QHBoxLayout()
        for button in (self.stop_button, self.open_output_button, self.open_statistics_button, self.open_review_button):
            actions.addWidget(button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.progress_bar)
        layout.addLayout(form)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.error_label)
        layout.addLayout(actions)
        layout.addStretch()
        self._set_result_buttons_enabled(False)

    def _emit(self, signal: Signal) -> None:
        """向主窗口发出当前任务操作；signal 为目标 Qt 信号。"""
        if self._task_id:
            signal.emit(self._task_id)

    def _set_result_buttons_enabled(self, enabled: bool) -> None:
        """统一更新结果入口；enabled 为是否可用。"""
        for button in (self.open_output_button, self.open_statistics_button, self.open_review_button):
            button.setEnabled(enabled)

    def set_task(self, record: TaskRecord, state: TaskState) -> None:
        """刷新页面；record 为任务索引，state 为最新状态。"""
        self._task_id = record.task_id
        self.title_label.setText(record.display_name)
        self.status_label.setText(STATUS_TEXT.get(state.status, state.status))
        self.stage_label.setText(STAGE_TEXT.get(state.stage, state.stage))
        if state.progress_percent is None or state.total_units is None:
            self.progress_bar.setRange(0, 0)
            self.progress_label.setText("正在扫描")
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(state.progress_percent)
            self.progress_label.setText(
                f"{state.completed_units} / {state.total_units}（{state.progress_percent}%）"
            )
        self.entity_label.setText(state.current_entity or "--")
        self.business_label.setText(state.current_business or "--")
        self.file_label.setText(state.current_file or "--")
        self.path_label.setText(record.output_root)
        self.elapsed_label.setText(_elapsed_text(state.started_at, state.heartbeat_at))
        terminal = state.status in {"completed", "partial", "failed", "cancelled", "interrupted"}
        self.stop_button.setEnabled(not terminal and state.status != "cancelling")
        self._set_result_buttons_enabled(state.status in {"completed", "partial"})
        summary = state.result_summary or {}
        if "input_files" in summary:
            self.summary_label.setText(
                f"处理文件 {summary.get('input_files', 0)}  ·  审核意见 {summary.get('findings', 0)}  ·  "
                f"待复核 {summary.get('warnings', 0)}  ·  未完成 {summary.get('limitations', 0)}"
            )
        elif summary:
            self.summary_label.setText(
                f"通过 {summary.get('passed', 0)}  ·  提醒 {summary.get('warnings', 0)}  ·  未通过 {summary.get('failed', 0)}"
            )
        else:
            self.summary_label.setText(state.message or "任务执行中")
        if state.status == "failed":
            # 只保留第一行业务说明，避免将内部调用栈暴露给普通用户。
            brief = (state.message or "审核执行失败").splitlines()[0]
            code = f"（错误编号：{state.error_code}）" if state.error_code else ""
            self.error_label.setText(f"{brief}{code}")
        else:
            self.error_label.clear()
