# -*- coding: utf-8 -*-
"""飞书表本地缓存读取工具。"""
from __future__ import annotations

import json
from typing import List


def 读取本地飞书缓存(路径: str) -> List[dict]:
    """从本地缓存 JSON 读取 records。"""
    with open(路径, "r", encoding="utf-8") as f:
        payload = json.load(f)
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise RuntimeError(f"缓存文件 records 格式错误：{路径}")
    return records
