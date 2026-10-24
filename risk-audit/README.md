# 重庆风控矩阵审核器

风控矩阵审核器 2.2.3 是完全离线的本机 Web 应用。发布包通过 Windows BAT 或 macOS `.command` 在终端前台运行包内 Python，只监听 `127.0.0.1` 随机端口，然后用默认浏览器打开页面。审核材料不会上传，前端也不访问 CDN 或外部 API。

## 用户启动方式

- Windows 10/11 x64：完整解压 `重庆风控矩阵审核器-v2.2.3-Windows-x64.zip`，双击 `启动审核器.bat`。
- Apple Silicon macOS：完整解压 `重庆风控矩阵审核器-v2.2.3-macOS-arm64.zip`，双击 `启动审核器.command`。

运行期间需保持终端窗口打开。关闭浏览器不会退出程序；再次双击同一启动脚本会重新打开已有页面。正常退出请使用页面的“退出审核器”按钮；关闭终端会强制结束服务和当前审核进程。

创建任务时只需选择输入目录。当前分支和发布包固定使用重庆规则，页面不提供地区选择。每个任务仍会记录地区、规则版本和内容哈希。输出位置固定为发布根下的 `outputs`。

当前发布包交付重庆 1.9.36 规则，以 1024 审核确认稿的六条规则为准。具体检查资料完整性、矩阵与岗位清单编号对应、不相容岗位清单、不相容岗位与岗位责任清单匹配、系统状态与控制方式对应，以及按单位性质适用的标准库措施完整性。两个附件已编译并冻结在规则包中，原始附件同时保留用于复验。每家公司新增一张“标准库比对结果-xx公司.xlsx”，正常项显示“无异常”且不计入问题数量；缺项意见按业务归集写回审核意见列。

公司名称或唯一简称能够匹配附件2的，同一公司的跨目录材料合并审核；无法唯一确认时保留目录边界并提示核实。岗位排序、模板责任主体辅助列和系统类型预处理保留，模板为可选资源。旧版人员规范、角色职责、人员分离、一人多岗和重复项等已删除检查不再执行。重新审核会清理可识别的旧程序意见并保留人工意见。详见 [1024更新说明](1024重庆六条规则更新说明-v2.2.3.md)。

## 发布目录

- `app`：`risk_audit` 和 `risk_audit_web` 产品源码。
- `runtime/python`：平台对应的便携 Python 和锁定运行依赖。
- `runtime/web`：本地 HTML、CSS、JavaScript、Bootstrap 和图标字体。
- `runtime/resources`：重庆地区索引、规则包、审核配置和模板基准。
- `runtime/libreoffice`：平台对应的 LibreOffice 转换运行时。
- `runtime/licenses`：Python、第三方依赖、Bootstrap 和 LibreOffice 许可证。
- `runtime/manifest.json`：不可变载荷的文件大小和 SHA-256。
- `data`：`tasks.sqlite3` 任务历史数据库、服务会话和日志。数据库在首次启动时创建。
- `outputs`：审核结果、Worker 日志和任务临时目录，每个任务使用独立子目录。

每次在 Web 页面成功创建审核请求就是一个任务，无论最终完成、失败、取消或中断，都会保留在任务列表中。复制或删除 `outputs` 下的目录不会新增或删除任务记录；如删除结果目录，历史任务仍会显示，但对应结果将无法打开。普通 `risk-audit audit`、`risk-audit trial` 命令不会写入桌面端任务数据库，也不会出现在 Web 任务列表中。

## 本地开发

项目需要 Python 3.11 或更高版本。

```bash
uv sync --no-install-project --extra web --extra test
PYTHONPATH=src .venv/bin/python -m pytest tests/web -q
./tools/start_dev.command
```

`start_dev.command` 固定使用项目 `.venv` 和当前 `src`，启动前会清除可能遗留的 `RISK_AUDIT_APP_ROOT`。开发模式直接读取仓库中的 `regions`、`rulepacks` 和 `baselines`，任务数据及结果分别写入项目的 `data` 和 `outputs`，不需要先构建便携包。终端启动时会显示实际应用版本、规则版本和资源根目录。

macOS 默认使用 `/Applications/LibreOffice.app/Contents/MacOS/soffice`。如果 LibreOffice 安装在其他位置，可在启动前指定绝对路径：

```bash
RISK_AUDIT_DEV_SOFFICE="/absolute/path/to/soffice" ./tools/start_dev.command
```

如果终端提示另一个审核器实例正在运行，请先在原页面点击“退出审核器”，再执行开发启动器。Web 前端使用普通 HTML、CSS、JavaScript 和本地 Bootstrap，不需要 Node.js 构建步骤。

```bash
.venv/bin/python tools/vendor_web_assets.py --verify
```

## 发布构建

Windows x64 发布由 `.github/workflows/build-windows-web.yml` 手动触发，Apple Silicon macOS 发布由 `.github/workflows/build-macos-web.yml` 手动触发。两个工作流都会校验锁定的 Python 和 LibreOffice 来源，在目标 Python 中使用 `requirements-runtime.lock` 安装带哈希的纯运行依赖，再调用对应平台的便携构建入口。

在 GitHub 仓库的 `Actions` 页面选择目标平台工作流，点击 `Run workflow`。构建成功后，从该次运行的 `Artifacts` 下载对应 ZIP。macOS 工作流固定使用 `macos-15` Apple Silicon runner，并校验官方 LibreOffice ARM64 DMG 后构建。

开发者仍可在 Apple Silicon Mac 本地运行 `./tools/build_macos_portable.command` 做发布预检；正式交付包以 GitHub Actions 的 Artifact 为准。

构建器会产生对应平台 ZIP，并通过 `tools/verify_portable_distribution.py --platform ...` 验证启动器、架构、发布清单、离线前端、许可证和禁止文件。

## 命令行核心

`risk-audit` 命令仍供开发、规则维护和自动化测试使用。普通审核人员只需使用 Web 页面。Web 任务从 `regions/index.json` 读取唯一的重庆规则包，不依赖全局 `active.json`。已发布规则不得原地修改。
