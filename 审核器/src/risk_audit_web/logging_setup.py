"""本机 Web 服务日志轮转配置。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_server_logging(
    data_root: Path,
    *,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> logging.Logger:
    """配置服务日志；data_root 为 data 根，其余参数控制轮转，返回 logger。"""
    log_path = data_root / "logs/server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger = logging.getLogger("risk_audit_web")
    for current in list(logger.handlers):
        current.close()
        logger.removeHandler(current)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger

