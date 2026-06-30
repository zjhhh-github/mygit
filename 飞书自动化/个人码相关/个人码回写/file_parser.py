# -*- coding: utf-8 -*-
"""
本地文件扫描
==============================================
列出指定目录下所有支持后缀的图片，返回结构化文件信息：

    {
        "raw_name":   原始文件名（含扩展名，可能含 ¿ / 控制字符）
        "raw_stem":   原始去扩展名（仅用于人工核对 / 日志展示）
        "clean_stem": 清洗后的 stem，用于和飞书"编号"做包含匹配（推荐）
        "abs_path":   绝对路径（用于 open / 上传）
    }

扫描过程对任意编码 / 任意乱码文件名都不会崩溃。
"""

from __future__ import annotations

import os
from typing import List, Dict

import config
import encoding_utils


def scan_images(folder: str | None = None) -> List[Dict[str, str]]:
    """
    扫描 folder 下的所有图片，按 clean_stem 升序返回。

    单条文件如果 os.listdir 返回了带代理字符的字符串（极端乱码情况下
    Windows 会出现），也会被 encoding_utils.clean_filename 兜底替换为
    安全字符串，从而避免后续 print / 字符串包含运算崩溃。
    """
    folder = folder or config.INPUT_DIR
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"目录不存在：{folder}")

    out: List[Dict[str, str]] = []
    for raw_name in os.listdir(folder):
        full = os.path.join(folder, raw_name)
        if not os.path.isfile(full):
            continue

        raw_stem, ext = os.path.splitext(raw_name)
        if ext.lower() not in config.IMAGE_EXTS:
            continue

        out.append(
            {
                "raw_name":   raw_name,
                "raw_stem":   raw_stem,
                "clean_stem": encoding_utils.clean_filename(raw_stem),
                "abs_path":   os.path.abspath(full),
            }
        )

    out.sort(key=lambda x: x["clean_stem"])
    return out
