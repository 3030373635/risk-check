# Web 审核任务汇总性能与最小产物 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Web 审核任务的统计汇总改为线性复杂度和单次归集，并把终态 `.task` 缩减为用户确认需要的日志、未审核文件和未完成项。

**Architecture:** 审核核心通过统一 `ArtifactPolicy` 区分 CLI 详细产物和 Web 最小产物；写回状态在最小模式下只保存在内存，最终只落盘两份用户明细。统计模块先建立精确位置索引并生成不可变 `AuditStatisticsData`，Excel 与 HTML 共用该对象；Web Worker 在发布终态前执行幂等白名单清理作为防御性收尾。

**Tech Stack:** Python 3.11、dataclasses/Enum、openpyxl、lxml、FastAPI Worker、SQLite、pytest 8.4.2。

**Spec:** `docs/superpowers/specs/2026-10-20-minimal-task-artifacts-design.md`

## Global Constraints

- 不兼容依赖旧详细 `.task` 目录结构的代码、旧测试或外部脚本，不增加旧逻辑分支。
- REST API 路径、任务状态集合和输出业务 Excel 目录结构保持不变。
- Web 终态只保留业务 Excel、`审核统计表.xlsx/html`、`worker.log`、`audit.log`、`未审核文件.json` 和 `未完成项.json`。
- 变量统一使用 `artifact_policy` 表示产物策略，不引入同义布尔参数。
- 所有新增或修改函数必须提供中文注释及参数解释，索引、清理和状态发布关键行必须有中文注释。
- 不新增第三方依赖。
- 按 TDD 顺序执行：先观察失败，再写最小实现，再运行相关回归。
- 遵照仓库 `AGENTS.md`，执行期间不运行 `git commit`；每个任务只检查 diff，最终输出一个中文规范 commit 信息。
- 保留工作区中现有未跟踪文档，不修改与本计划无关的文件。

## Review Focus

- 同一精确位置存在统计路由不一致的重复 Finding 时必须失败，不得随机选择；Task 2 的冲突测试覆盖。
- `file_path` 为空的资料级意见必须继续路由到真实业务；Task 2 的资料级回退测试覆盖。
- 成功、部分完成、失败和取消任务缺少可选明细时，清理仍须保留日志且不能伪造成功；Task 5 的 Worker 参数化测试覆盖。
- `.task` 内出现符号链接或 Windows 重解析点时不得跟随到输出根外；Task 5 的路径安全测试覆盖。
- `未审核文件.json` 和 `未完成项.json` 都是空数组时，现有 API 仍须返回 `count=0`；Task 5 的 API 回归测试覆盖。

---

## File Structure

- Create: `risk-audit/src/risk_audit/artifact_policy.py` — 定义统一产物策略枚举，不承载写入逻辑。
- Modify: `risk-audit/src/risk_audit/runner.py` — 传播产物策略、选择持久/临时报告根、跳过详细报告、单次归集统计。
- Modify: `risk-audit/src/risk_audit/writer.py` — 支持只更新 `OutputState` 而不持久化 ownership、pending 和增量未审核文件。
- Modify: `risk-audit/src/risk_audit/audit_statistics.py` — 定义不可变统计数据、精确索引和两种格式渲染入口。
- Modify: `risk-audit/src/risk_audit/audit_statistics_html.py` — 接收不可变统计数据或其稳定字典投影。
- Create: `risk-audit/src/risk_audit_web/task_artifacts.py` — 负责 Web `.task` 白名单校验、清理和保留文件验证。
- Modify: `risk-audit/src/risk_audit_web/worker.py` — 固定传入最小策略、发布显式结果路径、终态前清理。
- Modify: `risk-audit/tests/test_writer.py` — 覆盖纯内存写回元数据。
- Modify: `risk-audit/tests/test_audit_statistics.py` — 覆盖精确索引、冲突、资料级路由和共享统计数据。
- Modify: `risk-audit/tests/test_incremental_output.py` — 覆盖最小产物 Runner 行为。
- Create: `risk-audit/tests/web/test_task_artifacts.py` — 覆盖白名单清理和路径安全。
- Modify: `risk-audit/tests/web/test_worker.py` — 覆盖策略透传、各终态清理和结果摘要。
- Modify: `risk-audit/tests/web/test_tasks_api.py` — 覆盖空明细 API。
- Create: `risk-audit/tools/benchmark_task_finalization.py` — 生成高频同文本合成数据并测量统计归集耗时。

### Task 1: 统一产物策略与内存写回状态

**Files:**
- Create: `risk-audit/src/risk_audit/artifact_policy.py`
- Modify: `risk-audit/src/risk_audit/runner.py:154-237,443-475`
- Modify: `risk-audit/src/risk_audit/writer.py:42-91,387-465,592-607`
- Modify: `risk-audit/src/risk_audit_web/worker.py:238-258`
- Modify: `risk-audit/tests/test_writer.py`
- Modify: `risk-audit/tests/test_incremental_output.py`
- Modify: `risk-audit/tests/web/test_worker.py`

**Interfaces:**
- Produces: `ArtifactPolicy(str, Enum)`，成员 `DETAILED = "detailed"`、`MINIMAL = "minimal"`。
- Produces: `audit(..., artifact_policy: ArtifactPolicy = ArtifactPolicy.DETAILED) -> dict[str, Any]`。
- Produces: `write_outputs(..., metadata_dir: Path | None, ...) -> tuple[list[dict[str, Any]], dict[str, Any]]`；`None` 表示只更新 `OutputState`。
- Consumes: 现有 `OutputState`、`write_compact_json` 和 Worker `selected_audit` 调用约定。

- [ ] **Step 1: 写策略默认值和 Worker 透传失败测试**

在 `tests/web/test_worker.py` 新增 `test_worker_uses_minimal_artifact_policy`，用替换审核函数捕获 `kwargs["artifact_policy"]`，断言它等于 `ArtifactPolicy.MINIMAL`；在 `tests/test_incremental_output.py` 新增 `test_audit_defaults_to_detailed_artifact_policy`，替换 `_audit` 捕获参数并断言未显式传参时为 `ArtifactPolicy.DETAILED`。

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd risk-audit && .venv/bin/python -m pytest tests/web/test_worker.py::test_worker_uses_minimal_artifact_policy tests/test_incremental_output.py::test_audit_defaults_to_detailed_artifact_policy -q`

Expected: FAIL，原因是 `ArtifactPolicy` 或 `artifact_policy` 参数尚不存在。

- [ ] **Step 3: 实现策略枚举和参数传播**

创建 `artifact_policy.py`；为 `audit`、`_audit` 增加同名关键字参数并在 Worker 中固定传入 `ArtifactPolicy.MINIMAL`。CLI 不传参，继续使用详细策略。

- [ ] **Step 4: 写纯内存写回元数据失败测试**

在 `tests/test_writer.py` 新增 `test_write_outputs_keeps_metadata_in_memory_when_directory_is_none`：构造一份可写文件和 Finding，传入 `metadata_dir=None` 与显式 `OutputState`，断言 ownership/unparsed 更新且临时根下不存在 `ownership.json`、`待回填资料级意见.json`、`未审核文件.json`。

- [ ] **Step 5: 运行测试并确认失败**

Run: `cd risk-audit && .venv/bin/python -m pytest tests/test_writer.py::test_write_outputs_keeps_metadata_in_memory_when_directory_is_none -q`

Expected: FAIL，当前代码会对 `None / "ownership.json"` 执行路径运算。

- [ ] **Step 6: 最小实现可选元数据目录**

修改 `write_outputs` 和 `record_business_failure`：所有元数据写入集中通过一个有中文注释的私有辅助函数，并在 `metadata_dir is None` 时只维护 `OutputState`。警告中的报告路径只有实际写出文件时才提供，不能生成虚假路径。

- [ ] **Step 7: 运行相关测试**

Run: `cd risk-audit && .venv/bin/python -m pytest tests/test_writer.py tests/test_delivery_v180.py tests/web/test_worker.py -q`

Expected: PASS。

- [ ] **Step 8: 检查本任务 diff，不提交**

Run: `git diff --check -- risk-audit/src/risk_audit/artifact_policy.py risk-audit/src/risk_audit/runner.py risk-audit/src/risk_audit/writer.py risk-audit/src/risk_audit_web/worker.py risk-audit/tests/test_writer.py risk-audit/tests/test_incremental_output.py risk-audit/tests/web/test_worker.py`

Expected: 无输出。

### Task 2: 用精确位置索引替换高频文本线性扫描

**Files:**
- Modify: `risk-audit/src/risk_audit/audit_statistics.py:200-230,327-430`
- Modify: `risk-audit/tests/test_audit_statistics.py`

**Interfaces:**
- Produces: 私有不可变 `_FindingOccurrenceIndex`，包含精确位置和资料级回退映射。
- Produces: `_build_finding_occurrence_index(findings: list[Finding]) -> _FindingOccurrenceIndex`。
- Produces: `_find_occurrence(index: _FindingOccurrenceIndex, message: str, state: dict[str, object], file: FileRecord) -> Finding | None`。
- Consumes: Task 1 不提供直接依赖；继续使用现有 `Finding`、`FileRecord` 和业务注册函数。

- [ ] **Step 1: 写精确位置和高频同文本测试**

在 `tests/test_audit_statistics.py` 新增：

- `test_occurrence_index_selects_exact_file_sheet_and_row`
- `test_occurrence_index_does_not_scan_same_message_candidates`

第二个测试构造至少 10,000 条相同 `message`、不同位置的 Finding，并用目标位置断言直接命中；同时 monkeypatch/包装旧候选遍历入口，确保归集不调用它。

- [ ] **Step 2: 写冲突与资料级回退测试**

新增：

- `test_occurrence_index_rejects_conflicting_routes_at_same_location`
- `test_occurrence_index_routes_material_finding_without_file_path`

冲突测试使用相同 `(message, file_path, sheet, row)` 但不同 `business_id`，断言抛出包含文件、工作表和行号的 `ValueError`；资料级测试断言第 17 条仍归入 Finding 指定的真实业务。

- [ ] **Step 3: 运行测试并确认失败**

Run: `cd risk-audit && .venv/bin/python -m pytest tests/test_audit_statistics.py -q`

Expected: 新索引测试 FAIL，现有 `_finding_for_occurrence` 仍按候选列表遍历。

- [ ] **Step 4: 实现索引和路由一致性验证**

用精确键 `(message, file_path, sheet, row)` 建索引；资料级回退使用 `(message, entity_code)`。新增 `_statistics_route_key(finding: Finding) -> tuple[object, ...]` 只比较影响统计业务路由的字段；相同精确键但路由键不一致时立即失败。

- [ ] **Step 5: 将 `_collect_statistics` 改为索引查询**

移除 `findings_by_message` 和 `_finding_for_occurrence` 的线性候选扫描。每个 ownership 单元格只解析一次行号，并通过 `_find_occurrence` 常数时间查找。

- [ ] **Step 6: 运行统计测试**

Run: `cd risk-audit && .venv/bin/python -m pytest tests/test_audit_statistics.py -q`

Expected: PASS。

- [ ] **Step 7: 检查本任务 diff，不提交**

Run: `git diff --check -- risk-audit/src/risk_audit/audit_statistics.py risk-audit/tests/test_audit_statistics.py`

Expected: 无输出。

### Task 3: 生成不可变统计数据并复用到 Excel 和 HTML

**Files:**
- Modify: `risk-audit/src/risk_audit/audit_statistics.py:454-606`
- Modify: `risk-audit/src/risk_audit/audit_statistics_html.py:416-末尾`
- Modify: `risk-audit/src/risk_audit/runner.py:1091-1149`
- Modify: `risk-audit/tests/test_audit_statistics.py`
- Modify: `risk-audit/tests/test_incremental_output.py`

**Interfaces:**
- Produces: `AuditStatisticsBusiness(name: str, records: tuple[str, ...])`。
- Produces: `AuditStatisticsUnit(path: str, businesses: tuple[AuditStatisticsBusiness, ...])`。
- Produces: `AuditStatisticsData(units: tuple[AuditStatisticsUnit, ...])`。
- Produces: `collect_audit_statistics(...) -> AuditStatisticsData`。
- Produces: `write_audit_statistics(path: Path, statistics: AuditStatisticsData) -> None`。
- Produces: `write_audit_statistics_html(path: Path, statistics: AuditStatisticsData) -> None`。
- Consumes: Task 2 的精确位置索引。

- [ ] **Step 1: 写共享统计对象失败测试**

修改 `tests/test_audit_statistics.py`：先调用 `collect_audit_statistics`，再把同一对象传给两种 writer；断言 Excel 与 HTML 的主体、业务名称、意见文本和计数一致。增加 dataclass 冻结测试，断言字段不可修改。

- [ ] **Step 2: 写 Runner 只归集一次失败测试**

在 `tests/test_incremental_output.py` 新增 `test_runner_collects_audit_statistics_once_for_two_reports`，替换 `collect_audit_statistics` 记录调用次数，并替换两个 writer 验证收到的是同一个对象，断言调用次数为 1。

- [ ] **Step 3: 运行测试并确认失败**

Run: `cd risk-audit && .venv/bin/python -m pytest tests/test_audit_statistics.py tests/test_incremental_output.py::test_runner_collects_audit_statistics_once_for_two_reports -q`

Expected: FAIL，当前 writer 各自归集且接口仍接收原始 files/ownership/findings。

- [ ] **Step 4: 实现不可变数据类型和采集函数**

把现有 `_statistics_source_data` 的稳定排序结果转换为三个 frozen dataclass。为 HTML 渲染提供一个私有稳定字典投影，不让 HTML 模块重新读取 Finding。

- [ ] **Step 5: 收窄两个 writer 的接口**

Excel 和 HTML writer 只接收 `path` 与 `AuditStatisticsData`。删除旧参数签名及兼容分支，更新全部测试调用方。

- [ ] **Step 6: Runner 单次归集并顺序写两种报告**

在 `runner.py` 先生成 `statistics`，再传给两个 writer。两个 writer 继续使用同目录临时文件和原子替换；任一失败时沿用现有异常边界，不发布完成状态。

- [ ] **Step 7: 运行统计与增量输出回归**

Run: `cd risk-audit && .venv/bin/python -m pytest tests/test_audit_statistics.py tests/test_incremental_output.py tests/test_business_incremental.py -q`

Expected: PASS。

- [ ] **Step 8: 检查本任务 diff，不提交**

Run: `git diff --check -- risk-audit/src/risk_audit/audit_statistics.py risk-audit/src/risk_audit/audit_statistics_html.py risk-audit/src/risk_audit/runner.py risk-audit/tests/test_audit_statistics.py risk-audit/tests/test_incremental_output.py`

Expected: 无输出。

### Task 4: Runner 最小产物路径和显式保留结果

**Files:**
- Modify: `risk-audit/src/risk_audit/runner.py:443-475,617-680,1089-1214`
- Modify: `risk-audit/src/risk_audit_web/worker.py:61-86`
- Modify: `risk-audit/tests/test_incremental_output.py`
- Modify: `risk-audit/tests/web/test_worker.py`

**Interfaces:**
- Consumes: Task 1 的 `ArtifactPolicy` 与可选 `metadata_dir`。
- Consumes: Task 3 的 `collect_audit_statistics` 和两个 writer。
- Produces: 最小策略结果中的显式 `unaudited_files_report: str`、`incomplete_items_report: str`、`audit_statistics_report: str`、`audit_statistics_html_report: str`、`log_reference: str`。
- Produces: `_result_summary` 不再依赖 `run_dir` 推导未审核文件路径。

- [ ] **Step 1: 写最小策略不生成详细文件失败测试**

在 `tests/test_incremental_output.py` 新增 `test_minimal_artifact_policy_persists_only_required_reports`，使用现有小型审核夹具运行 `audit(..., artifact_policy=ArtifactPolicy.MINIMAL)`，断言：

- 存在 `audit.log`、两种统计报告、`未审核文件.json`、`未完成项.json`。
- 不存在 `findings.json`、`statuses.json`、`inventory.json`、`warnings.json`、`summary.json`、`entities/`、`ownership.json`、`review_tasks.json`、`复核事项.csv`。
- 结果字典包含两份明细的显式路径，不包含供前端使用的 `run_dir`。

- [ ] **Step 2: 写最小策略跳过昂贵构建步骤失败测试**

新增 `test_minimal_artifact_policy_skips_detailed_review_builders`，将 `build_review_tasks` 和 `build_patterns` 替换为一旦调用即失败的函数，断言最小策略审核仍成功。

- [ ] **Step 3: 运行测试并确认失败**

Run: `cd risk-audit && .venv/bin/python -m pytest tests/test_incremental_output.py -q`

Expected: 新测试 FAIL，当前 Runner 始终写详细报告。

- [ ] **Step 4: 分离临时报告根与持久报告根**

在 `_audit` 中统一计算 `persistent_audit_directory` 和 `internal_report_directory`：最小策略的内部目录位于 `work_root`，详细策略继续位于 `run_dir`。所有只供运行过程使用的报告写到内部目录；最终两份 JSON 直接由 `OutputState.unparsed` 和 `collect_incomplete_items` 写到持久 `_risk_audit`。

- [ ] **Step 5: 跳过最小策略不需要的构建和详细 JSON**

最小策略不得调用 review task、责任模式和详细诊断构建器，不写实体/业务 `result.json`，不序列化 Finding、Status、Inventory、Warnings 或 Summary。详细策略保持自身完整能力，但不得引入旧 Web 分支。

- [ ] **Step 6: 发布显式保留路径并更新 Worker 摘要**

Runner 结果增加 `unaudited_files_report`；Worker `_result_summary` 直接读取该字段。移除通过 `run_dir/_risk_audit` 推导路径的逻辑，结果摘要不再保存 `run_dir`。

- [ ] **Step 7: 运行 Runner 和 Worker 回归**

Run: `cd risk-audit && .venv/bin/python -m pytest tests/test_incremental_output.py tests/test_business_incremental.py tests/test_progress_and_cancel.py tests/web/test_worker.py -q`

Expected: PASS。

- [ ] **Step 8: 检查本任务 diff，不提交**

Run: `git diff --check -- risk-audit/src/risk_audit/runner.py risk-audit/src/risk_audit_web/worker.py risk-audit/tests/test_incremental_output.py risk-audit/tests/web/test_worker.py`

Expected: 无输出。

### Task 5: Web `.task` 白名单清理与终态集成

**Files:**
- Create: `risk-audit/src/risk_audit_web/task_artifacts.py`
- Modify: `risk-audit/src/risk_audit_web/worker.py:125-321`
- Create: `risk-audit/tests/web/test_task_artifacts.py`
- Modify: `risk-audit/tests/web/test_worker.py`
- Modify: `risk-audit/tests/web/test_tasks_api.py`

**Interfaces:**
- Produces: `TaskArtifactCleanupError(RuntimeError)`。
- Produces: `compact_task_artifacts(output_root: Path) -> tuple[str, ...]`，返回未能清理的安全警告文本。
- Produces: `validate_retained_task_artifacts(output_root: Path, required_paths: tuple[Path, ...]) -> None`。
- Consumes: Task 4 的显式保留路径和现有 `TaskEvent("cleanup_warning", ...)`。

- [ ] **Step 1: 写白名单清理失败测试**

创建 `tests/web/test_task_artifacts.py`，新增：

- `test_compact_task_artifacts_keeps_only_allowlist`
- `test_compact_task_artifacts_is_idempotent`
- `test_compact_task_artifacts_removes_empty_directories`

夹具创建全部四个保留文件以及 `findings.json`、`review_tasks.json`、`ownership.json`、`entities/result.json`，断言只剩设计白名单。

- [ ] **Step 2: 写路径安全失败测试**

新增：

- `test_compact_task_artifacts_rejects_task_root_symlink`
- `test_compact_task_artifacts_does_not_follow_inner_symlink`
- Windows 条件测试 `test_compact_task_artifacts_does_not_follow_reparse_point`

外部哨兵文件必须保持不变；内部链接本身可以安全删除，但不得遍历目标。

- [ ] **Step 3: 运行测试并确认失败**

Run: `cd risk-audit && .venv/bin/python -m pytest tests/web/test_task_artifacts.py -q`

Expected: FAIL，模块尚不存在。

- [ ] **Step 4: 实现幂等白名单清理**

使用 `lstat`/`os.scandir` 逐项检查，不对 `.task` 根或目录项调用跟随链接的递归 API。白名单使用相对 POSIX 路径常量；删除前验证解析目标仍属于 `<output_root>/.task`。函数和关键删除行添加中文注释。

- [ ] **Step 5: 写 Worker 终态参数化失败测试**

在 `tests/web/test_worker.py` 对 `completed`、`partial`、`failed`、`cancelled` 增加参数化场景：模拟审核在 `.task` 写入冗余文件，运行 Worker 后断言日志被保留、冗余文件被删除。增加清理异常场景，断言任务原终态不变且事件表出现 `cleanup_warning`。

- [ ] **Step 6: 在发布终态前集成清理和保留文件验证**

成功/部分完成路径在更新 100% 终态前调用清理并验证实际结果路径。失败/取消路径在异常处理完成后调用同一幂等函数。清理异常只追加事件，不覆盖审核终态；保留结果缺失则禁止发布完成状态。

- [ ] **Step 7: 写空明细 API 回归测试**

在 `tests/web/test_tasks_api.py` 新增空数组场景，分别调用未审核文件和未完成项 GET 接口，断言 HTTP 200、`count == 0`、`items == []`。

- [ ] **Step 8: 运行 Web 相关测试**

Run: `cd risk-audit && .venv/bin/python -m pytest tests/web/test_task_artifacts.py tests/web/test_worker.py tests/web/test_tasks_api.py -q`

Expected: PASS。

- [ ] **Step 9: 检查本任务 diff，不提交**

Run: `git diff --check -- risk-audit/src/risk_audit_web/task_artifacts.py risk-audit/src/risk_audit_web/worker.py risk-audit/tests/web/test_task_artifacts.py risk-audit/tests/web/test_worker.py risk-audit/tests/web/test_tasks_api.py`

Expected: 无输出。

### Task 6: 性能基准、体积验收与完整回归

**Files:**
- Create: `risk-audit/tools/benchmark_task_finalization.py`
- Modify: `risk-audit/pyproject.toml`
- Test: `risk-audit/tests/test_audit_statistics.py`
- Test: `risk-audit/tests/test_incremental_output.py`

**Interfaces:**
- Produces: `tools/benchmark_task_finalization.py --findings N --ownership N`，标准输出 JSON，字段固定为 `findings`、`ownership`、`collect_seconds`、`excel_seconds`、`html_seconds`、`total_seconds`。
- Consumes: Task 3 的 `collect_audit_statistics` 与两个 writer。
- Consumes: Task 4、5 的最小产物目录。

- [ ] **Step 1: 写基准脚本输出契约测试**

在 `tests/test_audit_statistics.py` 新增 `test_finalization_benchmark_reports_stable_metrics`，以小数据调用脚本入口函数，断言返回字段齐全、数量一致、时间为非负浮点数，不对单元测试设置脆弱的毫秒阈值。

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd risk-audit && .venv/bin/python -m pytest tests/test_audit_statistics.py::test_finalization_benchmark_reports_stable_metrics -q`

Expected: FAIL，基准模块尚不存在。

- [ ] **Step 3: 实现合成基准脚本**

脚本复用测试中稳定的 FileRecord/Finding 构造方式，默认生成 200,000 条 Finding 和 60,000 个 ownership 单元格，其中至少 50,000 条使用相同意见文本。脚本只写系统临时目录，结束时清理，并以 `json.dumps` 输出固定指标。

- [ ] **Step 4: 注册 pytest 标记并增加线性结构防回归测试**

在 `pyproject.toml` 注册 `performance` 标记；增加一个不依赖绝对机器速度的结构测试，确保采集函数只遍历 Finding 和 ownership 各一次。真实 20 万条耗时由基准脚本单独验收。

- [ ] **Step 5: 运行 20 万条基准**

Run: `cd risk-audit && .venv/bin/python tools/benchmark_task_finalization.py --findings 200000 --ownership 60000`

Expected: JSON 中 `total_seconds < 120`，Excel 与 HTML 均成功生成，进程退出码为 0。

- [ ] **Step 6: 使用同规模真实任务验收体积和 99% 阶段**

以原始“重庆电力公司”材料创建新的 Web 任务，记录 `生成汇总报告` 到 `审核结束` 的日志时间；运行：

```bash
du -sm '<new-output>/.task'
find '<new-output>/.task' -type f -print
```

Expected: `.task <= 5 MB`；文件清单只包含四个隐藏目录白名单文件；汇总阶段 `< 120 秒`。不得修改或复用任务 `20261020-102012-8839` 的历史输出。

- [ ] **Step 7: 运行重点回归**

Run: `cd risk-audit && .venv/bin/python -m pytest tests/test_audit_statistics.py tests/test_writer.py tests/test_incremental_output.py tests/test_business_incremental.py tests/test_progress_and_cancel.py tests/web/test_task_artifacts.py tests/web/test_worker.py tests/web/test_tasks_api.py -q`

Expected: PASS。

- [ ] **Step 8: 运行完整测试集**

Run: `cd risk-audit && .venv/bin/python -m pytest -q`

Expected: PASS，无失败和错误。

- [ ] **Step 9: 检查工作区和差异**

Run: `git status --short && git diff --check`

Expected: 只有本计划涉及的代码、测试和用户已有未跟踪文档；`git diff --check` 无输出。

- [ ] **Step 10: 输出建议 commit 信息，不提交**

建议：`perf: 优化Web任务汇总并精简运行产物`

不运行 `git add` 或 `git commit`。
