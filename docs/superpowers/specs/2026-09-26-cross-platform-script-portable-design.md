# 风控矩阵审核器跨平台脚本启动便携版设计

## 1. 文档信息

- 设计日期：2026-09-26
- 目标平台：Windows 10/11 x64、macOS Apple Silicon
- 产品形态：单机、完全离线、本地 Web 应用
- Windows 入口：`启动审核器.bat`
- macOS 入口：`启动审核器.command`
- 交付方式：两个平台独立 ZIP
- 前端：HTML5、CSS、原生 JavaScript、本地 Bootstrap
- 后端：包内 CPython、FastAPI、Uvicorn
- 审核核心：现有 `risk_audit` Python 包
- 文档状态：对话设计已确认，待用户审核本文档

本文档修订 `2026-09-25-local-web-portable-design.md` 中的交付方式：保留已实现的本机 Web 架构、REST API、任务持久化、Worker 和离线前端，取消 PyInstaller EXE 交付，改为“平台启动脚本＋包内可搬移运行时”。

## 2. 目标与成功标准

用户解压对应平台的 ZIP 后，通过 BAT 或 `.command` 脚本在终端前台启动审核器。终端是应用生命周期的明确宿主：终端保持打开时程序运行，关闭终端时程序和它启动的审核进程结束。

成功标准：

1. Windows 用户双击 `启动审核器.bat`，macOS Apple Silicon 用户双击 `启动审核器.command`。
2. 终端在前台执行包内 Python，默认浏览器自动打开。
3. 关闭浏览器不停止服务或任务；再次运行启动脚本只重新打开已有页面。
4. 关闭宿主终端会结束主服务、Worker 和它们启动的 LibreOffice 进程，不留孤儿审核进程。
5. 网页中的“退出审核器”仍是正常退出途径；直接关闭终端是可中断任务的强制退出。
6. 目标电脑无需预装 Python、Python 依赖、Node.js、Qt 或 LibreOffice。
7. 运行时完全离线，只监听 `127.0.0.1` 随机端口，不发起外部网络请求。
8. 用户只选择输入目录，输出始终写入发布目录下的 `outputs`。
9. 两个平台包都可整体移动到含中文、空格的路径后运行。
10. 发布包不包含 PyInstaller、开发机绝对路径、测试依赖或开发缓存。

## 3. 范围

### 3.1 本次包含

- Windows 10/11 x64 独立 ZIP。
- macOS Apple Silicon 独立 ZIP。
- Windows BAT 前台启动。
- macOS `.command` 前台启动。
- 平台对应的可搬移 CPython 和全部运行依赖。
- 平台对应的 LibreOffice 运行目录。
- Windows 系统目录选择器和 macOS Finder 目录选择器。
- 平台路径、单实例、信号、进程树和打开文件适配。
- 固定版本和 SHA-256 的运行时来源清单。
- 平台发布构建、校验、ZIP 及使用说明。
- macOS 第一版的 Gatekeeper 解除隔离说明。

### 3.2 本次不包含

- Linux 发布包或 `.sh` 启动器。
- Intel Mac、Universal 2 或 Rosetta 兼容承诺。
- macOS Developer ID 签名、Apple 公证、DMG 或 PKG。
- Windows 安装包、EXE 启动器、Windows 服务或系统托盘。
- PyInstaller 或其他单文件/应用封装器。
- 将 Windows 和 macOS 二进制合并在同一 ZIP。
- 关闭终端前的业务确认弹窗；终端窗口关闭由操作系统管理。

## 4. 交付目录

Windows 和 macOS 使用同一逻辑结构，只替换启动器和平台运行时：

```text
风控矩阵审核器-v2.0.0/
├── 启动审核器.bat             # 仅 Windows ZIP
│   或 启动审核器.command     # 仅 macOS ZIP
├── app/
│   ├── risk_audit/
│   └── risk_audit_web/
├── runtime/
│   ├── python/
│   ├── libreoffice/
│   ├── web/
│   ├── resources/
│   │   ├── rulepacks/
│   │   ├── baselines/
│   │   └── entities/
│   ├── licenses/
│   └── manifest.json
├── data/
├── outputs/
└── 使用说明.md
```

两个平台分别生成：

```text
风控矩阵审核器-v2.0.0-Windows-x64.zip
风控矩阵审核器-v2.0.0-macOS-arm64.zip
```

ZIP 内只有一个顶层目录。`data` 和 `outputs` 为空可写目录，必须在 ZIP 中显式保留。macOS ZIP 必须保留 `.command`、Python 及 LibreOffice 内部可执行文件的 Unix 权限位。

## 5. 启动和生命周期

### 5.1 Windows BAT

BAT 脚本必须：

1. 使用 `%~dp0` 定位发布目录，不依赖当前工作目录。
2. 设置 `PYTHONUTF8=1`、`PYTHONNOUSERSITE=1` 和包内 `PYTHONPATH`。
3. 将已解析的绝对发布根写入 `RISK_AUDIT_APP_ROOT`，供后端定位可写目录和运行资源。
4. 以前台方式执行 `runtime\python\python.exe -m risk_audit_web.app`，不使用 `start`、`pythonw.exe` 或后台脱离。
5. 将启动失败和非零退出码显示在终端中。
6. 正常网页退出后返回零并结束 BAT。

### 5.2 macOS `.command`

`.command` 脚本必须：

1. 使用脚本自身路径定位发布目录，支持中文和空格。
2. 设置 `PYTHONUTF8=1`、`PYTHONNOUSERSITE=1` 和包内 `PYTHONPATH`。
3. 将已解析的绝对发布根写入 `RISK_AUDIT_APP_ROOT`，供后端定位可写目录和运行资源。
4. 使用 `exec runtime/python/bin/python3 -m risk_audit_web.app` 替换 Shell 进程，不使用 `nohup`、`&` 或后台脱离。
5. 保持终端窗口与服务生命周期一致。
6. 文件模式必须包含可执行位。

### 5.3 重复启动

- 第一次启动成功后，主进程写入包含 PID、随机端口和会话令牌的会话文件。
- 再次双击启动器时，新进程先获取单实例锁；若锁已被占用，则验证会话文件和本机健康接口。
- 验证成功时只使用默认浏览器重新打开现有页面，然后新进程以零退出码结束；原终端和原服务继续运行。
- 验证失败时不得盲目打开旧端口或启动第二个服务，而是输出可诊断错误并返回非零退出码。

### 5.4 正常退出与强制退出

- 正常退出：用户在页面中点击“退出审核器”。无活动任务时直接退出；有活动任务时先发出安全停止请求，待任务终态后关闭服务。
- 强制退出：用户直接关闭终端，可中断正在处理的工作单元。主进程对 `SIGINT`、`SIGTERM`、macOS `SIGHUP` 和 Windows 控制台关闭事件做有界清理，结束已跟踪的 Worker 及 LibreOffice 子进程。
- 意外断电或操作系统强制终止时不承诺完成清理；下次启动依靠任务恢复机制将无存活 Worker 的活动任务标记为“意外中断”。
- 已写入的中间输出不删除，原始输入始终不修改。

## 6. 平台适配

### 6.1 便携路径

`PortablePaths` 增加平台感知的 LibreOffice 可执行文件：

- Windows：`runtime/libreoffice/program/soffice.exe`
- macOS：`runtime/libreoffice/LibreOffice.app/Contents/MacOS/soffice`

其余 `runtime`、`data`、`outputs`、规则、基准和主体清单路径保持一致。发布模式的应用根不再由 PyInstaller `sys.executable` 推断，而由启动脚本通过受控环境变量 `RISK_AUDIT_APP_ROOT` 明确传入。该变量只接受已解析的绝对发布根，并必须通过启动器、`app`、`runtime`、`data` 和 `outputs` 结构校验。

### 6.2 目录选择

- Windows：后端调用系统自带 Windows PowerShell 和 .NET 文件夹选择对话框，使用 `-NoProfile`、`-STA` 和固定脚本内容。用户路径不拼接进 PowerShell 源码。
- macOS：后端通过 `/usr/bin/osascript` 调用 `choose folder` 并返回 POSIX 路径。
- 两个平台共用现有非阻塞锁，并发第二个请求返回 `409 DIRECTORY_PICKER_BUSY`。
- 用户取消返回 `selected=false`，系统命令失败返回稳定错误码和中文提示。

### 6.3 单实例

- Windows 继续使用命名 Mutex。
- macOS 使用 `fcntl.flock` 持有建议锁，锁文件放在系统临时目录。内核在进程结束时释放锁，避免终端强制关闭后残留死锁。
- 会话文件仍位于 `data/server-session.json`，重开前必须同时核对 PID、随机端口和会话令牌健康接口。

### 6.4 进程树

- macOS 主进程、Worker 和 LibreOffice 保持在启动终端的进程会话中；主进程同时跟踪所启动 Worker，在收到退出信号时停止它们。
- Windows 主进程、Worker 和它们的子进程进入带 `KILL_ON_JOB_CLOSE` 的 Windows Job Object。终端或主进程被关闭时，操作系统回收该 Job 中仍存活的进程。
- 强制退出不把未完成任务伪装为“已取消”；下次启动时由恢复流程标记为“意外中断”。

### 6.5 打开结果

- Windows 使用 `os.startfile` 调用资源管理器或默认应用。
- macOS 使用 `/usr/bin/open` 打开 Finder 目录或结果文件。
- 打开前继续由后端校验目标属于对应任务的输出目录。

## 7. 可搬移 Python 与依赖

### 7.1 Python 运行时

两个发布包分别使用固定 CPython 3.11 补丁版本的可搬移运行时：

- Windows x64 运行时。
- macOS arm64 运行时。

运行时来源 URL、版本、平台、大小和 SHA-256 记录在版本化清单中。构建时先校验摘要再解压，不允许不受控的“最新版” URL。

### 7.2 运行依赖锁

新建仅包含产品运行依赖的哈希锁文件，包含审核核心、FastAPI 和 Uvicorn 的依赖，不包含：

- pytest、httpx 等测试依赖。
- PyInstaller、altgraph、pefile、macholib 等冻结构建依赖。
- 已退役的 Qt 或语义可选依赖。

每个平台在其目标运行时内用 `--require-hashes` 安装第三方运行依赖。项目自己的 `risk_audit` 和 `risk_audit_web` 包只复制到发布目录的 `app`，由启动器设置的 `PYTHONPATH` 加载，不再重复安装到包内 Python。发布包内不保留 pip 下载缓存、wheel 文件或测试代码。

### 7.3 许可证

继续收集：

- Python 许可证。
- 每个已安装 Python 发行版的许可证或 Notice。
- Bootstrap 和 Bootstrap Icons 许可证。
- LibreOffice 及随附组件说明。

许可证收集只处理当前目标平台实际安装的发行版，必须正确评估 PEP 508 平台标记。

## 8. LibreOffice

### 8.1 Windows

构建环境下载固定版本的官方 Windows x64 MSI，校验 SHA-256，安装到临时构建目录后复制完整 LibreOffice 运行目录。目标电脑不运行 MSI，也不写系统注册表。

### 8.2 macOS

第一版使用当前 Apple Silicon Mac 上已验证的 LibreOffice arm64 `.app`，构建器接受显式 `--libreoffice` 路径，使用能保留应用包结构和权限的 macOS 原生复制方式，并校验：

- `Contents/MacOS/soffice` 存在且可执行。
- 架构包含 arm64。
- 版本与发布清单一致。
- 官方签名在复制前后保持可验证，不修改 `.app` 内容。

后续自动化可改为下载固定官方 DMG，但不改变发布目录契约。

### 8.3 转换隔离

两个平台继续为每个 Worker 使用独立 LibreOffice profile 和临时目录，禁用宏、活动内容和外链更新。并行任务不共用 profile。

## 9. 构建链

### 9.1 通用组装器

新建平台无关的发布组装器，输入为：

- 平台标识 `windows-x64` 或 `macos-arm64`。
- 已解压的目标 Python 运行时。
- 已准备的 LibreOffice 运行目录。
- 项目源码、静态前端、规则、基准和主体清单。
- 第三方许可证目录。

组装器不跨平台执行目标 Python：Windows 包在 Windows 环境构建，macOS arm64 包在 Apple Silicon Mac 环境构建。

### 9.2 Windows 构建

- 替换现有 PyInstaller 工作流。
- 在 Windows x64 构建环境中获取锁定的独立 Python 和 LibreOffice。
- 安装锁定运行依赖、组装发布目录、运行平台测试和分发校验器。
- 上传 `风控矩阵审核器-v2.0.0-Windows-x64.zip`。

### 9.3 macOS 构建

- 第一版必须可在当前 Apple Silicon Mac 上本地构建。
- 构建命令显式接收包内 Python 来源和 `/Applications/LibreOffice.app`。
- 组装后在临时目录解压最终 ZIP，使用包内 Python 运行测试和烟雾验收。
- 输出 `风控矩阵审核器-v2.0.0-macOS-arm64.zip`。
- 只有在可用的 Apple Silicon CI 运行器上才新增自动工作流；第一版不用 Intel CI 交叉伪造 arm64 验收。

### 9.4 可重现性

- 所有下载资源固定版本、URL 和 SHA-256。
- 构建日期不进入 runtime 文件内容；ZIP 条目时间使用固定值或在验证中排除。
- `runtime/manifest.json` 记录不可变载荷（`app`、`runtime` 中除清单自身外的文件、平台启动器和使用说明）的相对路径、大小和 SHA-256；可写的 `data`、`outputs` 不进入摘要清单。
- 构建失败时删除本次临时半成品，不覆盖已有发布目录或 ZIP。

## 10. 发布校验

校验器按平台检查：

1. ZIP 只有一个顶层目录。
2. 根目录只包含该平台启动器、`app`、`runtime`、`data`、`outputs` 和说明。
3. 启动器不包含网络下载、系统 Python、未引用变量或开发机路径。
4. 启动器和平台 Python 可执行文件存在；macOS 权限位正确。
5. LibreOffice 可执行文件路径和平台匹配。
6. `runtime/manifest.json` 完整覆盖约定的不可变载荷且摘要正确，不包含 `data` 和 `outputs`。
7. 浏览器运行文件不引用 CDN、远程字体、远程图片或外部 API。
8. 发布包不含 PyInstaller bootloader、Qt/QML、`__pycache__`、`.pyc`、`.venv`、测试目录或开发机绝对路径。
9. Python、Python 发行版、Bootstrap、Bootstrap Icons 和 LibreOffice 许可证齐全。
10. `data` 和 `outputs` 可写，且用户不能通过 API 指定其他输出根。

## 11. 测试与验收

### 11.1 共用自动测试

- 现有 Web、REST、安全、任务、恢复、并行和路径测试继续通过。
- 新增启动器契约和临时目录行为测试。
- 新增平台路径、目录选择、单实例锁和终端信号测试。
- 新增分发目录、权限位、ZIP 单顶层和禁止文件测试。
- 删除只验证 PyInstaller、EXE、`_internal` 或 `onedir` 的旧测试。

### 11.2 macOS arm64 真实验收

在当前 Apple Silicon Mac 上验证：

1. 最终 ZIP 解压后可由 `启动审核器.command` 启动。
2. 终端保持打开，浏览器自动打开。
3. 页面调用 macOS 目录选择器并返回中文、空格路径。
4. 创建任务时只写入发布根的 `outputs`。
5. 包内 LibreOffice 完成一次真实 XLS/DOC 转换。
6. 关闭浏览器后任务继续，再次运行 `.command` 重开页面。
7. 关闭终端后主服务、Worker 和它启动的 LibreOffice 进程不再存活。
8. 重新启动后，中断任务显示为“意外中断”，已有输出保留。
9. 整体移动发布目录后重复启动和创建任务。
10. 断网环境中页面、审核和转换全部可用。

### 11.3 Windows x64 验收

通过 Windows 构建环境生成 ZIP，并在无预装 Python、Node.js、Qt 和 LibreOffice 的 Windows 10/11 x64 环境验证：

- BAT 前台启动、默认浏览器和单实例。
- PowerShell 文件夹选择器。
- 并行任务和固定 `outputs`。
- 关闭终端后 Job Object 内进程全部结束。
- 整体目录移动、断网运行和完整性校验。

## 12. macOS Gatekeeper 第一版策略

第一版不持有 Apple Developer ID，不做公证。使用说明明确要求在解压目录的上级目录执行：

```bash
xattr -dr com.apple.quarantine '风控矩阵审核器-v2.0.0'
chmod +x '风控矩阵审核器-v2.0.0/启动审核器.command'
```

说明必须解释：

- `xattr` 只用于用户已确认来源的本项目发布目录。
- 命令不得使用 `sudo`，不得作用于用户主目录或其他广泛路径。
- 解除隔离后运行仍完全离线。
- 未来加入 Developer ID 时可替换本节，不改变启动器和 runtime 目录契约。

## 13. 错误处理

- 启动器找不到包内 Python 时，终端显示“发布包不完整”及精确相对路径，返回非零退出码。
- runtime 清单、LibreOffice、规则、基准或主体清单无效时，页面显示诊断并禁止创建任务。
- PowerShell 或 `osascript` 目录选择失败时，API 返回稳定错误码，本机日志记录系统错误。
- 无法创建单实例锁或无法确认已有会话时，程序返回非零退出码，不启动第二个未受控服务。
- 端口、Host、Origin 或会话令牌不匹配时继续拒绝请求，不因脚本启动放宽安全边界。
- 构建中任一下载摘要、许可证、运行文件或平台架构校验失败时拒绝生成 ZIP。

## 14. 文档变更

以下文档统一改为脚本启动口径：

- 根目录 `使用说明.md`。
- `审核器/README.md`。
- Windows 使用说明。
- 新增 macOS Apple Silicon 使用说明。
- `风控矩阵审核器使用说明.docx`。

文档必须明确：

- 两个平台分别下载对应 ZIP。
- 终端窗口必须在运行期间保持打开。
- 关闭浏览器不退出，关闭终端会强制结束。
- 正常退出应使用页面的“退出审核器”。
- 输出不可选择，固定在 `outputs`。
- macOS 第一版的解除隔离命令和安全边界。

## 15. 实施约束

- 不保留 PyInstaller 兼容入口、EXE 分发逻辑或旧打包测试。
- 不为 Linux 预先新增未使用的抽象层或 `.sh` 脚本。
- Python 命名使用 `snake_case`，JavaScript 命名使用 `camelCase`，启动器变量使用清晰的大写名称。
- 所有新增 Python 函数必须包含用途、参数和返回值说明。
- 重要的路径信任、进程终止、平台命令、摘要校验和权限位代码必须有中文注释。
- API 继续遵守 RESTful 资源语义，不为平台脚本新增专有网页接口。
- 遵循 TDD：新平台契约和每个故障边界先证明测试失败，再实现最小代码。
- 按项目规则不执行 `git commit`；每个完成功能只输出建议的中文 Conventional Commit 信息。
