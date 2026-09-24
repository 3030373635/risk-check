"""桌面端 Qt 测试配置。"""

import os
from pathlib import Path
import sys


# 在 CI 和无显示器开发环境使用 Qt 的内存平台插件。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 构建工具按计划位于工程 tools 目录，测试时显式加入工程根。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
