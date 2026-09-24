"""桌面应用浅色与深色主题。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def _palette(dark: bool) -> QPalette:
    """创建统一调色板；dark 表示是否使用深色。"""
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#171b24" if dark else "#f4f6fa",
        QPalette.ColorRole.WindowText: "#f2f4f8" if dark else "#172033",
        QPalette.ColorRole.Base: "#202633" if dark else "#ffffff",
        QPalette.ColorRole.AlternateBase: "#272e3d" if dark else "#edf1f7",
        QPalette.ColorRole.Text: "#f2f4f8" if dark else "#172033",
        QPalette.ColorRole.Button: "#2a3242" if dark else "#ffffff",
        QPalette.ColorRole.ButtonText: "#f2f4f8" if dark else "#172033",
        QPalette.ColorRole.Highlight: "#557cff" if dark else "#315bea",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: "#9aa4b6" if dark else "#6d7788",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    return palette


def apply_theme(application: QApplication) -> str:
    """应用系统自适应主题；application 为当前 Qt 应用。"""
    scheme = application.styleHints().colorScheme()
    dark = scheme == Qt.ColorScheme.Dark
    application.setPalette(_palette(dark))
    border = "#3a4355" if dark else "#d8deea"
    application.setStyleSheet(
        f"""
        QWidget {{ font-family: 'Microsoft YaHei UI', 'Microsoft YaHei'; font-size: 14px; }}
        QLabel#pageTitle {{ font-size: 24px; font-weight: 700; padding: 8px 0 14px 0; }}
        QLineEdit, QTableView, QTextBrowser {{ border: 1px solid {border}; border-radius: 6px; padding: 7px; }}
        QPushButton {{ min-height: 34px; border: 1px solid {border}; border-radius: 6px; padding: 0 14px; }}
        QPushButton:default {{ background: #315bea; color: white; border-color: #315bea; }}
        QPushButton:disabled {{ color: #8b94a5; }}
        QLabel[status='error'] {{ color: #d13c45; }}
        QLabel[status='ok'] {{ color: #23874b; }}
        """
    )
    return "dark" if dark else "light"
