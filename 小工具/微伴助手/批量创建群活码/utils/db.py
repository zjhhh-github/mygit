# -*- coding: utf-8 -*-
"""
数据库查询：从 contact_内部专用.db 读取群昵称列表。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# 默认数据库路径与查询前缀（与 query_contact.py 保持一致）
DEFAULT_DB_PATH = Path(r"C:\Users\LENOVO\Desktop\contact_内部专用.db")
DEFAULT_NICK_NAME_PREFIX = "专属带领群"


def fetch_group_nick_names(
    db_path: str | Path | None = None,
    prefix: str = DEFAULT_NICK_NAME_PREFIX,
) -> list[str]:
    """查询 nick_name 或 remark 以指定前缀开头的所有记录。

    参数:
        db_path: SQLite 数据库路径，默认使用桌面 contact_内部专用.db
        prefix: 前缀关键词，默认「专属带领群」；
                同时匹配 nick_name LIKE prefix% 或 remark LIKE prefix%

    返回:
        有效名称列表（去重后按名称排序）。
        nick_name 有值时用 nick_name；nick_name 为空时用 remark 代替，
        确保返回的每个名称都可直接用于填写表单和搜索群聊。
    """
    db_path = Path(db_path or DEFAULT_DB_PATH)
    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在：{db_path}")

    like_pattern = prefix + "%"

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        # 同时查 nick_name 和 remark，两列都取出来用于后处理
        cursor.execute(
            """
            SELECT nick_name, remark
            FROM contact
            WHERE nick_name LIKE ?
               OR remark LIKE ?
            """,
            (like_pattern, like_pattern),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    # nick_name 有值用 nick_name，否则用 remark；去重后排序
    seen: set[str] = set()
    result: list[str] = []
    for nick_name, remark in rows:
        # 优先取 nick_name，为空则回退到 remark
        name = (nick_name or "").strip() or (remark or "").strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)

    return sorted(result)
