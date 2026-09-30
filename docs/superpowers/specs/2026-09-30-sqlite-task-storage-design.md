# SQLite 任务存储设计

## 背景

当前桌面 Web 审核器使用 `data/tasks.json` 保存任务索引，并在每个输出目录的 `.task` 子目录中使用 `request.json`、`state.json`、`events.jsonl` 和 `cancel.requested` 保存任务请求、运行状态、事件和取消信号。这种设计将一次任务拆散到多个文件中，多进程与心跳线程并发读写时容易产生竞争；复制、移动或清理输出目录也会造成任务历史丢失。

本次改造将任务管理数据统一迁移到 SQLite。一次通过创建前校验并成功写入数据库的审核请求就是一个任务。任务创建后，无论审核完成、部分完成、失败、取消还是意外中断，都必须保留并显示在前端任务列表中。

## 目标

- 使用 `data/tasks.sqlite3` 作为任务管理的唯一持久化数据源。
- 每次成功创建的审核任务都在前端列表中长期显示。
- 支持主服务、多个 Worker 和 Worker 心跳线程并发读写。
- 保持现有 REST API 路径和主要响应字段稳定。
- 不再使用 JSON 文件或取消标记文件保存任务管理数据。
- 保持审核核心、普通 CLI 与桌面 Web 任务中心之间的职责边界。

## 非目标

- 不迁移或兼容旧的 `tasks.json`、`request.json`、`state.json`、`events.jsonl` 和 `cancel.requested`。
- 不把审核结果 Excel、审核统计表、未审核文件报告、运行日志或临时工作目录存入 SQLite。
- 不让普通 `risk-audit audit` 或 `risk-audit trial` 命令写入桌面端任务数据库。
- 不新增前端“任务总数”统计卡片或任务分页功能。

## 任务定义与创建边界

创建接口先校验输入目录、路径关系和运行环境。校验失败时请求未被系统接受，不创建任务记录。

校验通过后，服务端生成任务编号、显示名称和预期输出目录，并在事务中向 `tasks` 表插入记录。事务提交后，该记录就是一个正式任务，必须由任务列表接口返回。随后发生的输出目录创建失败、Worker 启动失败或审核运行失败，只能把任务更新为 `failed`，不得删除任务。

任务状态继续使用以下集合：

- `running`
- `cancelling`
- `completed`
- `partial`
- `failed`
- `cancelled`
- `interrupted`

## 数据库位置与连接策略

数据库固定保存在便携程序根目录的 `data/tasks.sqlite3`。源码运行时仍使用源码模式对应的 `data` 目录。

程序初始化数据库时设置：

- `PRAGMA journal_mode=WAL`
- `PRAGMA synchronous=NORMAL`
- `PRAGMA busy_timeout=10000`
- `PRAGMA foreign_keys=ON`

主服务、每个 Worker 进程以及 Worker 内的心跳线程不得共享 SQLite 连接。每次仓储操作创建独立连接，执行短事务后立即关闭。数据库繁忙时依赖 `busy_timeout` 有界等待，不得回退到 JSON 文件。

数据库使用 `PRAGMA user_version` 标识结构版本。数据库无法打开、损坏或结构版本不受支持时，启动诊断失败并禁止创建新任务。

## 数据模型

### `tasks` 表

每行同时保存任务请求和当前状态，主要字段如下：

- `task_id TEXT PRIMARY KEY`
- `schema_version TEXT NOT NULL`
- `display_name TEXT NOT NULL`
- `input_root TEXT NOT NULL`
- `output_root TEXT NOT NULL UNIQUE`
- `rulepack TEXT NOT NULL`
- `entity_file TEXT NOT NULL`
- `baseline_root TEXT NOT NULL`
- `config_file TEXT`
- `soffice_path TEXT NOT NULL`
- `created_at TEXT NOT NULL`
- `status TEXT NOT NULL`
- `stage TEXT NOT NULL`
- `completed_units INTEGER NOT NULL`
- `total_units INTEGER`
- `progress_percent INTEGER`
- `current_entity TEXT`
- `current_business TEXT`
- `current_file TEXT`
- `worker_pid INTEGER`
- `started_at TEXT`
- `heartbeat_at TEXT NOT NULL`
- `message TEXT NOT NULL`
- `error_code TEXT`
- `result_summary_json TEXT`
- `cancel_requested INTEGER NOT NULL DEFAULT 0`

为 `created_at` 和 `status` 建立索引。任务列表按 `created_at` 降序返回。

`result_summary_json` 只保存前端需要的结果摘要；读取时必须验证其为 JSON 对象。`cancel_requested` 只允许 `0` 或 `1`。

### `task_events` 表

事件表用于保存诊断历史：

- `event_id INTEGER PRIMARY KEY AUTOINCREMENT`
- `task_id TEXT NOT NULL`
- `event_type TEXT NOT NULL`
- `occurred_at TEXT NOT NULL`
- `details_json TEXT NOT NULL`
- 外键 `task_id` 引用 `tasks.task_id`

为 `task_id, event_id` 建立联合索引。事件详情必须序列化为 JSON 对象。

## 组件设计

### SQLite 任务仓储

现有文件型 `TaskStore` 替换为 SQLite 任务仓储。仓储负责：

- 初始化和校验数据库结构。
- 创建任务及事件记录。
- 按编号读取任务。
- 按创建时间倒序读取全部任务。
- 按字段更新状态、进度、心跳和结果。
- 原子设置和读取取消请求。
- 恢复非终态任务。

状态更新必须只修改本次操作负责的字段。例如心跳不能覆盖进度，进度回调不能清除取消标志，取消操作只设置 `cancel_requested`。

### 应用服务与任务管理器

任务创建流程如下：

1. 校验输入目录、输出目录和启动资源。
2. 生成任务编号、名称和输出目录。
3. 在数据库事务中插入初始任务记录和创建事件。
4. 排他创建输出目录。
5. 启动 Worker。
6. 任一步骤在入库后失败时，将任务更新为 `failed` 并记录失败事件。

任务列表、详情、取消、强制终止及系统运行中任务数量全部查询 SQLite，不扫描 `outputs`。

任务管理器发现活动 Worker 已退出但数据库仍是活动状态时，重新读取任务状态；若仍未进入终态，则更新为 `interrupted`。

### Worker

Web 内部 Worker 的启动参数改为数据库路径和任务编号。Worker 启动后从数据库读取完整审核请求，不再读取 `request.json`。

Worker 通过 SQLite 完成：

- 发布启动状态和 Worker PID。
- 更新心跳。
- 更新审核进度和当前处理上下文。
- 查询取消请求。
- 写入完成、部分完成、取消或失败终态。
- 追加任务事件。

Worker 的心跳线程和审核线程分别使用独立连接。清理临时目录失败时记录事件，不创建事件文件。

输出目录仍可使用 `.task` 保存允许保留的文件型内容，包括审核运行报告、日志、LibreOffice 临时配置和工作目录；其中不得出现任务请求、状态、事件或取消标记文件。

### 普通 CLI

`risk-audit audit`、`risk-audit trial` 及其他普通 CLI 命令保持独立，不连接桌面端任务数据库，其运行不会出现在前端任务列表。

只有 `risk_audit_web.app --worker` 内部命令改为 SQLite Worker 协议。

## 并发控制

- 任务创建依赖数据库主键和输出目录排他创建，保证并发任务不会覆盖。
- 创建任务、创建事件使用同一事务。
- 心跳只更新 `heartbeat_at` 及必要的取消中状态。
- 进度回调只更新状态、阶段、进度和当前上下文字段。
- 取消接口只将 `cancel_requested` 设置为 `1` 并记录事件。
- Worker 进入终态后清除 Worker PID，但保留取消请求历史及事件。
- 强制终止前必须重新读取并核对 Worker PID，避免结束无关进程。

## REST API 与前端

以下接口路径保持不变：

- `GET /api/v1/tasks`
- `POST /api/v1/tasks`
- `GET /api/v1/tasks/{task_id}`
- 任务取消、强制终止、打开输出和读取结果相关子资源

创建接口在数据库任务创建成功后返回任务。任务列表返回全部有效数据库记录，包括 `completed`、`partial`、`failed`、`cancelled` 和 `interrupted`，前端不按输出目录或结果文件过滤任务。

前端继续按创建时间展示“最近任务”列表。页面结构不新增任务总数卡片；原有“正在审核”“今日完成”和“待人工复核”计算逻辑保持不变。

## 失败处理

- 创建前校验失败：返回现有稳定 API 错误，不创建任务。
- 数据库插入失败：创建请求失败，不创建输出目录。
- 输出目录创建失败：保留任务并更新为 `failed`。
- Worker 启动失败：保留任务并更新为 `failed`。
- Worker 审核异常：更新为 `failed`，保存安全错误信息和事件。
- Worker 意外退出或心跳过期：更新为 `interrupted`。
- SQLite 持续繁忙：操作在超时后失败并记录明确错误，不创建旁路文件状态。
- 结果文件丢失：任务仍展示；打开结果时返回现有 `RESULT_NOT_FOUND` 错误。

## 测试策略

测试必须先失败再实现，覆盖以下行为：

1. SQLite 初始化、结构版本、约束和索引。
2. 任务创建、读取、倒序列表、字段级状态更新和事件追加。
3. 同时创建多个任务不丢失、不覆盖。
4. 多个 Worker 并发更新状态、心跳和事件时无锁冲突或串写。
5. 取消操作与进度更新并发时取消标志不丢失。
6. 输出目录创建失败或 Worker 启动失败后任务仍为可见的 `failed`。
7. 审核完成、部分完成、失败、取消和中断状态正确持久化。
8. 服务重启后只从 SQLite 恢复任务，不扫描 `outputs`。
9. 列表 API 返回所有状态的任务。
10. 任务运行不生成旧任务管理 JSON、事件文件或取消标记文件。
11. 普通 CLI 行为保持独立。
12. 完整 Web 测试和项目测试回归。

## 验收标准

- 每个成功入库的任务都能在前端列表中看到，后续失败不会删除。
- 删除或增加 `outputs` 下的目录不会改变任务列表记录数。
- 多任务并发运行时，任务状态、进度、取消请求和事件互不覆盖。
- 服务重启后任务历史和终态完整保留。
- 任务管理不再依赖 `tasks.json` 或 `.task` 下的任务管理文件。
- 普通 CLI 无需数据库即可继续运行。
