# -*- coding: utf-8 -*-
"""
查询 contact_内部专用.db 中 nick_name 以「专属带领群」开头的所有记录。

说明：独立脚本，用于快速查看查询结果；主流程请运行 main.py。
"""
from __future__ import annotations

from pathlib import Path

from utils.db import DEFAULT_DB_PATH, fetch_group_nick_names

OUTPUT_PATH = Path(r"C:\Users\LENOVO\Desktop\query_result.txt")


def main() -> None:
    rows = fetch_group_nick_names()
    OUTPUT_PATH.write_text(
        f"共找到 {len(rows)} 条记录：\n"
        + "\n".join(f"{i}. {name}" for i, name in enumerate(rows, 1)),
        encoding="utf-8",
    )
    print(f"查询完成，共 {len(rows)} 条，结果已写入：{OUTPUT_PATH}")
    print(f"数据库：{DEFAULT_DB_PATH}")


if __name__ == "__main__":
    main()
