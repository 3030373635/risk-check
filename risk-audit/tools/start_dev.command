#!/bin/zsh
set -eu

AUDITOR_ROOT="${0:A:h:h}"
PYTHON_EXECUTABLE="$AUDITOR_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON_EXECUTABLE" ]]; then
    print -u2 -- "开发环境 Python 不存在：$PYTHON_EXECUTABLE"
    print -u2 -- "请先在 $AUDITOR_ROOT 执行：uv sync --no-install-project --extra web --extra test"
    exit 1
fi

# 禁止继承旧便携包的资源根，确保源码与仓库规则始终成对。
unset RISK_AUDIT_APP_ROOT
export PYTHONPATH="$AUDITOR_ROOT/src"

print -- "正在从当前仓库启动开发模式：$AUDITOR_ROOT"
exec "$PYTHON_EXECUTABLE" -m risk_audit_web.app
