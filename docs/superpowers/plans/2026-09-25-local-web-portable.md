# 风控矩阵审核器单机离线 Web 版实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 PySide6 Windows 便携桌面端替换为双击启动、完全离线、仅监听本机回环地址的 Web 界面，同时保留现有审核核心、并行 Worker、任务恢复和便携交付能力。

**Architecture:** 新建 `risk_audit_web` 包承接任务运行时、本机 FastAPI/Uvicorn 服务、Windows 平台操作和静态页面。浏览器通过带会话令牌的 REST API 管理任务，审核仍在独立 Worker 进程中执行，状态继续由任务目录中的原子 JSON 与 JSONL 文件持久化。最终删除 `risk_audit_desktop`、Qt 页面、托盘和兼容入口，发布方式改为 PyInstaller `onedir + windowed`。

**Tech Stack:** Python 3.11、FastAPI 0.141.1、Uvicorn 0.53.0、HTML5、CSS、原生 JavaScript、Bootstrap 5.3.8、Bootstrap Icons 1.13.1、PyInstaller 6.16.0、pytest 8.4.2

**Spec:** `docs/superpowers/specs/2026-09-25-local-web-portable-design.md`

## Global Constraints

- 目标平台固定为 Windows 10/11 x64，产品形态固定为单机、完全离线、本地 Web 应用。
- 服务只绑定 `127.0.0.1` 的系统随机端口；不得绑定 `0.0.0.0`、局域网地址或 IPv6 全局地址。
- 用户电脑不得要求预装 Python、Node.js、Qt、LibreOffice 或管理员权限。
- 前端只使用 HTML5、CSS、原生 JavaScript、Bootstrap 5.3.8 和 Bootstrap Icons 1.13.1；不得增加 Node.js、npm、Vite、React 或 Vue。
- Bootstrap、图标、字体和许可证必须进入发布包，运行时不得引用 CDN、远程字体、远程图片或其他外部资源。
- 输入目录由 Windows 原生目录选择框选择；输出固定为 `outputs/<材料目录名>-YYYYMMDD-HHMMSS`，客户端不得指定输出路径。
- 浏览器关闭不停止服务或 Worker；再次双击 EXE 只重新打开现有页面。
- 任务继续使用独立 Worker、独立 LibreOffice profile、独立临时目录和独立输出目录。
- 不兼容旧 PySide6 页面、旧桌面入口、旧 GUI 测试和旧行为，不新增兼容层。
- Python 使用 `snake_case`，JavaScript 使用 `camelCase`；所有新增 Python 函数必须说明用途、参数和返回值，安全关键代码必须带中文注释。
- 遵循 TDD：先写失败测试并确认失败，再写最小实现并确认通过。
- 按项目要求不执行 `git commit`；每个任务末尾只记录建议的中文 Conventional Commit 信息。

## Review Focus

- 含中文、空格、长名称或符号链接的输入目录：合法目录应可创建任务，越过输出边界或相互包含的目录必须返回 `422`；由 Task 5 的路径参数化测试覆盖。
- 两个浏览器标签页同时选择目录或同秒创建任务：目录选择只能有一个成功，其余返回 `409`；两个任务输出必须原子递增且不覆盖；由 Task 3 和 Task 5 的并发测试覆盖。
- 残留会话文件、PID 复用或端口被无关服务占用：只有 PID 存活且令牌健康检查匹配才允许重开，否则主实例必须覆盖残留会话；由 Task 3 和 Task 7 的会话恢复测试覆盖。
- Worker 在取消或强制终止请求期间自然完成：已发布的终态必须保留，不得被覆盖为 `cancelled` 或 `interrupted`；由 Task 2 和 Task 5 的竞态测试覆盖。
- 页面缺少、失效或被清除的会话令牌：不得进入无限 `401` 刷新循环；页面应停止轮询并提示用户重新双击 EXE；由 Task 6 的前端契约测试覆盖。

---

## 文件结构与职责

实施完成后的主要结构如下：

```text
risk-audit/
├── src/risk_audit_web/
│   ├── __init__.py                 # Web 包版本与公开入口
│   ├── app.py                      # EXE 主入口、Worker 分流、主/次实例流程
│   ├── api_models.py               # REST 请求与响应模型
│   ├── browser.py                  # 默认浏览器打开和启动 URL 生成
│   ├── diagnostics.py              # runtime 和规则资源检查
│   ├── directory_picker.py         # 单请求 Windows 原生目录选择
│   ├── logging_setup.py            # server.log 大小轮转配置
│   ├── platform_windows.py         # PID、终止和系统打开能力
│   ├── security.py                 # Host、Origin、令牌和响应头中间件
│   ├── server.py                   # FastAPI/Uvicorn 组装与停止控制
│   ├── services.py                 # API 与存储/进程之间的应用服务边界
│   ├── single_instance.py          # Windows mutex 和 server-session.json
│   ├── task_contracts.py           # Worker 请求、状态、事件和索引契约
│   ├── task_manager.py             # 纯 Python Worker 生命周期
│   ├── task_paths.py               # 便携目录、输出命名和路径保护
│   ├── task_store.py               # 原子任务存储和恢复
│   ├── worker.py                   # 独立审核 Worker
│   ├── api/
│   │   ├── __init__.py
│   │   ├── system.py               # 状态、目录选择、输出预览、退出
│   │   └── tasks.py                # 任务列表、创建、详情和操作资源
│   └── static/
│       ├── index.html              # 单页应用语义结构
│       ├── vendor-manifest.json    # Bootstrap 本地资源摘要
│       ├── css/
│       │   ├── bootstrap.min.css
│       │   ├── bootstrap-icons.min.css
│       │   └── theme.css
│       ├── js/
│       │   ├── bootstrap.bundle.min.js
│       │   ├── api.js
│       │   ├── tasks.js
│       │   └── app.js
│       └── fonts/bootstrap-icons.woff2
├── tests/web/                      # Web 运行时、API、前端和打包测试
├── tools/vendor_web_assets.py      # 固定版本前端资产下载与摘要
├── tools/build_windows_web.py      # onedir 便携包构建
├── tools/verify_portable_distribution.py
├── requirements-web.lock
└── Windows Web版使用说明.md
```

---

### Task 1: 建立无 Qt 的 Web 任务运行时包

**Files:**
- Create: `risk-audit/src/risk_audit_web/__init__.py`
- Create: `risk-audit/src/risk_audit_web/task_contracts.py`
- Create: `risk-audit/src/risk_audit_web/task_paths.py`
- Create: `risk-audit/src/risk_audit_web/task_store.py`
- Create: `risk-audit/src/risk_audit_web/diagnostics.py`
- Create: `risk-audit/src/risk_audit_web/platform_windows.py`
- Create: `risk-audit/src/risk_audit_web/worker.py`
- Create: `risk-audit/tests/web/conftest.py`
- Create: `risk-audit/tests/web/test_runtime_imports.py`
- Create: `risk-audit/tests/web/test_task_contracts.py`
- Create: `risk-audit/tests/web/test_task_paths.py`
- Create: `risk-audit/tests/web/test_task_store.py`
- Create: `risk-audit/tests/web/test_diagnostics.py`
- Create: `risk-audit/tests/web/test_worker.py`
- Create: `risk-audit/tests/web/test_parallel_integration.py`

**Interfaces:**
- Consumes: `risk_audit.runner.audit(...)`、`risk_audit.progress.AuditProgressEvent`、当前 `risk_audit_desktop` 中同名非 GUI 模块的已验证行为。
- Produces: `PortablePaths`、`TaskRequest`、`TaskState`、`TaskRecord`、`TaskEvent`、`TaskStore`、`run_worker(request_path, audit_func=None, heartbeat_interval_seconds=5.0) -> int`、`is_process_alive(pid) -> bool`、`terminate_process(pid) -> bool`、`open_path(path) -> None`。

- [ ] **Step 1: 写入无 Qt 导入失败测试**

```python
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
        "risk_audit_web.platform_windows",
        "risk_audit_web.worker",
    ):
        importlib.import_module(name)
```

- [ ] **Step 2: 运行测试并确认因新包不存在而失败**

Run: `cd risk-audit && python -m pytest tests/web/test_runtime_imports.py -q`

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'risk_audit_web'`。

- [ ] **Step 3: 创建 Web 包并迁移已验证的非 GUI 实现**

以当前同名桌面模块为唯一来源创建文件，保持数据契约和任务目录格式不变，只做以下明确替换：

```python
# 所有新模块统一使用 Web 包导入。
from risk_audit_web.task_contracts import TaskEvent, TaskRecord, TaskRequest, TaskState
from risk_audit_web.task_paths import PortablePaths
from risk_audit_web.task_store import append_event, atomic_write_json
```

`task_contracts.py` 顶部文案改为“Web 任务请求、状态、索引和事件的 JSON 契约”；`worker.py` 中进度注释改为“Web 状态”；其余业务行为保持现有测试确认的语义。

- [ ] **Step 4: 运行无 Qt 导入测试并确认通过**

Run: `cd risk-audit && python -m pytest tests/web/test_runtime_imports.py -q`

Expected: PASS。

- [ ] **Step 5: 迁移核心任务测试到 `tests/web`**

从现有以下测试复制用例并把导入前缀统一改为 `risk_audit_web`：

```text
tests/desktop/test_task_contracts.py
tests/desktop/test_task_paths.py
tests/desktop/test_task_store.py
tests/desktop/test_diagnostics.py
tests/desktop/test_worker.py
tests/desktop/test_parallel_integration.py
```

删除只验证 Qt 入口参数的 `test_worker_dispatch_does_not_import_qt_widgets` 和 `test_desktop_worker_arguments_support_source_and_frozen_modes`；新的入口分流由 Task 7 覆盖。`tests/web/conftest.py` 只保留把 `risk-audit` 工程根加入 `sys.path` 的逻辑，不设置 `QT_QPA_PLATFORM`。

- [ ] **Step 6: 运行迁移后的任务运行时测试**

Run: `cd risk-audit && python -m pytest tests/web/test_runtime_imports.py tests/web/test_task_contracts.py tests/web/test_task_paths.py tests/web/test_task_store.py tests/web/test_diagnostics.py tests/web/test_worker.py tests/web/test_parallel_integration.py -q`

Expected: PASS，且 `rg -n "PySide6|risk_audit_desktop" src/risk_audit_web tests/web` 无输出。

- [ ] **Step 7: 记录建议提交信息，不执行提交**

```text
refactor: 迁移无Qt任务运行时到Web包
```

---

### Task 2: 将 TaskManager 重构为纯 Python 快照接口

**Files:**
- Create: `risk-audit/src/risk_audit_web/task_manager.py`
- Create: `risk-audit/tests/web/test_task_manager.py`
- Modify: `risk-audit/tests/web/test_parallel_integration.py`

**Interfaces:**
- Consumes: `TaskStore.list_tasks()`、`TaskStore.read_state()`、`TaskStore.mark_interrupted()`、`is_process_alive()`、`terminate_process()`。
- Produces: `ManagedTask(record: TaskRecord, state: TaskState)`、`TaskWarning(task_id: str, message: str)`、`TaskSnapshot(tasks: list[ManagedTask], warnings: list[TaskWarning], running_count: int)`、`TaskManager.snapshot() -> TaskSnapshot`、`TaskManager.get_task(task_id) -> ManagedTask | None`、`start_task()`、`request_cancel()`、`request_cancel_all()`、`force_stop()`、`wait_for_all()`、`running_task_ids()`。

- [ ] **Step 1: 写入快照和损坏隔离失败测试**

```python
def write_task(store: TaskStore, tmp_path: Path, task_id: str, status: str) -> TaskRecord:
    """写入一个完整测试任务；store/tmp_path/task_id/status 为任务测试参数。"""
    output_root = (tmp_path / task_id).resolve()
    state_path = output_root / "_task/state.json"
    state_path.parent.mkdir(parents=True)
    record = TaskRecord(
        "1.0", task_id, task_id, str((tmp_path / "input").resolve()),
        str(output_root), "2026-09-25T10:00:00+08:00", str(state_path.resolve()),
    )
    state = TaskState(
        "1.0", task_id, status, status, 1, 2, 50, None, None, None,
        123 if status == "running" else None,
        "2026-09-25T10:00:00+08:00", "2026-09-25T10:00:01+08:00",
        "状态", None, None,
    )
    store.add_task(record)
    store.write_state(state_path, state)
    return record


def test_snapshot_isolates_corrupt_state_and_counts_running(tmp_path: Path) -> None:
    """损坏任务不能阻止有效任务形成快照；tmp_path 为隔离任务根。"""
    store = TaskStore(tmp_path / "data")
    running = write_task(store, tmp_path, "running", "running")
    completed = write_task(store, tmp_path, "completed", "completed")
    broken = write_task(store, tmp_path, "broken", "running")
    Path(broken.state_path).write_text("{invalid", encoding="utf-8")
    manager = TaskManager(
        store,
        tmp_path / "app.exe",
        popen_factory=lambda *args, **kwargs: None,
        process_alive=lambda pid: pid == 123,
    )

    snapshot = manager.snapshot()

    assert [(item.record.task_id, item.state.status) for item in snapshot.tasks] == [
        (running.task_id, "running"),
        (completed.task_id, "completed"),
    ]
    assert snapshot.running_count == 1
    assert snapshot.warnings[0].task_id == broken.task_id
```

- [ ] **Step 2: 写入取消/自然完成竞态失败测试**

```python
def test_cancel_does_not_replace_worker_terminal_state(monkeypatch, tmp_path: Path) -> None:
    """Worker 同时完成时必须保留完成态；monkeypatch/tmp_path 为测试隔离依赖。"""
    store = TaskStore(tmp_path / "data")
    record = write_task(store, tmp_path, "running", "running")
    original_touch = Path.touch

    def finish_when_cancel_appears(path: Path, *args, **kwargs) -> None:
        """创建取消标记后模拟 Worker 自然完成。"""
        original_touch(path, *args, **kwargs)
        if path.name == "cancel.requested":
            current = store.read_state(record)
            store.write_state(Path(record.state_path), current.with_updates(
                status="completed", stage="completed", progress_percent=100, worker_pid=None,
            ))

    monkeypatch.setattr(Path, "touch", finish_when_cancel_appears)
    manager = TaskManager(store, tmp_path / "app.exe")

    assert manager.request_cancel(record.task_id)
    assert store.read_state(record).status == "completed"
```

- [ ] **Step 3: 运行新测试并确认缺少纯 Python Manager**

Run: `cd risk-audit && python -m pytest tests/web/test_task_manager.py -q`

Expected: FAIL，错误指向 `risk_audit_web.task_manager` 不存在或 `snapshot` 不存在。

- [ ] **Step 4: 实现快照数据类和无信号 Manager**

```python
@dataclass(frozen=True)
class ManagedTask:
    """组合任务索引与当前状态。"""
    record: TaskRecord
    state: TaskState


@dataclass(frozen=True)
class TaskWarning:
    """描述一个被隔离的任务读取错误。"""
    task_id: str
    message: str


@dataclass(frozen=True)
class TaskSnapshot:
    """保存当前可用任务、警告和运行数量。"""
    tasks: list[ManagedTask]
    warnings: list[TaskWarning]
    running_count: int
```

`snapshot()` 逐条读取任务；对子进程已退出但状态仍为 `running/cancelling` 的任务重读一次，仍未进入终态时才标记 `interrupted`。取消标记只改变返回给页面的快照状态，不由 Manager 覆盖 Worker 持久状态。

- [ ] **Step 5: 迁移并适配现有进程管理测试**

把现有 `test_task_manager.py` 中启动参数、进程退出重读、单任务取消、强制停止 PID 复核和退出后终态保留测试迁到 Web 测试；删除 `SignalChannel`、`.connect()` 和 `start_timer` 断言，改为直接检查 `snapshot()` 返回值。

- [ ] **Step 6: 运行 TaskManager 与并行集成测试**

Run: `cd risk-audit && python -m pytest tests/web/test_task_manager.py tests/web/test_parallel_integration.py -q`

Expected: PASS，且 `rg -n "SignalChannel|QTimer|PySide6" src/risk_audit_web/task_manager.py` 无输出。

- [ ] **Step 7: 记录建议提交信息，不执行提交**

```text
refactor: 将任务管理器改为纯Python快照接口
```

---

### Task 3: 实现单实例会话、浏览器重开和目录选择

**Files:**
- Create: `risk-audit/src/risk_audit_web/single_instance.py`
- Create: `risk-audit/src/risk_audit_web/browser.py`
- Create: `risk-audit/src/risk_audit_web/directory_picker.py`
- Create: `risk-audit/tests/web/test_single_instance.py`
- Create: `risk-audit/tests/web/test_browser.py`
- Create: `risk-audit/tests/web/test_directory_picker.py`

**Interfaces:**
- Consumes: `atomic_write_json()`、Windows `CreateMutexW/GetLastError/CloseHandle`、`tkinter.filedialog.askdirectory`、`webbrowser.open`。
- Produces: `ServerSession`、`SingleInstanceLock.acquire() -> bool`、`write_server_session()`、`read_server_session()`、`wait_for_live_session()`、`build_launch_url()`、`open_default_browser()`、`DirectorySelection`、`DirectoryPicker.select_directory() -> DirectorySelection`、`DirectoryPickerBusy`。

- [ ] **Step 1: 写入会话文件与令牌健康检查失败测试**

```python
def test_wait_for_live_session_rejects_pid_reuse_and_wrong_token(tmp_path: Path) -> None:
    """存活 PID 但令牌不匹配时不得重开错误服务；tmp_path 为会话目录。"""
    session = ServerSession("1.0", 321, 49152, "expected-token", "2026-09-25T10:00:00+08:00")
    write_server_session(tmp_path / "server-session.json", session)

    found = wait_for_live_session(
        tmp_path / "server-session.json",
        is_process_alive=lambda pid: pid == 321,
        health_check=lambda candidate: candidate.token == "other-token",
        attempts=2,
        retry_seconds=0,
    )

    assert found is None
```

- [ ] **Step 2: 写入目录选择并发失败测试**

```python
def test_directory_picker_rejects_second_concurrent_request(tmp_path: Path) -> None:
    """同一时刻只能有一个系统目录框；tmp_path 提供选择结果。"""
    entered = threading.Event()
    release = threading.Event()

    def blocking_dialog() -> str:
        """阻塞首个目录框直到测试允许返回。"""
        entered.set()
        assert release.wait(1)
        return str(tmp_path)

    picker = DirectoryPicker(dialog=blocking_dialog)
    first = Thread(target=picker.select_directory)
    first.start()
    assert entered.wait(1)

    with pytest.raises(DirectoryPickerBusy):
        picker.select_directory()

    release.set()
    first.join(1)
```

- [ ] **Step 3: 运行测试并确认模块不存在**

Run: `cd risk-audit && python -m pytest tests/web/test_single_instance.py tests/web/test_browser.py tests/web/test_directory_picker.py -q`

Expected: FAIL，错误指向三个新模块尚未创建。

- [ ] **Step 4: 实现 Windows mutex 和会话契约**

```python
@dataclass(frozen=True)
class ServerSession:
    """描述当前本机 Web 服务会话。"""
    schema_version: str
    pid: int
    port: int
    token: str
    started_at: str


class SingleInstanceLock:
    """在进程生命周期内持有 Windows 命名 mutex。"""

    def acquire(self) -> bool:
        """尝试获取实例锁；返回当前进程是否为主实例。"""
        # CreateMutexW 成功但 GetLastError 为 ERROR_ALREADY_EXISTS 时必须关闭新句柄。
```

非 Windows 测试路径使用排他锁文件替身；生产 Windows 路径使用命名 mutex `Local\OpenAI.RiskAudit.Web.v2`。`read_server_session()` 对字段类型、端口范围、正 PID 和非空令牌做严格校验。

- [ ] **Step 5: 实现启动 URL 和默认浏览器打开**

```python
def build_launch_url(session: ServerSession) -> str:
    """生成一次性启动 URL；session 为当前服务会话。"""
    return f"http://127.0.0.1:{session.port}/?token={quote(session.token, safe='')}"


def open_default_browser(session: ServerSession, opener: Callable[[str], bool] = webbrowser.open) -> bool:
    """打开当前会话页面；session 为服务会话，opener 为可替换浏览器函数。"""
    return bool(opener(build_launch_url(session)))
```

- [ ] **Step 6: 实现串行目录选择器**

`DirectoryPicker.select_directory()` 使用非阻塞锁拒绝并发请求；Windows 线程调用 `CoInitializeEx(..., COINIT_APARTMENTTHREADED)`，创建隐藏 Tk 根窗口并调用 `filedialog.askdirectory(mustexist=True)`，最后销毁根窗口和调用 `CoUninitialize()`。用户取消返回 `DirectorySelection(selected=False, path=None)`，成功时返回解析后的绝对 `Path`。

- [ ] **Step 7: 运行平台服务测试**

Run: `cd risk-audit && python -m pytest tests/web/test_single_instance.py tests/web/test_browser.py tests/web/test_directory_picker.py -q`

Expected: PASS；并发选择测试稳定返回一个成功和一个 `DirectoryPickerBusy`。

- [ ] **Step 8: 记录建议提交信息，不执行提交**

```text
feat: 增加Web版单实例与本机目录选择
```

---

### Task 4: 建立 FastAPI 服务、安全中间件和依赖锁

**Files:**
- Modify: `risk-audit/pyproject.toml`
- Modify: `risk-audit/uv.lock`
- Create: `risk-audit/requirements-web.lock`
- Create: `risk-audit/src/risk_audit_web/security.py`
- Create: `risk-audit/src/risk_audit_web/server.py`
- Create: `risk-audit/src/risk_audit_web/api/__init__.py`
- Create: `risk-audit/tests/web/test_security.py`
- Create: `risk-audit/tests/web/test_server.py`

**Interfaces:**
- Consumes: FastAPI、Uvicorn、`ServerSession`、任意测试静态目录。
- Produces: `SecuritySettings(token, port)`、`LocalSecurityMiddleware`、`ServerController.request_exit()`、`create_app(settings, controller, static_root, routers=()) -> FastAPI`、`create_loopback_socket() -> socket.socket`、`run_uvicorn(app, listener, controller) -> None`。

- [ ] **Step 1: 写入安全边界失败测试**

```python
def test_api_requires_exact_host_origin_and_token(tmp_path: Path) -> None:
    """API 必须同时限制 Host、Origin 和令牌；tmp_path 为静态目录。"""
    tmp_path.joinpath("index.html").write_text("<main>ok</main>", encoding="utf-8")
    router = APIRouter()

    @router.get("/api/v1/probe")
    def read_probe() -> dict[str, bool]:
        """提供只读安全探针。"""
        return {"ok": True}

    @router.post("/api/v1/probe")
    def write_probe() -> dict[str, bool]:
        """提供修改型安全探针。"""
        return {"ok": True}

    app = create_app(
        SecuritySettings(token="secret", port=49152),
        ServerController(),
        tmp_path,
        routers=(router,),
    )
    client = TestClient(app, base_url="http://127.0.0.1:49152")

    assert client.get("/api/v1/probe").status_code == 401
    assert client.get(
        "/api/v1/probe",
        headers={"X-Local-Token": "secret", "Host": "evil.example"},
    ).status_code == 403
    assert client.post(
        "/api/v1/probe",
        headers={"X-Local-Token": "secret", "Origin": "http://evil.example"},
    ).status_code == 403
```

- [ ] **Step 2: 写入回环监听失败测试**

```python
def test_loopback_socket_uses_random_ipv4_port() -> None:
    """监听必须由系统分配 127.0.0.1 端口。"""
    listener = create_loopback_socket()
    try:
        host, port = listener.getsockname()
        assert host == "127.0.0.1"
        assert 0 < port < 65536
    finally:
        listener.close()
```

- [ ] **Step 3: 运行测试并确认 FastAPI 层不存在**

Run: `cd risk-audit && python -m pytest tests/web/test_security.py tests/web/test_server.py -q`

Expected: FAIL，错误包含缺少 `fastapi` 依赖或新模块不存在。

- [ ] **Step 4: 更新项目依赖并生成锁文件**

```toml
[project.optional-dependencies]
test = ["pytest==8.4.2", "httpx==0.28.1"]
web = ["fastapi==0.141.1", "uvicorn==0.53.0"]
build = ["PyInstaller==6.16.0"]

[project.scripts]
risk-audit = "risk_audit.cli:main"
risk-audit-web = "risk_audit_web.app:main"
```

Run:

```bash
cd risk-audit
uv lock
uv export --extra web --extra test --extra build --no-dev --no-emit-project --format requirements.txt --output-file requirements-web.lock
```

`requirements-web.lock` 必须包含哈希；后续 CI 使用 `pip install --require-hashes`。

- [ ] **Step 5: 实现本机安全中间件**

```python
@dataclass(frozen=True)
class SecuritySettings:
    """保存当前会话安全参数。"""
    token: str
    port: int

    @property
    def origin(self) -> str:
        """返回唯一允许的浏览器来源。"""
        return f"http://127.0.0.1:{self.port}"
```

中间件规则：`/healthz` 只接受正确 Host，并要求 `X-Local-Token`；`/api/v1` 还要求令牌，非只读方法要求精确 Origin；所有响应添加 CSP、`nosniff`、`no-referrer` 和 `frame-ancestors 'none'`。API 不安装 CORS 中间件。

- [ ] **Step 6: 实现 FastAPI 与 Uvicorn 外壳**

```python
class ServerController:
    """向 Uvicorn 暴露线程安全的退出请求。"""

    def attach(self, server: uvicorn.Server) -> None:
        """保存运行中的服务；server 为 Uvicorn 实例。"""
        self._server = server

    def request_exit(self) -> None:
        """请求服务在当前响应完成后退出。"""
        if self._server is not None:
            self._server.should_exit = True
```

`create_loopback_socket()` 显式创建 `AF_INET` socket、绑定 `("127.0.0.1", 0)` 并监听；`run_uvicorn()` 使用预绑定 socket，禁止 Uvicorn 自行改绑地址。

- [ ] **Step 7: 运行安全和服务测试**

Run: `cd risk-audit && uv run --extra web --extra test python -m pytest tests/web/test_security.py tests/web/test_server.py -q`

Expected: PASS；`GET /healthz` 只返回 `{"status":"ok"}`，错误响应不含 traceback。

- [ ] **Step 8: 记录建议提交信息，不执行提交**

```text
feat: 建立本机Web服务安全边界
```

---

### Task 5: 实现应用服务与 REST API

**Files:**
- Create: `risk-audit/src/risk_audit_web/api_models.py`
- Create: `risk-audit/src/risk_audit_web/services.py`
- Create: `risk-audit/src/risk_audit_web/api/system.py`
- Create: `risk-audit/src/risk_audit_web/api/tasks.py`
- Modify: `risk-audit/src/risk_audit_web/server.py`
- Create: `risk-audit/tests/web/test_system_api.py`
- Create: `risk-audit/tests/web/test_tasks_api.py`
- Create: `risk-audit/tests/web/test_services.py`

**Interfaces:**
- Consumes: `PortablePaths`、`TaskStore`、`TaskManager`、`DirectoryPicker`、`DiagnosticReport`、`ServerController`。
- Produces: `ApplicationServices`、`ApplicationServices.request_shutdown(mode: str) -> dict[str, object]`、`TaskCreateRequest`、`ShutdownRequest`、统一 `ApiProblem`、设计文档第 7 节全部 REST 路径。

- [ ] **Step 1: 写入任务创建边界失败测试**

```python
def test_create_task_ignores_no_client_output_and_returns_actual_path(client, input_root: Path) -> None:
    """客户端只能提交名称和输入路径；client/input_root 为 API 夹具。"""
    headers = {"X-Local-Token": "secret", "Origin": str(client.base_url)}
    response = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"display_name": "第一批审核", "input_root": str(input_root)},
    )

    assert response.status_code == 201
    payload = response.json()
    assert Path(payload["output_root"]).parent.name == "outputs"
    assert payload["status"] == "running"
```

再添加额外字段测试：请求含 `output_root` 时 Pydantic 使用 `extra="forbid"` 返回 `400 INVALID_REQUEST`。

- [ ] **Step 2: 写入路径与并发创建 Review Focus 测试**

```python
@pytest.mark.parametrize("name", ["中文 材料", "含.合法-标点", "目录末尾空格 "])
def test_output_preview_sanitizes_windows_names(client, tmp_path: Path, name: str) -> None:
    """常见 Windows 目录名应得到安全预览；name 为原目录名。"""
    input_root = tmp_path / name
    input_root.mkdir()
    headers = {"X-Local-Token": "secret", "Origin": str(client.base_url)}
    response = client.post(
        "/api/v1/output-path-previews",
        headers=headers,
        json={"input_root": str(input_root)},
    )
    assert response.status_code == 200
    assert Path(response.json()["output_root"]).parent.name == "outputs"
```

增加两个线程同秒创建相同输入目录的测试，断言两个 `output_root` 不同，第二个带 `-2`，两个目录都存在。

再增加两个边界：长度超过 120 个字符的中文目录名仍产生合法输出名；输入目录为指向 `outputs` 内部的符号链接时，解析后返回 `422 PATH_OVERLAP`，普通含空格符号链接若解析后不与输出重叠则允许创建。

- [ ] **Step 3: 写入取消终态竞态与打开路径边界测试**

测试必须断言：Worker 在取消 API 调用中自然完成时响应和持久状态均为 `completed`；输出打开接口只把后端任务记录解析出的目录传给 `open_path`，请求体传入任意路径应因额外字段返回 `400`。

```python
def test_output_opening_never_accepts_client_path(client, completed_task) -> None:
    """打开结果只能由任务编号解析目标。"""
    response = client.post(
        f"/api/v1/tasks/{completed_task.task_id}/output-openings",
        headers=authorized_post_headers(client),
        json={"path": "C:\\Windows"},
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_REQUEST"
```

同一测试文件定义 `authorized_post_headers(client)`，固定返回正确的 `X-Local-Token`、`Origin` 和 JSON Content-Type。

- [ ] **Step 4: 运行 API 测试并确认路由不存在**

Run: `cd risk-audit && uv run --extra web --extra test python -m pytest tests/web/test_system_api.py tests/web/test_tasks_api.py tests/web/test_services.py -q`

Expected: FAIL，接口返回 `404` 或应用服务模块不存在。

- [ ] **Step 5: 实现严格 API 模型和统一错误**

```python
class StrictModel(BaseModel):
    """禁止未声明字段进入本机系统操作。"""
    model_config = ConfigDict(extra="forbid")


class TaskCreateRequest(StrictModel):
    """描述用户可提交的新任务字段。"""
    display_name: str = Field(min_length=1, max_length=200)
    input_root: str = Field(min_length=1)


class ShutdownRequest(StrictModel):
    """描述退出策略。"""
    mode: Literal["immediate", "cancel_active_tasks"]
```

`ApiProblem` 包含 `status_code`、稳定 `error_code`、中文 `message` 和可选安全 `details`；全局异常处理器只返回这些字段。

- [ ] **Step 6: 实现应用服务边界**

```python
@dataclass
class ApplicationServices:
    """组合 API 所需的任务、平台和退出能力。"""
    paths: PortablePaths
    store: TaskStore
    manager: TaskManager
    directory_picker: DirectoryPicker
    server_controller: ServerController
    open_path: Callable[[Path], None]
    background_runner: Callable[[Callable[[], None]], None]

    def create_task(self, request: TaskCreateRequest) -> ManagedTask:
        """验证并启动任务；request 为唯一允许的客户端字段。"""
        record = self.store.create_task(
            input_root=Path(request.input_root),
            display_name=request.display_name,
            paths=self.paths,
        )
        self.manager.start_task(record)
        return self.manager.get_task(record.task_id) or ManagedTask(record, self.store.read_state(record))
```

生产默认 `background_runner` 以 daemon `Thread` 执行；测试传入 `lambda callback: callback()`，使退出等待同步、可断言且不增加测试专用生产方法。

服务层负责把 `PathValidationError`、不存在任务、状态冲突、诊断失败和目录选择忙转换为稳定 `ApiProblem`。路由层不读取文件、不调用 `subprocess`、不接触 PID。

- [ ] **Step 7: 实现 system 和 tasks 路由**

严格按设计文档表格注册全部端点。任务列表按 `created_at` 降序。输出、统计表和复核目录打开前使用任务记录与 `Path.resolve()` 再次确认目标位于该任务输出目录内。

```python
@router.post("/tasks", status_code=status.HTTP_201_CREATED)
def create_task(request: TaskCreateRequest, services: ApplicationServices = Depends(get_services)):
    """创建并启动审核任务；request 为受限客户端字段。"""
    return task_response(services.create_task(request))


@router.post("/tasks/{task_id}/cancellations", status_code=status.HTTP_201_CREATED)
def cancel_task(task_id: str, services: ApplicationServices = Depends(get_services)):
    """创建安全停止请求；task_id 为任务编号。"""
    return services.cancel_task(task_id)
```

- [ ] **Step 8: 实现退出模式**

`immediate` 存在活动任务时返回 `409 ACTIVE_TASKS_EXIST` 和任务编号列表；无任务时在响应发送后调用 `ServerController.request_exit()`。`cancel_active_tasks` 先调用 `request_cancel_all()`，后台等待 `wait_for_all()`；超时时返回未响应任务列表，不隐式强杀。

```python
def request_shutdown(self, mode: str) -> dict[str, object]:
    """按模式请求退出；mode 为 immediate 或 cancel_active_tasks。"""
    active = self.manager.running_task_ids()
    if mode == "immediate" and active:
        raise ApiProblem(409, "ACTIVE_TASKS_EXIST", "仍有审核任务正在运行", {"task_ids": active})
    if mode == "cancel_active_tasks":
        self.manager.request_cancel_all()
        self.background_runner(self._wait_then_exit)
        return {"status": "stopping_tasks", "task_ids": active}
    self.background_runner(self.server_controller.request_exit)
    return {"status": "shutting_down", "task_ids": []}
```

- [ ] **Step 9: 运行 REST API 和全部 Web 后端测试**

Run: `cd risk-audit && uv run --extra web --extra test python -m pytest tests/web/test_system_api.py tests/web/test_tasks_api.py tests/web/test_services.py tests/web/test_task_manager.py -q`

Expected: PASS；设计文档中的每条 API 路径至少有一个成功测试和一个错误测试。

- [ ] **Step 10: 记录建议提交信息，不执行提交**

```text
feat: 实现离线审核REST接口
```

---

### Task 6: 实现离线 Bootstrap 前端

**Files:**
- Create: `risk-audit/tools/vendor_web_assets.py`
- Create: `risk-audit/src/risk_audit_web/static/index.html`
- Create: `risk-audit/src/risk_audit_web/static/vendor-manifest.json`
- Create: `risk-audit/src/risk_audit_web/static/css/bootstrap.min.css`
- Create: `risk-audit/src/risk_audit_web/static/css/bootstrap-icons.min.css`
- Create: `risk-audit/src/risk_audit_web/static/css/theme.css`
- Create: `risk-audit/src/risk_audit_web/static/js/bootstrap.bundle.min.js`
- Create: `risk-audit/src/risk_audit_web/static/js/api.js`
- Create: `risk-audit/src/risk_audit_web/static/js/tasks.js`
- Create: `risk-audit/src/risk_audit_web/static/js/app.js`
- Create: `risk-audit/src/risk_audit_web/static/fonts/bootstrap-icons.woff2`
- Create: `risk-audit/licenses/web/Bootstrap.txt`
- Create: `risk-audit/licenses/web/Bootstrap-Icons.txt`
- Create: `risk-audit/tests/web/test_web_assets.py`
- Create: `risk-audit/tests/web/test_frontend_contract.py`

**Interfaces:**
- Consumes: Task 5 的 REST API 和启动 URL 查询参数 `token`。
- Produces: 无外部请求的任务中心、新建任务、任务详情、帮助页、统一 `apiRequest(path, options)` 和每秒轮询控制。

- [ ] **Step 1: 写入离线资产失败测试**

```python
@pytest.fixture
def static_root() -> Path:
    """返回仓库内 Web 静态目录。"""
    return Path(__file__).resolve().parents[2] / "src/risk_audit_web/static"


def test_frontend_has_no_remote_runtime_resources(static_root: Path) -> None:
    """页面和样式不得在运行时引用网络资源；static_root 为前端根。"""
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in static_root.rglob("*")
        if path.is_file() and path.suffix in {".html", ".css", ".js"}
    )
    assert not re.search(r"(?:src|href)=[\"']https?://", text, re.IGNORECASE)
    assert not re.search(r"url\(\s*[\"']?https?://", text, re.IGNORECASE)
    assert "innerHTML" not in text
```

- [ ] **Step 2: 写入令牌失效与页面结构契约测试**

测试读取 `api.js` 和 `index.html`，断言：令牌进入 `sessionStorage` 后调用 `history.replaceState` 移除查询参数；`401` 分支调用 `stopPolling()` 并显示“请重新双击风控矩阵审核器”；页面存在 `tasks-page`、`create-page`、`detail-page`、`help-page`、目录选择按钮和只读输出预览。

```python
def test_frontend_contains_session_recovery_and_required_pages(static_root: Path) -> None:
    """前端必须可从令牌失效恢复并包含全部页面。"""
    api_source = (static_root / "js/api.js").read_text(encoding="utf-8")
    html = (static_root / "index.html").read_text(encoding="utf-8")
    assert "sessionStorage.setItem" in api_source
    assert "history.replaceState" in api_source
    assert "stopPolling()" in api_source
    assert "请重新双击风控矩阵审核器" in api_source
    for page_id in ("tasks-page", "create-page", "detail-page", "help-page"):
        assert f'id="{page_id}"' in html
    assert 'id="input-directory-button"' in html
    assert 'id="output-path-preview"' in html and "readonly" in html
```

- [ ] **Step 3: 运行前端测试并确认静态文件不存在**

Run: `cd risk-audit && python -m pytest tests/web/test_web_assets.py tests/web/test_frontend_contract.py -q`

Expected: FAIL，错误指向静态文件或资产清单缺失。

- [ ] **Step 4: 创建可复现的固定版本资产脚本并下载资产**

`vendor_web_assets.py` 固定以下 URL：

```python
ASSET_URLS = {
    "css/bootstrap.min.css": "https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css",
    "js/bootstrap.bundle.min.js": "https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/js/bootstrap.bundle.min.js",
    "css/bootstrap-icons.min.css": "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.13.1/font/bootstrap-icons.min.css",
    "fonts/bootstrap-icons.woff2": "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.13.1/font/fonts/bootstrap-icons.woff2",
    "licenses/Bootstrap.txt": "https://raw.githubusercontent.com/twbs/bootstrap/v5.3.8/LICENSE",
    "licenses/Bootstrap-Icons.txt": "https://raw.githubusercontent.com/twbs/icons/v1.13.1/LICENSE.md",
}
```

脚本下载后把图标 CSS 中 `./fonts/bootstrap-icons.woff2` 改为 `../fonts/bootstrap-icons.woff2`，为每个文件记录版本、来源 URL、大小和 SHA-256。运行时不调用此脚本。

Run: `cd risk-audit && python tools/vendor_web_assets.py --download`

Expected: 生成四个静态资产、两个许可证和 `vendor-manifest.json`；再次运行 `python tools/vendor_web_assets.py --verify` 输出“Web 静态资源校验通过”。

- [ ] **Step 5: 实现确认原型的语义 HTML 和主题 CSS**

`index.html` 只引用相对本地文件：

```html
<link rel="stylesheet" href="/static/css/bootstrap.min.css">
<link rel="stylesheet" href="/static/css/bootstrap-icons.min.css">
<link rel="stylesheet" href="/static/css/theme.css">
<script defer src="/static/js/bootstrap.bundle.min.js"></script>
<script defer src="/static/js/api.js"></script>
<script defer src="/static/js/tasks.js"></script>
<script defer src="/static/js/app.js"></script>
```

使用 Bootstrap 表格、表单、进度条、Modal 和 Toast；`theme.css` 实现已确认原型的深蓝侧栏、蓝色主操作、柔和页面底色、卡片圆角和 `prefers-color-scheme` 深浅色。动态文本全部通过 `textContent` 或 `createTextNode` 写入。

- [ ] **Step 6: 实现令牌引导和统一请求**

```javascript
function initializeSessionToken() {
  const url = new URL(window.location.href);
  const queryToken = url.searchParams.get("token");
  if (queryToken) {
    sessionStorage.setItem("riskAuditToken", queryToken);
    url.searchParams.delete("token");
    history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }
  return sessionStorage.getItem("riskAuditToken");
}

async function apiRequest(path, options = {}) {
  const token = sessionStorage.getItem("riskAuditToken");
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Local-Token": token, ...(options.headers || {}) },
  });
  if (response.status === 401) {
    stopPolling();
    showSessionExpiredMessage();
    throw new Error("SESSION_EXPIRED");
  }
  return parseApiResponse(response);
}
```

- [ ] **Step 7: 实现任务页面和每秒轮询**

`tasks.js` 使用单一 `setInterval`；切换任务详情时停止列表轮询并启动详情轮询，返回列表时反向切换。刷新只更新文本、状态类和进度值，不替换整个页面节点，不关闭 Modal，不改变输入框值和滚动位置。

- [ ] **Step 8: 运行前端资产和服务静态文件测试**

Run: `cd risk-audit && uv run --extra web --extra test python -m pytest tests/web/test_web_assets.py tests/web/test_frontend_contract.py tests/web/test_server.py -q`

Expected: PASS；`python tools/vendor_web_assets.py --verify` PASS；`rg -n "https?://|//cdn" src/risk_audit_web/static` 只允许 `vendor-manifest.json` 中的来源元数据，不得出现在 HTML/CSS/JS 运行引用中。

- [ ] **Step 9: 记录建议提交信息，不执行提交**

```text
feat: 实现离线Bootstrap审核界面
```

---

### Task 7: 串联 EXE 启动、重开、Worker 分流和退出生命周期

**Files:**
- Create: `risk-audit/src/risk_audit_web/app.py`
- Create: `risk-audit/src/risk_audit_web/logging_setup.py`
- Modify: `risk-audit/src/risk_audit_web/server.py`
- Modify: `risk-audit/src/risk_audit_web/services.py`
- Create: `risk-audit/tests/web/test_app_lifecycle.py`
- Create: `risk-audit/tests/web/test_shutdown_lifecycle.py`

**Interfaces:**
- Consumes: Tasks 1–6 的 Worker、单实例、会话、浏览器、服务控制器、应用服务和静态页面。
- Produces: `main(argv: Sequence[str] | None = None) -> int`、`worker_arguments(executable: Path, request_path: Path, *, frozen: bool) -> list[str]`、`run_primary_instance(paths: PortablePaths, executable: Path, instance_lock: SingleInstanceLock, *, frozen: bool) -> int`、`reopen_existing_instance(session_path: Path, *, process_alive: Callable[[int], bool], health_check: Callable[[ServerSession], bool], browser_opener: Callable[[ServerSession], bool]) -> bool`、`resolve_static_root(paths: PortablePaths, *, frozen: bool) -> Path`。

- [ ] **Step 1: 写入 Worker 先分流失败测试**

```python
def test_worker_dispatch_does_not_import_fastapi_or_static_app(monkeypatch, tmp_path: Path) -> None:
    """Worker 模式必须在导入 Web 服务前分流；monkeypatch/tmp_path 隔离导入和请求。"""
    request = tmp_path / "request.json"
    imported = []
    original_import = builtins.__import__

    def record_import(name, *args, **kwargs):
        """记录导入并调用真实导入器。"""
        imported.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", record_import)
    monkeypatch.setattr("risk_audit_web.worker.run_worker", lambda path: 7)

    assert main(["--worker", str(request)]) == 7
    assert not any(name.startswith(("fastapi", "uvicorn")) for name in imported)
```

- [ ] **Step 2: 写入主/次实例和残留会话失败测试**

覆盖三种路径：主实例取得锁后启动服务并打开一次浏览器；次实例找到令牌健康检查匹配的会话后只打开浏览器；PID 存活但令牌健康检查不匹配时不得重开无关服务，取得主锁后覆盖残留会话。

```python
def test_secondary_instance_only_reopens_verified_session(tmp_path: Path) -> None:
    """次实例只能重开通过令牌健康检查的会话。"""
    session_path = tmp_path / "data/server-session.json"
    session = ServerSession("1.0", 321, 49152, "secret", "2026-09-25T10:00:00+08:00")
    write_server_session(session_path, session)
    opened = []

    assert reopen_existing_instance(
        session_path,
        process_alive=lambda pid: pid == 321,
        health_check=lambda current: current.token == "secret",
        browser_opener=lambda current: opened.append(current) or True,
    )
    assert opened == [session]
```

另一个测试把 `health_check` 改为 `False`，断言返回 `False` 且 `opened == []`；主实例测试用可控 `server_runner` 断言只调用一次。

- [ ] **Step 3: 写入退出生命周期失败测试**

```python
def test_cancel_active_tasks_shutdown_waits_before_server_exit(tmp_path: Path) -> None:
    """全部安全停止后退出必须等待任务终态；tmp_path 为任务存储。"""
    manager = StubManager(active=["a", "b"])
    controller = StubServerController()
    service = ApplicationServices(
        paths=PortablePaths.from_executable(tmp_path / "app.exe"),
        store=TaskStore(tmp_path / "data"),
        manager=manager,
        directory_picker=StubDirectoryPicker(),
        server_controller=controller,
        open_path=lambda path: None,
        background_runner=lambda callback: callback(),
    )

    result = service.request_shutdown("cancel_active_tasks")

    assert manager.cancelled == ["a", "b"]
    assert manager.waited
    assert controller.exit_requested
    assert result["status"] == "stopping_tasks"
```

同一测试文件定义 `StubManager`，其 `request_cancel_all()` 记录并返回 `active`、`wait_for_all()` 设置 `waited=True` 并返回 `True`；定义 `StubServerController.request_exit()` 设置 `exit_requested=True`；`StubDirectoryPicker` 在该测试中不执行系统操作。

- [ ] **Step 4: 运行生命周期测试并确认入口不存在**

Run: `cd risk-audit && uv run --extra web --extra test python -m pytest tests/web/test_app_lifecycle.py tests/web/test_shutdown_lifecycle.py -q`

Expected: FAIL，错误指向 `risk_audit_web.app` 或生命周期函数不存在。

- [ ] **Step 5: 实现 Worker 优先分流和参数生成**

```python
def main(argv: Sequence[str] | None = None) -> int:
    """启动 Web 主程序或 Worker；argv 为可选命令行参数，返回退出码。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--worker":
        from risk_audit_web.worker import run_worker
        return run_worker(Path(args[1]))
    return run_web_application(args)
```

源码 Worker 参数为 `[python, "-m", "risk_audit_web.app", "--worker", request]`；冻结模式为 `[exe, "--worker", request]`。

- [ ] **Step 6: 实现主实例启动顺序**

严格执行：解析 `PortablePaths` → 获取实例锁 → 运行任务恢复 → 预绑定回环 socket → 生成 token 和 `ServerSession` → 原子写会话 → 启动浏览器健康检查线程 → 阻塞运行 Uvicorn → 删除会话文件 → 释放锁。任何启动异常都必须关闭 socket、删除本次会话并释放锁。

冻结模式的 `PortablePaths` 以 `sys.executable` 为锚点，静态根固定为 `runtime/web`；源码模式以仓库根的哨兵路径为锚点，静态根固定为 `src/risk_audit_web/static`。源码模式即使缺少便携 `runtime` 也必须打开诊断页，但禁止创建任务。

- [ ] **Step 7: 实现次实例重开与有限重试**

次实例未取得锁时最多等待 20 次、每次 100ms 读取会话；健康检查请求携带 `X-Local-Token` 并要求返回 `{"status":"ok"}`。成功后打开浏览器并退出；失败时显示本地错误日志，不擅自删除主实例会话。

- [ ] **Step 8: 实现浏览器关闭独立性和退出清理**

服务生命周期不得持有浏览器窗口句柄，也不得注册浏览器关闭回调。正常退出、`KeyboardInterrupt` 和启动失败走同一个 `finally` 清理；Worker 独立进程不因浏览器标签页关闭而收到信号。

- [ ] **Step 9: 配置服务日志轮转**

```python
def configure_server_logging(data_root: Path) -> logging.Logger:
    """配置本机服务日志；data_root 为便携 data 目录，返回应用 logger。"""
    log_path = data_root / "logs/server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    logger = logging.getLogger("risk_audit_web")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger
```

新增测试连续写入超过上限的小尺寸测试 handler，断言生成 `server.log.1`，日志不含会话令牌。

- [ ] **Step 10: 运行生命周期、API 和并行测试**

Run: `cd risk-audit && uv run --extra web --extra test python -m pytest tests/web/test_app_lifecycle.py tests/web/test_shutdown_lifecycle.py tests/web/test_system_api.py tests/web/test_tasks_api.py tests/web/test_parallel_integration.py -q`

Expected: PASS；主实例、次实例、Worker 三种入口均有独立测试。

- [ ] **Step 11: 记录建议提交信息，不执行提交**

```text
feat: 串联Web审核器完整运行生命周期
```

---

### Task 8: 改造 onedir 便携构建、验证和 GitHub Actions

**Files:**
- Create: `risk-audit/tools/build_windows_web.py`
- Create: `risk-audit/tools/collect_python_licenses.py`
- Modify: `risk-audit/tools/verify_portable_distribution.py`
- Create: `risk-audit/tests/web/test_portable_distribution.py`
- Create: `risk-audit/tests/web/test_github_actions_workflow.py`
- Create: `.github/workflows/build-windows-web.yml`
- Create: `risk-audit/Windows Web版使用说明.md`

**Interfaces:**
- Consumes: PyInstaller 输出目录、LibreOffice、规则包、基准、主体文件、Web 静态目录和全部许可证。
- Produces: `风控矩阵审核器-v2.0.0-Windows-x64.zip`，顶层含 EXE、`_internal`、`runtime`、`data`、`outputs` 和 `使用说明.md`。

- [ ] **Step 1: 写入 onedir 与 Web 资源打包失败测试**

```python
def test_pyinstaller_command_uses_windowed_onedir_without_qt() -> None:
    """构建命令必须使用 onedir 且不收集 Qt。"""
    command = pyinstaller_command(Path("python.exe"), Path("app.py"), Path("app.ico"))
    assert "--onedir" in command
    assert "--windowed" in command
    assert "--onefile" not in command
    assert not any("PySide6" in item or "Qt" in item for item in command)
```

增加完整发布测试，断言 `_internal` 存在、`runtime/web` 四个资产存在、`runtime/licenses` 含 Bootstrap/Icons/FastAPI/Uvicorn/LibreOffice 声明、根目录没有 Qt 文件。

- [ ] **Step 2: 写入发布验证失败测试**

验证器必须拒绝：缺失 `_internal`、缺失 Web 资产、Web 清单哈希错误、HTML/CSS/JS 外部运行引用、任何 `PySide6`/`Qt6`/`.qml` 文件、多个 ZIP 顶层目录和开发机绝对路径。

```python
@pytest.mark.parametrize(
    ("relative_path", "content", "expected"),
    [
        ("runtime/web/index.html", b'<script src="https://cdn.example/a.js"></script>', "外部资源"),
        ("_internal/PySide6/Qt6Core.dll", b"qt", "Qt"),
        ("runtime/web/css/theme.css", b"body{background:url(http://example/a.png)}", "外部资源"),
    ],
)
def test_distribution_rejects_web_and_qt_leaks(
    tmp_path: Path, relative_path: str, content: bytes, expected: str,
) -> None:
    """发布验证必须拒绝网络依赖和 Qt 残留。"""
    root = make_valid_distribution(tmp_path)
    write_file(root / relative_path, content)
    assert any(expected in error for error in verify_distribution(root))
```

`make_valid_distribution()` 和 `write_file()` 在同一测试文件中创建完整最小目录及匹配 runtime manifest，不依赖真实 Windows 构建产物。

- [ ] **Step 3: 运行构建测试并确认旧脚本不满足要求**

Run: `cd risk-audit && uv run --extra web --extra test python -m pytest tests/web/test_portable_distribution.py tests/web/test_github_actions_workflow.py -q`

Expected: FAIL，至少包含旧构建使用 `--onefile`、未复制 `_internal` 或没有 `runtime/web`。

- [ ] **Step 4: 实现 onedir PyInstaller 命令**

```python
def pyinstaller_command(python_executable: Path, app_entry: Path, icon_path: Path) -> list[str]:
    """生成 Web 版 onedir 构建参数；参数为 Python、入口和图标。"""
    return [
        str(python_executable), "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onedir", "--windowed", "--name", APP_NAME, "--icon", str(icon_path),
        "--paths", str(app_entry.parent.parent), str(app_entry),
    ]
```

构建产物来源是 `dist/风控矩阵risk-audit/` 整个目录；组装时复制 EXE 和 `_internal`，再生成 `runtime`、空 `data`、空 `outputs` 和 `使用说明.md`。

- [ ] **Step 5: 扩展运行资源收集和 manifest**

`DistributionSources` 增加 `web_root` 和 `web_licenses_root`。收集前执行 `vendor_web_assets.py --verify` 等价校验；复制静态资源到 `runtime/web`、许可证到 `runtime/licenses`，最后对完整 `runtime` 生成 SHA-256 manifest。

`collect_python_licenses.py` 提供：

```python
def collect_distribution_licenses(
    distribution_names: Sequence[str],
    destination: Path,
) -> list[Path]:
    """复制已安装 wheel 的许可证；参数为发行名和目标目录，返回复制文件。"""
```

它通过 `importlib.metadata.distribution(name).files` 查找每个发行版 `.dist-info/licenses`、`LICENSE*` 或 `COPYING*`，缺少许可证即使构建失败；另从当前 Windows Python 安装根复制 `LICENSE.txt`。发行名从 `requirements-web.lock` 的锁定项目解析，至少覆盖 FastAPI、Uvicorn、Starlette、Pydantic、AnyIO、h11、PyInstaller 以及审核核心的所有第三方依赖。测试使用伪 distribution 元数据验证“每个发行版至少一份许可证”和缺失时失败。

- [ ] **Step 6: 更新发布验证器**

`ALLOWED_ROOT_ITEMS` 改为：

```python
ALLOWED_ROOT_ITEMS = {
    "风控矩阵审核器.exe", "_internal", "runtime", "data", "outputs", "使用说明.md",
}
```

扫描静态文本中的 `src="http`、`href="http`、`url(http` 和绝对 `fetch("http`；允许许可证正文和 `vendor-manifest.json` 记录来源 URL。扫描整个发布根，拒绝名称含 `PySide6`、`Qt6`、`qml` 的运行文件。

- [ ] **Step 7: 更新 Windows 手动构建工作流**

新工作流继续只允许 `workflow_dispatch`，安装 `requirements-web.lock`，运行 `tests/web`，构建脚本改为 `build_windows_web.py`，不设置 `QT_QPA_PLATFORM`，然后验证 ZIP 并上传同名产物。LibreOffice 版本、官方 URL 和 SHA-256 保持现有固定值。

- [ ] **Step 8: 编写 Web 版使用说明**

说明双击打开默认浏览器、完全离线、选择输入目录、固定 outputs、浏览器关闭后继续运行、再次双击重新打开、“退出审核器”的两种路径、任务失败日志位置和必须整体移动发布目录。

- [ ] **Step 9: 运行构建、验证器和工作流契约测试**

Run: `cd risk-audit && uv run --extra web --extra test python -m pytest tests/web/test_portable_distribution.py tests/web/test_github_actions_workflow.py -q`

Expected: PASS；测试生成的 ZIP 只有一个顶层目录并包含 `_internal` 与 `runtime/web`。

- [ ] **Step 10: 记录建议提交信息，不执行提交**

```text
build: 改为Windows离线Web版便携构建
```

---

### Task 9: 删除旧 GUI、更新说明并完成全量验证

**Files:**
- Delete: `risk-audit/src/risk_audit_desktop/`
- Delete: `risk-audit/tests/desktop/`
- Delete: `risk-audit/requirements-desktop.lock`
- Delete: `risk-audit/tools/build_windows_desktop.py`
- Delete: `risk-audit/Windows桌面版使用说明.md`
- Delete: `.github/workflows/build-windows-desktop.yml`
- Modify: `risk-audit/pyproject.toml`
- Modify: `risk-audit/README.md`
- Modify: `使用说明.md`
- Modify: `风控矩阵审核器使用说明.docx`

**Interfaces:**
- Consumes: Tasks 1–8 的完整 Web 应用与构建链。
- Produces: 仓库中唯一的便携交互层 `risk_audit_web`，无 Qt 运行依赖、旧 GUI 入口或旧 GUI 测试。

- [ ] **Step 1: 写入旧 GUI 清理失败测试**

在 `tests/web/test_repository_cleanup.py` 中检查：

```python
def test_repository_has_no_runtime_qt_or_desktop_entry() -> None:
    """最终仓库不得继续发布旧 GUI。"""
    root = Path(__file__).resolve().parents[2]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert not (root / "src/risk_audit_desktop").exists()
    assert "PySide6" not in pyproject
    assert "pytest-qt" not in pyproject
    assert "risk-audit-desktop" not in pyproject
```

- [ ] **Step 2: 运行清理测试并确认旧目录仍导致失败**

Run: `cd risk-audit && python -m pytest tests/web/test_repository_cleanup.py -q`

Expected: FAIL，指出旧包、Qt 依赖和旧入口仍存在。

- [ ] **Step 3: 删除旧 GUI 文件并收紧 pyproject**

删除列出的旧包、测试、构建脚本、工作流、锁文件和桌面说明。`pyproject.toml` 最终只保留 `test`、`web`、`build` 可选依赖以及 `risk-audit`、`risk-audit-web` 两个脚本入口。

- [ ] **Step 4: 更新项目说明**

README 和根使用说明统一描述“单机离线 Web 版”，删除 Qt、托盘、输出目录可选和单文件 EXE 描述；明确网页不是云服务、材料不会上传、关闭浏览器不停止任务。

使用 `documents:documents` 技能更新 `风控矩阵审核器使用说明.docx` 为相同口径，至少包含启动、选择材料、固定 outputs、关闭浏览器、再次双击、退出和常见错误；用该技能要求的 `render_docx.py` 渲染全部页面，确认无溢出、截断、乱码和旧 GUI 截图或描述后再保留最终 DOCX。

- [ ] **Step 5: 运行旧引用扫描**

Run:

```bash
rg -n "PySide6|pytest-qt|risk_audit_desktop|risk-audit-desktop|QT_QPA_PLATFORM|--onefile" \
  risk-audit/src risk-audit/tests/web risk-audit/tools risk-audit/pyproject.toml \
  risk-audit/README.md 使用说明.md .github/workflows/build-windows-web.yml
```

Expected: 无输出。历史设计与计划文档不参与此扫描，可保留作为决策记录。

- [ ] **Step 6: 运行代码编译与 Web 全套测试**

Run:

```bash
cd risk-audit
uv run --extra web --extra test python -m compileall src/risk_audit_web
uv run --extra web --extra test python -m pytest tests/web -q
```

Expected: compileall 成功，`tests/web` 全部 PASS。

- [ ] **Step 7: 运行审核核心全套测试**

Run: `cd risk-audit && uv run --extra web --extra test python -m pytest tests -q`

Expected: 全部 PASS；不存在因删除旧 GUI 测试产生的空测试目录或导入错误。

- [ ] **Step 8: 执行源码模式本机烟雾测试**

Run: `cd risk-audit && uv run --extra web risk-audit-web`

Expected: 仅监听 `127.0.0.1` 随机端口，默认浏览器打开诊断页；HTML/CSS/JS 均来自同源；源码目录缺少便携 runtime 时明确显示资源缺失并禁用创建任务；关闭页面后服务仍运行；再次执行同一命令重新打开页面；点击退出后进程结束并删除 `data/server-session.json`。完整任务创建和审核在下一步 Windows 便携产物中验收。

- [ ] **Step 9: 执行 Windows 发布验收**

通过 `.github/workflows/build-windows-web.yml` 手动构建；下载 ZIP 后在未安装 Python、Node.js、Qt 和 LibreOffice 的 Windows 10/11 x64 环境验证：解压即用、并行任务、浏览器关闭继续运行、重复双击重开、固定 outputs、无网络请求、完整目录移动后仍运行。

- [ ] **Step 10: 检查工作区并记录最终建议提交信息，不执行提交**

Run: `git status --short && git diff --check`

Expected: 只有本计划涉及的 Web 迁移、文档和依赖文件；`git diff --check` 无输出。

```text
feat: 将Windows审核器迁移为单机离线Web版
```
