"""将审核结果中的程序输出列清理到新目录。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
# 交付包内的源码是当前版本，直接运行时不依赖环境中已安装的旧版本。
sys.path.insert(0, str(ROOT / "risk-audit/src"))

from risk_audit.cleanup import clean_audit_directory


def main(argv: list[str] | None = None) -> int:
    """执行审核列清理。

    Args:
        argv: 可选命令行参数；未传入时读取系统命令行。
    """

    parser = argparse.ArgumentParser(description="删除审核结果中的程序输出列，并将结果写入新目录。")
    parser.add_argument("--input", required=True, help="待清理的审核结果目录")
    parser.add_argument("--output", required=True, help="不存在的清理结果目录")
    arguments = parser.parse_args(argv)
    try:
        summary = clean_audit_directory(arguments.input, arguments.output)
    except (FileNotFoundError, FileExistsError, ValueError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
