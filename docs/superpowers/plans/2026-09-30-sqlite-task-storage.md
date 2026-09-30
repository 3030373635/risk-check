# SQLite 任务存储实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将桌面 Web 审核器的任务请求、状态、进度、心跳、取消和事件统一迁移到 SQLite，保证每个成功创建的任务无论最终成功或失败都持续显示在前端列表中。

**Architecture:** 新增 `TaskRepository`，以 `data/tasks.sqlite3` 为唯一任务管理数据源，主服务、Worker 进程和心跳线程分别使用独立 SQLite 连接及短事务。REST API 保持现有资源路径，内部 Worker 改为接收数据库路径和任务编号；审核结果、日志和临时目录继续保存在 `outputs`。

**Tech Stack:** Python 3.11、标准库 `sqlite3`、FastAPI、pytest、多进程 Worker、SQLite WAL。

**Spec:** `docs/superpowers/specs/2026-09-30-sqlite-task-storage-design.md`

## Global Constraints

- Python 版本保持 `>=3.11`，SQLite 只使用标准库 `sqlite3`，不增加第三方数据库依赖。
- 不迁移、不读取、不兼容旧 `tasks.json`、`request.json`、`state.json`、`events.jsonl` 和 `cancel.requested`。
- 普通 `risk-audit audit`、`risk-audit trial` 不连接桌面端任务数据库。
- REST API 路径和前端依赖的响应字段保持稳定；内部 `state_path` 字段删除。
- 任务通过创建前校验并成功入库后即永久可见；之后的目录、进程或审核失败只能更新为 `failed`。
- 审核结果、统计表、报告、日志、LibreOffice 配置和临时工作目录继续使用文件系统。
- 新增或修改的函数必须有中文注释并解释参数；关键事务、并发和进程安全代码必须有中文注释。
- 统一使用 `repository` 表示 SQLite 任务仓储，不保留含义相同的 `store` 命名。
- 不执行 Git 提交；每个任务完成时只记录建议的中文 Conventional Commit 信息。

## Review Focus

- 数据库文件损坏、结构被手工修改或 `user_version` 不受支持时，服务仍能打开诊断页但禁止创建任务；Task 1、Task 5 添加测试。
- 心跳、进度和取消并发更新同一任务时，取消标志和进度均不得丢失；Task 2、Task 6 添加测试。
- 任务入库后输出目录创建或 Worker 启动失败时，列表必须返回 `failed` 任务；Task 5 添加测试。
- 单条任务的 `result_summary_json` 非法时，其他任务仍能列出并返回隔离警告；Task 1 添加测试。
- 服务重启时，存活且心跳新鲜的任务保留运行态，死进程或超时心跳变为 `interrupted`，终态保持不变；Task 2、Task 6 添加测试。

---

### Task 1: 建立 SQLite 契约与仓储基础

**Files:**
- Create: `risk-audit/src/risk_audit_web/task_repository.py`
- Create: `risk-audit/src/risk_audit_web/atomic_files.py`
- Delete: `risk-audit/src/risk_audit_web/task_store.py`
- Modify: `risk-audit/src/risk_audit_web/task_contracts.py`
- Modify: `risk-audit/src/risk_audit_web/task_paths.py`
- Modify: `risk-audit/src/risk_audit_web/single_instance.py`
- Create: `risk-audit/tests/web/test_task_repository.py`
- Create: `risk-audit/tests/web/test_atomic_files.py`
- Modify: `risk-audit/tests/web/test_task_contracts.py`
- Modify: `risk-audit/tests/web/test_task_paths.py`
- Modify: `risk-audit/tests/web/test_runtime_imports.py`
- Delete: `risk-audit/tests/web/test_task_store.py`

**Interfaces:**
- Consumes: `TaskRequest`、`TaskState`、`TaskRecord`、`TaskEvent` 及 `PortablePaths`。
- Produces: `TaskRepository(database_path: Path)`、`StoredTask`、`TaskQueryResult`、`TaskDatabaseError`、`PortablePaths.task_database` 和独立的 `atomic_write_json(path, value)`。

- [ ] **Step 1: 编写 SQLite 初始化与契约失败测试**

在 `test_task_repository.py` 中先固定这些可观察行为：新数据库自动建立 `tasks`、`task_events` 和索引，`user_version == 1`，连接使用 WAL、外键和 `busy_timeout=10000`；非零未知版本、缺列结构和损坏数据库抛出 `TaskDatabaseError`。在契约测试中断言 `TaskRecord` 不再包含 `state_path`，绝对路径校验只覆盖输入和输出目录。

- [ ] **Step 2: 运行测试并确认因仓储不存在而失败**

Run: `cd risk-audit && pytest tests/web/test_task_repository.py tests/web/test_task_contracts.py tests/web/test_task_paths.py -v`

Expected: FAIL，错误指向 `risk_audit_web.task_repository` 或 `PortablePaths.task_database` 不存在。

- [ ] **Step 3: 实现仓储结构和任务读写接口**

在 `task_repository.py` 实现以下接口，并为参数写中文说明：

- `TaskRepository.__init__(database_path: Path) -> None`
- `TaskRepository.initialize() -> None`
- `TaskRepository.create_task(request: TaskRequest, state: TaskState, event: TaskEvent) -> TaskRecord`
- `TaskRepository.get_task(task_id: str) -> StoredTask | None`
- `TaskRepository.list_tasks() -> TaskQueryResult`
- `TaskRepository.read_request(task_id: str) -> TaskRequest`
- `TaskRepository.read_state(task_id: str) -> TaskState`

`create_task` 必须在同一事务插入任务和创建事件。`list_tasks` 按 `created_at DESC` 返回；非法 `result_summary_json` 只隔离对应任务并写入 warnings。所有操作使用独立连接，连接初始化统一设置设计文档中的 PRAGMA。

- [ ] **Step 4: 迁移通用原子 JSON 写入并删除文件型仓储**

将 `single_instance.py` 所需的 `atomic_write_json` 移入 `atomic_files.py`，用 `test_atomic_files.py` 保留原子替换、失败保留旧值和 Windows 短暂占用重试测试。删除 `task_store.py`，更新运行时导入清单，不保留旧类或转发兼容层。

- [ ] **Step 5: 增加任务 CRUD 与坏记录隔离测试并验证通过**

测试真实 SQLite：创建两条任务、按时间倒序读取、按编号读取请求和状态、重复主键/输出目录被拒绝、非对象结果摘要只隔离该行。断言 `data` 中没有 `tasks.json`。

Run: `cd risk-audit && pytest tests/web/test_task_repository.py tests/web/test_atomic_files.py tests/web/test_task_contracts.py tests/web/test_task_paths.py tests/web/test_runtime_imports.py -v`

Expected: PASS。

- [ ] **Step 6: 记录建议提交信息**

建议：`feat: 建立SQLite任务仓储`

### Task 2: 实现字段级状态更新、取消、事件与恢复

**Files:**
- Modify: `risk-audit/src/risk_audit_web/task_repository.py`
- Modify: `risk-audit/tests/web/test_task_repository.py`

**Interfaces:**
- Consumes: Task 1 的 `TaskRepository`、`StoredTask` 和任务契约。
- Produces: `update_task_state`、`append_event`、`request_cancel`、`is_cancel_requested`、`mark_interrupted`、`recover_tasks` 和并发安全的字段级写入。

- [ ] **Step 1: 编写字段级更新和事件事务失败测试**

新增测试：

- `update_task_state(task_id: str, changes: Mapping[str, Any]) -> TaskState` 只修改允许的状态列，未知列抛出 `ValueError`。
- 更新心跳不得覆盖已有进度和取消标志。
- `append_event(event: TaskEvent) -> None` 按自增编号保留事件顺序。
- `request_cancel(task_id: str, occurred_at: str) -> bool` 只对活动任务原子设置标志并插入一次取消事件；终态返回 `False`。
- `is_cancel_requested(task_id: str) -> bool` 返回数据库标志。

- [ ] **Step 2: 运行测试并确认缺少更新接口**

Run: `cd risk-audit && pytest tests/web/test_task_repository.py -v`

Expected: FAIL，错误指向字段更新、取消或事件接口不存在。

- [ ] **Step 3: 实现短事务状态操作**

所有状态更新先校验任务存在及字段白名单，再执行参数化 `UPDATE`。取消标志与事件必须位于同一事务；重复取消不得重复插入事件。JSON 字段只接受字典或 `None`。

- [ ] **Step 4: 编写真实并发写入测试**

使用 `ThreadPoolExecutor` 让独立连接同时更新心跳、进度、追加事件和请求取消；循环多次后断言最终进度完整、取消标志为真、事件无丢失，并且没有 `database is locked`。测试同时创建不同任务并断言记录数准确。

- [ ] **Step 5: 实现并验证重启恢复**

实现：

- `mark_interrupted(task_id: str, message: str) -> TaskState`
- `recover_tasks(is_process_alive: Callable[[int], bool], now: datetime | None = None, heartbeat_timeout_seconds: int = 30) -> RecoveryResult`

测试存活且心跳新鲜、PID 消失、心跳超时、已完成和非法心跳五类记录；只有非终态的死进程或超时任务变为 `interrupted`。

Run: `cd risk-audit && pytest tests/web/test_task_repository.py -v`

Expected: PASS。

- [ ] **Step 6: 记录建议提交信息**

建议：`feat: 实现SQLite任务状态与恢复`

### Task 3: 迁移任务管理器和内部 Worker 启动协议

**Files:**
- Modify: `risk-audit/src/risk_audit_web/task_manager.py`
- Modify: `risk-audit/src/risk_audit_web/app.py`
- Modify: `risk-audit/tests/web/test_task_manager.py`
- Modify: `risk-audit/tests/web/test_app_lifecycle.py`
- Modify: `risk-audit/tests/web/conftest.py`
- Modify: `risk-audit/tests/web/test_shutdown_lifecycle.py`

**Interfaces:**
- Consumes: `TaskRepository` 的任务查询、状态更新、取消和恢复接口。
- Produces: `TaskManager(repository, executable, ...)`、`worker_arguments(python_executable: Path, database_path: Path, task_id: str) -> list[str]` 及 `--worker <database_path> <task_id>` 分流。

- [ ] **Step 1: 改写任务管理器测试以表达 SQLite 行为**

先修改测试夹具，通过真实仓储插入任务，不创建请求、状态或取消文件。断言：

- `snapshot()` 统计 `running` 与 `cancelling`。
- 数据库取消标志把运行态投影为 `cancelling`。
- `request_cancel()` 不覆盖 Worker 已发布的终态。
- 强制终止重新核对 PID，进程退出后才写 `cancelled`。
- 启动参数包含数据库绝对路径和任务编号，且 `shell=False`。

- [ ] **Step 2: 运行测试并确认旧文件协议导致失败**

Run: `cd risk-audit && pytest tests/web/test_task_manager.py tests/web/test_app_lifecycle.py tests/web/test_shutdown_lifecycle.py -v`

Expected: FAIL，结果仍引用 `request.json`、`state_path` 或 `cancel.requested`。

- [ ] **Step 3: 实现 TaskManager 的仓储协议**

统一把构造参数和字段命名为 `repository`。`snapshot`、`get_task`、运行任务查询、取消、强制停止和中断发布全部访问 SQLite；数据库错误转换为隔离 warning，不扫描输出目录。`start_task` 调用三参数 Worker 参数工厂，并继续注册进程监管器。

- [ ] **Step 4: 修改应用 Worker 分流与启动组装**

`app.main` 只接受精确的 `--worker <database_path> <task_id>`。`run_primary_instance` 使用 `paths.task_database` 创建 `TaskRepository`，不再在服务组装前读取 JSON 恢复。保留普通 Web 启动参数拒绝行为。

- [ ] **Step 5: 验证任务管理器与生命周期测试**

Run: `cd risk-audit && pytest tests/web/test_task_manager.py tests/web/test_app_lifecycle.py tests/web/test_shutdown_lifecycle.py -v`

Expected: PASS。

- [ ] **Step 6: 记录建议提交信息**

建议：`refactor: 迁移Web任务管理协议到SQLite`

### Task 4: 迁移审核 Worker 的状态、心跳和取消

**Files:**
- Modify: `risk-audit/src/risk_audit_web/worker.py`
- Modify: `risk-audit/tests/web/test_worker.py`

**Interfaces:**
- Consumes: `TaskRepository.read_request`、`read_state`、`update_task_state`、`is_cancel_requested` 和 `append_event`。
- Produces: `run_worker(database_path: Path, task_id: str, *, audit_func: AuditFunction | None = None, heartbeat_interval_seconds: float = 5.0) -> int` 和 `cleanup_task_runtime(repository: TaskRepository, task_dir: Path, task_id: str) -> None`。

- [ ] **Step 1: 把 Worker 测试改为真实 SQLite 夹具**

测试先通过仓储创建任务，再调用新 `run_worker`。审核成功、部分完成、取消、启动失败和审核异常均从仓储读取终态断言。明确断言输出目录不存在 `request.json`、`state.json`、`events.jsonl` 和 `cancel.requested`。

- [ ] **Step 2: 运行测试并确认 Worker 仍依赖请求文件**

Run: `cd risk-audit && pytest tests/web/test_worker.py -v`

Expected: FAIL，错误指向 `run_worker` 签名或旧任务文件不存在。

- [ ] **Step 3: 实现 Worker SQLite 数据流**

Worker 从仓储读取 `TaskRequest`，为审核线程和心跳线程分别执行独立仓储操作。进度回调仅写对应状态字段；心跳只更新心跳和必要的 `cancelling` 状态；取消检查查询数据库。终态清除 `worker_pid` 并追加事件。

- [ ] **Step 4: 保留允许的文件型产物并改造清理**

继续在 `.task` 下使用 `report`、`worker.log`、`work` 和 `libreoffice-profile`。清理失败通过 SQLite 追加 `cleanup_warning`，不创建事件文件；清理后保留报告和日志。

- [ ] **Step 5: 验证心跳线程和异常边界**

增加耗时审核测试，确认无进度事件时心跳仍变化；并发取消后核心 `cancel_check` 能看到请求。数据库状态写入失败时 Worker 返回非零，且不伪造成功终态。

Run: `cd risk-audit && pytest tests/web/test_worker.py -v`

Expected: PASS。

- [ ] **Step 6: 记录建议提交信息**

建议：`feat: 使用SQLite持久化Worker状态`

### Task 5: 保证创建后失败任务仍通过 REST API 可见

**Files:**
- Modify: `risk-audit/src/risk_audit_web/services.py`
- Modify: `risk-audit/src/risk_audit_web/diagnostics.py`
- Modify: `risk-audit/src/risk_audit_web/api/tasks.py`
- Modify: `risk-audit/tests/web/conftest.py`
- Modify: `risk-audit/tests/web/test_services.py`
- Modify: `risk-audit/tests/web/test_tasks_api.py`
- Modify: `risk-audit/tests/web/test_system_api.py`
- Modify: `risk-audit/tests/web/test_diagnostics.py`

**Interfaces:**
- Consumes: Task 1–4 的仓储、管理器和 Worker 启动协议。
- Produces: 创建即入库的 `ApplicationServices.create_task`、SQLite 启动诊断及保持现有路径的任务 REST API。

- [ ] **Step 1: 编写“入库后失败仍可见”的服务测试**

新增测试分别让输出目录创建和 `manager.start_task` 抛出异常。断言 `create_task` 返回状态为 `failed` 的任务，`list_tasks` 仍包含该 `task_id`，数据库有失败事件；只有创建前路径/环境校验失败和数据库插入失败不产生任务。

- [ ] **Step 2: 编写 API 全状态列表与响应测试**

通过真实仓储插入 `completed`、`partial`、`failed`、`cancelled`、`interrupted`，请求 `GET /api/v1/tasks` 并断言全部返回且按创建时间倒序。响应不得暴露已删除的 `state_path`、数据库路径或内部取消标志。

- [ ] **Step 3: 运行测试并确认创建顺序不符合要求**

Run: `cd risk-audit && pytest tests/web/test_services.py tests/web/test_tasks_api.py tests/web/test_system_api.py tests/web/test_diagnostics.py -v`

Expected: FAIL，旧实现仍先创建输出目录并依赖文件型 `TaskStore`。

- [ ] **Step 4: 实现入库优先的创建流程**

在现有 `_creation_lock` 内执行：创建前校验 → 生成候选输出路径和任务契约 → 数据库事务插入 → 排他创建准确目录 → 启动 Worker。入库后的异常统一更新为 `failed` 并返回该任务；数据库插入失败转换为安全 API 错误且不创建目录。

- [ ] **Step 5: 接入数据库启动诊断**

应用服务初始化时调用 `repository.initialize()` 和恢复逻辑。数据库无法打开、损坏或版本不支持时，把 `database_unavailable` 诊断加入系统状态，`can_create_task` 为假；服务和诊断页面仍可响应。任务查询返回安全 warning，不创建旁路文件。

- [ ] **Step 6: 验证结果资源和任务操作接口**

把测试中的状态写入改为仓储字段更新，确认打开输出、统计表、未审核文件、取消和强制终止仍只按 `task_id` 访问任务范围内资源。

Run: `cd risk-audit && pytest tests/web/test_services.py tests/web/test_tasks_api.py tests/web/test_system_api.py tests/web/test_diagnostics.py -v`

Expected: PASS。

- [ ] **Step 7: 记录建议提交信息**

建议：`feat: 保留并展示所有已创建审核任务`

### Task 6: 验证真实多进程并发与重启恢复

**Files:**
- Modify: `risk-audit/tests/web/fake_worker.py`
- Modify: `risk-audit/tests/web/test_parallel_integration.py`

**Interfaces:**
- Consumes: 完整 SQLite 仓储、TaskManager 及内部 Worker 参数协议。
- Produces: 真实子进程级并发、取消、崩溃隔离和重启恢复证据。

- [ ] **Step 1: 重写 fake Worker 使用真实数据库**

fake Worker 接收数据库路径、任务编号及测试专用栅栏参数，从仓储读取任务并写入 PID、状态和心跳。它不得创建任何任务管理 JSON 或取消标记文件。

- [ ] **Step 2: 运行并发测试并确认旧 fake Worker 失败**

Run: `cd risk-audit && pytest tests/web/test_parallel_integration.py -v`

Expected: FAIL，fake Worker 仍读取 `request.json` 和 `state.json`。

- [ ] **Step 3: 覆盖真实进程并发场景**

保留并改造以下场景：两个 Worker 同时运行且输出隔离；取消 A 不影响 B；A 崩溃后管理器标记 `interrupted` 且 B 完成；新仓储实例恢复存活、死亡和终态任务。新增多个进程同时高频更新心跳/事件测试，断言没有锁错误且所有任务终态正确。

- [ ] **Step 4: 验证任务管理文件完全移除**

对每个输出目录递归断言不存在 `request.json`、`state.json`、`events.jsonl` 和 `cancel.requested`；同时确认允许的结果、日志和任务独立临时目录行为不变。

Run: `cd risk-audit && pytest tests/web/test_parallel_integration.py -v`

Expected: PASS。

- [ ] **Step 5: 记录建议提交信息**

建议：`test: 验证SQLite多进程任务并发`

### Task 7: 更新说明并完成全量回归

**Files:**
- Modify: `risk-audit/README.md`
- Modify: `risk-audit/Windows Web版使用说明.md`
- Modify: `risk-audit/macOS Apple Silicon Web版使用说明.md`
- Modify: `risk-audit/tests/web/test_portable_builder.py`
- Modify: `risk-audit/tests/web/test_portable_distribution.py`

**Interfaces:**
- Consumes: 已完成的 SQLite 任务中心实现。
- Produces: 用户可读存储说明、干净便携包和最终回归证据。

- [ ] **Step 1: 更新用户文档和便携目录说明**

说明 `data/tasks.sqlite3` 保存任务历史，`outputs` 只保存审核结果；增加“删除或复制输出目录不会新增/删除任务记录”的说明。明确普通 CLI 不进入桌面端任务列表。

- [ ] **Step 2: 验证便携包数据库生命周期**

组装后的 `data` 目录仍为空，首次启动才创建 `tasks.sqlite3`；数据库、WAL 和 SHM 均不得进入不可变发布清单。测试完整 Python 运行时可导入标准库 `sqlite3`。

Run: `cd risk-audit && pytest tests/web/test_portable_builder.py tests/web/test_portable_distribution.py -v`

Expected: PASS。

- [ ] **Step 3: 运行 Web 测试集合**

Run: `cd risk-audit && pytest tests/web -v`

Expected: PASS，无 warning、error 或 skipped 回归。

- [ ] **Step 4: 运行完整项目测试集合**

Run: `cd risk-audit && pytest -v`

Expected: PASS；若存在环境限定的既有 skip，必须逐项报告名称和原因。

- [ ] **Step 5: 检查旧任务文件协议和代码质量**

Run: `rg -n "tasks\\.json|request\\.json|state\\.json|events\\.jsonl|cancel\\.requested|TaskStore|state_path" risk-audit/src/risk_audit_web risk-audit/tests/web`

Expected: 无旧任务协议引用；仅与非任务业务结果同名的合法文件可保留并单独说明。

Run: `git diff --check`

Expected: 无空白错误。

- [ ] **Step 6: 记录最终建议提交信息**

建议：`feat: 使用SQLite统一管理审核任务`
