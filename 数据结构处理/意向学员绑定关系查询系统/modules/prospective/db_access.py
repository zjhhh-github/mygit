# -*- coding: utf-8 -*-
"""
意向学员系统 —— 数据库访问层
==============================

包含三个职责：

    1. DatabaseReader：超微 db3 主/备路径自动切换的 SQLite 连接器
    2. get_database_connection(primary, backup)：上述类的便捷工厂函数
    3. get_contact_db_path(network_base, local_backup)：
       动态在 NAS 上找最新的 chatlog_backup 文件夹，返回其中
       db_storage/contact/contact.db 路径；网络不可达则回退到本地副本路径
    4. get_short_path(full_path, num_parts=3)：截断路径只显示最后 N 级，
       供状态栏显示使用

设计要点：
    - 函数签名显式接受路径参数，不再依赖模块级全局；
      历史调用方（db_viewer.DatabaseViewer）通过自身模块顶部的常量转发即可，
      继续兼容「外部覆写模块全局变量」这种依赖注入方式。
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional, Tuple


class DatabaseReader(object):
    """数据库读取器，支持主/备路径自动切换"""

    def __init__(self, primary_path, backup_path):
        """
        Args:
            primary_path: 主数据库文件路径
            backup_path: 备用数据库文件路径
        """
        self.primary_path = Path(primary_path)
        self.backup_path = Path(backup_path)
        self.current_db_path = None  # type: Optional[Path]

    def connect(self):
        # type: () -> Tuple[Optional[sqlite3.Connection], str]
        """
        尝试连接数据库：优先使用主路径，失败时切换到备用路径。

        Returns:
            (连接对象, 状态消息) 元组
        """
        conn, msg = self._try_connect(self.primary_path, is_primary=True)
        if conn:
            return conn, msg

        conn, msg = self._try_connect(self.backup_path, is_primary=False)
        return conn, msg

    def _try_connect(self, db_path, is_primary):
        # type: (Path, bool) -> Tuple[Optional[sqlite3.Connection], str]
        """
        尝试连接指定路径的数据库。

        Args:
            db_path: 数据库文件路径
            is_primary: 是否为主路径（仅用于状态消息文案）

        Returns:
            (连接对象, 状态消息) 元组
        """
        path_type = "主路径" if is_primary else "备用路径"

        if not db_path.exists():
            return None, "❌ {}文件不存在: {}".format(path_type, db_path)

        if not db_path.is_file():
            return None, "❌ {}不是有效文件: {}".format(path_type, db_path)

        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()

            self.current_db_path = db_path
            return conn, "✓ 成功连接{}: {}".format(path_type, db_path)

        except sqlite3.DatabaseError as e:
            return None, "❌ {}数据库损坏或格式错误: {}".format(path_type, e)
        except PermissionError:
            return None, "❌ {}权限不足,无法访问: {}".format(path_type, db_path)
        except Exception as e:
            return None, "❌ {}连接失败: {}: {}".format(path_type, type(e).__name__, e)


def get_database_connection(primary_db, backup_db):
    # type: (str, str) -> Tuple[Optional[sqlite3.Connection], str]
    """
    获取数据库连接的便捷函数。

    Args:
        primary_db: 主数据库路径
        backup_db: 备用数据库路径

    Returns:
        (连接对象, 状态消息) 元组
    """
    reader = DatabaseReader(primary_db, backup_db)
    return reader.connect()


def get_contact_db_path(network_base, local_backup):
    """
    动态获取 contact.db 数据库路径（智能日期+时间排序）

    逻辑：
        1. 尝试访问 network_base（一般为 X:\\chatlog_backup）
        2. 筛选出尾部包含日期（可选时间）的文件夹，兼容两种命名格式：
           - 旧格式：xxx_YYYYMMDD          （如 wxid_xxx_6ded_20260417）
           - 新格式：xxx_YYYYMMDD_HHMM      （如 wxid_xxx_6ded_20260417_1915）
        3. 用 _ 分割文件夹名称，按规则提取日期与可选时间
        4. 按"日期 + 时间"统一排序，取最新（旧格式无时间时按 0000 处理）
        5. 拼接 db_storage/contact/contact.db 并验证文件存在

    Args:
        network_base: NAS 根目录（找不到/不可访问时回退）
        local_backup: 本地副本路径（兜底返回值）

    Returns:
        contact.db 数据库完整路径（命中网络时为 NAS 路径，否则为本地副本路径）
    """
    try:
        if network_base and os.path.exists(network_base) and os.path.isdir(network_base):
            all_folders = [
                f for f in os.listdir(network_base)
                if os.path.isdir(os.path.join(network_base, f))
            ]

            # 筛选合法文件夹并构造排序键
            # 识别规则（按 '_' 分割后）：
            #   - 最后一段是 8 位数字 → 旧格式，日期 = 最后一段，时间补 0000
            #   - 倒数第二段是 8 位数字 + 最后一段是 3~6 位纯数字 → 新格式，日期 = 倒数第二段，时间 = 最后一段
            # 排序键：日期 * 10**6 + 时间数值，保证新旧混存时按"日期+时间"统一排序
            valid_folders = []
            for folder in all_folders:
                parts = folder.split('_')
                if len(parts) < 2:
                    continue

                date_str = ""
                time_str = "0000"  # 旧格式无时间，统一补 0000

                last = parts[-1]
                second_last = parts[-2] if len(parts) >= 2 else ""

                if (len(parts) >= 3
                        and second_last.isdigit() and len(second_last) == 8
                        and last.isdigit() and 3 <= len(last) <= 6):
                    date_str = second_last
                    time_str = last
                elif last.isdigit() and len(last) == 8:
                    date_str = last
                else:
                    continue

                # 验证日期有效性（年/月/日范围）
                try:
                    month = int(date_str[4:6])
                    day = int(date_str[6:8])
                    if not (1 <= month <= 12 and 1 <= day <= 31):
                        continue
                except ValueError:
                    continue

                # 排序键：日期 * 10**6 + 时间数值
                # 例：20260417 + 1915 → 20260417 * 1_000_000 + 1915 = 20260417001915
                #     20260417 + 0000 → 20260417 * 1_000_000 + 0    = 20260417000000
                try:
                    sort_key = int(date_str) * 1000000 + int(time_str)
                except ValueError:
                    continue

                valid_folders.append((folder, sort_key))

            if valid_folders:
                valid_folders.sort(key=lambda x: x[1], reverse=True)
                latest_folder = valid_folders[0][0]
                primary_db_path = os.path.join(
                    network_base, latest_folder,
                    "db_storage", "contact", "contact.db",
                )
                if os.path.exists(primary_db_path) and os.path.isfile(primary_db_path):
                    return primary_db_path

    except (OSError, PermissionError, Exception):
        # 网络路径访问失败，静默处理
        pass

    return local_backup


def get_short_path(full_path, num_parts=3):
    """
    截断路径，只显示最后 N 级

    Args:
        full_path: 完整路径
        num_parts: 保留的路径级数（默认 3 级）

    Returns:
        精简后的路径，格式: \\part1\\part2\\part3

    示例:
        C:\\Users\\LENOVO\\Desktop\\contact.db -> \\LENOVO\\Desktop\\contact.db
    """
    if not full_path:
        return ""

    # 统一使用反斜杠分割
    parts = full_path.replace('/', '\\').split('\\')
    parts = [p for p in parts if p]

    if len(parts) <= num_parts:
        return '\\' + '\\'.join(parts)
    return '\\' + '\\'.join(parts[-num_parts:])
