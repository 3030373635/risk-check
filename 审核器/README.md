# 风控矩阵审核器

风控矩阵审核器 2.0 是完全离线的本机 Web 应用。发布包通过 Windows BAT 或 macOS `.command` 在终端前台运行包内 Python，只监听 `127.0.0.1` 随机端口，然后用默认浏览器打开页面。审核材料不会上传，前端也不访问 CDN 或外部 API。

## 用户启动方式

- Windows 10/11 x64：完整解压 `风控矩阵审核器-v2.0.0-Windows-x64.zip`，双击 `启动审核器.bat`。
- Apple Silicon macOS：完整解压 `风控矩阵审核器-v2.0.0-macOS-arm64.zip`，双击 `启动审核器.command`。

运行期间需保持终端窗口打开。关闭浏览器不会退出程序；再次双击同一启动脚本会重新打开已有页面。正常退出请使用页面的“退出审核器”按钮；关闭终端会强制结束服务和当前审核进程。

用户只选择输入目录。输出位置固定为发布根下的 `outputs`，页面只显示后端生成的输出预览。

## 发布目录

- `app`：`risk_audit` 和 `risk_audit_web` 产品源码。
- `runtime/python`：平台对应的便携 Python 和锁定运行依赖。
- `runtime/web`：本地 HTML、CSS、JavaScript、Bootstrap 和图标字体。
- `runtime/resources`：规则包、审核配置、基准和会计主体清单。
- `runtime/libreoffice`：平台对应的 LibreOffice 转换运行时。
- `runtime/licenses`：Python、第三方依赖、Bootstrap 和 LibreOffice 许可证。
- `runtime/manifest.json`：不可变载荷的文件大小和 SHA-256。
- `data`：任务索引、服务会话和日志。
- `outputs`：所有审核输出，每个任务使用独立子目录。

## 本地开发

项目需要 Python 3.11 或更高版本。

```bash
uv sync --no-install-project --extra web --extra test
PYTHONPATH=src .venv/bin/python -m pytest tests/web -q
PYTHONPATH=src .venv/bin/python -m risk_audit_web.app
```

Web 前端使用普通 HTML、CSS、JavaScript 和本地 Bootstrap，不需要 Node.js 构建步骤。

```bash
.venv/bin/python tools/vendor_web_assets.py --verify
```

## 发布构建

Windows x64 发布由 `.github/workflows/build-windows-web.yml` 手动触发，Apple Silicon macOS 发布由 `.github/workflows/build-macos-web.yml` 手动触发。两个工作流都会校验锁定的 Python 和 LibreOffice 来源，在目标 Python 中使用 `requirements-runtime.lock` 安装带哈希的纯运行依赖，再调用对应平台的便携构建入口。

在 GitHub 仓库的 `Actions` 页面选择目标平台工作流，点击 `Run workflow`。构建成功后，从该次运行的 `Artifacts` 下载对应 ZIP。macOS 工作流固定使用 `macos-15` Apple Silicon runner，并校验官方 LibreOffice ARM64 DMG 后构建。

开发者仍可在 Apple Silicon Mac 本地运行 `./tools/build_macos_portable.command` 做发布预检；正式交付包以 GitHub Actions 的 Artifact 为准。

构建器会产生对应平台 ZIP，并通过 `tools/verify_portable_distribution.py --platform ...` 验证启动器、架构、发布清单、离线前端、许可证和禁止文件。

## 命令行核心

`risk-audit` 命令仍供开发、规则维护和自动化测试使用。普通审核人员只需使用 Web 页面。默认活动规则版本记录在 `rulepacks/active.json`，已发布规则不得原地修改。
