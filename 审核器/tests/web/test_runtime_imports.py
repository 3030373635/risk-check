"""验证 Web 任务运行时不依赖 Qt。"""


def test_web_runtime_modules_import_without_pyside(monkeypatch) -> None:
    """Worker 和任务运行时不得导入 Qt；monkeypatch 用于阻止隐式依赖。"""
    import importlib
    import sys

    monkeypatch.setitem(sys.modules, "PySide6", None)
    for name in (
        "risk_audit_web.task_contracts",
        "risk_audit_web.task_paths",
        "risk_audit_web.task_store",
        "risk_audit_web.diagnostics",
        "risk_audit_web.platform_runtime",
        "risk_audit_web.worker",
    ):
        importlib.import_module(name)
