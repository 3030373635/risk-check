"""桌面端主窗口及 GUI 启动编排。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import sys
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from risk_audit_desktop.diagnostics import DiagnosticReport, run_startup_diagnostics
from risk_audit_desktop.help_page import HelpPage
from risk_audit_desktop.platform_windows import is_process_alive, open_path
from risk_audit_desktop.single_instance import SingleInstanceCoordinator
from risk_audit_desktop.task_contracts import TaskRecord, TaskState
from risk_audit_desktop.task_create_page import CreateTaskFormData, TaskCreatePage
from risk_audit_desktop.task_detail_page import TaskDetailPage
from risk_audit_desktop.task_list_page import TaskListPage
from risk_audit_desktop.task_manager import TaskManager
from risk_audit_desktop.task_paths import PortablePaths
from risk_audit_desktop.task_store import TaskStore
from risk_audit_desktop.theme import apply_theme
from risk_audit_desktop.tray import TrayController


CloseDecisionProvider = Callable[[], str]


def recover_startup_tasks(store: TaskStore) -> Any:
    """在创建窗口前恢复历史任务；store 为便携任务存储。"""
    return store.recover_tasks(is_process_alive=is_process_alive)


def desktop_worker_arguments(executable: Path, request_path: Path, *, frozen: bool) -> list[str]:
    """生成 Worker 启动参数；参数为主程序、请求路径和是否已打包。"""
    if frozen:
        return [str(executable), "--worker", str(request_path)]
    return [str(executable), "-m", "risk_audit_desktop.app", "--worker", str(request_path)]


def load_application_icon() -> QIcon:
    """加载源码或 PyInstaller 包内图标；无参数。"""
    candidates = [Path(__file__).resolve().parent / "assets/app.ico"]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if isinstance(bundle_root, str):
        candidates.insert(0, Path(bundle_root) / "app.ico")
    for candidate in candidates:
        if candidate.is_file():
            return QIcon(str(candidate))
    return QIcon()


class MainWindow(QMainWindow):
    """协调页面、任务存储和 Worker 管理，不直接调用审核核心。"""

    def __init__(
        self,
        paths: PortablePaths,
        store: TaskStore,
        manager: TaskManager,
        diagnostics: DiagnosticReport,
        *,
        close_decision_provider: CloseDecisionProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """初始化主窗口；参数为便携路径、存储、管理器、诊断和关闭决策。"""
        super().__init__(parent)
        self.paths = paths
        self.store = store
        self.manager = manager
        self.diagnostics = diagnostics
        self._close_decision_provider = close_decision_provider or self._ask_close_decision
        self._states: dict[str, TaskState] = {}
        self._records: dict[str, TaskRecord] = {}
        self.setWindowTitle("风控矩阵审核器")
        self.resize(1160, 760)

        self.navigation = QListWidget()
        self.navigation.addItems(["审核任务", "新建任务", "使用帮助"])
        self.navigation.setFixedWidth(190)
        self.running_count_label = QLabel("0 个任务运行中")
        self.task_list_page = TaskListPage()
        self.task_create_page = TaskCreatePage(paths.outputs_root, diagnostics)
        self.help_page = HelpPage()
        self.task_detail_page = TaskDetailPage()
        self.pages = QStackedWidget()
        for page in (self.task_list_page, self.task_create_page, self.help_page, self.task_detail_page):
            self.pages.addWidget(page)

        sidebar = QVBoxLayout()
        sidebar.addWidget(self.navigation)
        sidebar.addStretch()
        sidebar.addWidget(self.running_count_label)
        body = QHBoxLayout()
        body.addLayout(sidebar)
        body.addWidget(self.pages, 1)
        container = QWidget()
        container.setLayout(body)
        self.setCentralWidget(container)

        self.navigation.currentRowChanged.connect(self._navigate)
        self.task_create_page.create_requested.connect(self._create_task)
        self.task_list_page.detail_requested.connect(self.show_task_detail)
        self.task_list_page.result_requested.connect(self.show_task_detail)
        self.task_list_page.retry_requested.connect(self._retry_task)
        self.task_detail_page.stop_requested.connect(self.manager.request_cancel)
        self.task_detail_page.open_output_requested.connect(self._open_output)
        self.task_detail_page.open_statistics_requested.connect(self._open_statistics)
        self.task_detail_page.open_review_requested.connect(self._open_review)
        self.manager.task_updated.connect(self._on_task_updated)
        self.manager.task_error.connect(self._on_task_error)
        self.manager.running_count_changed.connect(self.update_running_count)
        self.navigation.setCurrentRow(0)
        self.refresh_tasks()

    def _navigate(self, row: int) -> None:
        """切换主导航页；row 为导航索引。"""
        if 0 <= row <= 2:
            self.pages.setCurrentIndex(row)

    def refresh_tasks(self) -> None:
        """从磁盘重读任务列表和状态；无参数。"""
        records = self.store.list_tasks().records
        states: dict[str, TaskState] = {}
        for record in records:
            try:
                states[record.task_id] = self.store.read_state(record)
            except (OSError, ValueError, TypeError):
                continue
        self._records = {record.task_id: record for record in records}
        self._states.update(states)
        self.task_list_page.set_tasks(records, states)

    def _create_task(self, form: CreateTaskFormData) -> None:
        """持久化并立即启动任务；form 为页面提交数据。"""
        try:
            record = self.store.create_task(
                input_root=form.input_root,
                display_name=form.display_name,
                paths=self.paths,
                output_root=form.output_root,
            )
            self.manager.start_task(record)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "无法创建任务", str(error))
            return
        self.refresh_tasks()
        self.navigation.setCurrentRow(0)
        self.show_task_detail(record.task_id)

    def _retry_task(self, task_id: str) -> None:
        """将历史任务输入带入新建页；task_id 为原任务编号。"""
        record = self._records.get(task_id)
        if record is None:
            return
        self.navigation.setCurrentRow(1)
        self.task_create_page.set_input_directory(Path(record.input_root))
        self.task_create_page.name_edit.setText(record.display_name)

    def show_task_detail(self, task_id: str) -> None:
        """打开指定任务详情；task_id 为任务编号。"""
        record = self._records.get(task_id)
        state = self._states.get(task_id)
        if record is None or state is None:
            return
        self.task_detail_page.set_task(record, state)
        self.pages.setCurrentWidget(self.task_detail_page)
        self.navigation.clearSelection()

    def _on_task_updated(self, task_id: str, state: TaskState) -> None:
        """接收 Worker 状态刷新；参数为任务编号和状态。"""
        self._states[task_id] = state
        self.task_list_page.set_tasks(list(self._records.values()), self._states)
        if self.task_detail_page._task_id == task_id and task_id in self._records:
            self.task_detail_page.set_task(self._records[task_id], state)

    def _on_task_error(self, task_id: str, message: str) -> None:
        """显示单任务刷新错误；参数为任务编号和说明。"""
        self.statusBar().showMessage(f"任务 {task_id} 状态暂时无法读取：{message}", 8000)

    def update_running_count(self, count: int) -> None:
        """更新左下角运行数；count 为活动任务数。"""
        if count == 0:
            text = "0 个任务运行中"
        elif count == 1:
            text = "1 个任务运行中"
        else:
            text = f"{count} 个任务并行运行"
        self.running_count_label.setText(text)

    def _record_path(self, task_id: str, relative: str = "") -> Path | None:
        """返回任务输出内路径；参数为任务编号和相对路径。"""
        record = self._records.get(task_id)
        return Path(record.output_root) / relative if record else None

    def _open_output(self, task_id: str) -> None:
        """打开任务输出目录；task_id 为任务编号。"""
        path = self._record_path(task_id)
        if path:
            open_path(path)

    def _open_statistics(self, task_id: str) -> None:
        """打开审核统计表；task_id 为任务编号。"""
        summary = (self._states.get(task_id).result_summary or {}) if task_id in self._states else {}
        configured = summary.get("audit_statistics_report")
        path = Path(configured) if isinstance(configured, str) else self._record_path(task_id, "审核统计表.xlsx")
        if path:
            open_path(path)

    def _open_review(self, task_id: str) -> None:
        """打开集中复核资料；task_id 为任务编号。"""
        summary = (self._states.get(task_id).result_summary or {}) if task_id in self._states else {}
        configured = summary.get("review_report")
        path = Path(configured) if isinstance(configured, str) else None
        if path:
            open_path(path)

    def _ask_close_decision(self) -> str:
        """询问有运行任务时的关闭方式；返回 tray、stop 或 cancel。"""
        box = QMessageBox(self)
        box.setWindowTitle("任务仍在运行")
        box.setText("当前审核任务仍在运行，请选择关闭方式。")
        tray_button = box.addButton("最小化到托盘并继续运行（推荐）", QMessageBox.ButtonRole.AcceptRole)
        stop_button = box.addButton("停止全部任务并退出", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(tray_button)
        box.exec()
        if box.clickedButton() is tray_button:
            return "tray"
        if box.clickedButton() is stop_button:
            return "stop"
        return "cancel"

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """按活动任务和用户决策处理关闭；event 为 Qt 关闭事件。"""
        if not self.manager.running_task_ids():
            event.accept()
            return
        decision = self._close_decision_provider()
        if decision == "tray":
            self.hide()
            event.ignore()
        elif decision == "stop":
            self.manager.request_cancel_all()
            if self.manager.wait_for_all(10_000):
                event.accept()
            else:
                choice = QMessageBox.question(
                    self,
                    "任务仍在停止",
                    "部分任务未在 10 秒内停止。是否强制结束这些任务并退出？\n\n已完成的输出会保留。",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if choice == QMessageBox.StandardButton.Yes:
                    # 每个 PID 在 TaskManager.force_stop 内都会重读状态后再校验。
                    for task_id in self.manager.running_task_ids():
                        record = self._records.get(task_id)
                        if record is None:
                            continue
                        try:
                            state = self.store.read_state(record)
                        except (OSError, ValueError, TypeError):
                            continue
                        if state.worker_pid is not None:
                            self.manager.force_stop(task_id, expected_pid=state.worker_pid)
                    remaining = self.manager.running_task_ids()
                    if remaining:
                        QMessageBox.warning(
                            self,
                            "无法安全退出",
                            f"仍有 {len(remaining)} 个任务进程无法核对或结束，程序已取消退出。",
                        )
                        event.ignore()
                    else:
                        event.accept()
                else:
                    event.ignore()
        else:
            event.ignore()


def run_gui(argv: Sequence[str] | None = None) -> int:
    """启动完整桌面应用；argv 为保留的命令行参数。"""
    _ = argv
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("风控矩阵审核器")
    application.setWindowIcon(load_application_icon())
    apply_theme(application)
    coordinator = SingleInstanceCoordinator()
    if not coordinator.acquire():
        coordinator.notify_existing()
        return 0
    executable = Path(sys.executable).resolve()
    paths = PortablePaths.from_executable(executable)
    store = TaskStore(paths.data_root)
    recover_startup_tasks(store)
    diagnostics = run_startup_diagnostics(paths)
    frozen = bool(getattr(sys, "frozen", False))
    manager = TaskManager(
        store,
        executable,
        worker_arguments_factory=lambda binary, request: desktop_worker_arguments(
            binary, request, frozen=frozen,
        ),
    )
    window = MainWindow(paths, store, manager, diagnostics)
    tray = TrayController(window, application.windowIcon())
    manager.running_count_changed.connect(tray.update_running_count)
    coordinator.activated.connect(lambda: coordinator.activate_window(window))
    tray.show()
    window.show()
    # 保持引用，避免 Qt 运行期对象被 Python 提前回收。
    application.setProperty("desktopRuntime", (coordinator, tray, manager, window))
    return application.exec()
