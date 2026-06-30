# -*- coding: utf-8 -*-
"""
日志工具（编码安全版）
==============================================
- 所有日志同时输出到控制台 + LOG_PATH（UTF-8）
- 控制台 print 用 encoding_utils.safe_str 兜底，杜绝 UnicodeEncodeError
- 写文件 errors='replace'，杜绝 latin-1 / GBK 写入崩溃
"""

from datetime import datetime

import config
import encoding_utils


def _write(level: str, msg: str) -> None:
    safe_msg = encoding_utils.safe_str(msg)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {safe_msg}"

    # 控制台输出（控制台已被 setup_console 设为 utf-8 + replace，这里再保险）
    try:
        print(line)
    except Exception:
        # 极端兜底：转 ascii 再打
        try:
            print(line.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass

    # 文件输出
    try:
        with open(config.LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        # 写日志失败不影响主流程
        pass


def info(msg: str) -> None:
    _write("INFO", msg)


def warn(msg: str) -> None:
    _write("WARN", msg)


def error(msg: str) -> None:
    _write("ERROR", msg)


def success(msg: str) -> None:
    _write("OK", msg)


def reset() -> None:
    """清空日志文件，每次运行 main.py 开头调用一次"""
    try:
        with open(config.LOG_PATH, "w", encoding="utf-8", errors="replace") as f:
            f.write(
                f"# 日志开始：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
    except Exception:
        pass
