# -*- coding: utf-8 -*-
r"""
独立小工具：查询联系人「所在外教群」。

【功能说明】
    输入联系人关键词（微信ID / 微信号 / 备注 / 昵称，任意一个包含即可），
    找到匹配的联系人后，查询该联系人在内部专用微信库中属于哪些「外教群」，
    并打印出来。

    该脚本从 搜索联系人.py 中把「外教群查询」这一小功能单独拆出来，
    做成一个不依赖其他文件、可以独立运行的小工具；
    不会修改、也不影响 搜索联系人.py 和 搜索联系人_GUI.py 原有功能。

【外教群判定规则】
    群昵称中包含大写 "CT" 或 "XXJ"，视为外教群
    （必须是大写，避免误匹配到其他含 ct/xxj 字母的普通群名）。

【默认数据库】
    基本信息（微信ID/微信号/备注/昵称）来自：
        - C:\Users\LENOVO\Desktop\contact.db
        - C:\Users\LENOVO\Desktop\contact_内部专用.db
        - C:\Users\LENOVO\Desktop\contact_内部专用2.db

    外教群关系（chat_room / chatroom_member）只存在于内部专用库：
        - C:\Users\LENOVO\Desktop\contact_内部专用.db
        - C:\Users\LENOVO\Desktop\contact_内部专用2.db

【用法示例】
    # 查询关键词命中的联系人所在外教群
    python 查询外教群.py 张三

    # 自定义数据库（多个用分号分隔）
    python 查询外教群.py 张三 --db "C:\a.db;C:\b.db"

    # 交互式模式（不传关键词，进入循环输入）
    python 查询外教群.py

    # 默认每次查询前都会先从微信同步盘（Z:）自动拷贝最新的两个内部专用数据库，
    # 保证查到的是最新数据，无需再手动加参数。
    # 如果连不上同步盘，或者只想用本地已有的数据库文件，加 --skip-copy-db 跳过拷贝：
    python 查询外教群.py 张三 --skip-copy-db
"""

import argparse
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ============================================================
# 一、默认配置
# ============================================================

# 默认读取的三个数据库路径（用于匹配联系人基本信息）
默认数据库列表 = [
    # r"C:\Users\LENOVO\Desktop\contact.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
]

# 群成员关系（chat_room / chatroom_member）只存在于内部专用 contact.db
# 从这些库构建「微信原始ID → 所在外教群名」索引
群成员数据库关键字 = "内部专用"
默认群成员数据库列表 = [
    r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
]

# 拷贝数据库任务：只拷贝「内部专用」「内部专用2」这两个库（不涉及意向专用等其他库），
# 源路径与 搜索联系人_GUI.py 里的「拷贝任务面板」默认配置保持一致（微信同步盘 Z: 盘）。
# 每项为 (源文件路径, 目标文件路径)。
内部专用拷贝任务列表: List[Tuple[str, str]] = [
    (
        r"Z:\Documents\chatlog\wxid_42272spv9uq522_6ded\db_storage\contact\contact.db",
        r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    ),
    (
        r"Z:\Documents\chatlog\wxid_7u0rihcbbpbz12_ec5a\db_storage\contact\contact.db",
        r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
    ),
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

    原始列表 = [p.strip() for p in db_参数.replace("|", ";").split(";")]
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


def 从单个数据库搜索联系人(db文件路径: str, 关键词: str) -> List[Dict[str, str]]:
    """
    在单个 contact.db 的 contact 表中按关键词搜索联系人基本信息。
    搜索字段：username、alias、remark、nick_name，任意一个包含关键词即命中。
    """
    db路径 = Path(db文件路径)
    if not db路径.is_file():
        print(f"  [跳过] 数据库文件不存在：{db文件路径}", file=sys.stderr)
        return []

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
            "_db_source": db路径.name,
        })
    return 结果


def 跨库搜索联系人(db路径列表: List[str], 关键词: str) -> List[Dict[str, str]]:
    """
    遍历所有数据库搜索联系人，按 username 去重，
    冲突时保留 remark 更完整（更长）的那条。
    """
    已合并: Dict[str, Dict[str, str]] = {}

    for db路径 in db路径列表:
        条目列表 = 从单个数据库搜索联系人(db路径, 关键词)
        for 条目 in 条目列表:
            key = 条目["username"] or 条目["alias"]
            if not key:
                已合并[id(条目)] = 条目  # type: ignore[arg-type]
                continue
            已有 = 已合并.get(key)
            if 已有 is None:
                已合并[key] = 条目
            else:
                if len(条目.get("remark") or "") > len(已有.get("remark") or ""):
                    已合并[key] = 条目

    return list(已合并.values())


def _是外教群名(群名: str) -> bool:
    """判断群昵称是否为外教群：群名中必须包含大写 CT 或 XXJ。"""
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


def _获取文件信息(文件路径: str) -> Dict[str, float]:
    """读取文件的大小和修改时间，用于判断源文件是否还在被写入。"""
    stat = os.stat(文件路径)
    return {"size": stat.st_size, "mtime": stat.st_mtime}


def _等待源文件稳定(文件路径: str, 检查间隔秒: float = 1.0, 最大重试次数: int = 5) -> None:
    """
    连续两次检查文件大小和修改时间均一致，才认为文件已稳定、可以安全复制。
    （避免微信正在写入 contact.db 时拷贝到一半的文件）
    超过最大重试次数仍不稳定则抛出异常。
    """
    for _ in range(最大重试次数):
        第一次 = _获取文件信息(文件路径)
        time.sleep(检查间隔秒)
        第二次 = _获取文件信息(文件路径)
        if 第一次["size"] == 第二次["size"] and 第一次["mtime"] == 第二次["mtime"]:
            return
    raise RuntimeError(f"源文件长时间不稳定，无法安全复制：{文件路径}")


def _执行单个拷贝任务(源文件路径: str, 目标文件路径: str) -> str:
    """
    执行一次数据库文件拷贝（逻辑与 搜索联系人_GUI.py 的拷贝任务完全一致）：
      1. 检查源文件是否存在
      2. 等待源文件稳定（避免拷贝到写了一半的文件）
      3. 确保目标目录存在
      4. 拷贝文件，并校验拷贝前后文件大小是否一致
    返回描述结果的字符串（成功 / 跳过 / 失败 / 警告）。
    """
    if not os.path.exists(源文件路径):
        return f"[跳过] 源文件不存在：{源文件路径}"

    try:
        _等待源文件稳定(源文件路径)
    except RuntimeError as ex:
        return f"[失败] {ex}"

    源信息 = _获取文件信息(源文件路径)

    目标目录 = os.path.dirname(目标文件路径)
    if 目标目录 and not os.path.exists(目标目录):
        os.makedirs(目标目录, exist_ok=True)

    try:
        shutil.copyfile(源文件路径, 目标文件路径)
    except OSError as ex:
        return f"[失败] 拷贝出错：{ex} — {源文件路径} → {目标文件路径}"

    目标信息 = _获取文件信息(目标文件路径)
    if 源信息["size"] != 目标信息["size"]:
        return (
            f"[警告] 大小不一致：源={源信息['size']}字节，"
            f"目标={目标信息['size']}字节 — {目标文件路径}"
        )

    return f"[成功] {Path(源文件路径).name} → {目标文件路径}"


def 拷贝内部专用数据库(任务列表: Optional[List[Tuple[str, str]]] = None) -> List[str]:
    """
    只拷贝「内部专用」「内部专用2」这两个微信 contact.db（不涉及意向专用等其他库）。
    默认从微信同步盘 Z: 盘拷贝到本机 Desktop，覆盖已有的本地库文件。
    返回每个任务的结果描述，供调用方打印。
    """
    if 任务列表 is None:
        任务列表 = 内部专用拷贝任务列表

    结果列表: List[str] = []
    for 源路径, 目标路径 in 任务列表:
        结果列表.append(_执行单个拷贝任务(源路径, 目标路径))
    return 结果列表


def 构建外教群成员索引(db路径列表: List[str]) -> Dict[str, List[str]]:
    """
    从内部专用 contact.db 构建：微信原始ID(username) → 所在外教群名列表。
    外教群判定：群昵称中包含大写 CT 或 XXJ。
    同一 wxid 在多个内部库中出现时，群名去重合并。
    """
    索引: Dict[str, set] = {}
    群成员库列表 = _解析群成员数据库路径(db路径列表)

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

    return {wxid: sorted(群名集合) for wxid, 群名集合 in 索引.items()}


# ============================================================
# 三、结果展示
# ============================================================

def 打印单条结果(条目: Dict[str, str], 序号: int) -> None:
    """格式化打印一条联系人的外教群查询结果。"""
    print(f"  [{序号}]")
    print(f"    微信ID (username) : {条目['username'] or '—'}")
    print(f"    微信号 (alias)    : {条目['alias']    or '—'}")
    print(f"    备注   (remark)   : {条目['remark']   or '—'}")
    print(f"    昵称   (nick_name): {条目['nick_name'] or '—'}")
    print(f"    外教群            : {条目.get('_foreign_teacher_group') or '未加入任何外教群'}")


def 打印查询结果(结果列表: List[Dict[str, str]], 关键词: str) -> None:
    """打印查询结果汇总。"""
    print()
    if not 结果列表:
        print(f'  未找到包含"{关键词}"的联系人。')
        return

    print(f'  共找到 {len(结果列表)} 条结果（关键词："{关键词}"）：')
    print()
    for 序号, 条目 in enumerate(结果列表, start=1):
        打印单条结果(条目, 序号)
        if 序号 < len(结果列表):
            print()


# ============================================================
# 四、CLI 入口
# ============================================================

def 解析命令行参数() -> argparse.Namespace:
    解析器 = argparse.ArgumentParser(
        description="查询联系人所在外教群（群名含大写 CT 或 XXJ）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python 查询外教群.py 张三
  python 查询外教群.py 张三 --db "C:\\a.db;C:\\b.db"
  python 查询外教群.py 张三 --skip-copy-db  （跳过自动拷贝数据库，直接用本地文件）
  python 查询外教群.py        （进入交互式查询模式，默认也会先自动拷贝数据库）
        """,
    )
    解析器.add_argument(
        "关键词",
        nargs="?",
        default=None,
        help="要查询的联系人关键词（微信ID/微信号/备注/昵称），留空则进入交互式循环模式",
    )
    解析器.add_argument(
        "--db",
        type=str,
        default=None,
        metavar="路径",
        help="自定义数据库路径，多个用分号分隔（覆盖默认三个数据库）",
    )
    解析器.add_argument(
        "--skip-copy-db",
        action="store_true",
        help="跳过自动拷贝数据库这一步，直接使用本地已有的数据库文件"
             "（默认每次查询前都会先从微信同步盘拷贝最新的「内部专用」「内部专用2」两个数据库）",
    )
    return 解析器.parse_args()


def 执行一次查询(关键词: str, db路径列表: List[str]) -> None:
    """执行一次「联系人 → 所在外教群」查询并打印结果。"""
    关键词 = 关键词.strip()
    if not 关键词:
        print("  [提示] 关键词不能为空，请重新输入。")
        return

    print(f"\n正在查询：{关键词}")
    print(f"数据库：{len(db路径列表)} 个")
    for 路径 in db路径列表:
        print(f"  - {路径}")
    print("-" * 60)

    结果列表 = 跨库搜索联系人(db路径列表, 关键词)

    if 结果列表:
        群索引 = 构建外教群成员索引(db路径列表)
        for 条目 in 结果列表:
            wxid = (条目.get("username") or "").strip()
            群名列表 = 群索引.get(wxid) or []
            条目["_foreign_teacher_group"] = "、".join(群名列表)

    打印查询结果(结果列表, 关键词)


def 交互式查询模式(db路径列表: List[str]) -> None:
    """
    进入交互式循环模式：用户可以连续输入关键词查询，
    输入 q / quit / exit 或按 Ctrl+C 退出。
    """
    print("=" * 60)
    print("外教群查询工具（交互式模式）")
    print("输入联系人关键词后回车查询，输入 q 退出")
    print(f"数据库：{len(db路径列表)} 个")
    for 路径 in db路径列表:
        print(f"  - {路径}")
    print("=" * 60)

    while True:
        try:
            关键词 = input("\n请输入联系人关键词（q 退出）：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break

        if 关键词.lower() in ("q", "quit", "exit", "退出"):
            print("已退出。")
            break

        执行一次查询(关键词, db路径列表)


def main() -> int:
    确保控制台UTF8输出()

    参数 = 解析命令行参数()
    db路径列表 = 解析数据库路径列表(参数.db)

    if not 参数.skip_copy_db:
        print("正在拷贝内部专用数据库 ...")
        for 结果 in 拷贝内部专用数据库():
            print(f"  {结果}")
        print()

    if 参数.关键词:
        执行一次查询(参数.关键词, db路径列表)
    else:
        交互式查询模式(db路径列表)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已取消]")
        sys.exit(130)
