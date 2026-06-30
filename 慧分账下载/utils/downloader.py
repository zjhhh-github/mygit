# -*- coding: utf-8 -*-
"""
下载完成检测模块（wait_download_finish）
- 通过轮询下载目录，检测临时文件是否消失 + 出现新文件
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Optional

TEMP_SUFFIXES = (".crdownload", ".tmp", ".part")
DEFAULT_ALLOWED_EXTS = (".xlsx", ".xls", ".csv")


def snapshot_dir(directory: str | Path) -> set[str]:
    """对下载目录做一次快照，返回当前所有最终文件名集合（不含临时文件）。"""
    directory = Path(directory)
    return {p.name for p in directory.iterdir()
            if p.is_file() and not p.name.endswith(TEMP_SUFFIXES)}


def wait_download_finish(
    directory: str | Path,
    before_files: set[str],
    timeout: int = 120,
    poll_interval: float = 1.0,
    allowed_exts: Optional[Iterable[str]] = DEFAULT_ALLOWED_EXTS,
) -> Path:
    """等待下载完成，返回新生成文件的路径。

    判定（同时满足）：
        1. 目录里不存在任何 .crdownload / .tmp / .part 临时文件
        2. 出现至少一个 before_files 没有 + 扩展名命中 allowed_exts 的新文件

    allowed_exts 传 None 表示不过滤扩展名（任何新文件都算）。
    超时抛 TimeoutError，外层可在 except 里 driver.refresh() 重试。
    """
    directory = Path(directory)
    deadline = time.time() + timeout

    if allowed_exts is not None:
        allowed_norm = tuple(e.lower() for e in allowed_exts)
    else:
        allowed_norm = None

    while time.time() < deadline:
        files = list(directory.iterdir())
        has_temp = any(f.is_file() and f.name.endswith(TEMP_SUFFIXES) for f in files)
        current = {f.name for f in files
                   if f.is_file() and not f.name.endswith(TEMP_SUFFIXES)}
        new_files = current - before_files

        if allowed_norm is not None:
            new_files = {n for n in new_files if Path(n).suffix.lower() in allowed_norm}

        if not has_temp and new_files:
            newest = max(
                (directory / name for name in new_files),
                key=lambda p: p.stat().st_mtime,
            )
            return newest

        time.sleep(poll_interval)

    raise TimeoutError(f"下载等待超时（{timeout}s），目录: {directory}")
