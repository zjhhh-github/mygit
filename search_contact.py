"""
通讯录搜索工具 - 多数据库关键词查询

功能:
    - 在多个 SQLite 数据库的 contact 表中搜索关键词
    - 搜索字段: username, alias, remark, nick_name
    - 返回字段: username, alias, remark, nick_name
    - 支持自定义数据库路径

用法:
    交互模式:  python search_contact.py
    命令行模式: python search_contact.py "关键词"
    自定义数据库: python search_contact.py "关键词" --db "路径1" "路径2" ...

默认数据库:
    C:\Users\LENOVO\Desktop\contact.db
    C:\Users\LENOVO\Desktop\contact_内部专用.db
    C:\Users\LENOVO\Desktop\contact_内部专用2.db
"""

import sqlite3
import sys
import os
from pathlib import Path

# ==================== 默认数据库路径配置 ====================

DEFAULT_DB_PATHS = [
    r"C:\Users\LENOVO\Desktop\contact.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
]

# ==================== 核心搜索函数 ====================


def search_contact(db_path: str, keyword: str) -> list:
    """
    在单个数据库中搜索联系人。

    Args:
        db_path: 数据库文件路径
        keyword: 搜索关键词

    Returns:
        匹配的结果列表，每条结果为 (db_name, username, alias, remark, nick_name)
    """
    results = []
    db_name = Path(db_path).stem

    if not os.path.exists(db_path):
        print(f"  [跳过] 数据库不存在: {db_path}")
        return results

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 检查 contact 表是否存在
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='contact'"
        )
        if not cursor.fetchone():
            print(f"  [跳过] 数据库中无 contact 表: {db_path}")
            conn.close()
            return results

        # 模糊搜索四个字段
        like_keyword = f"%{keyword}%"
        cursor.execute(
            """
            SELECT username, alias, remark, nick_name
            FROM contact
            WHERE username LIKE ?
               OR alias LIKE ?
               OR remark LIKE ?
               OR nick_name LIKE ?
            """,
            (like_keyword, like_keyword, like_keyword, like_keyword),
        )

        for row in cursor.fetchall():
            results.append(
                (
                    db_name,
                    row["username"] or "",
                    row["alias"] or "",
                    row["remark"] or "",
                    row["nick_name"] or "",
                )
            )

        conn.close()
    except Exception as e:
        print(f"  [错误] 读取 {db_path} 失败: {e}")

    return results


def search_all_databases(db_paths: list, keyword: str) -> list:
    """
    在多个数据库中搜索，返回合并后的结果。
    """
    all_results = []
    for db_path in db_paths:
        print(f"\n正在搜索: {db_path}")
        results = search_contact(db_path, keyword)
        all_results.extend(results)
        print(f"  找到 {len(results)} 条匹配")
    return all_results


def print_results(results: list, keyword: str):
    """
    格式化打印搜索结果。
    """
    if not results:
        print(f"\n未找到与 "{keyword}" 匹配的联系人。")
        return

    print(f"\n{'=' * 80}")
    print(f'搜索关键词: "{keyword}" | 共 {len(results)} 条结果')
    print(f"{'=' * 80}")

    # 计算各列宽度
    col_widths = {
        "db": max(len(r[0]) for r in results) if results else 10,
        "username": min(max(len(r[1]) for r in results), 30),
        "alias": min(max(len(r[2]) for r in results), 20),
        "remark": min(max(len(r[3]) for r in results), 30),
        "nick_name": min(max(len(r[4]) for r in results), 20),
    }
    col_widths["db"] = max(col_widths["db"], 8)

    # 表头
    header = (
        f"{'来源库':<{col_widths['db']}}  "
        f"{'username':<{col_widths['username']}}  "
        f"{'alias':<{col_widths['alias']}}  "
        f"{'remark':<{col_widths['remark']}}  "
        f"{'nick_name':<{col_widths['nick_name']}}"
    )
    print(header)
    print("-" * len(header))

    # 数据行
    for i, (db, username, alias, remark, nick_name) in enumerate(results, 1):
        print(
            f"{db:<{col_widths['db']}}  "
            f"{username:<{col_widths['username']}}  "
            f"{alias:<{col_widths['alias']}}  "
            f"{remark:<{col_widths['remark']}}  "
            f"{nick_name:<{col_widths['nick_name']}}"
        )

    print(f"{'=' * 80}")


# ==================== 命令行参数解析 ====================


def parse_args():
    """
    解析命令行参数。
    返回 (keyword, db_paths)
    """
    args = sys.argv[1:]

    keyword = None
    db_paths = DEFAULT_DB_PATHS.copy()

    i = 0
    while i < len(args):
        if args[i] in ("--db", "-d"):
            # 自定义数据库路径，从当前位置开始收集直到遇到下一个选项
            custom_paths = []
            j = i + 1
            while j < len(args) and not args[j].startswith("-"):
                custom_paths.append(args[j])
                j += 1
            if custom_paths:
                db_paths = custom_paths
            else:
                print("警告: --db 后未指定数据库路径，使用默认路径")
            i = j
        elif args[i] in ("--help", "-h"):
            print(__doc__)
            sys.exit(0)
        elif not args[i].startswith("-") and keyword is None:
            keyword = args[i]
            i += 1
        else:
            i += 1

    return keyword, db_paths


# ==================== 主入口 ====================


def main():
    keyword, db_paths = parse_args()

    # 如果没有提供关键词，交互模式
    if keyword is None:
        print("通讯录搜索工具")
        print("-" * 40)
        print("当前数据库:")
        for p in db_paths:
            exists = "✓" if os.path.exists(p) else "✗"
            print(f"  {exists} {p}")
        print("-" * 40)
        keyword = input("\n请输入搜索关键词: ").strip()
        if not keyword:
            print("关键词为空，退出。")
            return

    print(f'\n开始搜索关键词: "{keyword}"')
    results = search_all_databases(db_paths, keyword)
    print_results(results, keyword)

    # 暂停以便查看结果（双击运行时不会闪退）
    if sys.stdin.isatty():
        input("\n按 Enter 键退出...")


if __name__ == "__main__":
    main()