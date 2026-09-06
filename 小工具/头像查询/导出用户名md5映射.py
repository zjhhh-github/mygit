# -*- coding: utf-8 -*-
"""
脚本用途：
    读取桌面上的两个 SQLite 数据库文件（内部专用、内部专用2）中的 contact 表，
    取出 username、alias、remark、nick_name 字段，对每个 username 计算 MD5 值，
    生成 "username -> md5(username)" 的映射关系（附带 alias 微信号、remark 备注名、nick_name 微信昵称），
    保存为 CSV 文件到当前脚本所在目录。

    这个 md5 值的用途：微信本地缓存的头像文件，文件名就是 md5(username)，
    所以导出这份映射后，可以配合“头像查询”功能，通过 username 或 remark
    找到对应的头像缓存文件名，从而定位到具体是哪个联系人的头像。

使用方法（Windows，直接双击或在终端执行）：
    python 导出用户名md5映射.py

输出：
    在脚本所在目录生成 username_md5_mapping.csv，包含六列：
    来源数据库、username、alias、remark、nick_name、md5
"""

import csv
import hashlib
import os
import sqlite3

# 桌面上两个数据库文件的路径（按用户描述：内部专用、内部专用2）
DB_PATHS = [
    r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
]

# 输出文件保存到当前脚本所在目录（即当前项目目录）
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "username_md5_mapping.csv")

# 本次需要从 contact 表读取的字段（除 username 外，其余字段允许缺失，缺失则留空）
可选字段列表 = ["alias", "remark", "nick_name"]


def 计算md5(文本: str) -> str:
    """对字符串计算 MD5，统一按 UTF-8 编码后计算，返回32位小写十六进制字符串。"""
    return hashlib.md5(文本.encode("utf-8")).hexdigest()


def 读取数据库中的联系人列表(db_path: str):
    """
    连接指定的 SQLite 数据库文件，查询 contact 表中的 username、alias、remark 字段。
    - username 是必需字段，如果表结构中没有 username 列，会抛出异常，方便排查问题。
    - alias、remark、nick_name 属于可选字段，如果某个库的 contact 表没有该列，则统一返回空字符串，不会中断执行。
    返回值：[(username, alias, remark, nick_name), ...]
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库文件不存在: {db_path}")

    连接 = sqlite3.connect(db_path)
    try:
        游标 = 连接.cursor()
        # 先检查 contact 表是否存在
        游标.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='contact'"
        )
        if 游标.fetchone() is None:
            raise ValueError(f"数据库中不存在 contact 表: {db_path}")

        # 检查 contact 表的列信息，确认 username 及可选字段是否存在
        游标.execute("PRAGMA table_info(contact)")
        列名列表 = [行[1] for 行 in 游标.fetchall()]
        if "username" not in 列名列表:
            raise ValueError(f"contact 表中不存在 username 列，实际列为: {列名列表}")

        # 根据实际存在的列，动态拼接要查询的字段（缺失的可选字段用空字符串占位）
        实际查询字段 = ["username"]
        缺失字段列表 = []
        for 字段名 in 可选字段列表:
            if 字段名 in 列名列表:
                实际查询字段.append(字段名)
            else:
                缺失字段列表.append(字段名)

        if 缺失字段列表:
            print(f"  提示：{os.path.basename(db_path)} 的 contact 表缺少列 {缺失字段列表}，对应内容将留空")

        游标.execute(f"SELECT {', '.join(实际查询字段)} FROM contact")

        结果 = []
        for 行 in 游标.fetchall():
            username = 行[0]
            if username is None:
                continue
            行数据 = dict(zip(实际查询字段, 行))
            alias = 行数据.get("alias") or ""
            remark = 行数据.get("remark") or ""
            nick_name = 行数据.get("nick_name") or ""
            结果.append((username, alias, remark, nick_name))

        return 结果
    finally:
        连接.close()


def 执行导出(db_paths=None, output_path=None):
    """
    导出 username -> md5 映射到 CSV。

    参数:
        db_paths: 要读取的 SQLite 数据库路径列表；留空则使用 DB_PATHS 默认值
        output_path: CSV 输出路径；留空则输出到脚本/exe 所在目录的 username_md5_mapping.csv

    返回:
        (output_path, 写入条数) 元组
    """
    实际数据库路径列表 = db_paths if db_paths else DB_PATHS
    实际输出路径 = output_path if output_path else OUTPUT_PATH

    所有映射行 = []
    已出现的username集合 = set()

    for db_path in 实际数据库路径列表:
        print(f"正在读取数据库: {db_path}")
        try:
            联系人列表 = 读取数据库中的联系人列表(db_path)
        except Exception as 错误:
            print(f"  读取失败，跳过该数据库。原因: {错误}")
            continue

        db_name = os.path.basename(db_path)
        新增数量 = 0
        for username, alias, remark, nick_name in 联系人列表:
            if username in 已出现的username集合:
                continue
            已出现的username集合.add(username)
            所有映射行.append({
                "来源数据库": db_name,
                "username": username,
                "alias": alias,
                "remark": remark,
                "nick_name": nick_name,
                "md5": 计算md5(username),
            })
            新增数量 += 1

        print(f"  共读取 {len(联系人列表)} 条，新增 {新增数量} 条唯一 username")

    # 写入 CSV，使用 utf-8-sig 编码，方便 Excel 直接打开不乱码
    with open(实际输出路径, "w", newline="", encoding="utf-8-sig") as 文件:
        写入器 = csv.DictWriter(文件, fieldnames=["来源数据库", "username", "alias", "remark", "nick_name", "md5"])
        写入器.writeheader()
        写入器.writerows(所有映射行)

    print(f"完成！共写入 {len(所有映射行)} 条映射记录。")
    print(f"输出文件路径: {实际输出路径}")
    return 实际输出路径, len(所有映射行)


def 主流程():
    """命令行入口：使用脚本顶部 DB_PATHS 与 OUTPUT_PATH 默认值执行导出。"""
    执行导出()


if __name__ == "__main__":
    主流程()
