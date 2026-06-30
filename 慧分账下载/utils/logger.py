# -*- coding: utf-8 -*-
"""
日志模块（基于 loguru）
- 同时输出到控制台和文件 logs/runtime.log
- 文件按 5MB 滚动，保留 7 个，UTF-8 编码
- 提供 setup_logger() 一次性初始化；其他模块直接 from loguru import logger 即可使用
"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

_INITIALIZED = False


def setup_logger(log_dir: str | Path = "logs") -> "logger":
    """初始化 loguru 全局 logger。重复调用会跳过。"""
    global _INITIALIZED
    if _INITIALIZED:
        return logger

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()

    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>[{time:YYYY-MM-DD HH:mm:ss}]</green> "
               "<level>[{level}]</level> {message}",
        colorize=True,
        enqueue=False,
    )

    logger.add(
        log_dir / "runtime.log",
        level="DEBUG",
        rotation="5 MB",
        retention=7,
        encoding="utf-8",
        format="[{time:YYYY-MM-DD HH:mm:ss}] [{level}] {message}",
        enqueue=True,
    )

    _INITIALIZED = True
    return logger
