# 多地区规则包与任务级地区选择实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在一套离线审核器中按任务选择湖南或重庆，并把地区、规则版本和内容哈希固定到任务及审核快照。

**Architecture:** 新增只读地区配置目录，由地区配置加载器统一解析并校验规则包、主体清单、模板和运行配置。REST 服务创建任务时根据 `region_code` 固定全部资源路径，Worker 只消费数据库中的任务请求；湖南和重庆规则分别从共同基线构建完整不可变发布包，运行时不动态合并规则。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLite、pytest、原生 JavaScript、JSON 规则包、python-docx、openpyxl

**Spec:** `docs/superpowers/specs/2026-10-12-multi-region-rulepack-design.md`

## Global Constraints

- 不兼容缺少 `region_code` 的旧创建请求、旧任务协议、旧数据库结构或旧测试契约。
- REST 资源使用 `GET /api/v1/regions` 和现有 `POST /api/v1/tasks`。
- 地区代码只使用统一的小写英文命名：`hunan`、`chongqing`。
- 不从文件名自动猜地区，不允许一个任务混合地区。
- 重庆发布包从公共基线 `1.9.20` 生成，不从湖南 `1.9.21` 派生。
- 不得用湖南主体清单或湖南模板填充重庆资源空缺。
- 所有新增或修改函数必须包含中文用途说明、参数解释和返回值说明；关键路径校验、哈希校验及任务资源固定代码必须有中文注释。
- 按测试先行执行每项任务；本次不执行 `git commit`，只在每项完成时输出符合规范的中文提交信息。
- 保留用户未要求修改的现有文件和数据，不删除旧 SQLite 数据库。

## Review Focus

- 地区索引含重复代码、未知字段或 `../` 路径时，整个索引必须拒绝加载，不能部分接受。
- 某地区规则包哈希错误或资源缺失时，只禁用该地区，其他已就绪地区仍可创建任务。
- 任务创建后地区索引被修改时，Worker 必须继续使用数据库中固定的版本和路径，并在规则包内容变化时以 `RULEPACK_CHANGED` 失败。
- 湖南和重庆任务同秒并发创建时，资源路径、显示名称、输出目录和数据库字段不能串用。
- 重庆规则来源表出现未知业务、缺少业务、多个关键控制点表或非法编号时，发布工具必须失败且不生成部分发布包。

---

### Task 1: 地区配置加载与资源诊断

**Files:**
- Create: `risk-audit/src/risk_audit_web/region_profiles.py`
- Create: `risk-audit/tests/web/test_region_profiles.py`
- Modify: `risk-audit/src/risk_audit_web/task_paths.py`
- Modify: `risk-audit/src/risk_audit_web/diagnostics.py`
- Modify: `risk-audit/tests/web/test_diagnostics.py`
- Modify: `risk-audit/tests/web/conftest.py`

**Interfaces:**
- Consumes: `PortablePaths.runtime_root`、规则包 `manifest.json` 和现有 `sha256_file`/`sha256_json`。
- Produces: `RegionProfile`、`RegionCatalog`、`RegionProfileError`、`load_region_catalog(resources_root: Path) -> RegionCatalog`、`RegionCatalog.require(region_code: str) -> RegionProfile`、`RegionProfile.to_public_dict() -> dict[str, object]`。

- [ ] **Step 1: 为合法配置、重复代码、未知字段、目录穿越和单地区资源缺失编写失败测试**

```python
def test_load_region_catalog_keeps_missing_region_isolated(resources_root: Path) -> None:
    catalog = load_region_catalog(resources_root)
    assert catalog.require("hunan").available is True
    assert catalog.require("chongqing").available is False
    assert catalog.require("chongqing").unavailable_reasons

def test_load_region_catalog_rejects_duplicate_codes(resources_root: Path) -> None:
    with pytest.raises(RegionProfileError, match="地区代码重复"):
        load_region_catalog(resources_root)

def test_load_region_catalog_rejects_path_escape(resources_root: Path) -> None:
    with pytest.raises(RegionProfileError, match="越过资源根目录"):
        load_region_catalog(resources_root)
```

- [ ] **Step 2: 运行地区配置测试并确认因模块不存在而失败**

Run from `risk-audit`: `.venv/bin/python -m pytest tests/web/test_region_profiles.py -q`

Expected: FAIL，提示 `risk_audit_web.region_profiles` 不存在。

- [ ] **Step 3: 实现地区配置数据类型和严格加载器**

在 `region_profiles.py` 定义：

```python
@dataclass(frozen=True)
class RegionProfile:
    region_code: str
    region_name: str
    rulepack_version: str
    rulepack_content_hash: str
    rulepack: Path
    entity_file: Path
    baseline_root: Path
    config_file: Path
    enabled: bool
    unavailable_reasons: tuple[str, ...]

    @property
    def available(self) -> bool: ...

    def to_public_dict(self) -> dict[str, object]: ...

@dataclass(frozen=True)
class RegionCatalog:
    profiles: dict[str, RegionProfile]

    def require(self, region_code: str) -> RegionProfile: ...

def load_region_catalog(resources_root: Path) -> RegionCatalog: ...
```

索引只允许 `schema_version` 和 `regions`；地区条目只允许规格定义的八个字段。路径必须是 POSIX 相对路径，解析后仍位于 `resources_root` 内。缺文件、目录类型错误、规则包版本或哈希不一致写入该地区的 `unavailable_reasons`；重复代码、未知字段、非法代码和路径越界抛出 `RegionProfileError`。

- [ ] **Step 4: 将 `PortablePaths` 改为地区资源入口并替换全局活动包诊断**

新增 `resources_root`、`region_index` 属性；移除 Web 层对单一 `entity_file`、`baseline_root`、`config_file` 和 `load_active_rulepack()` 的依赖。`run_startup_diagnostics(paths)` 只在索引整体无效或没有任何可用地区时阻止创建任务，单地区问题由地区响应公开。

- [ ] **Step 5: 补齐 Review Focus 中索引未知字段与哈希隔离测试并运行**

Run from `risk-audit`: `.venv/bin/python -m pytest tests/web/test_region_profiles.py tests/web/test_diagnostics.py -q`

Expected: PASS。

- [ ] **Step 6: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 增加地区配置加载与资源隔离诊断`

### Task 2: 任务协议与 SQLite 固定地区资源

**Files:**
- Modify: `risk-audit/src/risk_audit_web/task_contracts.py`
- Modify: `risk-audit/src/risk_audit_web/task_repository.py`
- Modify: `risk-audit/tests/web/test_task_contracts.py`
- Modify: `risk-audit/tests/web/test_task_repository.py`
- Modify: `risk-audit/tests/web/test_parallel_integration.py`
- Modify: `risk-audit/tests/web/test_task_manager.py`
- Modify: `risk-audit/tests/web/test_worker.py`

**Interfaces:**
- Consumes: Task 1 的 `RegionProfile` 字段。
- Produces: `SCHEMA_VERSION = "2.0"`；含地区信息的 `TaskRequest` 和 `TaskRecord`；`DATABASE_VERSION = 2` 的全新数据库结构。

- [ ] **Step 1: 修改契约测试，要求任务请求和公开记录包含地区及规则版本**

```python
assert request.region_code == "chongqing"
assert request.region_name == "重庆"
assert request.rulepack_version == "1.9.22"
assert request.rulepack_content_hash == "a" * 64
assert record.region_code == "chongqing"
assert record.rulepack_version == "1.9.22"
```

同时增加缺少任一新字段时抛出 `ContractError` 的测试。

- [ ] **Step 2: 运行契约测试并确认旧数据结构失败**

Run from `risk-audit`: `.venv/bin/python -m pytest tests/web/test_task_contracts.py tests/web/test_task_repository.py -q`

Expected: FAIL，提示新字段缺失或构造参数未知。

- [ ] **Step 3: 升级任务协议和数据库结构**

为 `TaskRequest`、`TaskRecord`、`tasks` 表及行还原逻辑增加：

```python
region_code: str
region_name: str
rulepack_version: str
rulepack_content_hash: str
```

将协议和数据库版本升级为 `2.0`/`2`。不迁移旧表；旧版本数据库返回明确的 `TaskDatabaseError`，但不得删除或覆盖原文件。

- [ ] **Step 4: 增加湖南与重庆并发任务隔离测试**

测试同秒写入两个任务，断言各自的 `region_code`、`rulepack`、`entity_file`、`baseline_root`、`config_file` 和哈希在读取、恢复及 Worker 请求中保持原值。

- [ ] **Step 5: 运行任务存储、管理和 Worker 测试**

Run from `risk-audit`: `.venv/bin/python -m pytest tests/web/test_task_contracts.py tests/web/test_task_repository.py tests/web/test_parallel_integration.py tests/web/test_task_manager.py tests/web/test_worker.py -q`

Expected: PASS。

- [ ] **Step 6: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 持久化任务地区及规则包版本`

### Task 3: 地区 REST 资源与任务创建服务

**Files:**
- Create: `risk-audit/src/risk_audit_web/api/regions.py`
- Modify: `risk-audit/src/risk_audit_web/api_models.py`
- Modify: `risk-audit/src/risk_audit_web/app.py`
- Modify: `risk-audit/src/risk_audit_web/services.py`
- Modify: `risk-audit/src/risk_audit_web/worker.py`
- Modify: `risk-audit/src/risk_audit/snapshot.py`
- Modify: `risk-audit/src/risk_audit/runner.py`
- Create: `risk-audit/tests/web/test_regions_api.py`
- Modify: `risk-audit/tests/web/test_tasks_api.py`
- Modify: `risk-audit/tests/web/test_services.py`
- Modify: `risk-audit/tests/test_hidden_sheet_policy.py`

**Interfaces:**
- Consumes: `load_region_catalog()`、`RegionCatalog.require()`、Task 2 的任务协议。
- Produces: `GET /api/v1/regions`；`TaskCreateRequest(input_root: str, region_code: str)`；任务和快照中的地区字段；稳定错误码 `UNKNOWN_REGION`、`REGION_DISABLED`、`REGION_NOT_READY`、`RULEPACK_CHANGED`。

- [ ] **Step 1: 编写地区列表和严格任务创建 API 失败测试**

```python
def test_list_regions_exposes_availability(client) -> None:
    payload = client.get("/api/v1/regions", headers={"X-Local-Token": "secret"}).json()
    assert payload["regions"][0]["region_code"] == "hunan"
    assert "unavailable_reasons" in payload["regions"][1]

def test_create_task_requires_region_code(client, input_root: Path) -> None:
    response = client.post("/api/v1/tasks", headers=authorized_headers(client),
                           json={"input_root": str(input_root)})
    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_REQUEST"
```

另测未知、禁用、未就绪地区均返回规格中的 `422` 错误码。

- [ ] **Step 2: 运行 API 和服务测试并确认失败**

Run from `risk-audit`: `.venv/bin/python -m pytest tests/web/test_regions_api.py tests/web/test_tasks_api.py tests/web/test_services.py -q`

Expected: FAIL，提示地区路由不存在或创建请求不接受 `region_code`。

- [ ] **Step 3: 注册地区路由并实现任务级资源解析**

`ApplicationServices.__post_init__()` 缓存 `RegionCatalog`。新增：

```python
def list_regions(self) -> dict[str, object]: ...
def _resolve_region(self, region_code: str) -> RegionProfile: ...
def _verify_pinned_rulepack(self, request: TaskRequest) -> None: ...
```

创建任务时只接受 `input_root` 和 `region_code`，从 `RegionProfile` 写入全部路径及版本字段。显示名称格式固定为 `“{region_name} {input_name}-{YYYYMMDD-HHMMSS}”`；输出目录使用相同名称。`system_status()` 返回 `available_region_count`，不再返回单一 `rule_version`。

- [ ] **Step 4: 在 Worker 启动前校验固定规则包并记录地区**

Worker 从数据库读取请求后，对发布包 `manifest.version`、`manifest.content_hash` 和实际 JSON 摘要进行复核；不一致时以 `RULEPACK_CHANGED` 失败。任务启动日志写入地区代码、地区名称、规则版本和内容哈希。

- [ ] **Step 5: 扩展审核快照并测试索引变化不影响已创建任务**

将 `region_code`、`region_name` 写入地区规则包 manifest；`build_snapshot()` 从 manifest 输出这两个字段及已有 `rulepack_version`、`rulepack_content_hash`。测试创建任务后改写地区索引，断言 Worker 仍使用原任务路径；改写规则包 JSON 时断言任务失败为 `RULEPACK_CHANGED`。

- [ ] **Step 6: 运行 REST、服务、Worker 和快照测试**

Run from `risk-audit`: `.venv/bin/python -m pytest tests/web/test_regions_api.py tests/web/test_tasks_api.py tests/web/test_services.py tests/web/test_worker.py tests/test_hidden_sheet_policy.py -q`

Expected: PASS。

- [ ] **Step 7: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 支持按地区创建并固定审核任务`

### Task 4: 前端地区选择与任务追溯展示

**Files:**
- Modify: `risk-audit/src/risk_audit_web/static/index.html`
- Modify: `risk-audit/src/risk_audit_web/static/js/app.js`
- Modify: `risk-audit/src/risk_audit_web/static/js/tasks.js`
- Modify: `risk-audit/src/risk_audit_web/static/css/theme.css`
- Modify: `risk-audit/tests/web/test_frontend_contract.py`
- Modify: `risk-audit/tests/web/test_browser.py`

**Interfaces:**
- Consumes: Task 3 的 `GET /api/v1/regions` 和任务响应地区字段。
- Produces: 必选的 `#region-code` 控件、不可用原因、任务表地区列、详情地区和规则版本展示。

- [ ] **Step 1: 编写前端契约失败测试**

断言 HTML/JS 包含：

```python
assert 'id="region-code"' in html
assert '/api/v1/regions' in app_source
assert 'region_code: document.getElementById("region-code").value' in app_source
assert '<th>地区</th>' in html
assert 'setText("detail-region", task.region_name)' in tasks_source
assert 'setText("detail-rule-version", task.rulepack_version)' in tasks_source
```

同时断言页面不会预选地区，可用地区和材料目录缺一时创建按钮禁用，不可用地区显示 `unavailable_reasons`。

- [ ] **Step 2: 运行前端契约测试并确认失败**

Run from `risk-audit`: `.venv/bin/python -m pytest tests/web/test_frontend_contract.py tests/web/test_browser.py -q`

Expected: FAIL，提示缺少地区控件或请求字段。

- [ ] **Step 3: 实现地区加载、必选校验和创建请求**

新增 `loadRegions() -> Promise<void>` 和 `renderRegionOptions(regions: object[]) -> void`，页面初始化时加载地区。可用地区渲染为空值后的正常选项；不可用地区使用 `disabled` 选项并在说明区展示原因。`updateCreateButton()` 同时检查地区、输入目录和系统状态。

- [ ] **Step 4: 增加任务列表和详情追溯字段**

任务表在状态之前显示地区；详情元数据展示地区和规则版本。顶部“规则版本”改为“可用地区”，显示 `available_region_count`，避免形成全局规则版本错觉。

- [ ] **Step 5: 运行前端和浏览器测试**

Run from `risk-audit`: `.venv/bin/python -m pytest tests/web/test_frontend_contract.py tests/web/test_browser.py -q`

Expected: PASS。

- [ ] **Step 6: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 增加审核地区选择与任务版本展示`

### Task 5: 重庆专项能力与独立规则包发布

**Files:**
- Copy: `/Users/67m/Desktop/1012-重庆_同步模板匹配规则版.docx` to `risk-audit/规则来源/1012-重庆_同步模板匹配规则版.docx`
- Create: `risk-audit/src/risk_audit/checks/rules_1012_chongqing.py`
- Modify: `risk-audit/src/risk_audit/checks/registry.py`
- Modify: `risk-audit/src/risk_audit/system_type_preprocessing.py`
- Modify: `risk-audit/src/risk_audit/runner.py`
- Create: `risk-audit/tools/publish_rules_1012_chongqing.py`
- Create: `risk-audit/tests/test_rules_1012_chongqing.py`
- Create: `risk-audit/rulepacks/drafts/rules-1012-chongqing-1.9.22/**`
- Create: `risk-audit/rulepacks/releases/1.9.22/**`

**Interfaces:**
- Consumes: 公共基线 `rulepacks/releases/1.9.20`、现有 `template_match_notice`、`CheckContext` 和规则发布器。
- Produces: `system_control_consistency(context: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]`、`power_supply_branch_key_points(context: CheckContext, params: dict[str, Any]) -> list[dict[str, Any]]`、重庆发布包 `1.9.22`。

- [ ] **Step 1: 写入来源文件并校验固定哈希**

使用 `apply_patch` 以外的安全文件复制工具仅复制该二进制 DOCX；随后运行：

Run: `shasum -a 256 规则来源/1012-重庆_同步模板匹配规则版.docx`

Expected: `64d7f929910f08b1b0a961d10ef54f6c11bd90e7c3d65ca768b0ba1fd629699f`。

- [ ] **Step 2: 编写重庆规则和发布失败测试**

覆盖系统清单缺行、建设状态不一致、系统名称集合不一致、非供电分公司跳过、缺少关键控制点、超范围控制点、未匹配模板时按唯一业务别名识别。发布测试断言 `BASE_VERSION == "1.9.20"`，规则包 manifest 含 `region_code == "chongqing"`，并拒绝未知业务、缺少业务、两张表和非法编号。

- [ ] **Step 3: 运行重庆规则测试并确认失败**

Run from `risk-audit`: `.venv/bin/python -m pytest tests/test_rules_1012_chongqing.py -q`

Expected: FAIL，提示重庆检查模块或 `1.9.22` 发布包不存在。

- [ ] **Step 4: 实现重庆检查能力并注册操作符**

系统一致性按 `entity_code + business_id + variant_id + 控制措施数字键` 关联矩阵与系统清单；系统名称按“、，,；;/换行”拆分为规范化集合。供电分公司规则只对主体名称含“供电分公司”的主体执行，业务身份未匹配时只允许唯一别名命中。

- [ ] **Step 5: 扩展系统类型识别为明确的湖南和重庆省级名称集合**

将当前湖南常量改为统一的省级名称集合，加入“重庆”“重庆电力”“重庆市”“国网重庆市电力有限公司”“重庆市电力公司”“重庆电力公司”“重庆市公司”“重庆市级单位”；仍先排除市县、分公司和子公司，避免全称包含省级名称导致误判。

- [ ] **Step 6: 实现确定性重庆发布脚本并生成发布包**

`publish_rules_1012_chongqing.py` 从 `1.9.20` 创建草稿，独立为 R03 增加重庆模板匹配提示，增加重庆系统一致性和供电分公司关键控制点规则，不生成湖南财务管理 R17。所有规则来源指向重庆冻结文档；manifest 写入 `region_code`、`region_name`、来源哈希和 `1.9.22`。同时把 `1.9.22` 加入 `runner.py` 的确认版读取和系统类型预处理版本集合。

- [ ] **Step 7: 运行重庆测试、湖南回归和可重复发布校验**

Run from `risk-audit`: `.venv/bin/python -m pytest tests/test_rules_1012_chongqing.py tests/test_rules_1012.py tests/test_rules_0923_2.py -q`

Run from `risk-audit`: `.venv/bin/python tools/publish_rules_1012_chongqing.py --verify-only`

Expected: 全部 PASS，发布重建哈希一致，且不修改全局 `active.json`。

- [ ] **Step 8: 记录建议提交信息，不执行 Git 提交**

建议：`feat: 增加重庆专项规则及独立规则包`

### Task 6: 地区资源索引与便携发布

**Files:**
- Create: `risk-audit/regions/index.json`
- Modify: `risk-audit/tools/portable_release.py`
- Modify: `risk-audit/tools/verify_portable_distribution.py`
- Modify: `risk-audit/tests/web/test_portable_builder.py`
- Modify: `risk-audit/tests/web/test_portable_distribution.py`
- Modify: `risk-audit/tests/web/test_runtime_sources.py`

**Interfaces:**
- Consumes: Task 1 的地区索引格式、湖南 `1.9.21` 和重庆 `1.9.22` 发布包。
- Produces: 便携包中的 `runtime/resources/regions/index.json`、两个地区规则包和地区隔离资源目录。

- [ ] **Step 1: 编写便携构建失败测试**

断言发布包包含：

```text
runtime/resources/regions/index.json
runtime/resources/rulepacks/releases/1.9.21
runtime/resources/rulepacks/releases/1.9.22
runtime/resources/entities/hunan/会计主体清单20260907.xlsx
runtime/resources/baselines/hunan/templates
```

重庆主体清单和模板未提供时，索引仍包含重庆，但地区加载结果为 `available == false`，构建本身不得伪造或复制湖南资源到重庆目录。

- [ ] **Step 2: 运行便携构建测试并确认单活动包实现失败**

Run from `risk-audit`: `.venv/bin/python -m pytest tests/web/test_portable_builder.py tests/web/test_portable_distribution.py -q`

Expected: FAIL，提示地区索引或第二个规则包缺失。

- [ ] **Step 3: 新增仓库地区索引并改造资源复制**

`regions/index.json` 中湖南指向 `rulepacks/releases/1.9.21`、`entities/hunan/...`、`baselines/hunan/templates` 和湖南配置；重庆指向 `1.9.22` 及独立重庆路径。构建器读取索引并复制索引引用的已存在资源；规则包必须全部存在且哈希匹配，地区业务资源可以缺失并由运行时标记未就绪。

- [ ] **Step 4: 删除便携构建对 `active.json` 的选择依赖并升级校验器**

构建器不再通过 `_active_rulepack_version()` 只复制一个版本。校验器加载地区目录，要求至少一个可用地区、两个规则包进入发布清单，并确认重庆缺少资源时不会错误通过为可用。

- [ ] **Step 5: 运行便携构建与发布分发测试**

Run from `risk-audit`: `.venv/bin/python -m pytest tests/web/test_portable_builder.py tests/web/test_portable_distribution.py tests/web/test_runtime_sources.py tests/web/test_diagnostics.py -q`

Expected: PASS。

- [ ] **Step 6: 记录建议提交信息，不执行 Git 提交**

建议：`build: 打包多地区规则与隔离资源`

### Task 7: 全量回归、文档同步与交付检查

**Files:**
- Modify: `risk-audit/README.md`
- Modify: `risk-audit/macOS Apple Silicon Web版使用说明.md`
- Modify: `risk-audit/Windows Web版使用说明.md`
- Modify: `使用说明.md`
- Create: `risk-audit/重庆规则与地区选择说明-v2.2.0.md`
- Modify: `risk-audit/src/risk_audit/__init__.py`
- Modify: `risk-audit/src/risk_audit_web/__init__.py`

**Interfaces:**
- Consumes: Tasks 1–6 的最终接口和资源布局。
- Produces: 面向用户的地区选择说明、版本号 `2.2.0`、完整测试与发布验证结果。

- [ ] **Step 1: 更新说明文档和版本号**

文档明确：创建任务必须选择地区；不同地区任务可并发；任务固定规则版本；重庆主体清单和模板未部署时重庆选项不可用；补齐资源后无需更换审核器代码。不得声称缺失的重庆资源已经交付。

- [ ] **Step 2: 运行静态契约、编译和规则发布校验**

Run from `risk-audit`: `.venv/bin/python -m compileall -q src tools tests`

Run from `risk-audit`: `.venv/bin/python tools/publish_rules_1012_hunan.py --verify-only`

Run from `risk-audit`: `.venv/bin/python tools/publish_rules_1012_chongqing.py --verify-only`

Expected: 三条命令退出码均为 0。

- [ ] **Step 3: 运行完整测试套件**

Run from `risk-audit`: `.venv/bin/python -m pytest -q`

Expected: 全部 PASS，无跳过的新增地区测试。

- [ ] **Step 4: 运行 Git 差异和资源泄漏检查**

Run: `git diff --check`

Run: `rg -n "国网湖南|湖南省电力" risk-audit/regions risk-audit/rulepacks/releases/1.9.22`

Expected: `git diff --check` 无输出；重庆地区索引和重庆规则包中不存在湖南主体或湖南专属规则引用。公共历史资源命中不作为失败。

- [ ] **Step 5: 检查工作区未误含构建缓存和临时文件**

Run: `git status --short`

Expected: 只包含计划内源文件、测试、规则来源和确定性规则包产物；不包含 `.pytest_cache`、`__pycache__`、临时渲染文件或本地 SQLite 数据库。

- [ ] **Step 6: 输出最终中文提交信息，不执行 Git 提交**

建议：`feat: 支持湖南重庆多地区任务审核`
