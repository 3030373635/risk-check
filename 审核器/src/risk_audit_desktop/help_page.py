"""桌面应用使用帮助页。"""

from PySide6.QtWidgets import QLabel, QTextBrowser, QVBoxLayout, QWidget


class HelpPage(QWidget):
    """向非技术用户解释便携运行和审核任务。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化帮助页；parent 为 Qt 父窗口。"""
        super().__init__(parent)
        title = QLabel("使用帮助")
        title.setObjectName("pageTitle")
        self.help_text = QTextBrowser()
        self.help_text.setHtml(
            """
            <h3>便携使用</h3>
            <p>本程序无需安装。复制到其他电脑时，请将 EXE、runtime、data 等内容整体移动，不要单独拿走 EXE。</p>
            <h3>输出位置</h3>
            <p>默认结果保存在程序同级 outputs 目录，每次审核都有独立的时间戳目录。</p>
            <h3>停止任务</h3>
            <p>点击“停止任务”后，程序会在安全位置停止，已写入的结果不会被删除。</p>
            <h3>数据安全</h3>
            <p>审核全程不联网，材料、模型与结果都保留在本机。</p>
            """
        )
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.help_text)
