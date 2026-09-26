#!/bin/zsh

# 只以脚本所在目录为发布根，避免双击启动时受到终端当前目录影响。
APP_ROOT="${0:A:h}"
PYTHON_EXECUTABLE="$APP_ROOT/runtime/python/bin/python3"

export RISK_AUDIT_APP_ROOT="$APP_ROOT"
export PYTHONPATH="$APP_ROOT/app"
export PYTHONUTF8=1
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1

if [[ ! -x "$PYTHON_EXECUTABLE" ]]; then
  print -u2 -- "发布包不完整：缺少可执行文件 runtime/python/bin/python3"
  exit 1
fi

# exec 让 Python 直接继承终端生命周期，禁止后台脱离。
exec "$PYTHON_EXECUTABLE" -m risk_audit_web.app
