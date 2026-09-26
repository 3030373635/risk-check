# 跨平台脚本启动便携版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有单机离线 Web 审核器改造成 Windows x64 与 macOS Apple Silicon 两个独立便携 ZIP，分别通过 BAT 和 `.command` 在终端前台运行包内 Python，并在终端结束时清理审核进程。

**Architecture:** 保留 FastAPI、原生 HTML/CSS/JavaScript、任务存储和独立 Worker 架构，移除 PyInstaller 分支。启动器只负责解析发布根、设置受控环境并以前台方式执行包内 Python；Python 侧统一通过平台适配器处理目录选择、单实例、打开文件和进程生命周期；平台原生构建器从锁定来源组装 `app/runtime/data/outputs`，再由同一发布校验器验收。

**Tech Stack:** CPython 3.11.16 standalone、FastAPI、Uvicorn、原生 JavaScript、Bootstrap、LibreOffice、pytest、GitHub Actions、Windows BAT、macOS command/zsh。

**Spec:** `docs/superpowers/specs/2026-09-26-cross-platform-script-portable-design.md`

## Global Constraints

- 目标平台仅为 Windows 10/11 x64 与 macOS Apple Silicon；本次不实现 Linux、Intel Mac、Universal 2、DMG、PKG、签名或公证。
- Windows 入口固定为 `启动审核器.bat`，macOS 入口固定为 `启动审核器.command`；不得使用 PyInstaller、`pythonw`、`start`、`nohup` 或后台 `&`。
- 产品运行时完全离线，只监听 `127.0.0.1` 随机端口，不加载 CDN、远程字体、远程图片或外部 API。
- 用户只选择输入目录，输出只能由后端创建在发布根 `outputs` 中。
- 发布根通过绝对环境变量 `RISK_AUDIT_APP_ROOT` 传入；源码开发模式不得伪造发布结构。
- 两个平台使用 Astral `python-build-standalone` 20260924 的 CPython 3.11.16 `install_only_stripped` 基线架构构建，不使用 x86-64-v2/v3 或 free-threaded 变体。
- Windows Python SHA-256 为 `f86b3cbd425e1c446b56aa24e20a7be1223c1a8146e5e3a68c8e18d08b76e810`；macOS Python SHA-256 为 `e1d745b07b6acc0641dbb3237d3c5953deeeed182141bab2242684076fd86547`。
- 第三方运行依赖必须来自 `--require-hashes` 的运行专用锁，不得包含 pytest、httpx、PyInstaller、altgraph、pefile 或 macholib。
- Python 使用 `snake_case`，JavaScript 使用 `camelCase`，启动器变量使用清晰的大写名称；所有新增 Python 函数必须说明用途、参数和返回值，关键路径信任、进程终止和摘要校验代码必须有中文注释。
- REST API 继续使用现有资源语义；不得为了脚本启动增加非 RESTful 页面专用接口。
- 不兼容旧 PyInstaller 目录、冻结入口、旧 EXE 测试或旧分发结构，直接删除对应逻辑。
- 按项目规则不执行 `git commit`；每个任务完成后仅记录一条中文 Conventional Commit 建议。
- 采用 TDD：每项行为先增加失败测试，确认失败原因正确，再实现最小代码并运行相关回归。

## Review Focus

- 发布目录位于含中文、空格和符号的路径时，两个启动器仍应把同一个绝对根传给应用；由 Task 1 的启动器契约测试覆盖。
- 锁已占用但会话文件损坏、PID 复用或令牌不匹配时，次实例必须失败且不得打开错误页面；由 Task 2 的单实例测试覆盖。
- 终端在 Worker 正调用 LibreOffice 时关闭，主进程、Worker 和其子进程都不得残留，且任务下次启动标为意外中断；由 Task 3 的进程树和恢复测试覆盖。
- 下载归档摘要正确但包含绝对路径、`..` 或符号链接越界时，构建必须拒绝解压；由 Task 4 的安全解压测试覆盖。
- macOS ZIP 解压后必须保留 `.command`、Python、LibreOffice 可执行位和应用包符号链接；由 Task 6 的归档往返测试覆盖。

---

### Task 1: 便携运行根、平台路径与前台启动器

**Files:**
- Create: `审核器/packaging/启动审核器.bat`
- Create: `审核器/packaging/启动审核器.command`
- Modify: `审核器/src/risk_audit_web/task_paths.py`
- Modify: `审核器/src/risk_audit_web/app.py`
- Modify: `审核器/tests/web/test_task_paths.py`
- Modify: `审核器/tests/web/test_app_lifecycle.py`
- Create: `审核器/tests/web/test_launchers.py`

**Interfaces:**
- Produces: `PortablePaths.from_app_root(app_root: Path, *, platform_name: str | None = None) -> PortablePaths`。
- Produces: `resolve_application_paths(environment: Mapping[str, str], source_root: Path, *, platform_name: str | None = None) -> tuple[PortablePaths, bool]`；布尔值表示发布模式。
- Produces: `worker_arguments(python_executable: Path, request_path: Path) -> list[str]`，始终以 `-m risk_audit_web.app --worker` 启动。
- Produces: `resolve_static_root(paths: PortablePaths, *, portable: bool) -> Path`。
- Consumes: 无。

- [ ] **Step 1: 写路径和启动器失败测试**

  增加以下断言：Windows `soffice` 为 `runtime/libreoffice/program/soffice.exe`，macOS 为 `runtime/libreoffice/LibreOffice.app/Contents/MacOS/soffice`；`RISK_AUDIT_APP_ROOT` 必须是绝对路径且包含 `app/runtime/data/outputs`；源码模式仍加载包内 `static`；BAT 使用 `%~dp0`、前台 `python.exe`、`PYTHONNOUSERSITE`、`PYTHONPATH`，`.command` 使用自身路径、`exec` 和 `runtime/python/bin/python3`；两者均不得出现后台启动或网络下载。

- [ ] **Step 2: 运行测试并确认旧冻结分支导致失败**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_task_paths.py tests/web/test_app_lifecycle.py tests/web/test_launchers.py -q`

  Expected: FAIL，原因包含 `from_app_root`/`resolve_application_paths` 或启动器文件不存在，而不是测试环境异常。

- [ ] **Step 3: 实现便携路径和两个启动器**

  删除 `sys.frozen`、`from_executable()` 和冻结 Worker 参数；发布模式只信任 `RISK_AUDIT_APP_ROOT`，源码模式由显式 `source_root` 推导。BAT 与 `.command` 都设置 `PYTHONUTF8=1`、`PYTHONNOUSERSITE=1`、`PYTHONPATH=<root>/app` 和绝对 `RISK_AUDIT_APP_ROOT`，并以前台方式替换/等待包内 Python。

- [ ] **Step 4: 运行相关回归**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_task_paths.py tests/web/test_app_lifecycle.py tests/web/test_launchers.py tests/web/test_worker.py -q`

  Expected: PASS。

- [ ] **Step 5: 记录提交建议**

  建议：`feat: 增加跨平台前台启动器与便携路径`

### Task 2: 平台目录选择、单实例与打开文件适配

**Files:**
- Create: `审核器/src/risk_audit_web/platform_runtime.py`
- Delete: `审核器/src/risk_audit_web/platform_windows.py`
- Modify: `审核器/src/risk_audit_web/directory_picker.py`
- Modify: `审核器/src/risk_audit_web/single_instance.py`
- Modify: `审核器/src/risk_audit_web/app.py`
- Modify: `审核器/src/risk_audit_web/task_manager.py`
- Modify: `审核器/tests/web/test_directory_picker.py`
- Rename: `审核器/tests/web/test_platform_windows.py` to `审核器/tests/web/test_platform_runtime.py`
- Modify: `审核器/tests/web/test_single_instance.py`
- Modify: `审核器/tests/web/test_app_lifecycle.py`

**Interfaces:**
- Consumes: Task 1 的 `PortablePaths` 与发布根解析。
- Produces: `run_directory_dialog(platform_name: str, *, runner: Callable[..., CompletedProcess[str]] = subprocess.run) -> str`。
- Produces: `DirectoryPickerError(error_code: str, message: str)`；取消仍返回空字符串，不作为错误。
- Produces: `is_process_alive(pid: int) -> bool`、`terminate_process(pid: int) -> bool`、`open_path(path: Path) -> None`。
- Produces: `SingleInstanceLock.acquire() -> bool` 在 Windows 使用 Mutex，在 macOS/测试 POSIX 使用 `fcntl.flock(LOCK_EX | LOCK_NB)`。

- [ ] **Step 1: 写平台行为失败测试**

  测试 Windows 调用固定 PowerShell `-NoProfile -STA` 脚本且不把用户路径拼入源码；macOS 只调用 `/usr/bin/osascript` 的 `choose folder`；区分取消和系统失败；macOS 打开文件固定调用 `/usr/bin/open`；`flock` 第二持有者失败、首持有者进程关闭后无需删除锁文件即可重获；损坏会话、PID 复用和错误令牌不触发浏览器。

- [ ] **Step 2: 运行测试并确认 tkinter/O_EXCL 实现失败**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_directory_picker.py tests/web/test_platform_runtime.py tests/web/test_single_instance.py tests/web/test_app_lifecycle.py -q`

  Expected: FAIL，原因指向系统命令契约、`flock` 或模块重命名。

- [ ] **Step 3: 实现平台适配并更新引用**

  Windows 目录选择器使用固定 PowerShell/.NET 脚本；macOS 使用 `osascript` 并识别用户取消错误 `-128`。删除 tkinter 和非 Windows `O_EXCL` 残留锁；把 `app.py`、`task_manager.py` 的进程引用统一改到 `platform_runtime.py`。

- [ ] **Step 4: 运行平台与 API 回归**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_directory_picker.py tests/web/test_platform_runtime.py tests/web/test_single_instance.py tests/web/test_app_lifecycle.py tests/web/test_system_api.py -q`

  Expected: PASS。

- [ ] **Step 5: 记录提交建议**

  建议：`feat: 增加Windows与macOS平台适配`

### Task 3: 终端生命周期与子进程树清理

**Files:**
- Create: `审核器/src/risk_audit_web/process_supervisor.py`
- Modify: `审核器/src/risk_audit_web/platform_runtime.py`
- Modify: `审核器/src/risk_audit_web/task_manager.py`
- Modify: `审核器/src/risk_audit_web/app.py`
- Modify: `审核器/src/risk_audit_web/server.py`
- Modify: `审核器/tests/web/test_task_manager.py`
- Modify: `审核器/tests/web/test_shutdown_lifecycle.py`
- Create: `审核器/tests/web/test_process_supervisor.py`
- Modify: `审核器/tests/web/test_parallel_integration.py`

**Interfaces:**
- Consumes: Task 2 的进程检查和终止函数。
- Produces: `create_process_supervisor(platform_name: str | None = None) -> ProcessSupervisor`。
- Produces: `ProcessSupervisor.prepare() -> None`、`ProcessSupervisor.register(process: subprocess.Popen[Any]) -> None`、`ProcessSupervisor.close() -> None`。
- Produces: `descendant_pids(parent_pid: int, process_rows: Sequence[tuple[int, int]]) -> list[int]`，返回按最深后代优先排列且不含父进程的 PID。
- Produces: `TaskManager.stop_all_workers(timeout_seconds: float = 5.0) -> list[int]`，返回未能在时限内结束的 PID。
- Produces: `install_terminal_shutdown_handler(controller: ServerController, *, platform_name: str | None = None) -> Callable[[], None]`，返回恢复原信号处理器的回调。

- [ ] **Step 1: 写进程树和信号失败测试**

  Windows 测试 Job Object 设置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 且保留 64 位句柄；macOS 测试 `SIGHUP` 请求服务退出；`run_primary_instance` 的 `finally` 必须停止全部 Worker 后再关闭 supervisor；模拟 Worker 拥有 LibreOffice 子进程时，进程树终止顺序为后代到父进程；下次恢复把仍为活动态但 PID 已消失的任务标为 `interrupted`。

- [ ] **Step 2: 运行测试并确认缺少 supervisor**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_process_supervisor.py tests/web/test_task_manager.py tests/web/test_shutdown_lifecycle.py tests/web/test_parallel_integration.py -q`

  Expected: FAIL，原因是 supervisor/终端清理接口不存在。

- [ ] **Step 3: 实现 Windows Job Object 与 macOS 有界清理**

  Windows supervisor 创建 Job Object、设置 kill-on-close，并把主进程放入 Job；macOS 保持 Worker 在终端会话中，捕获 `SIGHUP`，退出时从系统进程表解析已登记 Worker 的后代并按后代优先发送 `SIGTERM`，超时后仅对仍属于该树的 PID 发送 `SIGKILL`。不得把强制退出写成用户取消态。

- [ ] **Step 4: 运行生命周期回归**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_process_supervisor.py tests/web/test_task_manager.py tests/web/test_shutdown_lifecycle.py tests/web/test_parallel_integration.py tests/web/test_app_lifecycle.py -q`

  Expected: PASS。

- [ ] **Step 5: 记录提交建议**

  建议：`feat: 绑定终端生命周期并清理审核进程树`

### Task 4: 锁定便携 Python 来源与纯运行依赖

**Files:**
- Create: `审核器/packaging/runtime-sources.json`
- Create: `审核器/requirements-runtime.lock`
- Delete: `审核器/requirements-web.lock`
- Modify: `审核器/pyproject.toml`
- Modify: `审核器/uv.lock`
- Create: `审核器/tools/runtime_sources.py`
- Create: `审核器/tests/web/test_runtime_sources.py`
- Modify: `审核器/tests/web/test_repository_cleanup.py`

**Interfaces:**
- Consumes: 无运行代码接口。
- Produces: `RuntimeSource` 数据类，字段为 `platform_id`、`kind`、`version`、`url`、`sha256`、`size`、`archive_root`。
- Produces: `load_runtime_sources(path: Path) -> dict[str, dict[str, RuntimeSource]]`。
- Produces: `download_verified(source: RuntimeSource, destination: Path) -> Path`。
- Produces: `extract_verified_archive(archive: Path, destination: Path, *, archive_root: str) -> Path`，拒绝绝对路径、`..` 和解压后越界的符号链接。

- [ ] **Step 1: 写来源清单、锁文件与安全解压失败测试**

  固定以下 Python 资产：

  - macOS：`cpython-3.11.16+20260924-aarch64-apple-darwin-install_only_stripped.tar.gz`，大小 `26965707`，SHA-256 `e1d745b07b6acc0641dbb3237d3c5953deeeed182141bab2242684076fd86547`。
  - Windows：`cpython-3.11.16+20260924-x86_64-pc-windows-msvc-install_only_stripped.tar.gz`，大小 `25273229`，SHA-256 `f86b3cbd425e1c446b56aa24e20a7be1223c1a8146e5e3a68c8e18d08b76e810`。

  测试摘要/大小不符拒绝、目标已存在拒绝、恶意 tar 路径拒绝、运行锁不含测试/冻结依赖、`pyproject.toml` 不再声明 PyInstaller build extra。

- [ ] **Step 2: 运行测试并确认清单和纯运行锁尚不存在**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_runtime_sources.py tests/web/test_repository_cleanup.py -q`

  Expected: FAIL，原因是新文件或函数不存在。

- [ ] **Step 3: 实现来源加载、校验下载、安全解压并生成运行锁**

  来源 URL 固定为 `https://github.com/astral-sh/python-build-standalone/releases/download/20260924/<asset>`；运行锁由基础依赖加 `web` extra 生成，不包含项目自身和开发 extras。Windows LibreOffice 26.2.6 的 MSI URL/SHA 沿用现有工作流；macOS 清单只声明本机构建所需 LibreOffice `25.2.6.2` 和 `arm64`，不伪造下载地址。

- [ ] **Step 4: 运行锁与安全解压回归**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_runtime_sources.py tests/web/test_repository_cleanup.py tests/web/test_runtime_imports.py -q`

  Expected: PASS。

- [ ] **Step 5: 记录提交建议**

  建议：`build: 锁定跨平台便携运行时与产品依赖`

### Task 5: 通用发布组装器、目标 Python 安装与许可证

**Files:**
- Create: `审核器/tools/portable_release.py`
- Create: `审核器/tools/build_portable.py`
- Delete: `审核器/tools/build_windows_web.py`
- Modify: `审核器/tools/collect_python_licenses.py`
- Modify: `审核器/src/risk_audit_web/diagnostics.py`
- Modify: `审核器/src/risk_audit_web/services.py`
- Modify: `审核器/tests/web/test_diagnostics.py`
- Create: `审核器/tests/web/test_portable_builder.py`
- Modify: `审核器/tests/web/test_portable_distribution.py`

**Interfaces:**
- Consumes: Task 1 的启动器/目录结构，Task 4 的 `RuntimeSource`、下载和解压函数。
- Produces: `BuildInputs` 数据类，包含平台、项目根、目标 Python 根、LibreOffice 根、许可证根、输出根和使用说明。
- Produces: `assemble_distribution(inputs: BuildInputs) -> Path`，失败时删除本次半成品且不覆盖已有目标。
- Produces: `install_runtime_dependencies(python_executable: Path, lock_file: Path) -> None`，固定使用 `-m pip --require-hashes --only-binary=:all:`。
- Produces: `build_release_manifest(distribution_root: Path) -> dict[str, object]`，覆盖 `app`、`runtime`（排除清单本身）、平台启动器和 `使用说明.md`，排除 `data/outputs`。
- Produces: `verify_release_manifest(app_root: Path) -> DiagnosticReport`。
- Produces: `collect_distribution_licenses(python_executable: Path, distribution_names: list[str], destination: Path) -> list[Path]`，必须从目标 Python 环境读取元数据。

- [ ] **Step 1: 写组装和清单失败测试**

  测试源码只复制 `risk_audit`/`risk_audit_web` 到 `app` 且不复制测试、缓存或 `risk_audit_web/static`；第三方依赖安装到目标 Python；Web 静态文件只存在于 `runtime/web` 的运行副本；`data/outputs` 为空；清单包含不可变载荷但排除自身和可写目录；许可证按目标 Python 当前平台标记收集；二次构建拒绝覆盖；中途失败删除半成品。

- [ ] **Step 2: 运行测试并确认旧 onedir 组装器失败**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_portable_builder.py tests/web/test_portable_distribution.py tests/web/test_diagnostics.py -q`

  Expected: FAIL，原因指向新组装接口不存在或旧 `_internal/EXE` 契约。

- [ ] **Step 3: 实现通用组装器和目标环境许可证收集**

  平台构建必须在目标系统运行；Windows 复制完整 LibreOffice 目录，macOS 使用 `/usr/bin/ditto` 复制 `.app` 并用 `lipo -archs`、`codesign --verify --deep --strict` 验证 arm64 与签名。依赖安装后删除 pip 缓存、wheel、`__pycache__` 和 `.pyc`。启动诊断改为读取发布根清单，并在 `ApplicationServices` 中缓存一次启动诊断，避免状态轮询重复哈希全部 LibreOffice 文件。

- [ ] **Step 4: 运行组装和诊断回归**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_portable_builder.py tests/web/test_portable_distribution.py tests/web/test_diagnostics.py tests/web/test_services.py -q`

  Expected: PASS。

- [ ] **Step 5: 记录提交建议**

  建议：`build: 实现跨平台便携包通用组装器`

### Task 6: 平台发布校验器与保真归档

**Files:**
- Modify: `审核器/tools/verify_portable_distribution.py`
- Modify: `审核器/tools/portable_release.py`
- Modify: `审核器/tests/web/test_portable_distribution.py`
- Create: `审核器/tests/web/test_archive_round_trip.py`

**Interfaces:**
- Consumes: Task 5 的目录结构和发布清单。
- Produces: `verify_distribution(distribution_root: Path, platform_id: str) -> list[str]`。
- Produces: `create_distribution_archive(distribution_root: Path, platform_id: str) -> Path`。
- Produces: `verify_archive(archive_path: Path, platform_id: str) -> list[str]`。
- Produces: 校验器 CLI 接受发布目录或 ZIP 作为位置参数，并要求 `--platform windows-x64|macos-arm64`。

- [ ] **Step 1: 写双平台校验和归档往返失败测试**

  Windows 根只允许 BAT，要求 `runtime/python/python.exe` 和 Windows soffice；macOS 根只允许 `.command`，要求 `runtime/python/bin/python3` 和 `.app` soffice。两者拒绝 PyInstaller、Qt、开发机绝对路径、测试目录、外部 Web 引用和可由 API 指定的外部输出根。macOS 归档解压后必须保留启动器/Python/soffice 可执行位和应用包符号链接；ZIP 只能有一个顶层目录并显式保留空 `data/outputs`。

- [ ] **Step 2: 运行测试并确认旧 Windows-only 校验失败**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_portable_distribution.py tests/web/test_archive_round_trip.py -q`

  Expected: FAIL，原因包含旧 EXE/`_internal` 约束或 macOS 权限丢失。

- [ ] **Step 3: 实现平台感知校验和归档**

  Windows 使用 Python ZIP 实现；macOS 使用 `/usr/bin/ditto -c -k --keepParent` 创建归档并以 `ditto -x -k` 做验收，避免 `zipfile.extractall` 丢失权限/符号链接。校验器同时核对发布清单、离线 Web、许可证、架构、根目录白名单和禁止文件。

- [ ] **Step 4: 运行发布工具回归**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_portable_distribution.py tests/web/test_archive_round_trip.py tests/web/test_web_assets.py tests/web/test_frontend_contract.py -q`

  Expected: PASS。

- [ ] **Step 5: 记录提交建议**

  建议：`build: 增加双平台发布校验与保真归档`

### Task 7: Windows CI 与 macOS Apple Silicon 本机构建入口

**Files:**
- Modify: `.github/workflows/build-windows-web.yml`
- Create: `审核器/tools/build_macos_portable.command`
- Modify: `审核器/tests/web/test_github_actions_workflow.py`
- Create: `审核器/tests/web/test_build_entrypoints.py`

**Interfaces:**
- Consumes: Task 4 的来源清单、Task 5 的 `build_portable.py`、Task 6 的校验器。
- Produces: Windows artifact `风控矩阵审核器-v2.0.0-Windows-x64.zip`。
- Produces: macOS artifact `风控矩阵审核器-v2.0.0-macOS-arm64.zip`。

- [ ] **Step 1: 写构建入口失败测试**

  Windows 工作流必须手动触发、使用 `windows-2022`、安装构建测试依赖但把纯运行锁装入目标 Python、校验 Python/LibreOffice 摘要、调用通用构建器和双平台校验器、上传单个 ZIP；不得调用 PyInstaller。macOS 本地构建入口必须拒绝非 Darwin/非 arm64，默认接收 `/Applications/LibreOffice.app`，且只把参数传给包内 Python/通用构建器，不修改系统 Python。

- [ ] **Step 2: 运行测试并确认旧工作流失败**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_github_actions_workflow.py tests/web/test_build_entrypoints.py -q`

  Expected: FAIL，原因包含 PyInstaller 构建器引用或 macOS 入口缺失。

- [ ] **Step 3: 更新 Windows 工作流并实现 macOS 本地构建入口**

  Windows 继续使用固定 LibreOffice 26.2.6 MSI 和现有 SHA-256；通用构建器负责下载/校验目标 Python。macOS `.command` 构建脚本只服务开发者本机构建，解析仓库根后以前台方式调用开发环境 Python 的 `tools/build_portable.py --platform macos-arm64 --libreoffice /Applications/LibreOffice.app`。

- [ ] **Step 4: 运行构建契约与完整 Web 测试**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web -q`

  Expected: PASS。

- [ ] **Step 5: 记录提交建议**

  建议：`ci: 增加脚本便携版双平台构建入口`

### Task 8: 使用文档与页面帮助统一

**Files:**
- Modify: `使用说明.md`
- Modify: `审核器/README.md`
- Modify: `审核器/Windows Web版使用说明.md`
- Create: `审核器/macOS Apple Silicon Web版使用说明.md`
- Modify: `审核器/src/risk_audit_web/static/index.html`
- Modify: `审核器/src/risk_audit_web/static/js/app.js`
- Modify: `审核器/tests/web/test_frontend_contract.py`
- Modify: `风控矩阵审核器使用说明.docx`

**Interfaces:**
- Consumes: Task 1 的启动器名称、Task 7 的最终 ZIP 名称。
- Produces: 无代码接口。

- [ ] **Step 1: 写文档与页面文案失败测试**

  断言页面/Markdown 不再宣称 EXE/PyInstaller，不显示输出选择；明确 Windows 双击 BAT、macOS 双击 `.command`、终端必须保持打开、浏览器关闭不退出、再次双击重开页面、网页按钮正常退出、关闭终端强制退出、输出固定在 `outputs`。macOS 说明包含不带 `sudo` 的限定目录 `xattr -dr com.apple.quarantine` 与 `chmod +x`。

- [ ] **Step 2: 运行前端契约测试并确认旧文案失败**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_frontend_contract.py tests/web/test_repository_cleanup.py -q`

  Expected: FAIL，原因是旧 Windows-only/EXE 文案或 macOS 文档缺失。

- [ ] **Step 3: 更新 Markdown、页面帮助和 DOCX**

  使用 `documents` skill 的渲染校验流程编辑 DOCX；保持现有视觉风格，只修改交付、启动、退出、输出和 Gatekeeper 说明。macOS 命令必须使用具体解压目录示例，明确只能对已确认来源的发布目录执行。

- [ ] **Step 4: 运行文档契约并渲染 DOCX 验收**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web/test_frontend_contract.py tests/web/test_repository_cleanup.py -q`

  Expected: PASS；DOCX 渲染页无截断、重叠、乱码或失效页码。

- [ ] **Step 5: 记录提交建议**

  建议：`docs: 更新跨平台脚本便携版使用说明`

### Task 9: 完整回归与真实 macOS arm64 发布验收

**Files:**
- Modify only if verification exposes a defect: files owned by Tasks 1–8
- Create as build output, not source: `release/风控矩阵审核器-v2.0.0-macOS-arm64.zip`

**Interfaces:**
- Consumes: Tasks 1–8 的完整产品与构建链。
- Produces: 经校验的 macOS arm64 第一版发布 ZIP；Windows 包由 Task 7 工作流生成。

- [ ] **Step 1: 运行 Web 专项回归**

  Run: `cd 审核器 && .venv/bin/python -m pytest tests/web -q`

  Expected: 全部通过。

- [ ] **Step 2: 运行完整项目测试并区分既有核心失败**

  Run: `cd 审核器 && .venv/bin/python -m pytest -q`

  Expected: Web/本次改动相关测试全部通过；若仍存在已记录的核心业务基线失败，必须逐项与改造前名单比对，不得把新增失败归类为基线。

- [ ] **Step 3: 在当前 Apple Silicon Mac 构建最终 ZIP**

  Run: `cd 审核器 && ./tools/build_macos_portable.command`

  Expected: 生成 `release/风控矩阵审核器-v2.0.0-macOS-arm64.zip`，下载资源摘要、LibreOffice 25.2.6.2 arm64、代码签名、许可证和发布校验全部通过。

- [ ] **Step 4: 从最终 ZIP 做真实解压和启动烟雾测试**

  在临时目录用 `ditto -x -k` 解压，运行 `xattr -dr com.apple.quarantine <具体发布目录>` 和 `chmod +x <具体启动器>`，双击/执行 `.command`；验证默认浏览器、系统目录选择、中文空格输入目录、固定 `outputs`、真实 LibreOffice 转换、关闭浏览器后继续、重复启动重开、网页正常退出。

- [ ] **Step 5: 验证终端强制关闭与恢复**

  启动一个真实任务后关闭宿主终端，确认主服务、Worker 和该任务 LibreOffice 均不存活；再次启动后任务显示“意外中断”且已有输出保留。随后断网重复一次页面、审核和转换烟雾测试。

- [ ] **Step 6: 运行发布校验器并记录摘要**

  Run: `cd 审核器 && .venv/bin/python tools/verify_portable_distribution.py release/风控矩阵审核器-v2.0.0-macOS-arm64.zip --platform macos-arm64`

  Expected: `0` 个错误，并输出最终 ZIP SHA-256。

- [ ] **Step 7: 验收 Windows x64 最终制品**

  手动触发 `.github/workflows/build-windows-web.yml`，下载最终 ZIP，并在未预装 Python、Node.js、Qt 或 LibreOffice 的 Windows 10/11 x64 环境验证：BAT 前台启动、PowerShell 目录选择、默认浏览器、重复启动、固定 `outputs`、真实转换、断网运行，以及关闭终端后 Job Object 内无残留进程。

- [ ] **Step 8: 记录最终提交建议**

  建议：`feat: 发布Apple Silicon离线脚本便携版`
