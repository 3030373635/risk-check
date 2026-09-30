"""提供非任务用途的小型 JSON 文件原子写入。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Mapping


WRITE_ATTEMPTS = 10
WRITE_RETRY_SECONDS = 0.01


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """原子写入 JSON；path 为目标文件，value 为可序列化映射。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            # 先持久化完整临时文件，再通过同目录替换发布。
            os.fsync(handle.fileno())
        for attempt in range(WRITE_ATTEMPTS):
            try:
                os.replace(temporary_path, path)
                temporary_path = None
                return
            except PermissionError:
                if attempt == WRITE_ATTEMPTS - 1:
                    raise
                # Windows 读取端可能短暂占用目标文件。
                time.sleep(WRITE_RETRY_SECONDS)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
