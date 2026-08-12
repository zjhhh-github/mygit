# -*- coding: utf-8 -*-
r"""
从一个或多个微信 contact.db 中搜索联系人。

【搜索字段】
    contact 表中的 username、alias、remark、nick_name 四个字段，
    任意字段包含关键词即返回。

【默认数据库】
    - C:\Users\LENOVO\Desktop\contact.db
    - C:\Users\LENOVO\Desktop\contact_内部专用.db
    - C:\Users\LENOVO\Desktop\contact_内部专用2.db

【返回字段】
    username（微信ID）、alias（微信号）、remark（备注）、nick_name（昵称）
    若内部专用库可用，还会显示所在外教群（群名包含大写 CT 或 XXJ）

【用法示例】
    # 使用默认数据库搜索
    python 搜索联系人.py 张三

    # 自定义数据库（多个用分号分隔）
    python 搜索联系人.py 张三 --db "C:\a.db;C:\b.db"

    # 搜索结果限制条数
    python 搜索联系人.py 张三 --limit 20

    # 不查询外教群（加快搜索）
    python 搜索联系人.py 张三 --no-groups

    # 交互式模式（不传关键词，进入循环输入）
    python 搜索联系人.py
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional


# ============================================================
# 一、默认配置
# ============================================================

# 默认读取的三个数据库路径
默认数据库列表 = [
    r"C:\Users\LENOVO\Desktop\contact.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
]

# 默认最大显示条数（0 表示不限制）
默认最大条数 = 0

# 群成员关系（chat_room / chatroom_member）只存在于内部专用 contact.db
# 从这些库构建「微信原始ID → 所在外教群名」索引，再回填到搜索结果
群成员数据库关键字 = "内部专用"
默认群成员数据库列表 = [
    r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
]


# ============================================================
# 二、工具函数
# ============================================================

def 确保控制台UTF8输出() -> None:
    """Windows 下强制 stdout/stderr 使用 utf-8，避免中文乱码。"""
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def 解析数据库路径列表(db_参数: Optional[str]) -> List[str]:
    """
    将分号/竖线分隔的路径字符串拆成去重列表。
    若 db_参数 为 None，则返回默认数据库列表。
    """
    if db_参数 is None:
        return list(默认数据库列表)

    # 支持分号或竖线分隔
    原始列表 = [p.strip() for p in db_参数.replace("|", ";").split(";")]
    # 去重，保留顺序
    已见路径 = set()
    结果列表 = []
    for 路径 in 原始列表:
        if not 路径:
            continue
        归一化路径 = os.path.normcase(os.path.abspath(路径))
        if 归一化路径 in 已见路径:
            continue
        已见路径.add(归一化路径)
        结果列表.append(路径)
    return 结果列表


def 从单个数据库搜索(
    db文件路径: str,
    关键词: str,
) -> List[Dict[str, str]]:
    """
    在单个 contact.db 的 contact 表中搜索关键词。

    搜索范围：username、alias、remark、nick_name 四个字段，
    任意一个字段包含关键词即命中（大小写不敏感）。

    返回：包含四个字段的字典列表，来源数据库字段为 db_source。
    """
    db路径 = Path(db文件路径)
    if not db路径.is_file():
        print(f"  [跳过] 数据库文件不存在：{db文件路径}", file=sys.stderr)
        return []

    # 用 LIKE 模糊匹配，SQLite 默认 LIKE 对 ASCII 不区分大小写
    # LOWER() 处理中文场景（保险）
    搜索SQL = """
        SELECT username, alias, remark, nick_name
        FROM contact
        WHERE
            LOWER(username)  LIKE LOWER(:keyword)
         OR LOWER(alias)     LIKE LOWER(:keyword)
         OR LOWER(remark)    LIKE LOWER(:keyword)
         OR LOWER(nick_name) LIKE LOWER(:keyword)
        ORDER BY remark, nick_name, username
    """

    like关键词 = f"%{关键词}%"

    try:
        conn = sqlite3.connect(str(db路径))
        conn.text_factory = str
        cur = conn.cursor()
        cur.execute(搜索SQL, {"keyword": like关键词})
        行列表 = cur.fetchall()
    except sqlite3.Error as ex:
        print(f"  [错误] 读取 {db文件路径} 时出错：{ex}", file=sys.stderr)
        return []
    finally:
        conn.close()

    结果 = []
    for username, alias, remark, nick_name in 行列表:
        结果.append({
            "username":  (username  or "").strip(),
            "alias":     (alias     or "").strip(),
            "remark":    (remark    or "").strip(),
            "nick_name": (nick_name or "").strip(),
            # 标记来自哪个数据库（仅供显示，不是业务字段）
            "_db_source": db路径.name,
        })
    return 结果


def 跨库搜索(
    db路径列表: List[str],
    关键词: str,
    最大条数: int = 0,
) -> List[Dict[str, str]]:
    """
    遍历所有数据库，合并结果。
    按 username 去重，冲突时保留字段更完整（remark 更长）的那条。
    返回最终列表，若 最大条数 > 0 则截断。
    """
    # key = username（微信ID），保证同一个人只出现一次
    已合并: Dict[str, Dict[str, str]] = {}

    for db路径 in db路径列表:
        条目列表 = 从单个数据库搜索(db路径, 关键词)
        for 条目 in 条目列表:
            key = 条目["username"] or 条目["alias"]
            if not key:
                # username 和 alias 都为空则直接追加，不去重
                已合并[id(条目)] = 条目  # type: ignore[arg-type]
                continue
            已有 = 已合并.get(key)
            if 已有 is None:
                已合并[key] = 条目
            else:
                # 保留 remark 更完整（字段更长）的那条
                if len(条目.get("remark") or "") > len(已有.get("remark") or ""):
                    已合并[key] = 条目

    全部结果 = list(已合并.values())

    if 最大条数 > 0:
        return 全部结果[:最大条数]
    return 全部结果


def _是外教群名(群名: str) -> bool:
    """
    判断群昵称是否为外教群：群名中必须包含大写 CT 或 XXJ。
    不使用小写 ct / xxj，避免误匹配。
    """
    return "CT" in 群名 or "XXJ" in 群名


def _解析群成员数据库路径(db路径列表: List[str]) -> List[str]:
    """
    收集用于查询群成员关系的数据库路径。
    优先使用当前搜索列表里含「内部专用」的库，并合并默认内部库（去重、仅保留存在的文件）。
    """
    候选: List[str] = []
    已见: set = set()

    def _追加(路径: str) -> None:
        规范 = os.path.normcase(os.path.abspath(路径))
        if 规范 in 已见:
            return
        if Path(路径).is_file():
            已见.add(规范)
            候选.append(路径)

    for 路径 in db路径列表:
        if 群成员数据库关键字 in Path(路径).name:
            _追加(路径)
    for 路径 in 默认群成员数据库列表:
        _追加(路径)
    return 候选


def _数据库含群成员表(db文件路径: str) -> bool:
    """判断数据库是否包含 chatroom_member（内部专用微信库才有群成员表）。"""
    try:
        conn = sqlite3.connect(db文件路径)
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chatroom_member' LIMIT 1"
        )
        存在 = cur.fetchone() is not None
        conn.close()
        return 存在
    except sqlite3.Error:
        return False


def 构建外教群成员索引(db路径列表: List[str]) -> Dict[str, List[str]]:
    """
    从内部专用 contact.db 构建：微信原始ID(username) → 所在外教群名列表。

    外教群判定：群昵称中包含大写 CT 或 XXJ。
    同一 wxid 在多个内部库中出现时，群名去重合并。
    """
    索引: Dict[str, set] = {}
    群成员库列表 = _解析群成员数据库路径(db路径列表)

    # 一次 SQL 拉出成员 wxid 与其所在群昵称，再在 Python 中按大写 CT/XXJ 过滤
    _SQL = """
        SELECT
            c_member.username,
            cg.nick_name
        FROM chatroom_member cm
        JOIN chat_room cr ON cr.id = cm.room_id
        JOIN contact cg ON cg.username = cr.username
        JOIN contact c_member ON c_member.id = cm.member_id
        WHERE cg.nick_name LIKE '%CT%'
           OR cg.nick_name LIKE '%XXJ%'
    """

    for db路径 in 群成员库列表:
        if not _数据库含群成员表(db路径):
            continue
        try:
            conn = sqlite3.connect(db路径)
            conn.text_factory = str
            cur = conn.cursor()
            for 成员wxid, 群昵称 in cur.execute(_SQL):
                wxid = (成员wxid or "").strip()
                群名 = (群昵称 or "").strip()
                if not wxid or not 群名:
                    continue
                if not _是外教群名(群名):
                    continue
                索引.setdefault(wxid, set()).add(群名)
            conn.close()
        except sqlite3.Error:
            continue

    # set → 排序后的 list，便于展示时顺序稳定
    return {wxid: sorted(群名集合) for wxid, 群名集合 in 索引.items()}


def 填充联系人外教群信息(
    结果列表: List[Dict[str, str]],
    群索引: Dict[str, List[str]],
) -> None:
    """
    就地给每条搜索结果补充 _foreign_teacher_group 字段。
    通过联系人的 username（微信原始ID）在群索引中查找；未加入任何外教群则留空。
    """
    for 条目 in 结果列表:
        wxid = (条目.get("username") or "").strip()
        群名列表 = 群索引.get(wxid) or []
        条目["_foreign_teacher_group"] = "、".join(群名列表)


# ============================================================
# 三、结果展示
# ============================================================

def 打印单条结果(条目: Dict[str, str], 序号: int, 显示外教群: bool = True) -> None:
    """格式化打印一条联系人信息。"""
    print(f"  [{序号}]")
    print(f"    微信ID (username) : {条目['username'] or '—'}")
    print(f"    微信号 (alias)    : {条目['alias']    or '—'}")
    print(f"    备注   (remark)   : {条目['remark']   or '—'}")
    print(f"    昵称   (nick_name): {条目['nick_name'] or '—'}")
    if 显示外教群:
        print(f"    外教群            : {条目.get('_foreign_teacher_group') or '—'}")
    print(f"    来源数据库        : {条目['_db_source']}")


def 打印搜索结果(
    结果列表: List[Dict[str, str]],
    关键词: str,
    显示外教群: bool = True,
) -> None:
    """打印搜索结果汇总。"""
    print()
    if not 结果列表:
        print(f'  未找到包含"{关键词}"的联系人。')
        return

    print(f'  共找到 {len(结果列表)} 条结果（关键词："{关键词}"）：')
    print()
    for 序号, 条目 in enumerate(结果列表, start=1):
        打印单条结果(条目, 序号, 显示外教群)
        if 序号 < len(结果列表):
            print()


# ============================================================
# 四、CLI 入口
# ============================================================

def 解析命令行参数() -> argparse.Namespace:
    解析器 = argparse.ArgumentParser(
        description="从微信 contact.db 中搜索联系人（支持多库）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python 搜索联系人.py 张三
  python 搜索联系人.py 张三 --limit 10
  python 搜索联系人.py 张三 --db "C:\\a.db;C:\\b.db"
  python 搜索联系人.py 张三 --no-groups
  python 搜索联系人.py        （进入交互式搜索模式）
        """,
    )
    解析器.add_argument(
        "关键词",
        nargs="?",
        default=None,
        help="要搜索的文字（留空则进入交互式循环模式）",
    )
    解析器.add_argument(
        "--db",
        type=str,
        default=None,
        metavar="路径",
        help="自定义数据库路径，多个用分号分隔（覆盖默认三个数据库）",
    )
    解析器.add_argument(
        "--limit",
        type=int,
        default=默认最大条数,
        metavar="条数",
        help="最多返回几条结果（默认 0 表示不限制）",
    )
    解析器.add_argument(
        "--no-groups",
        action="store_true",
        help="不查询外教群（群名含大写 CT 或 XXJ），可加快搜索",
    )
    return 解析器.parse_args()


def 执行一次搜索(
    关键词: str,
    db路径列表: List[str],
    最大条数: int,
    显示外教群: bool = True,
) -> None:
    """执行一次完整的搜索并打印结果。"""
    关键词 = 关键词.strip()
    if not 关键词:
        print("  [提示] 关键词不能为空，请重新输入。")
        return

    print(f"\n正在搜索：{关键词}")
    print(f"数据库：{len(db路径列表)} 个")
    for 路径 in db路径列表:
        print(f"  - {路径}")
    print("-" * 60)

    结果列表 = 跨库搜索(db路径列表, 关键词, 最大条数)

    if 显示外教群 and 结果列表:
        群索引 = 构建外教群成员索引(db路径列表)
        填充联系人外教群信息(结果列表, 群索引)

    打印搜索结果(结果列表, 关键词, 显示外教群)


def 交互式搜索模式(
    db路径列表: List[str],
    最大条数: int,
    显示外教群: bool = True,
) -> None:
    """
    进入交互式循环模式：用户可以连续输入关键词搜索，
    输入 q / quit / exit 或按 Ctrl+C 退出。
    """
    print("=" * 60)
    print("联系人搜索工具（交互式模式）")
    print("输入关键词后回车搜索，输入 q 退出")
    print(f"数据库：{len(db路径列表)} 个")
    for 路径 in db路径列表:
        print(f"  - {路径}")
    print("=" * 60)

    while True:
        try:
            关键词 = input("\n请输入搜索关键词（q 退出）：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break

        if 关键词.lower() in ("q", "quit", "exit", "退出"):
            print("已退出。")
            break

        执行一次搜索(关键词, db路径列表, 最大条数, 显示外教群)


def main() -> int:
    确保控制台UTF8输出()

    参数 = 解析命令行参数()
    db路径列表 = 解析数据库路径列表(参数.db)
    最大条数 = 参数.limit
    显示外教群 = not 参数.no_groups

    if 参数.关键词:
        # 命令行直接传入关键词，执行一次搜索后退出
        执行一次搜索(参数.关键词, db路径列表, 最大条数, 显示外教群)
    else:
        # 未传关键词，进入交互式循环模式
        交互式搜索模式(db路径列表, 最大条数, 显示外教群)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已取消]")
        sys.exit(130)
