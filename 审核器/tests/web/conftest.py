"""Web 测试的本地源码导入配置。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"

# 测试直接运行仓库源码，不依赖开发机是否执行过可编辑安装。
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


@pytest.fixture
def portable_paths(tmp_path: Path):
    """创建通过启动诊断的最小便携资源；tmp_path 为测试根。"""
    from risk_audit_web.task_paths import PortablePaths

    paths = PortablePaths.from_app_root(tmp_path / "app")
    paths.soffice.parent.mkdir(parents=True)
    paths.soffice.write_bytes(b"exe")
    paths.entity_file.parent.mkdir(parents=True)
    paths.entity_file.write_bytes(b"xlsx")
    paths.baseline_root.mkdir(parents=True)
    release = paths.rulepacks / "releases/1.9.19"
    rule_path = release / "rules/R01.json"
    rule_path.parent.mkdir(parents=True)
    rule_path.write_text('{"id":"R01"}\n', encoding="utf-8")
    rule_hash = hashlib.sha256(rule_path.read_bytes()).hexdigest()
    content_hash = hashlib.sha256(json.dumps(
        {"rules/R01.json": rule_hash},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()).hexdigest()
    (release / "manifest.json").write_text(json.dumps({
        "version": "1.9.19", "status": "released", "content_hash": content_hash,
    }), encoding="utf-8")
    (paths.rulepacks / "active.json").write_text(json.dumps({
        "version": "1.9.19", "content_hash": content_hash,
    }), encoding="utf-8")
    paths.config_file.write_text('{"disabled_rules":[]}', encoding="utf-8")
    from tools.portable_release import build_release_manifest

    (paths.runtime_root / "manifest.json").write_text(
        json.dumps(build_release_manifest(paths.app_root), ensure_ascii=False),
        encoding="utf-8",
    )
    return paths


@pytest.fixture
def input_root(tmp_path: Path) -> Path:
    """创建一个可审核输入目录；tmp_path 为测试根。"""
    path = tmp_path / "中文 材料"
    path.mkdir()
    return path


@pytest.fixture
def web_services(portable_paths):
    """创建使用真实存储和可控进程的应用服务；portable_paths 为便携资源。"""
    from risk_audit_web.directory_picker import DirectoryPicker
    from risk_audit_web.server import ServerController
    from risk_audit_web.services import ApplicationServices
    from risk_audit_web.task_manager import TaskManager
    from risk_audit_web.task_store import TaskStore

    store = TaskStore(portable_paths.data_root)
    manager = TaskManager(
        store,
        portable_paths.app_root / "风控矩阵审核器.exe",
        popen_factory=lambda *args, **kwargs: SimpleNamespace(pid=700, poll=lambda: None),
    )
    opened = []
    services = ApplicationServices(
        paths=portable_paths,
        store=store,
        manager=manager,
        directory_picker=DirectoryPicker(dialog=lambda: ""),
        server_controller=ServerController(),
        open_path=opened.append,
        background_runner=lambda callback: callback(),
    )
    services.opened_paths = opened
    return services


@pytest.fixture
def client(tmp_path: Path, web_services):
    """创建注册全部 REST 路由的安全客户端；tmp_path/services 为测试依赖。"""
    from fastapi.testclient import TestClient
    from risk_audit_web.api.system import router as system_router
    from risk_audit_web.api.tasks import router as tasks_router
    from risk_audit_web.security import SecuritySettings
    from risk_audit_web.server import create_app

    static_root = tmp_path / "static"
    static_root.mkdir()
    (static_root / "index.html").write_text("<main>ok</main>", encoding="utf-8")
    app = create_app(
        SecuritySettings("secret", 49152),
        web_services.server_controller,
        static_root,
        routers=(system_router, tasks_router),
    )
    app.state.services = web_services
    with TestClient(app, base_url="http://127.0.0.1:49152") as test_client:
        yield test_client
