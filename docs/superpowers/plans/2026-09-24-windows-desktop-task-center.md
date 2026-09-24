# Windows 便携桌面端与并行任务中心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有风控矩阵审核器增加一个面向非技术用户的 Windows 10/11 x64 便携桌面端，用户解压 ZIP 后双击 `风控矩阵审核器.exe` 即可创建、并行运行、查看和停止审核任务，默认结果写入程序目录下 `outputs/<材料目录名>-YYYYMMDD-HHMMSS`。

**Architecture:** 使用 PySide6 + Qt Widgets 构建单实例主界面；每次审核由同一 EXE 的 `--worker <request.json>` 模式启动独立进程。GUI 只管理任务和读取原子 `state.json`，Worker 调用现有 `risk_audit` 核心，并通过可选进度回调、安全取消检查、任务独立 LibreOffice profile 与临时目录实现并行隔离。任务索引放在便携目录 `data/tasks.json`，LibreOffice 与业务资源放在外部 `runtime/`，最终用 PyInstaller 生成无控制台 EXE 并在 Windows x64 构建 ZIP。

**Tech Stack:** Python 3.11、PySide6 6.8.3、Qt Widgets、PyInstaller 6.16.0、pytest 8.4.2、pytest-qt 4.5.0、现有 `risk_audit` 包、便携 LibreOffice Windows x64。

**Spec:** `docs/superpowers/specs/2026-09-24-windows-desktop-task-center-design.md`

## Global Constraints

- 本计划取代 `docs/superpowers/plans/2026-09-23-windows-desktop-portable.md`；不得沿用其中的 QML、单任务、`%LOCALAPPDATA%`、Nuitka 或旧目录结构。
- 只支持 Windows 10/11 x64；最终 EXE 和 ZIP 必须在 Windows x64 环境构建与实机验收，macOS 只运行跨平台单元测试。
- 交付为免安装 ZIP，不要求管理员权限，不写注册表，不创建 Windows 服务，不访问网络。
- 原始材料始终只读；每个任务只能写入自己的输出目录、`_task` 目录和独立临时目录。
- Python、PySide6 和普通依赖打入主 EXE；LibreOffice、规则、基准、主体名册、模型和许可证保留在外部 `runtime/`。
- GUI 不直接调用审核核心；Worker 不导入 Qt Widgets 页面；`risk_audit` 不反向依赖桌面包。
- 不增加 HTTP 接口，本功能无 REST API 变更。
- 不兼容旧桌面协议、旧 QML 代码或旧桌面测试；现有审核核心的业务行为与命令行调用必须保持可用。
- 所有新增或修改的函数必须有中文注释，说明用途、参数和返回值；进程管理、原子替换、路径保护、取消点及 LibreOffice 隔离等关键代码必须有中文行内注释。
- Python 变量统一使用清晰的 `snake_case`，状态枚举与 JSON 字段统一使用本计划定义的英文小写值。
- 遵循仓库要求：实施过程中不得执行 `git commit`。每个任务结束只记录建议的中文 Conventional Commit 信息，最终统一交给用户。

## Review Focus

- 输出命名只取 `input_root.name`，不取任务展示名称；中文、空格、非法字符、尾部空格/句点和同秒冲突由 Task 2 覆盖。
- 输入输出重叠、已有目录覆盖、资源缺失或哈希错误必须在启动 Worker 前阻止；Task 2、4、7 覆盖。
- `state.json` 必须原子更新，单个损坏状态或索引记录不得拖垮其他任务；Task 3、6 覆盖。
- 至少两个 Worker 可同时进入 `running`；输出、profile、临时目录、日志和取消标记不得串任务；Task 5、7、12 覆盖。
- 取消只能在安全工作单元边界生效，已完成输出保留但任务不得显示成功；Task 1、5、7 覆盖。
- 关闭窗口时的三个选项、托盘继续运行、完成通知和显式退出停止所有 Worker；Task 10 覆盖。
- 重启后用 PID 与心跳重新关联存活 Worker，否则标为 `interrupted`；Task 6、7 覆盖。
- 打包不能依赖当前工作目录、系统 LibreOffice、已安装 Python 或未声明资源；Task 4、11、13 覆盖。

---

## File Structure

主要新增和修改文件如下：

```text
审核器/
├── pyproject.toml
├── requirements-desktop.lock
├── src/
│   ├── risk_audit/
│   │   ├── progress.py                    # 核心进度事件与安全取消异常
│   │   ├── runner.py                      # 发送阶段/业务进度并检查取消
│   │   └── readers/xls.py                 # 显式 LibreOffice 与 profile 配置
│   └── risk_audit_desktop/
│       ├── __init__.py
│       ├── app.py                         # GUI 与 --worker 统一入口
│       ├── task_contracts.py              # 请求、状态、事件和索引模型
│       ├── task_paths.py                  # 便携根目录、输出命名和目录保护
│       ├── task_store.py                  # 原子状态、事件和任务索引
│       ├── diagnostics.py                 # runtime 清单及创建前检查
│       ├── worker.py                      # 独立审核 Worker
│       ├── task_manager.py                # 多 Worker 生命周期和轮询
│       ├── platform_windows.py            # PID、打开路径和强制终止
│       ├── single_instance.py             # Qt 本地套接字单实例
│       ├── tray.py                        # 托盘菜单与完成通知
│       ├── main_window.py                 # 主窗口、导航和关闭策略
│       ├── task_list_page.py              # 任务列表页
│       ├── task_create_page.py            # 新建任务页
│       ├── task_detail_page.py            # 任务详情页
│       ├── help_page.py                   # 使用帮助页
│       ├── theme.py                       # 浅色/深色样式
│       └── assets/app.ico                 # Windows 程序和托盘图标
├── tests/
│   ├── test_progress_and_cancel.py
│   ├── test_xls_runtime.py
│   └── desktop/
│       ├── conftest.py
│       ├── test_task_contracts.py
│       ├── test_task_paths.py
│       ├── test_task_store.py
│       ├── test_diagnostics.py
│       ├── test_worker.py
│       ├── test_task_manager.py
│       ├── test_task_pages.py
│       ├── test_main_window.py
│       ├── test_tray_and_single_instance.py
│       └── test_portable_distribution.py
├── tools/
│   ├── build_windows_desktop.py            # 生成 runtime、清单、EXE 与 ZIP
│   └── verify_portable_distribution.py    # 离线检查交付结构和哈希
└── Windows桌面版使用说明.md
```

最终 ZIP 固定为：

```text
风控矩阵审核器-v2.0.0/
├── 风控矩阵审核器.exe
├── runtime/
│   ├── libreoffice/program/soffice.exe
│   ├── resources/
│   │   ├── rulepacks/
│   │   │   └── audit-config.json
│   │   ├── baselines/审核/                 # 保留规则包已冻结的相对路径
│   │   ├── entities/会计主体清单20260907.xlsx
│   │   └── models/bge-small-zh-v1.5/
│   ├── licenses/
│   └── manifest.json
├── data/                                  # 首次启动后生成
└── outputs/                               # 首次创建任务后生成
```

---

### Task 1: 为审核核心增加真实进度与安全取消点

**Files:**
- Create: `审核器/src/risk_audit/progress.py`
- Modify: `审核器/src/risk_audit/runner.py:75-513`
- Create: `审核器/tests/test_progress_and_cancel.py`

**Interfaces:**
- Consumes: 现有 `risk_audit.runner.audit(...) -> dict[str, Any]` 及主体/业务循环。
- Produces: `AuditProgressEvent`、`ProgressCallback`、`CancelCheck`、`AuditCancelled`，以及 `audit(..., progress_callback=None, cancel_check=None, model_root=None)`；未传 `model_root` 时保持现有命令行模型路径。

- [ ] **Step 1: 编写进度模型和空回调失败测试**

```python
def test_progress_event_serializes_defined_fields():
    event = AuditProgressEvent(stage="audit", completed_units=2, total_units=5)
    assert event.to_dict() == {
        "stage": "audit",
        "completed_units": 2,
        "total_units": 5,
    }


def test_raise_if_cancelled_is_noop_without_check():
    raise_if_cancelled(None)
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `python3 -m pytest 审核器/tests/test_progress_and_cancel.py -q`

Expected: FAIL，提示 `ModuleNotFoundError: risk_audit.progress`。

- [ ] **Step 3: 实现进度契约与取消异常**

```python
@dataclass(frozen=True)
class AuditProgressEvent:
    """描述审核核心的一次进度变化；字段均可序列化为 JSON。"""

    stage: str
    completed_units: int | None = None
    total_units: int | None = None
    current_entity: str | None = None
    current_business: str | None = None
    current_file: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回去除空值的 JSON 字典；无参数。"""
        return {key: value for key, value in asdict(self).items() if value is not None}


class AuditCancelled(RuntimeError):
    """表示审核在安全工作单元边界收到用户取消。"""


ProgressCallback = Callable[[AuditProgressEvent], None]
CancelCheck = Callable[[], bool]
```

- [ ] **Step 4: 编写 `audit()` 开始、完成、失败透传测试**

使用 monkeypatch 替换 `_audit`，断言成功事件阶段依次为 `startup`、`completed`；异常时发出 `failed` 后原样抛出，不吞掉业务异常。

- [ ] **Step 5: 在扫描完成后固定总业务数并测试进度分母**

将 `total_units = sum(len(_business_keys(scopes[key], group_files)) ...)` 在进入主体循环前计算一次。测试一个包含两个业务的最小 fixture，断言扫描前 `total_units is None`，扫描后为 `2`。

- [ ] **Step 6: 在安全边界接入取消检查**

取消检查位置固定为：扫描完成后、每个业务开始前、每个业务完整写出后、最终汇总前。不得在单个 Excel 写入中间抛异常。

```python
raise_if_cancelled(cancel_check)
emit_progress(progress_callback, AuditProgressEvent(
    stage="audit",
    completed_units=len(business_results),
    total_units=total_units,
    current_entity=entity_name,
    current_business=unit_label,
    current_file=str(unit_files[0].relative_path) if unit_files else None,
    message="正在执行审核规则",
))
```

同时把语义模型目录改为显式可选参数：桌面 Worker 传入 `runtime/resources/models/bge-small-zh-v1.5`；现有 CLI 未传参时继续使用 `project_root/审核器/models/bge-small-zh-v1.5`，避免改变命令行行为。

- [ ] **Step 7: 运行核心新增测试和关键回归测试**

Run: `python3 -m pytest 审核器/tests/test_progress_and_cancel.py 审核器/tests/test_business_incremental.py 审核器/tests/test_single_material_audit.py -q`

Expected: PASS。

- [ ] **Step 8: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 为审核核心增加真实进度与安全取消点`

---

### Task 2: 实现便携路径、默认输出命名和目录保护

**Files:**
- Create: `审核器/src/risk_audit_desktop/__init__.py`
- Create: `审核器/src/risk_audit_desktop/task_paths.py`
- Create: `审核器/tests/desktop/test_task_paths.py`

**Interfaces:**
- Consumes: EXE 或源码入口路径、材料目录、创建时间和可选用户输出目录。
- Produces: `PortablePaths`、`sanitize_directory_name()`、`default_output_path()`、`validate_task_paths()`。

- [ ] **Step 1: 编写材料目录名清理测试**

```python
@pytest.mark.parametrize(("raw", "expected"), [
    ("第一批资料0924", "第一批资料0924"),
    ("甲:乙*丙?", "甲_乙_丙_"),
    ("名称. ", "名称"),
    ("...", "审核任务"),
])
def test_sanitize_directory_name(raw, expected):
    assert sanitize_directory_name(raw) == expected
```

- [ ] **Step 2: 编写默认输出名与冲突递增测试**

固定 `created_at=datetime(2026, 9, 24, 10, 30, 15)`，断言材料目录 `D:/报送材料/第一批资料0924` 生成 `outputs/第一批资料0924-20260924-103015`；预建该目录后生成 `...-2`，且任务展示名称不参与结果。

- [ ] **Step 3: 实现 `PortablePaths` 和输出命名**

```python
@dataclass(frozen=True)
class PortablePaths:
    """保存便携程序的固定目录；app_root 为 EXE 所在目录。"""

    app_root: Path
    runtime_root: Path
    data_root: Path
    outputs_root: Path

    @classmethod
    def from_executable(cls, executable: Path) -> "PortablePaths":
        """由主程序路径生成固定目录；executable 为 EXE 或源码入口。"""
```

`default_output_path()` 必须用 `strftime("%Y%m%d-%H%M%S")`，禁止 shell 调用。表单只把它用于预览；真正创建时由 `reserve_output_path()` 在同一进程锁内重新检查并独占创建，避免两个同秒任务取得同一路径。

- [ ] **Step 4: 编写输入输出重叠与不可写测试**

覆盖：相同目录、输出位于输入内、输入位于输出内、输出目录已存在、输出父目录不可写；错误对象必须包含具体路径与中文可操作提示。

- [ ] **Step 5: 实现 `validate_task_paths()`**

先 `resolve(strict=False)` 再比较父子关系；已有输出目录一律拒绝，创建任务时不得复用或清空。

- [ ] **Step 6: 运行路径测试**

Run: `python3 -m pytest 审核器/tests/desktop/test_task_paths.py -q`

Expected: PASS。

- [ ] **Step 7: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 实现便携目录与审核输出命名规则`

---

### Task 3: 定义任务 JSON 契约和原子文件操作

**Files:**
- Create: `审核器/src/risk_audit_desktop/task_contracts.py`
- Create: `审核器/src/risk_audit_desktop/task_store.py`
- Create: `审核器/tests/desktop/test_task_contracts.py`
- Create: `审核器/tests/desktop/test_task_store.py`

**Interfaces:**
- Consumes: 用户创建参数、Worker 进度、结果摘要和现有 `data/tasks.json`。
- Produces: `TaskRequest`、`TaskState`、`TaskRecord`、`TaskEvent`、原子 JSON 与追加 JSONL API。

- [ ] **Step 1: 编写请求和状态往返测试**

固定 `schema_version="1.0"`，任务状态只允许 `running`、`cancelling`、`completed`、`partial`、`failed`、`cancelled`、`interrupted`。未知字段可忽略，缺失必填字段必须返回具体校验错误。

- [ ] **Step 2: 实现不可变数据类及显式解析器**

```python
@dataclass(frozen=True)
class TaskRequest:
    """描述 Worker 的完整输入；所有路径均为绝对路径。"""

    schema_version: str
    task_id: str
    display_name: str
    input_root: str
    output_root: str
    rulepack: str
    entity_file: str
    baseline_root: str
    model_root: str
    config_file: str | None
    soffice_path: str
    created_at: str


@dataclass(frozen=True)
class TaskState:
    """描述 GUI 可轮询的单任务状态。"""

    task_id: str
    status: str
    stage: str
    completed_units: int
    total_units: int | None
    progress_percent: int | None
    current_entity: str | None
    current_business: str | None
    current_file: str | None
    worker_pid: int | None
    started_at: str | None
    heartbeat_at: str
    message: str
    error_code: str | None
    result_summary: dict[str, Any] | None
```

- [ ] **Step 3: 编写原子替换测试**

monkeypatch `os.replace`，断言临时文件位于目标同级目录、写完并 `fsync` 后才替换；替换失败时旧 `state.json` 保持可解析。

- [ ] **Step 4: 实现 `atomic_write_json()` 和 `append_event()`**

```python
def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """原子写入 JSON；path 为目标文件，value 为可序列化映射，无返回值。"""


def append_event(path: Path, event: TaskEvent) -> None:
    """追加单行 JSON 事件；path 为 events.jsonl，event 为事件对象。"""
```

关键行添加中文注释，确保 `flush()`、`os.fsync()`、`os.replace()` 顺序固定。

- [ ] **Step 5: 编写任务索引损坏隔离测试**

`tasks.json` 中放入一条合法、一条字段缺失记录，断言只返回合法记录和一条 warning；整个文件非法 JSON 时返回空列表与 warning，不阻止启动。

- [ ] **Step 6: 实现 `TaskStore`**

提供 `add_task()`、`list_tasks()`、`read_state()`、`write_state()`、`append_event()`、`mark_interrupted()`；索引写入统一走原子替换。

- [ ] **Step 7: 运行契约和存储测试**

Run: `python3 -m pytest 审核器/tests/desktop/test_task_contracts.py 审核器/tests/desktop/test_task_store.py -q`

Expected: PASS。

- [ ] **Step 8: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 建立桌面任务状态与原子存储协议`

---

### Task 4: 实现 runtime 资源定位和完整性诊断

**Files:**
- Create: `审核器/src/risk_audit_desktop/diagnostics.py`
- Create: `审核器/tests/desktop/test_diagnostics.py`
- Modify: `审核器/src/risk_audit_desktop/task_paths.py`

**Interfaces:**
- Consumes: `runtime/manifest.json`、资源相对路径、大小和 SHA-256。
- Produces: `DiagnosticItem` 列表、可启动判定、桌面端所需的确定性资源路径。

- [ ] **Step 1: 编写正确、缺失、大小错误和哈希错误测试**

最小清单格式固定为：

```json
{
  "schema_version": "1.0",
  "app_version": "2.0.0",
  "resources": [
    {"path": "libreoffice/program/soffice.exe", "size": 123, "sha256": "..."}
  ]
}
```

- [ ] **Step 2: 实现清单解析和路径逃逸保护**

拒绝绝对路径、`..`、解析后位于 `runtime_root` 外的条目。逐项输出 `ok`、`missing`、`size_mismatch` 或 `hash_mismatch`，错误信息含相对路径。

- [ ] **Step 3: 编写固定业务资源路径测试**

断言：

```python
paths.soffice == app_root / "runtime/libreoffice/program/soffice.exe"
paths.rulepacks == app_root / "runtime/resources/rulepacks"
paths.baseline_root == app_root / "runtime/resources/baselines"
paths.entity_file == app_root / "runtime/resources/entities/会计主体清单20260907.xlsx"
paths.model_root == app_root / "runtime/resources/models/bge-small-zh-v1.5"
paths.config_file == app_root / "runtime/resources/rulepacks/audit-config.json"
```

- [ ] **Step 4: 实现创建任务前诊断**

除资源清单外，检查活动规则包、审核运行配置、主体名册、模型目录、LibreOffice 和输出上级目录；返回结构化结果，GUI 不显示 Python 堆栈。

- [ ] **Step 5: 运行诊断测试**

Run: `python3 -m pytest 审核器/tests/desktop/test_diagnostics.py -q`

Expected: PASS。

- [ ] **Step 6: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 增加便携运行资源完整性诊断`

---

### Task 5: 隔离 LibreOffice 运行环境并实现 Worker

**Files:**
- Modify: `审核器/src/risk_audit/readers/xls.py:15,19-36,228-244`
- Create: `审核器/src/risk_audit_desktop/worker.py`
- Create: `审核器/tests/test_xls_runtime.py`
- Create: `审核器/tests/desktop/test_worker.py`

**Interfaces:**
- Consumes: `TaskRequest`、任务自己的 `_task` 目录、核心 `audit()`。
- Produces: 原子 `state.json`、`events.jsonl`、`worker.log`、终态和隔离 LibreOffice 调用。

- [ ] **Step 1: 编写显式 LibreOffice 路径/profile 测试**

monkeypatch `subprocess.run` 捕获参数，断言命令第一个元素为请求中的 `soffice.exe`，包含 `-env:UserInstallation=file:///.../_task/libreoffice-profile`，且未启用 `shell=True`。

- [ ] **Step 2: 将 `xls.py` 改为进程内显式配置**

```python
def configure_conversion_runtime(soffice_path: Path, profile_path: Path | None) -> None:
    """配置本进程的 LibreOffice；两个参数分别为可执行文件和独立 profile。"""
```

CLI 未提供 profile 时仍使用临时目录；Worker 必须传任务 profile。保留禁用宏、活动内容、外链更新和 `SAL_DISABLE_OPENCL`。

- [ ] **Step 3: 编写 Worker 成功、部分完成、失败、取消测试**

用假的 `audit_func`：

- `write_completed=True` → `completed`
- `write_completed=False` → `partial`
- 抛 `AuditCancelled` → `cancelled`
- 抛其他异常 → `failed` 且 `worker.log` 含堆栈

- [ ] **Step 4: 实现 Worker 状态写入器**

进度回调把 `AuditProgressEvent` 映射为 `TaskState`；扫描前 `progress_percent=None`，扫描后 `completed_units / total_units` 向下取整并限制在 `0..100`。每次写状态同步刷新带时区的 `heartbeat_at`。

- [ ] **Step 5: 实现取消标记检查和审核调用**

```python
def is_cancel_requested(task_dir: Path) -> bool:
    """检查安全停止标记；task_dir 为当前任务的 `_task` 目录。"""
    return (task_dir / "cancel.requested").is_file()
```

`run_worker(request_path, audit_func=audit)` 配置 `TMP`、`TEMP`、`TMPDIR` 到 `_task/work`，配置 LibreOffice，并以 `_task/work/runs`、`task_id`、`model_root`、`config_file` 调用核心后写终态。

- [ ] **Step 6: 实现结束清理策略**

清理 `_task/work`、`_task/libreoffice-profile` 和取消标记；清理失败只追加 warning 事件，不覆盖 `completed`/`partial`/`cancelled` 终态。保留 `request.json`、`state.json`、`events.jsonl`、`worker.log`。

- [ ] **Step 7: 运行 Worker 与 LibreOffice 测试**

Run: `python3 -m pytest 审核器/tests/test_xls_runtime.py 审核器/tests/desktop/test_worker.py -q`

Expected: PASS。

- [ ] **Step 8: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 实现隔离审核工作进程与LibreOffice环境`

---

### Task 6: 实现任务创建、索引恢复和中断判定

**Files:**
- Modify: `审核器/src/risk_audit_desktop/task_store.py`
- Create: `审核器/src/risk_audit_desktop/platform_windows.py`
- Create: `审核器/tests/desktop/test_task_store.py`
- Create: `审核器/tests/desktop/test_task_manager.py`

**Interfaces:**
- Consumes: 表单参数、资源诊断结果、`tasks.json`、PID 和心跳。
- Produces: 一次性任务目录、任务请求/初始状态、恢复后的任务列表。

- [ ] **Step 1: 编写任务创建原子性测试**

断言创建顺序为：验证 → 独占创建输出目录 → 创建 `_task` → 写 `request.json` → 写初始 `state.json` → 加入索引。任一步失败时不写索引；已经创建的空目录可清理，存在业务文件时绝不删除。

- [ ] **Step 2: 实现 `create_task()`**

任务编号格式固定为本地时间加随机后缀，例如 `20260924-103015-a1b2`。展示名默认材料目录名，但请求同时保存显示名与真实 `input_root`，输出命名只在 Task 2 完成。

- [ ] **Step 3: 编写 PID/心跳恢复测试**

覆盖：终态原样恢复；`running` 且 PID 存活、心跳新鲜则保持运行；PID 不存在或心跳超过 30 秒则改为 `interrupted`；状态文件缺失或损坏只影响当前任务。

- [ ] **Step 4: 实现跨平台可测试的 PID 探测接口**

`platform_windows.is_process_alive(pid)` 在 Windows 使用 `OpenProcess` + `GetExitCodeProcess`，测试通过依赖注入传 fake；非 Windows 开发环境使用 `os.kill(pid, 0)` 作为测试路径。

- [ ] **Step 5: 实现 `recover_tasks()`**

恢复时不得重启审核、不得猜测完成状态；只有存活 PID + 新鲜心跳才重新关联，其他非终态统一原子写为 `interrupted`，消息明确“任务进程已中断，可重新创建任务”。

- [ ] **Step 6: 运行存储与恢复测试**

Run: `python3 -m pytest 审核器/tests/desktop/test_task_store.py 审核器/tests/desktop/test_task_manager.py -q -k 'create or recover or heartbeat'`

Expected: PASS。

- [ ] **Step 7: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 增加审核任务创建与异常恢复机制`

---

### Task 7: 实现多 Worker 并行生命周期管理

**Files:**
- Create: `审核器/src/risk_audit_desktop/task_manager.py`
- Modify: `审核器/src/risk_audit_desktop/platform_windows.py`
- Extend: `审核器/tests/desktop/test_task_manager.py`

**Interfaces:**
- Consumes: 已持久化任务请求、主程序路径和 Worker 状态文件。
- Produces: 不排队的并行启动、轮询信号、安全停止、强制停止与显式退出。

- [ ] **Step 1: 编写并行启动参数测试**

连续创建两个任务，fake `popen_factory` 必须立即收到两次调用；参数精确为 `[executable, "--worker", request_path]`，`cwd` 不参与资源定位，禁止字符串命令和 `shell=True`。

- [ ] **Step 2: 实现 `TaskManager.start_task()`**

Windows 设置 `CREATE_NO_WINDOW`；内存只保存本次 GUI 启动的 `Popen` 句柄，重启关联的 Worker 只通过 PID/状态轮询管理。

- [ ] **Step 3: 编写轮询隔离测试**

准备三个状态文件：合法运行、非法 JSON、合法完成。一次轮询必须分别发出更新、单任务读取错误、更新；不能因非法 JSON 中断整个循环。

- [ ] **Step 4: 实现 Qt 定时轮询桥接**

`TaskManager(QObject)` 暴露 `task_updated(task_id, state)`、`task_error(task_id, message)`、`running_count_changed(count)` 信号；`QTimer` 每 500ms 读取状态，不在 GUI 线程执行审核。

- [ ] **Step 5: 编写取消隔离和强制结束测试**

取消任务 A 只创建 A 的 `_task/cancel.requested` 并写 `cancelling`；任务 B 状态和目录不变。强制结束必须指定已解析 PID，并二次检查 PID 仍与任务 `state.json` 一致。

- [ ] **Step 6: 实现停止 API**

提供 `request_cancel(task_id)`、`force_stop(task_id)`、`request_cancel_all()`、`wait_for_all(timeout_ms)`。安全停止默认等待 15 秒后由 UI 决定是否显示强制操作，不自动强杀。

- [ ] **Step 7: 运行任务管理测试**

Run: `python3 -m pytest 审核器/tests/desktop/test_task_manager.py -q`

Expected: PASS。

- [ ] **Step 8: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 实现审核任务并行进程管理`

---

### Task 8: 建立统一入口与 Worker 早期分流

**Files:**
- Create: `审核器/src/risk_audit_desktop/app.py`
- Modify: `审核器/pyproject.toml`
- Create: `审核器/requirements-desktop.lock`
- Create: `审核器/tests/desktop/test_task_contracts.py`

**Interfaces:**
- Consumes: `sys.argv` 中的 GUI 参数或 `--worker <request.json>`。
- Produces: `risk-audit-desktop` 开发入口和 PyInstaller 的唯一入口函数。

- [ ] **Step 1: 编写 Worker 分流不导入 Qt Widgets 测试**

在子进程中调用 `main(["--worker", request])`，fake `run_worker`，断言 `PySide6.QtWidgets` 不在 `sys.modules`。这保证 Worker 不加载页面或托盘资源。

- [ ] **Step 2: 实现最小参数分流**

```python
def main(argv: Sequence[str] | None = None) -> int:
    """启动桌面界面或 Worker；argv 为可选命令行参数，返回进程退出码。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--worker":
        from risk_audit_desktop.worker import run_worker
        return run_worker(Path(args[1]))
    from risk_audit_desktop.main_window import run_gui
    return run_gui(args)
```

- [ ] **Step 3: 更新桌面依赖和入口**

`pyproject.toml` 新增 `desktop` 可选依赖与 `risk-audit-desktop = "risk_audit_desktop.app:main"`。`requirements-desktop.lock` 固定 PySide6、PyInstaller、pytest-qt 及现有运行依赖，禁止宽泛版本范围。

- [ ] **Step 4: 安装开发依赖并运行入口测试**

Run: `python3 -m pip install -e '审核器[desktop,test]'`

Run: `python3 -m pytest 审核器/tests/desktop/test_task_contracts.py -q -k worker_dispatch`

Expected: PASS。

- [ ] **Step 5: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 增加桌面端与工作进程统一入口`

---

### Task 9: 实现任务列表、新建任务和任务详情页面

**Files:**
- Create: `审核器/src/risk_audit_desktop/task_list_page.py`
- Create: `审核器/src/risk_audit_desktop/task_create_page.py`
- Create: `审核器/src/risk_audit_desktop/task_detail_page.py`
- Create: `审核器/src/risk_audit_desktop/help_page.py`
- Create: `审核器/src/risk_audit_desktop/theme.py`
- Create: `审核器/tests/desktop/conftest.py`
- Create: `审核器/tests/desktop/test_task_pages.py`

**Interfaces:**
- Consumes: `TaskManager` 信号、任务记录、诊断结果和用户表单输入。
- Produces: Qt Widgets 页面信号，不直接调用审核核心。

- [ ] **Step 1: 配置无显示器 Qt 测试环境**

在测试环境设置 `QT_QPA_PLATFORM=offscreen`，通过 `qtbot` 创建/销毁页面，避免遗留 QApplication。

- [ ] **Step 2: 编写任务列表渲染测试**

输入 running/completed/failed 三条任务，断言展示名称、编号、创建时间、中文状态、进度，以及运行任务“查看”、完成任务“结果”、失败任务“重试”操作。

- [ ] **Step 3: 实现 `TaskListPage`**

使用 `QTableView + QAbstractTableModel`，状态与原始数据分离；按创建时间倒序。进度未知显示不确定状态文本“正在扫描”，不得伪造百分比。

- [ ] **Step 4: 编写新建任务表单测试**

选择材料目录后：展示名默认材料目录名，输出目录按 Task 2 自动生成；修改展示名不改变输出目录；诊断失败时创建按钮禁用并显示具体路径。

- [ ] **Step 5: 实现 `TaskCreatePage`**

提供材料目录、任务名称、输出目录选择器和诊断区。点击“创建并开始审核”只发出结构化 `create_requested` 信号，由主窗口协调 `TaskStore` 与 `TaskManager`。

- [ ] **Step 6: 编写任务详情进度与结果测试**

覆盖不确定进度、`17/25 = 68%`、四阶段状态、当前主体/业务/文件、运行时间、终态统计、打开目录/统计表/集中复核入口。

- [ ] **Step 7: 实现 `TaskDetailPage` 与 `HelpPage`**

详情页仅消费状态和结果摘要；错误显示简要说明与错误编号，不显示堆栈。帮助页说明“必须整体移动目录”“输出位置”“停止任务”“不联网”。

- [ ] **Step 8: 实现浅色/深色主题**

根据 `QStyleHints.colorScheme()` 切换统一 palette 和样式；状态颜色必须同时有文字/图标，不能只依赖颜色。

- [ ] **Step 9: 运行页面测试**

Run: `QT_QPA_PLATFORM=offscreen python3 -m pytest 审核器/tests/desktop/test_task_pages.py -q`

Expected: PASS。

- [ ] **Step 10: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 实现桌面审核任务主要页面`

---

### Task 10: 实现主窗口、托盘、关闭策略和单实例

**Files:**
- Create: `审核器/src/risk_audit_desktop/main_window.py`
- Create: `审核器/src/risk_audit_desktop/tray.py`
- Create: `审核器/src/risk_audit_desktop/single_instance.py`
- Create: `审核器/src/risk_audit_desktop/assets/app.ico`
- Create: `审核器/tests/desktop/test_main_window.py`
- Create: `审核器/tests/desktop/test_tray_and_single_instance.py`

**Interfaces:**
- Consumes: 四个页面、`TaskManager`、运行任务数和第二次启动激活消息。
- Produces: 完整主窗口、系统托盘、三选项关闭行为和唯一 GUI 实例。

- [ ] **Step 1: 编写导航和运行数量测试**

断言左侧导航包含“审核任务”“新建任务”“使用帮助”，左下角能从 `0 个任务运行中` 更新到 `2 个任务并行运行`。

- [ ] **Step 2: 实现 `MainWindow` 页面协调**

主窗口负责创建任务、启动 Worker、路由详情页和刷新列表；页面保持无业务核心依赖。

- [ ] **Step 3: 编写关闭窗口分支测试**

- 无运行任务：直接接受 close event。
- 有运行任务 + 默认按钮：隐藏窗口、任务继续。
- 选择“停止全部任务并退出”：调用 `request_cancel_all()`，等待完成后退出。
- 选择“取消”：忽略 close event 并保持窗口。

- [ ] **Step 4: 实现关闭对话框和托盘行为**

默认按钮必须是“最小化到托盘并继续运行（推荐）”。托盘提示实时显示运行数，双击恢复并激活窗口；运行数从大于 0 变为 0 时通知“所有审核任务已结束”。

- [ ] **Step 5: 编写单实例测试**

第一个实例成功监听固定 server name；第二个实例发送 `activate` 后退出码为 0；第一个实例收到消息后 `showNormal()`、`raise_()`、`activateWindow()`。

- [ ] **Step 6: 使用 `QLocalServer/QLocalSocket` 实现单实例**

server name 包含稳定应用 ID，不含用户名或路径。仅在确认没有活跃 server 后清理陈旧 endpoint，避免破坏真实运行实例。

- [ ] **Step 7: 实现显式退出兜底**

应用 `aboutToQuit` 时，如果还有 Worker，则发出安全取消并等待；超时后弹出一次明确确认，用户选择强制退出才逐个核对 PID 后终止。不得静默遗留 Worker。

- [ ] **Step 8: 运行窗口、托盘和单实例测试**

Run: `QT_QPA_PLATFORM=offscreen python3 -m pytest 审核器/tests/desktop/test_main_window.py 审核器/tests/desktop/test_tray_and_single_instance.py -q`

Expected: PASS。

- [ ] **Step 9: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 完成桌面主窗口托盘与单实例控制`

---

### Task 11: 构建便携 runtime、资源清单和 Windows EXE

**Files:**
- Create: `审核器/tools/build_windows_desktop.py`
- Create: `审核器/tools/verify_portable_distribution.py`
- Create: `审核器/tests/desktop/test_portable_distribution.py`
- Create: `审核器/Windows桌面版使用说明.md`

**Interfaces:**
- Consumes: 当前发布规则包、冻结基准、主体名册、模型、Windows LibreOffice 目录和许可证源目录。
- Produces: `dist/风控矩阵审核器-v2.0.0/`、`runtime/manifest.json`、无控制台 EXE 和 ZIP。

- [ ] **Step 1: 编写资源收集与清单测试**

在临时 fixture 中创建规则、基准、主体、模型、LibreOffice 和 licenses，运行收集函数，断言目标结构、每个清单条目的 POSIX 相对路径、字节数和 SHA-256 完全一致。

- [ ] **Step 2: 实现确定性资源收集**

基准复制到 `runtime/resources/baselines/审核/...`，保留规则包内 `审核/...` 相对路径；`audit-config.json` 复制到 `runtime/resources/rulepacks/audit-config.json`。只复制发布所需规则包和活动版本，不包含测试数据、历史 outputs、runs、草稿或 `.git`。

- [ ] **Step 3: 编写缺许可证和缺 soffice 阻止构建测试**

缺少任何必需项时构建脚本返回非零并列出路径，不生成看似完整的 ZIP。

- [ ] **Step 4: 实现 PyInstaller 构建命令**

Windows 上执行：

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name 风控矩阵审核器 `
  --icon 审核器/src/risk_audit_desktop/assets/app.ico `
  --collect-all PySide6 `
  审核器/src/risk_audit_desktop/app.py
```

构建脚本使用参数列表调用，不用 `shell=True`；构建后再复制外部 `runtime/`，避免 LibreOffice 被塞进单文件临时解压区。

- [ ] **Step 5: 实现交付验证脚本**

验证根目录只有允许项、EXE 存在、manifest 全量通过、没有绝对开发机路径、没有 `__pycache__`/`.pyc`/测试 fixture，ZIP 内顶层目录唯一。

- [ ] **Step 6: 编写非技术用户使用说明**

包含解压、双击、创建任务、并行任务、托盘、停止、输出目录、整体移动、常见错误和隐私说明；不得要求命令行。

- [ ] **Step 7: 运行便携结构测试**

Run: `python3 -m pytest 审核器/tests/desktop/test_portable_distribution.py -q`

Expected: PASS；macOS 只测收集和验证逻辑，不声称生成可用 Windows EXE。

- [ ] **Step 8: 记录建议提交信息，不执行 Git 提交**

建议：`build: 增加Windows便携桌面版构建流程`

---

### Task 12: 增加并行、取消、崩溃和恢复集成测试

**Files:**
- Extend: `审核器/tests/desktop/test_worker.py`
- Extend: `审核器/tests/desktop/test_task_manager.py`
- Create: `审核器/tests/desktop/test_parallel_integration.py`

**Interfaces:**
- Consumes: 真实子进程式 fake Worker、真实文件协议、两个独立任务目录。
- Produces: 对并行隔离、取消隔离、异常隔离和恢复协议的端到端证据。

- [ ] **Step 1: 编写可控测试 Worker**

测试 Worker 接受 request，按 barrier 文件同步进入 `running`，周期写心跳与进度；收到自身取消标记后写 `cancelled`；指定参数可模拟异常退出。

- [ ] **Step 2: 测试两个任务真正重叠运行**

同时启动 A/B，等待两者均为 `running` 后释放 barrier。断言时间窗口重叠，而非 A 完成后 B 才开始；两者 PID、输出、work、profile、日志均不同。

- [ ] **Step 3: 测试取消一个任务不影响另一个**

取消 A，断言 A 最终 `cancelled`、B 最终 `completed`；B 目录没有 A 的 cancel 文件、事件或日志内容。

- [ ] **Step 4: 测试单 Worker 崩溃不影响 GUI 轮询**

A 无终态退出后被标为 `interrupted`，B 继续更新并完成；任务管理器仍能发出 B 的状态信号。

- [ ] **Step 5: 测试重启恢复**

新建第二个 `TaskManager`/`TaskStore` 实例读取同一便携目录；存活 Worker 保持 running，已死 PID 超时后变 interrupted，已完成任务保持 completed。

- [ ] **Step 6: 运行集成测试**

Run: `QT_QPA_PLATFORM=offscreen python3 -m pytest 审核器/tests/desktop/test_parallel_integration.py -q`

Expected: PASS，且测试有最长等待时间，失败时不会永久挂起。

- [ ] **Step 7: 记录建议提交信息，不执行 Git 提交**

建议：`test: 覆盖桌面任务并行取消与恢复场景`

---

### Task 13: 运行完整回归并在 Windows 构建交付包

**Files:**
- Verify: `审核器/tests/`
- Verify: `审核器/src/risk_audit_desktop/`
- Generate on Windows: `dist/风控矩阵审核器-v2.0.0-Windows-x64.zip`

**Interfaces:**
- Consumes: 全部实现、Windows x64 Python 3.11 环境、正式 LibreOffice 和许可证目录。
- Produces: 自动化测试证据、可复现 ZIP 和清单验证结果。

- [ ] **Step 1: 运行静态语法检查**

Run: `python3 -m compileall -q 审核器/src`

Expected: exit code 0。

- [ ] **Step 2: 运行桌面端完整测试**

Run: `QT_QPA_PLATFORM=offscreen python3 -m pytest 审核器/tests/desktop 审核器/tests/test_progress_and_cancel.py 审核器/tests/test_xls_runtime.py -q`

Expected: PASS。

- [ ] **Step 3: 运行现有审核核心完整回归**

Run: `python3 -m pytest 审核器/tests -q`

Expected: PASS；不得通过删除或放宽旧业务测试取得通过。

- [ ] **Step 4: 在 Windows x64 创建干净虚拟环境并安装锁定依赖**

```powershell
py -3.11 -m venv .venv-desktop
.venv-desktop\Scripts\python -m pip install --require-hashes -r 审核器\requirements-desktop.lock
```

Expected: 不联网运行时使用预先准备的 wheelhouse；依赖版本与锁文件完全一致。

- [ ] **Step 5: 在 Windows 构建并验证 ZIP**

```powershell
.venv-desktop\Scripts\python 审核器\tools\build_windows_desktop.py `
  --libreoffice C:\build-inputs\LibreOfficePortable `
  --licenses C:\build-inputs\licenses `
  --output dist
.venv-desktop\Scripts\python 审核器\tools\verify_portable_distribution.py `
  dist\风控矩阵审核器-v2.0.0
```

Expected: 生成 `dist\风控矩阵审核器-v2.0.0-Windows-x64.zip`，manifest 全部通过。

- [ ] **Step 6: 在无 Python、无 LibreOffice 的 Windows 10/11 x64 机器冒烟验收**

逐项记录：双击启动、中文空格路径、两个含 `.xls` 转换的并行任务、取消其中一个、托盘继续、完成通知、异常关闭恢复、默认输出命名、原文件哈希不变。

- [ ] **Step 7: 验证便携边界**

将整个目录移动到另一中文路径后再次运行；只复制 EXE 时必须显示 runtime 缺失而不是崩溃；断网运行行为不变；普通用户权限可创建 `data` 和 `outputs`。

- [ ] **Step 8: 保存验收记录并检查工作树**

Run: `git status --short`

Run: `git diff --check`

Expected: 仅出现本功能预期文件，`git diff --check` 无输出；不执行 `git commit`。

- [ ] **Step 9: 记录最终建议提交信息，不执行 Git 提交**

建议：`feat: 交付Windows便携并行任务桌面端`

---

## Final Acceptance Checklist

- [ ] `风控矩阵审核器.exe` 在无 Python、无 LibreOffice 的 Windows 10/11 x64 普通用户环境可双击启动。
- [ ] 交付目录与设计文档一致，LibreOffice 固定在 `runtime/libreoffice/program/soffice.exe`。
- [ ] 一次审核对应一次任务，至少两个任务可真正并行运行。
- [ ] 默认输出严格为 `outputs/<材料目录名>-YYYYMMDD-HHMMSS`，不使用任务展示名，不覆盖已有目录。
- [ ] 每个任务的输出、profile、临时目录、日志、取消标记和状态互不影响。
- [ ] 任务列表、创建页、详情页、帮助页、浅深色、托盘和单实例行为符合设计。
- [ ] 安全取消、失败、部分完成、异常中断和重启恢复均有自动化测试。
- [ ] `runtime/manifest.json` 能阻止缺失、被替换或哈希不匹配的运行资源。
- [ ] 原始材料审核前后哈希一致，程序无网络访问。
- [ ] 桌面测试、审核核心回归和 Windows 实机验收全部通过。
- [ ] 第三方许可证、使用说明、构建验证结果齐全。
- [ ] 未执行 Git 提交，已向用户提供中文 Conventional Commit 建议信息。
