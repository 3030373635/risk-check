# Windows 免安装桌面端实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有风控矩阵审核器增加一个现代化、完全离线、解压即用的 Windows 10/11 x64 桌面端，并生成包含固定 LibreOffice 运行环境的免安装 ZIP。

**Architecture:** PySide6/QML 负责界面，现有 `risk_audit` 继续负责审核。桌面主进程使用同一可执行文件的 `--worker` 模式启动独立审核进程，通过逐行 JSON 事件接收进度；程序资源只读，历史与日志写入 `%LOCALAPPDATA%\RiskAuditDesktop`。

**Tech Stack:** Python 3.11+、PySide6 6.8.3、Qt Quick/QML、Nuitka 2.6.8、pytest 8.4.2、内置 LibreOffice Windows x64。

**Spec:** `docs/superpowers/specs/2026-09-23-windows-desktop-portable-design.md`

## Global Constraints

- 只支持 Windows 10/11 x64；最终可执行文件必须在 Windows x64 环境构建和验收。
- 交付物是免安装 ZIP；不得要求管理员权限，不写注册表，不创建系统服务。
- 程序完全离线，不增加任何网络请求、账号或自动更新能力。
- 原始材料始终只读；正式结果只能写入用户选择的新输出目录。
- 第一版只允许同时运行一个审核任务。
- 桌面框架固定为 PySide6，界面固定使用 Qt Quick/QML。
- 用户数据固定写入 `%LOCALAPPDATA%\RiskAuditDesktop`；程序资源保持只读。
- 使用 `pyside6-deploy` 对应的 Nuitka standalone 能力，不使用单文件自解压模式。
- 不为旧桌面协议或旧命令行输出增加兼容层；现有审核业务结果和核心测试必须继续通过。
- 新增 Python 函数必须包含用途、参数和返回值说明；关键进程、完整性校验及输出保护代码必须有中文注释。
- Python 变量使用清晰统一的 snake_case；QML 属性使用 camelCase。
- 本功能不提供 HTTP 接口，因此无 REST API 变更。
- 遵循项目要求：实施过程中不执行 `git commit`；每个任务完成后只报告建议的中文 Conventional Commit 信息。

## Review Focus

- 中文、空格和长路径：任务请求必须通过参数列表或 JSON 文件传递，不能拼接 shell 命令；Task 2 和 Task 5 的测试覆盖。
- 输入输出目录重叠、输出已存在或不可写：必须在启动工作进程前阻止任务；Task 3 和 Task 6 的测试覆盖。
- 规则、基准、主体清单、LibreOffice 缺失或哈希变化：诊断必须列出具体缺失项并禁止审核；Task 3 和 Task 8 的测试覆盖。
- 工作进程输出半行、非法 JSON、异常退出或被取消：界面必须恢复到可再次运行状态，且不能显示成功；Task 5 和 Task 6 的测试覆盖。
- 历史记录损坏或结果目录被移动、删除：程序必须保留可用记录、提示路径失效并继续启动；Task 4 的测试覆盖。

---

## File Structure

新增和修改后的主要结构如下：

```text
审核器/
├── pyproject.toml                         # 增加桌面依赖与桌面入口
├── requirements-desktop.lock             # 固定桌面和构建依赖
├── src/
│   ├── risk_audit/
│   │   ├── progress.py                    # 审核进度事件模型
│   │   └── runner.py                      # 在关键阶段发送进度事件
│   └── risk_audit_desktop/
│       ├── __init__.py                    # 桌面包版本
│       ├── app.py                         # GUI 与 --worker 统一入口
│       ├── contracts.py                   # 任务请求和事件解析契约
│       ├── worker.py                      # 独立审核工作进程
│       ├── paths.py                       # 程序、资源和用户数据路径
│       ├── diagnostics.py                 # 环境及资源完整性检查
│       ├── history.py                     # 历史任务持久化
│       ├── process_protocol.py             # JSON 行流解析和状态归并
│       ├── task_manager.py                # QProcess 生命周期管理
│       ├── platform_windows.py            # 打开文件及终止进程树
│       ├── controller.py                  # QML 可调用应用控制器
│       ├── assets/app-icon.svg            # 应用图标源文件
│       └── qml/
│           ├── Main.qml                   # 窗口和主导航
│           ├── components/FolderCard.qml  # 文件夹选择卡片
│           └── pages/
│               ├── AuditPage.qml          # 准备、运行、结果状态
│               ├── HistoryPage.qml        # 历史记录
│               └── DiagnosticsPage.qml    # 设置与诊断
├── tests/
│   ├── test_progress_events.py
│   └── desktop/
│       ├── test_contracts.py
│       ├── test_worker.py
│       ├── test_paths_and_diagnostics.py
│       ├── test_history.py
│       ├── test_process_protocol.py
│       ├── test_task_manager.py
│       ├── test_controller.py
│       ├── test_qml_smoke.py
│       └── test_portable_build.py
├── tools/build_windows_desktop.py          # 收集资源、编译和生成 ZIP
└── Windows桌面版使用说明.md
```

最终 ZIP 内部结构固定为：

```text
风控矩阵审核器-v2.0.0/
├── 风控矩阵审核器.exe
├── runtime/libreoffice/
├── resources/project/审核器/rulepacks/
├── resources/project/审核/
├── resources/project/audit-config.json
├── resources/qml/
├── resources/assets/
├── licenses/
├── 使用说明.md
└── resource-manifest.json
```

`resources/project` 保留现有规则包中的相对路径，因此不需要重写冻结的基准注册表。

---

### Task 1: 为审核核心增加结构化进度事件

**Files:**
- Create: `审核器/src/risk_audit/progress.py`
- Modify: `审核器/src/risk_audit/runner.py:75-100,185-494`
- Create: `审核器/tests/test_progress_events.py`

**Interfaces:**
- Consumes: 现有 `risk_audit.runner.audit(...) -> dict[str, Any]`。
- Produces: `AuditProgressEvent`、`ProgressCallback`、`emit_progress(...)`，以及新增可选参数 `progress_callback: ProgressCallback | None = None`。

- [ ] **Step 1: 编写进度事件模型失败测试**

```python
from risk_audit.progress import AuditProgressEvent, emit_progress


def test_progress_event_omits_empty_optional_fields():
    event = AuditProgressEvent(event="scan_progress", stage="scan", completed=3, total=8)
    assert event.to_dict() == {
        "event": "scan_progress",
        "stage": "scan",
        "completed": 3,
        "total": 8,
    }


def test_emit_progress_is_noop_without_callback():
    emit_progress(None, AuditProgressEvent(event="task_started", stage="startup"))
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `python -m pytest 审核器/tests/test_progress_events.py -q`

Expected: FAIL，提示 `ModuleNotFoundError: risk_audit.progress`。

- [ ] **Step 3: 实现最小进度事件模型**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class AuditProgressEvent:
    """表示一次审核进度变化；各字段均可安全序列化为 JSON。"""

    event: str
    stage: str
    completed: int | None = None
    total: int | None = None
    entity_name: str | None = None
    business_name: str | None = None
    file_name: str | None = None
    message: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容字典，并移除未设置的可选字段。"""
        return {key: value for key, value in asdict(self).items() if value is not None}


ProgressCallback = Callable[[AuditProgressEvent], None]


def emit_progress(callback: ProgressCallback | None, event: AuditProgressEvent) -> None:
    """发送进度事件；callback 为可选接收器，event 为待发送事件。"""
    if callback is not None:
        callback(event)
```

- [ ] **Step 4: 为 `audit()` 包装层添加开始、完成和失败事件测试**

```python
def test_audit_reports_terminal_event(monkeypatch, tmp_path):
    from risk_audit import runner

    events = []
    monkeypatch.setattr(runner, "_audit", lambda *args, **kwargs: {
        "entity_results": [], "findings": 0, "warnings": 0, "write_completed": True,
    })
    result = runner.audit(tmp_path / "input", tmp_path / "output", tmp_path / "pack",
                          tmp_path / "entities.xlsx", tmp_path, tmp_path / "runs",
                          progress_callback=events.append)
    assert result["write_completed"] is True
    assert [event.event for event in events] == ["task_started", "task_completed"]
```

再增加一个 `_audit` 抛出 `ValueError` 的测试，断言最后事件为 `task_failed` 且异常继续向上抛出。

- [ ] **Step 5: 在 `runner.py` 的关键节点发送事件**

在不改变默认命令行输出的前提下增加：

```python
emit_progress(progress_callback, AuditProgressEvent("scan_progress", "scan", message="正在扫描报送材料"))

# 每个业务开始前发送，completed 使用已完成数量而不是当前序号。
emit_progress(progress_callback, AuditProgressEvent(
    event="business_progress",
    stage="audit",
    completed=len(business_results),
    total=sum(len(_business_keys(scopes[key], [f for f in files if submission_group(f, input_resolved) == key])) for key in entity_keys),
    entity_name=entity_name,
    business_name=unit_label,
    file_name=unit_files[0].relative_path.name if unit_files else None,
    message="正在执行审核规则",
))
```

总业务数在进入主体循环前只计算一次，保存为 `total_businesses`，避免每次事件重复扫描。业务完成后再次发送 `business_progress`，并在 `details` 中包含本业务 `findings`、`warnings` 和 `write_completed`。

- [ ] **Step 6: 运行进度测试和审核核心回归测试**

Run: `python -m pytest 审核器/tests/test_progress_events.py 审核器/tests/test_engine.py 审核器/tests/test_audit_v180.py -q`

Expected: 全部 PASS，现有 CLI 在未传回调时仍只输出最终 JSON。

- [ ] **Step 7: 记录建议提交信息，不执行提交**

建议：`feat: 增加审核任务结构化进度事件`

---

### Task 2: 建立桌面任务契约和工作进程入口

**Files:**
- Create: `审核器/src/risk_audit_desktop/__init__.py`
- Create: `审核器/src/risk_audit_desktop/contracts.py`
- Create: `审核器/src/risk_audit_desktop/worker.py`
- Create: `审核器/tests/desktop/test_contracts.py`
- Create: `审核器/tests/desktop/test_worker.py`

**Interfaces:**
- Consumes: `risk_audit.runner.audit(..., progress_callback=...)`、`AuditProgressEvent.to_dict()`。
- Produces: `AuditTaskRequest.from_dict()`、`AuditTaskRequest.to_dict()`、`AuditTaskRequest.to_audit_kwargs()`、`run_worker(request, emit_line, audit_func=audit) -> int`。

- [ ] **Step 1: 编写任务请求校验失败测试**

```python
from pathlib import Path
import pytest

from risk_audit_desktop.contracts import AuditTaskRequest


def test_request_preserves_chinese_and_space_paths(tmp_path):
    request = AuditTaskRequest.from_dict({
        "input_root": str(tmp_path / "中文 材料"),
        "output_root": str(tmp_path / "审核 结果"),
        "project_root": str(tmp_path / "项目 资源"),
        "runs_root": str(tmp_path / "运行 记录"),
        "rulepack": str(tmp_path / "规则 包"),
        "entity_file": str(tmp_path / "会计主体 清单.xlsx"),
        "soffice_path": str(tmp_path / "Libre Office" / "soffice.exe"),
    })
    assert request.input_root == Path(tmp_path / "中文 材料")


def test_request_rejects_overlapping_output(tmp_path):
    with pytest.raises(ValueError, match="相同或相互包含"):
        AuditTaskRequest.from_dict({
            "input_root": str(tmp_path / "材料"),
            "output_root": str(tmp_path / "材料" / "结果"),
            "project_root": str(tmp_path),
            "runs_root": str(tmp_path / "runs"),
            "rulepack": str(tmp_path / "pack"),
            "entity_file": str(tmp_path / "entities.xlsx"),
            "soffice_path": str(tmp_path / "soffice.exe"),
        })
```

- [ ] **Step 2: 实现不可变任务请求对象**

```python
@dataclass(frozen=True)
class AuditTaskRequest:
    """描述一个桌面审核任务；路径字段在构造时转换为绝对路径。"""

    input_root: Path
    output_root: Path
    project_root: Path
    runs_root: Path
    rulepack: Path
    entity_file: Path
    soffice_path: Path
    config_file: Path | None = None
    enabled_rules: tuple[str, ...] = ()
    include_hidden: bool = False
    run_id: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AuditTaskRequest":
        """校验字典并构造任务；value 为桌面主进程写出的请求数据。"""
        required = {
            "input_root", "output_root", "project_root", "runs_root",
            "rulepack", "entity_file", "soffice_path",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise ValueError(f"任务请求缺少字段: {', '.join(missing)}")
        request = cls(
            input_root=Path(value["input_root"]).resolve(),
            output_root=Path(value["output_root"]).resolve(),
            project_root=Path(value["project_root"]).resolve(),
            runs_root=Path(value["runs_root"]).resolve(),
            rulepack=Path(value["rulepack"]).resolve(),
            entity_file=Path(value["entity_file"]).resolve(),
            soffice_path=Path(value["soffice_path"]).resolve(),
            config_file=Path(value["config_file"]).resolve() if value.get("config_file") else None,
            enabled_rules=tuple(value.get("enabled_rules", [])),
            include_hidden=bool(value.get("include_hidden", False)),
            run_id=value.get("run_id"),
        )
        if not request.input_root.is_dir():
            raise ValueError(f"输入目录不存在: {request.input_root}")
        if (request.input_root == request.output_root
                or request.input_root in request.output_root.parents
                or request.output_root in request.input_root.parents):
            raise ValueError("输入目录与输出目录相同或相互包含")
        if request.output_root.exists():
            raise ValueError(f"输出目录已经存在: {request.output_root}")
        if not request.output_root.parent.is_dir() or not os.access(request.output_root.parent, os.W_OK):
            raise ValueError(f"输出目录的上级目录不可写: {request.output_root.parent}")
        return request

    def to_audit_kwargs(self) -> dict[str, Any]:
        """返回调用 risk_audit.runner.audit 所需参数。"""
        return {
            "input_root": self.input_root,
            "output_root": self.output_root,
            "rulepack": self.rulepack,
            "entity_file": self.entity_file,
            "project_root": self.project_root,
            "runs_root": self.runs_root,
            "write": True,
            "run_id": self.run_id,
            "include_hidden": self.include_hidden,
            "config_file": self.config_file,
            "enabled_rules": list(self.enabled_rules),
        }

    def to_dict(self) -> dict[str, Any]:
        """返回可写入任务请求文件的 JSON 兼容字典。"""
        return {
            "input_root": str(self.input_root),
            "output_root": str(self.output_root),
            "project_root": str(self.project_root),
            "runs_root": str(self.runs_root),
            "rulepack": str(self.rulepack),
            "entity_file": str(self.entity_file),
            "soffice_path": str(self.soffice_path),
            "config_file": str(self.config_file) if self.config_file else None,
            "enabled_rules": list(self.enabled_rules),
            "include_hidden": self.include_hidden,
            "run_id": self.run_id,
        }
```

实现必须拒绝输入输出相同、相互包含、输出已存在、输入不存在以及输出父目录不可写；不得通过字符串前缀判断路径关系。

- [ ] **Step 3: 编写工作进程事件测试**

```python
def test_worker_emits_progress_and_completed_event(valid_request):
    lines = []

    def fake_audit(**kwargs):
        kwargs["progress_callback"](
            AuditProgressEvent("business_progress", "audit", completed=1, total=1)
        )
        return {"write_completed": True, "findings": 2, "warnings": 0}

    exit_code = run_worker(valid_request, lines.append, audit_func=fake_audit)

    assert exit_code == 0
    assert [json.loads(line)["event"] for line in lines] == [
        "business_progress", "worker_completed"
    ]
```

另写异常测试：`fake_audit` 抛出异常，返回码为 `2`，最后一行事件为 `worker_failed`，事件不得包含完整材料内容。

- [ ] **Step 4: 实现工作进程**

```python
def run_worker(
    request: AuditTaskRequest,
    emit_line: Callable[[str], None],
    *,
    audit_func: Callable[..., dict[str, Any]] = audit,
) -> int:
    """执行一个审核任务；request 为任务参数，emit_line 接收逐行 JSON。"""
    from risk_audit.readers import xls

    # 工作进程只使用随桌面包提供的固定 LibreOffice。
    xls.SOFFICE = request.soffice_path

    def emit(event: AuditProgressEvent) -> None:
        emit_line(json.dumps(event.to_dict(), ensure_ascii=False))

    try:
        result = audit_func(**request.to_audit_kwargs(), progress_callback=emit)
        emit_line(json.dumps({"event": "worker_completed", "result": result}, ensure_ascii=False))
        return 0
    except Exception as error:
        emit_line(json.dumps({"event": "worker_failed", "error": str(error)}, ensure_ascii=False))
        return 2
```

命令入口使用 `--request <json-file>`，逐行写标准输出并立即 `flush=True`。完整堆栈写入工作进程日志或标准错误，不混入事件流。

- [ ] **Step 5: 运行任务契约与工作进程测试**

Run: `python -m pytest 审核器/tests/desktop/test_contracts.py 审核器/tests/desktop/test_worker.py -q`

Expected: 全部 PASS。

- [ ] **Step 6: 记录建议提交信息，不执行提交**

建议：`feat: 增加桌面审核工作进程协议`

---

### Task 3: 实现便携目录解析与运行前诊断

**Files:**
- Create: `审核器/src/risk_audit_desktop/paths.py`
- Create: `审核器/src/risk_audit_desktop/diagnostics.py`
- Create: `审核器/tests/desktop/test_paths_and_diagnostics.py`

**Interfaces:**
- Consumes: 最终 ZIP 目录结构、现有 `RulePackStore.validate()`、`sha256_file()`。
- Produces: `DesktopPaths.discover()`、`DiagnosticItem`、`DiagnosticReport`、`verify_resource_manifest()`、`run_preflight(paths) -> DiagnosticReport`。

- [ ] **Step 1: 编写路径发现和缺少 LOCALAPPDATA 的测试**

```python
def test_paths_use_portable_resources_and_local_app_data(tmp_path):
    app_root = tmp_path / "风控矩阵审核器"
    local_data = tmp_path / "本地 数据"
    paths = DesktopPaths.discover(app_root=app_root, environ={"LOCALAPPDATA": str(local_data)})
    assert paths.project_root == app_root / "resources/project"
    assert paths.soffice_path == app_root / "runtime/libreoffice/program/soffice.exe"
    assert paths.user_data_root == local_data / "RiskAuditDesktop"


def test_paths_reject_missing_local_app_data(tmp_path):
    with pytest.raises(RuntimeError, match="LOCALAPPDATA"):
        DesktopPaths.discover(app_root=tmp_path, environ={})
```

- [ ] **Step 2: 实现路径对象**

```python
@dataclass(frozen=True)
class DesktopPaths:
    """保存桌面程序的只读资源和可写用户目录。"""

    app_root: Path
    project_root: Path
    rulepacks_root: Path
    entity_file: Path
    audit_config: Path
    soffice_path: Path
    qml_root: Path
    user_data_root: Path
    runs_root: Path
    history_file: Path
    task_root: Path

    @classmethod
    def discover(
        cls,
        *,
        app_root: Path,
        environ: Mapping[str, str],
        portable: bool = True,
    ) -> "DesktopPaths":
        """根据程序目录和环境变量构造全部路径，不依赖当前工作目录。"""
        local_app_data = environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise RuntimeError("Windows 环境变量 LOCALAPPDATA 不存在")
        resolved_root = app_root.resolve()
        project_root = resolved_root / "resources/project" if portable else resolved_root
        qml_root = (resolved_root / "resources/qml" if portable
                    else resolved_root / "审核器/src/risk_audit_desktop/qml")
        soffice_path = (resolved_root / "runtime/libreoffice/program/soffice.exe" if portable
                        else Path(environ.get("RISK_AUDIT_SOFFICE", "__missing_soffice__")).resolve())
        user_data_root = Path(local_app_data).resolve() / "RiskAuditDesktop"
        return cls(
            app_root=resolved_root,
            project_root=project_root,
            rulepacks_root=project_root / "审核器/rulepacks",
            entity_file=project_root / "审核/会计主体清单20260907.xlsx",
            audit_config=project_root / "audit-config.json",
            soffice_path=soffice_path,
            qml_root=qml_root,
            user_data_root=user_data_root,
            runs_root=user_data_root / "runs",
            history_file=user_data_root / "history.json",
            task_root=user_data_root / "tasks",
        )
```

- [ ] **Step 3: 编写预检失败测试**

```python
def test_preflight_reports_every_missing_required_resource(portable_paths):
    report = run_preflight(portable_paths)
    assert report.ready is False
    assert {item.code for item in report.items if not item.ok} >= {
        "rulepack_missing", "entity_file_missing", "soffice_missing", "manifest_missing"
    }


def test_preflight_rejects_changed_manifest_file(portable_tree):
    target = portable_tree.project_root / "审核器/rulepacks/active.json"
    target.write_text("changed", encoding="utf-8")
    report = run_preflight(portable_tree)
    assert any(item.code == "resource_hash_mismatch" for item in report.items)
```

- [ ] **Step 4: 实现完整性和环境诊断**

```python
@dataclass(frozen=True)
class DiagnosticItem:
    """表示单项诊断结果；code 为稳定机器编号，message 为中文说明。"""

    code: str
    ok: bool
    message: str
    path: str | None = None


@dataclass(frozen=True)
class DiagnosticReport:
    """汇总启动前诊断结果。"""

    items: tuple[DiagnosticItem, ...]

    @property
    def ready(self) -> bool:
        """仅在全部必需检查通过时返回 True。"""
        return all(item.ok for item in self.items)
```

`run_preflight()` 必须检查资源清单、清单内每个 SHA-256、激活规则包、规则包校验、主体清单、`soffice.exe` 和用户数据目录可写性。一次运行返回全部问题，不在首个错误处提前结束。

资源清单校验使用以下稳定接口，Task 8 的构建测试也调用同一函数：

```python
def verify_resource_manifest(app_root: Path, manifest_path: Path) -> list[DiagnosticItem]:
    """校验清单列出的文件；返回全部缺失、大小或哈希异常，不修改文件。"""
    items: list[DiagnosticItem] = []
    if not manifest_path.is_file():
        return [DiagnosticItem("manifest_missing", False, "资源清单缺失", str(manifest_path))]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest.get("files", []):
        path = app_root / entry["path"]
        if not path.is_file():
            items.append(DiagnosticItem("resource_missing", False, "程序资源缺失", str(path)))
        elif path.stat().st_size != entry["size"] or sha256_file(path) != entry["sha256"]:
            items.append(DiagnosticItem("resource_hash_mismatch", False, "程序资源校验失败", str(path)))
    return items
```

- [ ] **Step 5: 运行路径和诊断测试**

Run: `python -m pytest 审核器/tests/desktop/test_paths_and_diagnostics.py -q`

Expected: 全部 PASS。

- [ ] **Step 6: 记录建议提交信息，不执行提交**

建议：`feat: 增加桌面端资源定位和启动诊断`

---

### Task 4: 实现历史记录持久化

**Files:**
- Create: `审核器/src/risk_audit_desktop/history.py`
- Create: `审核器/tests/desktop/test_history.py`

**Interfaces:**
- Consumes: `%LOCALAPPDATA%\RiskAuditDesktop\history.json`。
- Produces: `HistoryEntry`、`HistoryDisplayStatus`、`HistoryStore.load()`、`HistoryStore.append()`、`HistoryStore.resolve_status()`。

- [ ] **Step 1: 编写正常、损坏和失效路径测试**

```python
def test_history_round_trip_uses_atomic_json(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    entry = HistoryEntry(task_id="run-1", input_name="中文材料", output_root=str(tmp_path / "结果"),
                         status="completed", completed_at="2026-09-23T14:32:00+08:00",
                         findings=137, warnings=2)
    store.append(entry)
    assert store.load() == [entry]
    assert not (tmp_path / "history.json.tmp").exists()


def test_corrupt_history_does_not_prevent_startup(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{bad json", encoding="utf-8")
    result = HistoryStore(path).load()
    assert result == []
    assert list(tmp_path.glob("history.corrupt-*.json"))


def test_missing_output_is_reported_without_deleting_entry(tmp_path):
    entry = HistoryEntry(task_id="run-2", input_name="已移动材料",
                         output_root=str(tmp_path / "moved"), status="completed",
                         completed_at="2026-09-23T15:00:00+08:00", findings=1, warnings=0)
    assert HistoryStore.resolve_status(entry).output_available is False
```

- [ ] **Step 2: 实现历史数据模型和原子写入**

```python
@dataclass(frozen=True)
class HistoryEntry:
    """保存任务摘要，不保存原始材料内容。"""

    task_id: str
    input_name: str
    output_root: str
    status: str
    completed_at: str
    findings: int
    warnings: int


class HistoryStore:
    """管理本机历史记录；path 为 history.json 的绝对路径。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[HistoryEntry]:
        """读取记录；损坏文件保留为带时间戳的副本并返回空列表。"""
        if not self.path.is_file():
            return []
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
            return [HistoryEntry(**value) for value in values]
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            corrupt = self.path.with_name(f"history.corrupt-{stamp}.json")
            self.path.replace(corrupt)
            return []

    def append(self, entry: HistoryEntry) -> None:
        """追加记录并在同一目录原子替换目标文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entries = [entry, *self.load()][:100]
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps([asdict(item) for item in entries], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @staticmethod
    def resolve_status(entry: HistoryEntry) -> "HistoryDisplayStatus":
        """检查历史结果路径是否仍存在，不修改原记录。"""
        return HistoryDisplayStatus(entry=entry, output_available=Path(entry.output_root).exists())
```

同时定义不可变 `HistoryDisplayStatus(entry: HistoryEntry, output_available: bool)`，供 QML 区分“审核失败”和“结果路径已失效”。

最多保留 100 条记录；截断发生在成功写入新文件时。不得记录输入目录完整路径、文件清单或材料内容，只记录显示名称和输出路径。

- [ ] **Step 3: 运行历史记录测试**

Run: `python -m pytest 审核器/tests/desktop/test_history.py -q`

Expected: 全部 PASS。

- [ ] **Step 4: 记录建议提交信息，不执行提交**

建议：`feat: 增加桌面端本机审核历史记录`

---

### Task 5: 实现 JSON 行解析和审核进程管理

**Files:**
- Modify: `审核器/pyproject.toml`
- Create: `审核器/requirements-desktop.lock`
- Create: `审核器/src/risk_audit_desktop/process_protocol.py`
- Create: `审核器/src/risk_audit_desktop/task_manager.py`
- Create: `审核器/src/risk_audit_desktop/platform_windows.py`
- Create: `审核器/tests/desktop/test_process_protocol.py`
- Create: `审核器/tests/desktop/test_task_manager.py`

**Interfaces:**
- Consumes: `AuditTaskRequest` 和 `--worker --request <path>` 入口。
- Produces: `WorkerStreamParser.feed()`、`TaskManager.start()`、`TaskManager.cancel()`；Qt 信号 `event_received`、`task_finished`、`task_failed`。

- [ ] **Step 1: 固定桌面依赖**

扩展 `pyproject.toml` 已存在的两个表，不创建重复表头：

```toml
[project.optional-dependencies]
test = ["pytest==8.4.2"]
semantic = ["numpy==2.3.5", "onnxruntime==1.22.1", "tokenizers==0.21.4"]
desktop = ["PySide6==6.8.3"]
desktop-build = ["PySide6==6.8.3", "Nuitka==2.6.8", "ordered-set==4.1.0", "zstandard==0.23.0"]

[project.scripts]
risk-audit = "risk_audit.cli:main"
risk-audit-desktop = "risk_audit_desktop.app:main"
```

`requirements-desktop.lock` 写入同样的固定版本。安装并运行 `python -m pip check`，Expected: `No broken requirements found.`

- [ ] **Step 2: 编写半行和非法事件测试**

```python
def test_parser_waits_for_complete_utf8_line():
    parser = WorkerStreamParser()
    assert parser.feed('{"event":"business_'.encode("utf-8")) == []
    events = parser.feed('progress","message":"中文"}\n'.encode("utf-8"))
    assert events == [{"event": "business_progress", "message": "中文"}]


def test_parser_reports_invalid_json_without_discarding_following_line():
    parser = WorkerStreamParser()
    events = parser.feed(b'not-json\n{"event":"worker_completed","result":{}}\n')
    assert events[0]["event"] == "protocol_error"
    assert events[1]["event"] == "worker_completed"
```

- [ ] **Step 3: 实现增量 UTF-8 JSON 行解析器**

```python
class WorkerStreamParser:
    """把 QProcess 输出的任意字节分块转换为完整 JSON 事件。"""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        """追加字节并返回所有完整事件；不完整末行保留到下一次调用。"""
        self._buffer += self._decoder.decode(chunk)
        lines = self._buffer.split("\n")
        self._buffer = lines.pop()
        return [self._parse_line(line) for line in lines if line.strip()]

    def finish(self) -> list[dict[str, Any]]:
        """结束流并将残余非法内容转换为 protocol_error。"""
        self._buffer += self._decoder.decode(b"", final=True)
        if not self._buffer.strip():
            return []
        line, self._buffer = self._buffer, ""
        return [self._parse_line(line)]

    @staticmethod
    def _parse_line(line: str) -> dict[str, Any]:
        """解析单行 JSON；非法内容转换为可显示的协议错误事件。"""
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            return {"event": "protocol_error", "message": f"工作进程返回无效事件: {error.msg}"}
        if not isinstance(value, dict) or not isinstance(value.get("event"), str):
            return {"event": "protocol_error", "message": "工作进程事件缺少 event 字段"}
        return value
```

- [ ] **Step 4: 编写进程生命周期测试**

使用 `FakeProcess` 注入 `TaskManager(process_factory=fake_process_factory)`，覆盖：

```python
def test_manager_rejects_second_running_task(manager, request):
    manager.start(request)
    with pytest.raises(RuntimeError, match="已有审核任务"):
        manager.start(request)


def test_nonzero_exit_without_terminal_event_is_failure(manager, fake_process, request):
    manager.start(request)
    fake_process.finish(exit_code=2)
    assert manager.state == "failed"
    assert manager.last_error.code == "worker_unexpected_exit"


def test_cancelled_process_never_becomes_success(manager, fake_process, request):
    manager.start(request)
    manager.cancel()
    fake_process.finish(exit_code=1)
    assert manager.state == "cancelled"
```

- [ ] **Step 5: 实现 `TaskManager` 和同一 EXE 工作模式**

```python
class TaskManager(QObject):
    """管理唯一审核工作进程，并把 JSON 事件转换为 Qt 信号。"""

    event_received = Signal(dict)
    task_finished = Signal(dict)
    task_failed = Signal(str, str)
    state_changed = Signal(str)

    def start(self, request: AuditTaskRequest) -> None:
        """写入请求文件并启动当前程序的 --worker 模式。"""
        if self.state == "running":
            raise RuntimeError("已有审核任务正在运行")
        self.task_root.mkdir(parents=True, exist_ok=True)
        request_path = self.task_root / f"{request.run_id or uuid.uuid4()}.json"
        request_path.write_text(json.dumps(request.to_dict(), ensure_ascii=False), encoding="utf-8")
        process = self._process_factory(self)
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.finished.connect(self._process_finished)
        process.setProgram(self._worker_command[0])
        process.setArguments([*self._worker_command[1:], "--worker", "--request", str(request_path)])
        self._process = process
        self._set_state("running")
        process.start()

    def cancel(self) -> None:
        """终止工作进程树并将任务标记为 cancelled。"""
        if self.state != "running" or self._process is None:
            return
        process_id = int(self._process.processId())
        self._set_state("cancelled")
        terminate_process_tree(process_id)
```

开发模式命令为 `[sys.executable, "-m", "risk_audit_desktop.app", "--worker", "--request", request_path]`；Nuitka 模式命令为 `[sys.executable, "--worker", "--request", request_path]`。全部参数通过 `QProcess.setArguments()` 传递，不拼接命令字符串。

- [ ] **Step 6: 实现 Windows 进程树终止**

```python
def terminate_process_tree(process_id: int, *, system_name: str = platform.system()) -> None:
    """终止审核工作进程及其 LibreOffice 子进程；process_id 为根进程编号。"""
    if system_name == "Windows":
        subprocess.run(["taskkill", "/PID", str(process_id), "/T", "/F"],
                       check=False, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
```

非 Windows 开发环境使用 `os.kill(process_id, signal.SIGTERM)`。测试必须注入 runner，不实际终止系统进程。

- [ ] **Step 7: 运行进程协议测试**

Run: `python -m pytest 审核器/tests/desktop/test_process_protocol.py 审核器/tests/desktop/test_task_manager.py -q`

Expected: 全部 PASS，测试过程中不出现真实子进程或终端窗口。

- [ ] **Step 8: 记录建议提交信息，不执行提交**

建议：`feat: 增加桌面审核进程管理器`

---

### Task 6: 实现桌面应用控制器和平台操作

**Files:**
- Create: `审核器/src/risk_audit_desktop/controller.py`
- Modify: `审核器/src/risk_audit_desktop/platform_windows.py`
- Create: `审核器/tests/desktop/test_controller.py`

**Interfaces:**
- Consumes: `DesktopPaths`、`run_preflight()`、`HistoryStore`、`TaskManager`。
- Produces: QML 上下文对象 `DesktopController`，包含属性 `page_state`、`input_path`、`output_path`、`progress`、`status_message`、`result_summary`、`history_items`、`diagnostic_items`、`enabled_rules` 和 `include_hidden`。

- [ ] **Step 1: 编写启动保护和状态归并测试**

```python
def test_controller_blocks_start_when_preflight_failed(controller, fake_manager):
    controller.set_input_path("D:/材料")
    controller.set_output_path("D:/结果")
    controller.start_audit()
    assert fake_manager.started_requests == []
    assert controller.page_state == "error"


def test_controller_maps_progress_event(controller):
    controller.handle_event({
        "event": "business_progress", "completed": 3, "total": 8,
        "entity_name": "示例单位", "business_name": "设备管理",
        "message": "正在执行审核规则",
    })
    assert controller.progress == pytest.approx(0.375)
    assert controller.current_business == "设备管理"


def test_failed_worker_returns_controller_to_retryable_state(controller):
    controller.handle_failure("worker_unexpected_exit", "工作进程异常退出")
    assert controller.page_state == "error"
    assert controller.can_start is True
```

- [ ] **Step 2: 实现控制器属性和槽函数**

```python
class DesktopController(QObject):
    """连接 QML 和桌面服务；不直接执行审核业务。"""

    state_changed = Signal()

    @Slot(str)
    def set_input_path(self, value: str) -> None:
        """设置输入目录；value 为 QML 传入的本地路径。"""
        self._input_path = str(Path(value).resolve()) if value else ""
        self.state_changed.emit()

    @Slot(str)
    def set_output_path(self, value: str) -> None:
        """设置输出目录；value 为 QML 传入的本地路径。"""
        self._output_path = str(Path(value).resolve()) if value else ""
        self.state_changed.emit()

    @Slot()
    def start_audit(self) -> None:
        """完成预检和目录保护后启动唯一审核任务。"""
        report = run_preflight(self._paths)
        if not report.ready:
            self._page_state = "error"
            self._error_message = "；".join(item.message for item in report.items if not item.ok)
            self.state_changed.emit()
            return
        request = self._build_request(self._input_path, self._output_path)
        self._task_manager.start(request)
        self._page_state = "running"
        self.state_changed.emit()

    @Slot()
    def cancel_audit(self) -> None:
        """请求任务管理器取消当前审核任务。"""
        self._task_manager.cancel()
        self._page_state = "setup"
        self._status_message = "任务已取消，已完整生成的结果仍会保留。"
        self.state_changed.emit()
```

控制器只保存界面状态；任务成功时从 `worker_completed.result` 建立历史记录，失败或取消不得记录为成功。

`handle_event(event)` 只接受 Task 1 和 Task 2 声明的事件类型；未知事件写入诊断日志但不崩溃。`handle_failure(code, message)` 清除运行中标志、保留错误编号并把 `can_start` 恢复为 `True`。高级选项通过 `set_rule_enabled(rule_id, enabled)` 和 `set_include_hidden(enabled)` 更新请求，第一版只在界面开放规则 `R03`。

- [ ] **Step 3: 实现安全打开路径与诊断导出**

```python
def open_local_path(path: Path) -> None:
    """使用 Windows Explorer 打开本地文件或目录；path 必须已经存在。"""
    if not path.exists():
        raise FileNotFoundError(f"路径不存在: {path}")
    os.startfile(path)  # type: ignore[attr-defined]
```

测试通过注入 opener，不能在自动化测试中实际打开 Explorer。诊断导出为 UTF-8 JSON，仅包含版本、资源检查、运行阶段、错误代码和日志路径，不包含输入路径、文件名或材料内容。

- [ ] **Step 4: 覆盖输出保护边界**

增加参数化测试，覆盖输入等于输出、输出位于输入内、输入位于输出内、输出已经存在、输出父目录不可写。每种情况都断言 `TaskManager.start()` 未被调用，界面显示具体中文原因。

- [ ] **Step 5: 运行控制器测试**

Run: `python -m pytest 审核器/tests/desktop/test_controller.py -q`

Expected: 全部 PASS。

- [ ] **Step 6: 记录建议提交信息，不执行提交**

建议：`feat: 增加桌面端应用状态控制器`

---

### Task 7: 实现 PySide6/QML 桌面界面

**Files:**
- Create: `审核器/src/risk_audit_desktop/app.py`
- Create: `审核器/src/risk_audit_desktop/assets/app-icon.svg`
- Create: `审核器/src/risk_audit_desktop/qml/Main.qml`
- Create: `审核器/src/risk_audit_desktop/qml/components/FolderCard.qml`
- Create: `审核器/src/risk_audit_desktop/qml/pages/AuditPage.qml`
- Create: `审核器/src/risk_audit_desktop/qml/pages/HistoryPage.qml`
- Create: `审核器/src/risk_audit_desktop/qml/pages/DiagnosticsPage.qml`
- Create: `审核器/tests/desktop/test_qml_smoke.py`

**Interfaces:**
- Consumes: `DesktopController` 的 Qt 属性、信号和槽。
- Produces: `main(argv: list[str] | None = None) -> int`，正常模式加载 QML，`--worker` 模式直接运行 Task 2 工作入口。

- [ ] **Step 1: 编写统一入口测试**

```python
def test_worker_mode_does_not_create_qapplication(monkeypatch, tmp_path):
    from risk_audit_desktop import app

    called = []
    monkeypatch.setattr(app, "worker_main", lambda argv: called.append(argv) or 0)
    code = app.main(["--worker", "--request", str(tmp_path / "request.json")])
    assert code == 0
    assert called == [["--request", str(tmp_path / "request.json")]]
```

- [ ] **Step 2: 实现 GUI/worker 统一入口**

```python
def main(argv: list[str] | None = None) -> int:
    """启动桌面界面或工作模式；argv 为可选命令行参数。"""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["--worker"]:
        return worker_main(arguments[1:])

    application = QApplication([sys.argv[0], *arguments])
    app_root, portable = resolve_application_root()
    paths = DesktopPaths.discover(app_root=app_root, environ=os.environ, portable=portable)
    controller = build_controller(paths)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("desktopController", controller)
    engine.load(QUrl.fromLocalFile(str(paths.qml_root / "Main.qml")))
    if not engine.rootObjects():
        return 2
    return application.exec()


def resolve_application_root() -> tuple[Path, bool]:
    """返回程序根目录及是否为免安装构建；开发模式使用仓库根目录。"""
    executable_root = Path(sys.executable).resolve().parent
    if (executable_root / "resource-manifest.json").is_file():
        return executable_root, True
    return Path(__file__).resolve().parents[3], False
```

开发模式通过 `RISK_AUDIT_SOFFICE` 指定本机 LibreOffice；便携模式忽略该变量，始终使用随包版本。开发机没有该变量时界面仍可打开，但预检必须显示 LibreOffice 缺失并禁止开始审核。

- [ ] **Step 3: 实现应用窗口和主导航**

`Main.qml` 使用 `ApplicationWindow`、左侧导航和 `StackLayout`。窗口最小尺寸为 960×680，默认尺寸为 1180×760；导航固定包含“开始审核”“历史记录”“设置与诊断”。颜色、圆角和间距集中定义在根窗口只读属性中，不在各页面重复硬编码。

```qml
ApplicationWindow {
    id: window
    width: 1180
    height: 760
    minimumWidth: 960
    minimumHeight: 680
    visible: true
    title: qsTr("风控矩阵审核器")

    readonly property color accentColor: "#1769E0"
    readonly property color backgroundColor: palette.window
    readonly property int cardRadius: 14

    RowLayout {
        anchors.fill: parent
        NavigationPanel { Layout.preferredWidth: 208 }
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: desktopController.currentSection
            AuditPage { }
            HistoryPage { }
            DiagnosticsPage { }
        }
    }
}
```

- [ ] **Step 4: 实现文件夹卡片和审核三状态**

`FolderCard.qml` 暴露 `title`、`pathText`、`actionText` 和 `clicked()`。`AuditPage.qml` 使用 `StackLayout` 显示 `setup`、`running`、`result`、`error` 四种状态；文件夹选择使用 `FolderDialog`，路径通过 `selectedFolder.toLocalFile()` 传给控制器。

`FolderCard.qml` 还包含 `DropArea`：只接受单个本地目录 URL，悬停时显示强调色，释放后调用 `folderDropped(string localPath)`；文件和多个 URL 必须显示“请选择一个文件夹”，不能静默取第一项。准备页的“高级选项”默认折叠，仅包含规则三临时开关和“读取隐藏工作表”开关。

```qml
StackLayout {
    currentIndex: ({"setup": 0, "running": 1, "result": 2, "error": 3})[
        desktopController.pageState
    ] ?? 0

    AuditSetupPane { onStartRequested: desktopController.startAudit() }
    AuditRunningPane {
        progress: desktopController.progress
        onCancelRequested: cancelDialog.open()
    }
    AuditResultPane { result: desktopController.resultSummary }
    AuditErrorPane { message: desktopController.errorMessage }
}
```

运行页必须展示四阶段进度、当前主体/业务/文件、已完成数量和耗时；结果页必须区分成功、部分完成和失败，不能只依赖绿色颜色表达状态。

- [ ] **Step 5: 实现历史和诊断页面**

`HistoryPage.qml` 使用 `ListView` 展示任务摘要，对失效结果路径显示“结果已移动或删除”；`DiagnosticsPage.qml` 展示全部 `DiagnosticItem`，失败项显示路径和建议，提供“重新检查”和“导出诊断报告”按钮。`Main.qml` 内部定义导航组件，`AuditPage.qml` 内部定义准备、运行、结果和错误四个面板，避免引用未声明的 QML 类型。运行页“最小化到后台”直接调用根窗口 `showMinimized()`。

- [ ] **Step 6: 编写离屏 QML 冒烟测试**

```python
@pytest.mark.usefixtures("qapplication")
def test_main_qml_loads_without_errors(qml_engine, desktop_controller, qml_root):
    qml_engine.rootContext().setContextProperty("desktopController", desktop_controller)
    warnings = []
    qml_engine.warnings.connect(lambda values: warnings.extend(values))
    qml_engine.load(QUrl.fromLocalFile(str(qml_root / "Main.qml")))
    assert qml_engine.rootObjects()
    assert warnings == []
```

测试命令设置 `QT_QPA_PLATFORM=offscreen`；再用 QML 对象名断言准备、运行、结果、历史和诊断页面均可实例化。

- [ ] **Step 7: 运行桌面界面测试和手工检查**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest 审核器/tests/desktop/test_qml_smoke.py -q`

Expected: PASS，无 QML warning。

手工运行：`python -m risk_audit_desktop.app`。核对 100%、125%、150% Windows 缩放下无裁切，键盘 Tab 可到达所有操作，浅色和深色主题文字对比清晰。

- [ ] **Step 8: 记录建议提交信息，不执行提交**

建议：`feat: 增加Windows桌面端现代化界面`

---

### Task 8: 实现免安装包资源收集、校验和 Windows 构建

**Files:**
- Create: `审核器/tools/build_windows_desktop.py`
- Create: `审核器/tests/desktop/test_portable_build.py`
- Modify: `审核器/src/risk_audit_desktop/diagnostics.py`
- Create: `审核器/Windows桌面版使用说明.md`

**Interfaces:**
- Consumes: 项目根目录、激活规则包、基准注册表、LibreOffice 外部构建输入目录。
- Produces: `collect_project_resources()`、`write_resource_manifest()`、`build_portable_zip()` 和 `风控矩阵审核器-v2.0.0-Windows-x64.zip`。

- [ ] **Step 1: 编写资源收集测试**

```python
def test_collects_only_active_pack_and_referenced_baselines(fake_project, stage_root):
    collect_project_resources(fake_project, stage_root)
    assert (stage_root / "resources/project/审核器/rulepacks/active.json").is_file()
    assert (stage_root / "resources/project/审核器/rulepacks/releases/2.0.0/manifest.json").is_file()
    assert not (stage_root / "resources/project/审核器/rulepacks/releases/1.0.0").exists()
    assert (stage_root / "resources/project/审核/基准.xlsx").is_file()
    assert not (stage_root / "resources/project/审核器/models").exists()


def test_manifest_detects_missing_and_changed_files(stage_root):
    manifest_path = write_resource_manifest(stage_root)
    target = next(path for path in stage_root.rglob("*.json") if path != manifest_path)
    target.write_text("changed", encoding="utf-8")
    errors = verify_resource_manifest(stage_root, manifest_path)
    assert any(error.code == "resource_hash_mismatch" for error in errors)
```

- [ ] **Step 2: 实现资源收集**

构建脚本读取 `审核器/rulepacks/active.json`，只复制激活版本、`audit-config.json`、主体清单和激活规则 `baseline_registry.json` 引用的基准。复制时保留相对于项目根的原路径。若语义配置 `enabled` 为 `false`，明确不复制 `审核器/models`。

```python
def collect_project_resources(project_root: Path, stage_root: Path) -> None:
    """收集桌面审核必需资源；两个参数分别为源码根和暂存根目录。"""
    active = json.loads((project_root / "审核器/rulepacks/active.json").read_text(encoding="utf-8"))
    version = active["version"]
    source_pack = project_root / "审核器/rulepacks/releases" / version
    target_project = stage_root / "resources/project"
    shutil.copy2(project_root / "审核器/rulepacks/active.json",
                 target_project / "审核器/rulepacks/active.json")
    shutil.copytree(source_pack, target_project / "审核器/rulepacks/releases" / version)
    registry = json.loads((source_pack / "baseline_registry.json").read_text(encoding="utf-8"))
    for item in registry["entries"]:
        source = project_root / item["path"]
        target = target_project / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copy2(project_root / "审核/会计主体清单20260907.xlsx",
                 target_project / "审核/会计主体清单20260907.xlsx")
    shutil.copy2(project_root / "audit-config.json", target_project / "audit-config.json")


def write_resource_manifest(stage_root: Path) -> Path:
    """为暂存目录全部只读资源写出相对路径、大小和 SHA-256。"""
    manifest_path = stage_root / "resource-manifest.json"
    entries = [
        {"path": path.relative_to(stage_root).as_posix(), "size": path.stat().st_size,
         "sha256": sha256_file(path)}
        for path in sorted(stage_root.rglob("*"))
        if path.is_file() and path != manifest_path
    ]
    manifest_path.write_text(json.dumps({"files": entries}, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return manifest_path
```

- [ ] **Step 3: 实现 Windows 构建命令**

构建脚本使用参数列表运行 Nuitka，禁止 `shell=True`：

```python
command = [
    sys.executable, "-m", "nuitka",
    "--standalone",
    "--enable-plugin=pyside6",
    "--windows-console-mode=disable",
    "--output-filename=风控矩阵审核器.exe",
    f"--output-dir={build_root}",
    str(source_root / "risk_audit_desktop/app.py"),
]
subprocess.run(command, check=True)
```

编译完成后将 QML、图标、项目资源、第三方许可证和 `--libreoffice-root` 指定的固定 LibreOffice 目录复制到暂存目录，再生成 `resource-manifest.json` 和 ZIP。

- [ ] **Step 4: 校验 LibreOffice 构建输入**

`--libreoffice-root` 必须包含：

```text
program/soffice.exe
program/soffice.bin
program/fundamental.ini
LICENSE
NOTICE
```

缺少任意文件立即失败；构建脚本不得从网络下载 LibreOffice。这样构建过程仍然完全可审计、可离线复现。

- [ ] **Step 5: 编写免安装使用说明**

`Windows桌面版使用说明.md` 必须写明：完整解压、双击主程序、不能单独复制 EXE、输入输出目录要求、取消任务的影响、Windows 安全提示、如何导出诊断报告，以及程序完全离线且不修改原文件。

- [ ] **Step 6: 运行资源构建单元测试**

Run: `python -m pytest 审核器/tests/desktop/test_portable_build.py -q`

Expected: 全部 PASS，测试只使用伪造的小型 LibreOffice 目录，不创建真实大包。

- [ ] **Step 7: 在 Windows x64 构建真实 ZIP**

Run:

```powershell
py -3.12 -m venv .venv-desktop
.\.venv-desktop\Scripts\python.exe -m pip install -r .\审核器\requirements.lock
.\.venv-desktop\Scripts\python.exe -m pip install -r .\审核器\requirements-desktop.lock
.\.venv-desktop\Scripts\python.exe -m pip install --no-deps .\审核器
.\.venv-desktop\Scripts\python.exe .\审核器\tools\build_windows_desktop.py `
  --project-root . `
  --libreoffice-root C:\BuildDependencies\LibreOffice-x64 `
  --output .\审核器\build\desktop
```

Expected: 生成 `审核器\build\desktop\风控矩阵审核器-v2.0.0-Windows-x64.zip`，解压目录只有一个用户入口 EXE，不需要安装。

- [ ] **Step 8: 记录建议提交信息，不执行提交**

建议：`build: 增加Windows免安装桌面包构建流程`

---

### Task 9: 完成端到端验收与交付检查

**Files:**
- Modify: `delivery_test.py`
- Modify: `使用说明.md`
- Create: `审核器/tests/desktop/test_desktop_delivery.py`
- Create: `审核器/build/desktop/验收记录.md`（构建产物，不纳入 Git）

**Interfaces:**
- Consumes: Task 1-8 的桌面程序、测试和 ZIP。
- Produces: 可重复执行的桌面交付测试入口和 Windows 实机验收记录。

- [ ] **Step 1: 添加桌面交付测试入口**

在 `delivery_test.py` 增加桌面测试组，但不要求非 Windows 开发机生成 EXE：

```python
DESKTOP_TESTS = [
    "审核器/tests/test_progress_events.py",
    "审核器/tests/desktop/test_contracts.py",
    "审核器/tests/desktop/test_worker.py",
    "审核器/tests/desktop/test_paths_and_diagnostics.py",
    "审核器/tests/desktop/test_history.py",
    "审核器/tests/desktop/test_process_protocol.py",
    "审核器/tests/desktop/test_task_manager.py",
    "审核器/tests/desktop/test_controller.py",
    "审核器/tests/desktop/test_qml_smoke.py",
    "审核器/tests/desktop/test_portable_build.py",
]
```

Windows 环境额外执行 `test_desktop_delivery.py`；非 Windows 环境必须明确显示“跳过 Windows 二进制验收”，不能把跳过计作通过。

- [ ] **Step 2: 编写 Windows ZIP 黑盒测试**

测试解压 ZIP 到含中文和空格的临时目录，以 `--worker` 模式传入构造请求，断言：

```python
completed = subprocess.run(
    [str(executable), "--worker", "--request", str(request_file)],
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=300,
)
events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
assert completed.returncode == 0
assert events[-1]["event"] == "worker_completed"
assert events[-1]["result"]["write_completed"] is True
```

再覆盖损坏资源清单、缺失 LibreOffice、输出已存在、非法请求 JSON 和工作进程取消。

- [ ] **Step 3: 运行全量自动化测试**

Run: `python -m pytest 审核器/tests -q`

Expected: 全部适用测试 PASS；已有明确依赖缺失的测试只允许按现有交付测试规则排除，不允许为桌面改造新增静默排除。

Run: `python delivery_test.py`

Expected: 核心交付测试及桌面跨平台测试 PASS，并明确列出 Windows 二进制测试状态。

- [ ] **Step 4: 在干净 Windows 10/11 机器执行实机验收**

逐项记录：

1. 无 Python、无 LibreOffice、普通用户权限下启动。
2. 中文用户名、中文路径、空格路径和长路径。
3. Windows Defender 开启且无控制台闪窗。
4. `.xlsx`、`.xls`、BIFF `.et`、OOXML `.et`、`.docx`、`.doc`、`.pdf`。
5. 正常完成、部分完成、主体冲突、取消、工作进程崩溃。
6. 100%、125%、150% 显示缩放和深浅色模式。
7. 原材料哈希运行前后保持不变。
8. 桌面结果与相同规则包的命令行基准结果一致。

- [ ] **Step 5: 更新总使用说明**

在根 `使用说明.md` 开头增加“普通用户 Windows 桌面版”入口，命令行安装和开发方式保留在后续章节。明确桌面包的规则版本从 `active.json` 读取，不在界面代码中硬编码。

- [ ] **Step 6: 完成最终差异与资源检查**

Run: `git diff --check`

Expected: 无空白错误。

Run: `git status --short`

Expected: 只包含本计划列出的源码、测试、说明和设计文件；`审核器/build/desktop` 继续由 `.gitignore` 排除。

- [ ] **Step 7: 记录建议提交信息，不执行提交**

建议：`feat: 完成Windows免安装桌面端交付`

---

## Execution Notes

- 开发阶段可在 macOS 完成事件、任务契约、诊断、历史记录、控制器和 QML 离屏测试。
- 最终 Nuitka 编译、内置 LibreOffice 校验、Windows Defender 和 Office/WPS 文件占用行为必须在 Windows x64 执行。
- 若当前没有可用 Windows 构建机，Task 1-7 可以完成，Task 8 的真实 ZIP 构建和 Task 9 的 Windows 实机验收必须保持未完成状态，不能宣称已经交付。
- 实现期间如发现现有 `runner.py` 的进度挂点需要重构，允许提取小型私有函数，但不得借机修改审核规则或做无关重构。
