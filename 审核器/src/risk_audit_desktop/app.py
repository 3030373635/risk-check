"""风控矩阵审核器桌面界面与 Worker 的统一入口。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import sys


def main(argv: Sequence[str] | None = None) -> int:
    """启动桌面界面或 Worker；argv 为可选命令行参数，返回进程退出码。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--worker":
        # Worker 必须在导入任何 Qt Widgets 页面前分流，降低独立进程开销。
        from risk_audit_desktop.worker import run_worker

        return run_worker(Path(args[1]))
    from risk_audit_desktop.main_window import run_gui

    return run_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())
