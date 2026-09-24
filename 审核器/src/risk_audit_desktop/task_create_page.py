"""新建审核任务表单页。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from risk_audit_desktop.diagnostics import DiagnosticReport
from risk_audit_desktop.task_paths import default_output_path


@dataclass(frozen=True)
class CreateTaskFormData:
    """保存用户确认的新任务表单数据。"""

    input_root: Path
    display_name: str
    output_root: Path


class TaskCreatePage(QWidget):
    """收集任务输入，但不直接调用审核核心。"""

    create_requested = Signal(object)

    def __init__(
        self,
        outputs_root: Path,
        diagnostics: DiagnosticReport,
        *,
        clock: Callable[[], datetime] = datetime.now,
        parent: QWidget | None = None,
    ) -> None:
        """初始化表单；参数为输出根、诊断报告、时钟和父窗口。"""
        super().__init__(parent)
        self._outputs_root = outputs_root.resolve()
        self._diagnostics = diagnostics
        self._clock = clock
        self.title_label = QLabel("新建审核任务")
        self.title_label.setObjectName("pageTitle")
        self.input_edit = QLineEdit()
        self.input_edit.setReadOnly(True)
        self.input_button = QPushButton("选择材料目录")
        self.input_button.clicked.connect(self._choose_input_directory)
        self.name_edit = QLineEdit()
        self.output_edit = QLineEdit()
        self.output_button = QPushButton("选择保存位置")
        self.output_button.clicked.connect(self._choose_output_directory)
        self.diagnostics_label = QLabel()
        self.diagnostics_label.setWordWrap(True)
        self.create_button = QPushButton("创建并开始审核")
        self.create_button.clicked.connect(self._emit_create_request)
        self.input_edit.textChanged.connect(self._update_create_enabled)
        self.name_edit.textChanged.connect(self._update_create_enabled)
        self.output_edit.textChanged.connect(self._update_create_enabled)

        input_row = QHBoxLayout()
        input_row.addWidget(self.input_edit)
        input_row.addWidget(self.input_button)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit)
        output_row.addWidget(self.output_button)
        form = QFormLayout()
        form.addRow("材料目录", input_row)
        form.addRow("任务名称", self.name_edit)
        form.addRow("输出目录", output_row)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addLayout(form)
        layout.addWidget(self.diagnostics_label)
        layout.addWidget(self.create_button)
        layout.addStretch()
        self._render_diagnostics()
        self._update_create_enabled()

    def _render_diagnostics(self) -> None:
        """将运行资源诊断转为用户可读文本；无参数。"""
        prefix = "✓ 运行资源已就绪" if self._diagnostics.can_start else "⚠ 运行资源检查未通过"
        details = [item.message for item in self._diagnostics.items if not item.ok]
        self.diagnostics_label.setText("\n".join([prefix, *details]))
        self.diagnostics_label.setProperty("status", "ok" if self._diagnostics.can_start else "error")

    def set_input_directory(self, input_root: Path) -> None:
        """设置材料目录并产生首次默认值；input_root 为用户选择的目录。"""
        resolved = input_root.resolve()
        self.input_edit.setText(str(resolved))
        self.name_edit.setText(resolved.name)
        output_root = default_output_path(resolved, self._outputs_root, self._clock())
        self.output_edit.setText(str(output_root.resolve()))

    def _choose_input_directory(self) -> None:
        """打开材料目录选择器；无参数。"""
        selected = QFileDialog.getExistingDirectory(self, "选择待审核材料目录")
        if selected:
            self.set_input_directory(Path(selected))

    def _choose_output_directory(self) -> None:
        """选择输出父目录并生成新任务目录；无参数。"""
        selected = QFileDialog.getExistingDirectory(self, "选择输出上级目录")
        if selected and self.input_edit.text():
            parent = Path(selected).resolve()
            input_root = Path(self.input_edit.text()).resolve()
            self.output_edit.setText(str(default_output_path(input_root, parent, self._clock()).resolve()))

    def _update_create_enabled(self, *_args: object) -> None:
        """根据诊断和必填字段刷新创建按钮；_args 为 Qt 信号参数。"""
        complete = all((self.input_edit.text(), self.name_edit.text().strip(), self.output_edit.text()))
        self.create_button.setEnabled(self._diagnostics.can_start and complete)

    def _emit_create_request(self) -> None:
        """发出结构化创建请求；无参数。"""
        if not self.create_button.isEnabled():
            return
        form = CreateTaskFormData(
            input_root=Path(self.input_edit.text()).resolve(),
            display_name=self.name_edit.text().strip(),
            output_root=Path(self.output_edit.text()).resolve(),
        )
        self.create_requested.emit(form)
