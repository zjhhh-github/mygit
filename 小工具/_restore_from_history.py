# -*- coding: utf-8 -*-
"""一次性脚本：从 Cursor 本地历史恢复误删的小工具脚本。"""
from __future__ import annotations

import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
HISTORY = Path.home() / "AppData" / "Roaming" / "Cursor" / "User" / "History"

RESTORE_MAP = {
    HISTORY / "-5334e3c1" / "wMf3.py": BASE / "联系人搜索" / "搜索联系人_GUI.py",
    HISTORY / "30bd601b" / "S4Ag.py": BASE / "联系人搜索" / "搜索联系人.py",
    HISTORY / "-6a9aca0" / "k9Tu.py": BASE / "联系人搜索" / "_check_table.py",
    HISTORY / "-32e43c8" / "QzvY.py": BASE / "微信数据库同步" / "拷贝内部专用数据库到桌面.py",
}

for src, dst in RESTORE_MAP.items():
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not src.is_file():
        raise FileNotFoundError(f"历史文件不存在：{src}")
    shutil.copy2(src, dst)
    print(f"已恢复：{dst.name} -> {dst.parent.name}/")

icon_src = BASE / "icon.ico"
icon_dst = BASE / "联系人搜索" / "icon.ico"
if icon_src.is_file() and not icon_dst.is_file():
    shutil.copy2(icon_src, icon_dst)
    print("已复制 icon.ico -> 联系人搜索/")

print("恢复完成。")
