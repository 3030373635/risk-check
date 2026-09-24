"""审核任务列表页及其表格模型。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QTableView, QVBoxLayout, QWidget

from risk_audit_desktop.task_contracts import TaskRecord, TaskState


STATUS_TEXT = {
    "running": "审核中",
    "cancelling": "正在停止",
    "completed": "已完成",
    "partial": "部分完成",
    "failed": "审核失败",
    "cancelled": "已停止",
    "interrupted": "意外中断",
}


def task_action_text(status: str) -> str:
    """返回任务主操作文案；status 为任务状态。"""
    if status in {"completed", "partial"}:
        return "结果"
    if status in {"failed", "cancelled", "interrupted"}:
        return "重试"
    return "查看"


@dataclass(frozen=True)
class TaskListItem:
    """保存列表中同一任务的索引记录和最新状态。"""

    record: TaskRecord
    state: TaskState


class TaskTableModel(QAbstractTableModel):
    """将任务契约转换为只读表格。"""

    HEADERS = ("任务名称", "任务编号", "创建时间", "状态", "进度", "操作")

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化空模型；parent 为 Qt 父对象。"""
        super().__init__(parent)
        self._items: list[TaskListItem] = []

    def set_items(self, items: Sequence[TaskListItem]) -> None:
        """替换表格内容；items 为已排序任务项。"""
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()

    def item_at(self, row: int) -> TaskListItem | None:
        """返回指定行任务；row 为从零开始的行号。"""
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        """返回行数；parent 为 Qt 父索引。"""
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        """返回列数；parent 为 Qt 父索引。"""
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        """返回表头；section、orientation、role 由 Qt 传入。"""
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        """返回单元格数据；index 和 role 由 Qt 传入。"""
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        item = self._items[index.row()]
        record, state = item.record, item.state
        progress = "正在扫描" if state.progress_percent is None else f"{state.progress_percent}%"
        values = (
            record.display_name,
            record.task_id,
            record.created_at.replace("T", " "),
            STATUS_TEXT.get(state.status, state.status),
            progress,
            task_action_text(state.status),
        )
        return values[index.column()]


class TaskListPage(QWidget):
    """展示任务历史并将用户操作转换为页面信号。"""

    detail_requested = Signal(str)
    result_requested = Signal(str)
    retry_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化任务列表页；parent 为 Qt 父窗口。"""
        super().__init__(parent)
        self.title_label = QLabel("审核任务")
        self.title_label.setObjectName("pageTitle")
        self.table = QTableView()
        self.model = TaskTableModel(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.clicked.connect(self._on_table_clicked)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.table)

    def set_tasks(
        self,
        records: Sequence[TaskRecord],
        states: Mapping[str, TaskState],
    ) -> None:
        """刷新任务列表；records 为索引，states 为按编号存放的状态。"""
        items = [TaskListItem(record, states[record.task_id]) for record in records if record.task_id in states]
        items.sort(key=lambda item: item.record.created_at, reverse=True)
        self.model.set_items(items)

    def _on_table_clicked(self, index: QModelIndex) -> None:
        """处理表格点击；index 为被点击单元格。"""
        if index.column() != len(TaskTableModel.HEADERS) - 1:
            return
        item = self.model.item_at(index.row())
        if item is None:
            return
        action = task_action_text(item.state.status)
        if action == "结果":
            self.result_requested.emit(item.record.task_id)
        elif action == "重试":
            self.retry_requested.emit(item.record.task_id)
        else:
            self.detail_requested.emit(item.record.task_id)
