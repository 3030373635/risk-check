#!/bin/zsh

# 本入口只在 Apple Silicon Mac 上构建 macOS arm64 发布包。
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  print -u2 -- "构建失败：只支持 Apple Silicon macOS（arm64）。"
  exit 2
fi

TOOLS_ROOT="${0:A:h}"
AUDITOR_ROOT="${TOOLS_ROOT:h}"
REPOSITORY_ROOT="${AUDITOR_ROOT:h}"
BUILD_PYTHON="$AUDITOR_ROOT/.venv/bin/python"
LIBREOFFICE_ROOT="${1:-/Applications/LibreOffice.app}"
PYTHON_ARCHIVE="${2:-}"
DIST_ROOT="$REPOSITORY_ROOT/dist"
EXPECTED_ARCHIVE="$DIST_ROOT/风控矩阵审核器-v2.0.0-macOS-arm64.zip"

if [[ ! -x "$BUILD_PYTHON" ]]; then
  print -u2 -- "构建失败：缺少项目 Python：审核器/.venv/bin/python"
  exit 2
fi
if [[ ! -d "$LIBREOFFICE_ROOT" ]]; then
  print -u2 -- "构建失败：LibreOffice.app 不存在：$LIBREOFFICE_ROOT"
  exit 2
fi
if [[ -e "$EXPECTED_ARCHIVE" ]]; then
  print -u2 -- "构建失败：发布 ZIP 已存在：$EXPECTED_ARCHIVE"
  exit 2
fi

# 签名应用包在系统临时目录组装，避免桌面目录的扩展属性复制限制。
STAGING_ROOT="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/risk-audit-build.XXXXXX")"
if [[ -z "$STAGING_ROOT" || ! -d "$STAGING_ROOT" ]]; then
  print -u2 -- "构建失败：无法创建临时组装目录。"
  exit 2
fi
OUTPUT_ROOT="$STAGING_ROOT/风控矩阵审核器-v2.0.0"
STAGED_ARCHIVE="$STAGING_ROOT/风控矩阵审核器-v2.0.0-macOS-arm64.zip"

# 清理本次构建的临时目录；无参数。
cleanup_staging_root() {
  rm -rf -- "$STAGING_ROOT"
}
trap cleanup_staging_root EXIT

BUILD_ARGUMENTS=(
  --platform macos-arm64
  --project-root "$REPOSITORY_ROOT"
  --libreoffice "$LIBREOFFICE_ROOT"
  --licenses "$AUDITOR_ROOT/licenses"
  --usage-guide "$AUDITOR_ROOT/macOS Apple Silicon Web版使用说明.md"
  --output "$OUTPUT_ROOT"
)
if [[ -n "$PYTHON_ARCHIVE" ]]; then
  BUILD_ARGUMENTS+=(--python-archive "$PYTHON_ARCHIVE")
fi

# 构建器会完成目标 Python 校验、依赖安装、组装和 ZIP 往返验收。
"$BUILD_PYTHON" "$AUDITOR_ROOT/tools/build_portable.py" "${BUILD_ARGUMENTS[@]}"
BUILD_EXIT_CODE=$?
if [[ $BUILD_EXIT_CODE -eq 0 ]]; then
  mkdir -p "$DIST_ROOT"
  /usr/bin/ditto --noextattr --noqtn --noacl "$STAGED_ARCHIVE" "$EXPECTED_ARCHIVE"
  BUILD_EXIT_CODE=$?
fi
if [[ $BUILD_EXIT_CODE -eq 0 ]]; then
  print -- "构建完成：$EXPECTED_ARCHIVE"
fi
exit $BUILD_EXIT_CODE
