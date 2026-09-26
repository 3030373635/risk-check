# 模板业务唯一标识实施计划

> **执行要求：** 使用 `superpowers:executing-plans` 在当前任务内逐项实施；每项严格执行测试先行。项目要求不执行 `git commit`，完成后仅输出建议的中文 Conventional Commit 信息。

**目标：** 第一批、第二批及后续模板即使业务序号重复，也能按业务名称稳定识别唯一业务并选择正确模板。

**架构：** 新增独立的业务目录模块，负责配置校验、路径规范化、业务别名匹配和变体匹配。`business_id` 作为审核隔离与模板索引主键，`business_code` 仅保留为展示字段；主体、业务、措施三层匹配依次使用 `entity_code`、`business_id + variant_id`、`measure_id`。

**技术栈：** Python 3.11、dataclasses、pytest、openpyxl、现有 JSON 规则包发布机制。

**设计文档：** `docs/superpowers/specs/2026-09-23-template-business-identity-design.md`

## 全局约束

- 不兼容旧版仅含 `entries` 的基准配置；新发布包使用 `schema_version: "2.0"` 和 `businesses`。
- `business_id` 全局唯一，`business_code` 只用于展示，不参与业务选择。
- 业务识别只使用报送文件相对路径和配置别名，不读取工作簿正文，不使用编辑距离或语义模型猜测。
- 零匹配、多匹配、变体不确定、模板缺失、哈希变化和模板解析失败都必须停止对应材料的模板写入并给出可定位错误。
- 所有新增或修改函数必须有中文注释及参数说明，关键隔离逻辑必须有注释。
- 不执行 `git commit`；最终只输出一条中文 Conventional Commit 建议。

## 重点复核

- 相同编号的第一批 `01 营销售电` 与第二批 `01 股权（产权）管理` 必须生成不同 `business_id`，并选择各自模板。
- 路径同时包含短别名和长别名时，最长别名唯一胜出；同长度并列必须报冲突。
- 路径括号、连接号、空格和英文大小写变化不得改变业务匹配结果。
- `03 电网基建` 的两个电压变体必须在业务确定后再匹配，第二批 `03 合同管理` 不得进入电压变体逻辑。
- `28` 至 `31` 不依赖编号正则，必须通过业务名称识别并参与完整审核链路。

---

### 任务 1：业务目录配置与确定性匹配器

**文件：**
- 新建：`risk-audit/src/risk_audit/business_identity.py`
- 修改：`risk-audit/src/risk_audit/configuration/validator.py`
- 新建：`risk-audit/tests/test_business_identity.py`

**接口：**
- `normalize_business_text(value: str) -> str`
- `validate_business_registry(registry: dict[str, Any]) -> list[str]`
- `identify_business(relative_path: Path, registry: dict[str, Any]) -> BusinessIdentity`
- `BusinessIdentity(business_id, business_code, business_name, variant_id)`

- [x] 编写失败测试：覆盖重复展示编号、28–31、括号空格归一化、最长别名、同长度冲突、无匹配和电网基建变体。
- [x] 运行 `pytest -q risk-audit/tests/test_business_identity.py`，确认因模块/API 不存在而失败。
- [x] 实现规范化、配置校验和确定性匹配；错误必须包含相对路径、候选项、命中别名和 `baseline_registry.json` 提示。
- [x] 再次运行目标测试并确认通过。
- [x] 记录建议提交信息：`feat: 新增模板业务唯一标识与路径匹配`

### 任务 2：模型、扫描和读取链路传播 business_id

**文件：**
- 修改：`risk-audit/src/risk_audit/models.py`
- 修改：`risk-audit/src/risk_audit/inventory.py`
- 修改：`risk-audit/src/risk_audit/inventory_v180.py`
- 修改：`risk-audit/src/risk_audit/readers/excel.py`
- 修改：`risk-audit/src/risk_audit/readers/confirmed_v180.py`
- 修改：`risk-audit/src/risk_audit/runner.py`
- 修改：`risk-audit/tests/test_business_identity.py`

**接口：**
- `FileRecord.business_id: str | None`
- `Record.business_id: str`
- `Finding.business_id: str`
- 扫描器新增 `business_registry` 参数，并把匹配结果写入文件记录。

- [x] 编写失败测试：扫描同一单位下第一批 01 与第二批 01，断言 `business_code` 都为 `01`、`business_id` 不同；扫描 28–31 均无解析错误。
- [x] 运行目标测试并确认现有扫描器缺少新参数或字段而失败。
- [x] 扩展模型与扫描器，解析记录和意见记录完整传播 `business_id`；说明材料保持空业务身份。
- [x] 运行目标测试并确认通过。
- [x] 记录建议提交信息：`feat: 在审核记录中传播业务唯一标识`

### 任务 3：基准模板按 business_id 加载

**文件：**
- 修改：`risk-audit/src/risk_audit/baselines.py`
- 修改：`risk-audit/tests/test_reference_prerequisites.py`
- 修改：`risk-audit/tests/test_business_identity.py`

**接口：**
- `load_baselines(...) -> dict[tuple[str, str], dict[str, Any]]`，键为 `(business_id, variant_id)`。
- 基准条目由 `businesses[].variants[].template` 展开，保留业务展示字段和模板 SHA-256。

- [x] 编写失败测试：重复 `business_code=01` 的两个业务同时加载且不覆盖；缺文件、哈希变化、重复 `business_id` 和重复模板路径分别失败。
- [x] 运行目标测试，确认旧 `entries` 加载方式无法满足断言。
- [x] 实现 v2 配置展开与基准索引，模板 `FileRecord` 写入对应 `business_id`。
- [x] 运行目标测试并确认通过。
- [x] 记录建议提交信息：`feat: 按业务唯一标识加载省公司模板`

### 任务 4：审核范围、业务分组、规则上下文和输出隔离

**文件：**
- 修改：`risk-audit/src/risk_audit/submission_scope.py`
- 修改：`risk-audit/src/risk_audit/runner.py`
- 修改：`risk-audit/src/risk_audit/engine.py`
- 修改：`risk-audit/src/risk_audit/output_0916.py`
- 修改：`risk-audit/src/risk_audit/checks/*.py`
- 修改：`risk-audit/src/risk_audit/review_tasks.py`
- 修改：`risk-audit/src/risk_audit/semantic_matching.py`
- 修改：`risk-audit/src/risk_audit/writer.py`
- 修改：`risk-audit/tests/test_business_incremental.py`
- 新建：`risk-audit/tests/test_duplicate_business_codes.py`

**接口：**
- 所有隔离键统一为 `(entity_code, business_id, variant_id)`；展示和规则选择仍可读取 `business_code`。
- 手动范围条目要求 `business_id`、`business_code`、`variant_id`、`required`。

- [x] 编写失败集成测试：同一主体上传两个 `01` 业务，断言生成两个独立业务结果、各自只拿到对应模板、输出责任主体不串线。
- [x] 运行目标测试并确认当前 `(business_code, variant_id)` 分组发生合并或模板串用。
- [x] 将范围、runner、engine、检查器、审核任务、语义匹配和输出中的业务隔离键统一切换为 `business_id`；保留 `business_code` 作为报告字段。
- [x] 运行新增重复序号回归并确认通过；旧 `test_business_incremental.py` 的既有失败单独记录，不作为本功能回归结果。
- [x] 记录建议提交信息：`refactor: 使用业务唯一标识隔离审核链路`

### 任务 5：迁移 19 份模板并发布新规则包

**文件：**
- 新建：`risk-audit/rulepacks/drafts/template-business-identity-1.9.19/baseline_registry.json`
- 新建：`risk-audit/tools/publish_template_business_identity_1919.py`
- 新建：`risk-audit/rulepacks/releases/1.9.19/`
- 修改：`risk-audit/rulepacks/active.json`
- 新建：`risk-audit/tests/test_template_business_identity_1919.py`

**接口：**
- 新规则包包含第一批 11 份模板和第二批 8 份模板。
- 发布脚本支持 `--verify-only`，验证目录、哈希、配置和不可变发布内容。

- [x] 编写失败测试：断言 19 份模板、18 个唯一业务、重复展示编号集合 `{01,02,03,08}`、28–31 存在、所有模板哈希与磁盘一致。
- [x] 运行目标测试并确认 1.9.19 发布包不存在而失败。
- [x] 从 1.9.18 创建草稿，写入 v2 基准配置和发布脚本，计算真实 SHA-256，发布并激活 1.9.19。
- [x] 运行发布验证和目标测试并确认通过。
- [x] 记录建议提交信息：`feat: 发布第二批省公司模板规则包`

### 任务 6：全链路回归与交付核对

**文件：**
- 修改：受新必填字段影响的测试夹具与断言。
- 修改：`risk-audit/README.md`
- 新建：`risk-audit/第二批模板业务识别说明-v1.9.19.md`

- [x] 运行 `pytest -q risk-audit/tests/test_business_identity.py risk-audit/tests/test_duplicate_business_codes.py risk-audit/tests/test_template_business_identity_1919.py`，确认核心新行为全部通过。
- [x] 运行 `pytest -q risk-audit/tests`，记录测试总数、通过数和任何遗留失败。
- [x] 运行 `python3 -m compileall -q risk-audit/src risk-audit/tools`，确认无语法错误。
- [x] 使用真实第二批 8 份模板执行只读识别/加载验收，核对业务 ID、展示编号、变体、工作表和模板哈希。
- [x] 检查 `git diff --check`、`git status --short` 和最终 diff，确认没有覆盖用户原有未跟踪文件、没有提交 Git。
- [x] 输出建议提交信息：`feat: 支持重复序号的多批次风控模板`
