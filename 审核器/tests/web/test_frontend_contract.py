"""验证前端页面与会话恢复契约。"""

from pathlib import Path


def static_root() -> Path:
    """返回仓库内 Web 静态目录；无参数。"""
    return Path(__file__).resolve().parents[2] / "src/risk_audit_web/static"


def test_frontend_contains_session_recovery_and_required_pages() -> None:
    """前端必须可从令牌失效恢复并包含全部页面；无参数。"""
    root = static_root()
    api_source = (root / "js/api.js").read_text(encoding="utf-8")
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "sessionStorage.setItem" in api_source
    assert "history.replaceState" in api_source
    assert "stopPolling()" in api_source
    assert "请重新双击风控矩阵审核器" in api_source
    for page_id in ("tasks-page", "create-page", "detail-page", "help-page"):
        assert f'id="{page_id}"' in html
    assert 'id="input-directory-button"' in html
    assert 'id="output-path-preview"' in html and "readonly" in html


def test_frontend_uses_local_bootstrap_and_semantic_controls() -> None:
    """页面必须只引用本地资产并保留可访问控件；无参数。"""
    html = (static_root() / "index.html").read_text(encoding="utf-8")

    assert 'href="/static/css/bootstrap.min.css"' in html
    assert 'src="/static/js/bootstrap.bundle.min.js"' in html
    assert "aria-label=" in html
    assert "<main" in html and "<nav" in html


def test_progress_updates_do_not_require_csp_blocked_inline_styles() -> None:
    """进度条必须在严格 style-src 策略下正常更新。"""
    root = static_root()
    html = (root / "index.html").read_text(encoding="utf-8")
    tasks_source = (root / "js/tasks.js").read_text(encoding="utf-8")

    assert '<progress class="audit-progress audit-progress-large mt-4"' in html
    assert ".style.width" not in tasks_source
    assert "document.createElement(\"progress\")" in tasks_source


def test_frontend_help_explains_script_and_terminal_lifecycle() -> None:
    """页面帮助必须使用双平台脚本口径，并说明终端与浏览器生命周期。"""
    html = (static_root() / "index.html").read_text(encoding="utf-8")

    assert "启动审核器.bat" in html
    assert "启动审核器.command" in html
    assert "终端" in html
    assert "关闭浏览器不会停止" in html
    assert "再次双击" in html
    assert "风控矩阵审核器.exe" not in html
    assert "_internal" not in html


def test_frontend_only_selects_input_and_keeps_output_readonly() -> None:
    """页面只允许选择输入目录，输出仅显示后端固定预览。"""
    root = static_root()
    html = (root / "index.html").read_text(encoding="utf-8")
    app_source = (root / "js/app.js").read_text(encoding="utf-8")

    assert 'id="input-directory-button"' in html
    assert 'id="output-path-preview" readonly' in html
    assert "output-directory-button" not in html
    assert "输出位置固定" in html
    assert "output_root:" not in app_source


def test_frontend_uses_automatic_name_and_readable_unaudited_files_modal() -> None:
    """页面不得要求任务名，并应在弹窗中展示未审核文件；无参数。"""
    root = static_root()
    html = (root / "index.html").read_text(encoding="utf-8")
    app_source = (root / "js/app.js").read_text(encoding="utf-8")

    assert 'id="task-name"' not in html
    assert "任务名称由系统自动生成" in html
    assert 'id="view-unaudited-files-button"' in html
    assert 'id="unaudited-files-modal"' in html
    assert "/unaudited-files" in app_source
    assert "display_name:" not in app_source
    assert "review-openings" not in app_source


def test_markdown_guides_use_cross_platform_script_terms() -> None:
    """Markdown 说明必须去除 EXE/PyInstaller 口径并覆盖双平台启动。"""
    project_root = static_root().parents[2]
    repository_root = project_root.parent
    root_guide = (repository_root / "使用说明.md").read_text(encoding="utf-8")
    readme = (project_root / "README.md").read_text(encoding="utf-8")
    windows_guide = (project_root / "Windows Web版使用说明.md").read_text(encoding="utf-8")
    macos_guide = (project_root / "macOS Apple Silicon Web版使用说明.md").read_text(encoding="utf-8")
    combined = "\n".join((root_guide, readme, windows_guide, macos_guide))

    assert "启动审核器.bat" in root_guide
    assert "启动审核器.command" in root_guide
    assert "终端" in root_guide and "关闭浏览器" in root_guide
    assert "输出位置固定" in combined
    assert "风控矩阵审核器.exe" not in combined
    assert "PyInstaller" not in combined
    assert "_internal" not in combined
    assert "xattr -dr com.apple.quarantine" in macos_guide
    assert "chmod +x" in macos_guide
    assert "sudo" not in macos_guide
    assert "已确认来源" in macos_guide
