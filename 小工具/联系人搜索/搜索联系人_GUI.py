# -*- coding: utf-8 -*-
r"""
联系人搜索工具 - 可视化界面版本（Canvas 滚动版）
基于 tkinter（Python 内置，无需额外安装）

功能：
  - 搜索：输入关键词跨多个 contact.db 模糊搜索联系人
  - 数据库面板：可折叠，支持选中删除、双击就地编辑、文件选择添加、恢复默认
  - 拷贝任务面板：可折叠，配置"源文件 → 目标文件"的拷贝任务列表
  - 设备选择：内部专用 / 意向专用 / 其他，自动切换拷贝任务源路径盘符
  - 界面配置持久化：设备、拷贝任务、数据库列表写入 Windows 注册表（不生成额外文件）
  - 刷新数据库按钮：执行全部拷贝任务后自动重新搜索，刷新结果
  - Canvas 表格：像素级平滑滚动，单击单元格复制内容
  - 群信息列：根据内部专用库 chatroom_member，显示联系人所在内部直播群 / 专属带领群
"""

import hashlib
import io
import json
import os
import shutil
import sqlite3
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple


# ============================================================
# 一、默认配置
# ============================================================

# 默认搜索数据库列表
默认数据库列表 = [
    r"C:\Users\LENOVO\Desktop\contact_意向专用.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
    r"C:\Users\LENOVO\Desktop\contact_超威意向专用.db3",
]

# 数据库来源优先级：数字越小优先级越高。
# 同一联系人在多个库中都有记录时，保留优先级最高（数字最小）的那条。
# 文件名（不含路径）做匹配，支持部分匹配（只要文件名包含该关键字即可）。
# 未在列表中的数据库默认优先级为 999（最低）。
数据库优先级 = {
    "内部专用":   1,   # contact_内部专用.db 和 contact_内部专用2.db 均包含此关键字
    "意向专用":   2,   # contact_意向专用.db
    "超威意向专用": 3,  # contact_超威意向专用.db3
}

# 默认拷贝任务列表，每项为 (源文件路径, 目标文件路径)
默认拷贝任务列表: List[Tuple[str, str]] = [
    (
        r"Z:\Documents\chatlog\wxid_7u0rihcbbpbz12_ec5a\db_storage\contact\contact.db",
        r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
    ),
    (
        r"Z:\Documents\chatlog\wxid_42272spv9uq522_6ded\db_storage\contact\contact.db",
        r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    ),
    (
        r"Y:\Documents\chatlog\wxid_3iidhz1xmnta22_68f1\db_storage\contact\contact.db",
        r"C:\Users\LENOVO\Desktop\contact_意向专用.db",
    ),
    (
        r"Y:/AppData/Local/WxRobot/db_wxid_3iidhz1xmnta22.db3",
        r"C:\Users\LENOVO\Desktop\contact_超威意向专用.db3",
    )
]

# 设备选择：切换后自动改写拷贝任务「源路径」中的盘符前缀
# - 内部专用：Z: → 设备本地路径根
# - 意向专用：Y: → 设备本地路径根
# - 其他：保持 默认拷贝任务列表 原样（Z: / Y: 不变）
设备选项列表 = ("内部专用", "意向专用", "其他")
设备本地路径根 = r"C:\Users\LENOVO"
设备盘符映射 = {
    "内部专用": "Z:",
    "意向专用": "Y:",
}

# 界面配置持久化：写入 Windows 注册表，不在 exe 旁单独建 json 文件
注册表根路径 = r"Software\ContactSearchGUI"
注册表配置项名 = "Settings"
旧版配置文件名 = "contact_search_config.json"  # 仅用于一次性迁移，迁移后删除

字号 = 13   # 正文字号（表格默认列）
行高 = 100   # 表格最小行高（px）；实际行高按单元格换行内容动态增高

# 单列可覆盖正文字号：key = 表格列定义中的字段 key
列内容字号: Dict[str, int] = {
    "_exclusive_lead_group": 8,   # 专属带领群：群名较长，用小号字完整显示
}

# 每次向表格追加的批次大小（首次加载 + 每次滚动到底部追加）
分页批次大小 = 50

# 输入框自动搜索防抖延迟（毫秒），停止输入后等待此时间再触发搜索
自动搜索延迟毫秒 = 400

# 过滤掉 username 以这些后缀结尾的记录（群聊/openim 机器人）
过滤username后缀 = ("@openim", "@chatroom")

# 头像列宽度（px）
头像列宽 = 100

# 鼠标滚轮每次滚动的像素数（调大更快，调小更慢，推荐范围 10～60）
滚动速度像素 = 150

# 表格列定义：(字段key, 列标题, 列宽px)
# 头像列单独用 头像列宽 控制，不在此列表中
表格列定义 = [
    ("_binah",      "编号",       180),
    ("_child_name", "孩子中文全名", 280),
    ("remark",     "备注",       500),
    ("nick_name",  "昵称",       360),
    ("alias",      "微信号",     500),
    ("username",   "微信原始ID", 500),
    ("_internal_live_group",  "内部直播群",   320),
    ("_exclusive_lead_group", "专属带领群",   360),
    ("_db_source", "来源数据库", 500),
]

# 群成员关系（chat_room / chatroom_member）只存在于内部专用 contact.db
# 搜索时从这些库构建「微信原始ID → 所在群名」索引，再回填到结果行
群成员数据库关键字 = "内部专用"
默认群成员数据库列表 = [
    r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
]

# 头像缩略图尺寸（px），需与 行高 匹配，建议不超过 行高
头像尺寸 = (80, 80)

# 头像本地磁盘缓存目录（存放在桌面的临时文件夹，以 username 的 MD5 命名）
# 下次启动时优先读取本地缓存，无需重新下载
头像缓存目录 = Path.home() / "Desktop" / "_avatar_cache"

# 表格内边距（文字距行顶部的像素偏移，用于垂直居中）
单元格内边距 = 8

# 表头背景色、表头文字色
表头背景色  = "#E3F2FD"
表头文字色  = "#1565C0"
偶数行背景  = "#F5F8FF"
奇数行背景  = "#FFFFFF"
选中行背景  = "#BBDEFB"
分隔线颜色  = "#DDEEFF"

# 搜索栏匹配按钮样式（模糊/精确 单选，及字段 Checkbutton）
匹配按钮内边距水平 = 10   # padx，调大按钮更宽
匹配按钮内边距垂直 = 4    # pady，调大按钮更高
匹配模式选中色    = "#1976D2"   # 模糊/精确选中时背景色（蓝）
匹配字段选中色    = "#388E3C"   # 字段勾选时背景色（绿）

# 单击单元格复制成功后的浮层提示（毫秒）
复制提示显示毫秒 = 1800
复制提示距顶像素 = 88      # 浮层固定在窗口水平居中、距顶部该像素
复制提示预览最大字数 = 80   # 浮层中内容预览最长字符数，超出截断
复制提示透明度 = 1       # 0~1，Windows 下 Toplevel 整体半透明
复制提示背景色 = "#E3F2FD"  # 淡蓝底
复制提示边框色 = "#90CAF9"  # 淡蓝边框
复制提示主字色 = "#1565C0"  # 主标题深蓝，保证半透明底上可读
复制提示副字色 = "#1976D2"  # 副标题蓝


# ============================================================
# 二、搜索逻辑
# ============================================================

# ── 两种表的 SQL 模板 ────────────────────────────────────────
# contact 表（微信原生格式）
_SQL_CONTACT = """
    SELECT username, alias, remark, nick_name, small_head_url
    FROM contact
    WHERE
        LOWER(username)              LIKE LOWER(:keyword)
     OR LOWER(alias)                 LIKE LOWER(:keyword)
     OR LOWER(remark)                LIKE LOWER(:keyword)
     OR LOWER(nick_name)             LIKE LOWER(:keyword)
     OR LOWER(remark_quan_pin)       LIKE LOWER(:keyword)
     OR LOWER(remark_pin_yin_initial) LIKE LOWER(:keyword)
     OR LOWER(pin_yin_initial)       LIKE LOWER(:keyword)
     OR LOWER(quan_pin)              LIKE LOWER(:keyword)
    ORDER BY remark, nick_name, username
"""

# ContactConfigTable（另一种格式）：字段名不同，但语义一致
# WxID=微信ID, Number=微信号, Remark=备注, Nick=昵称, SmallHeader=头像URL
_SQL_CONTACT_CONFIG = """
    SELECT WxID, Number, Remark, Nick, SmallHeader
    FROM ContactConfigTable
    WHERE
        LOWER(WxID)   LIKE LOWER(:keyword)
     OR LOWER(Number) LIKE LOWER(:keyword)
     OR LOWER(Remark) LIKE LOWER(:keyword)
     OR LOWER(Nick)   LIKE LOWER(:keyword)
    ORDER BY Remark, Nick, WxID
"""


def _检测表类型(cur: sqlite3.Cursor) -> Optional[str]:
    """
    检测数据库中存在哪种联系人表。
    返回 'contact'、'ContactConfigTable' 或 None（两者都不存在）。
    """
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    表名集合 = {r[0] for r in cur.fetchall()}
    if "contact" in 表名集合:
        return "contact"
    if "ContactConfigTable" in 表名集合:
        return "ContactConfigTable"
    return None


def 从单个数据库搜索(
    db文件路径: str,
    关键词: str,
    精确匹配字段: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """
    在单个数据库中搜索联系人。
    精确匹配字段：传入字段名列表（如 ['nick_name', 'alias']），这些字段用精确匹配（= :keyword）；
    其余字段仍用模糊匹配（LIKE :like_keyword）。
    关键词为空时返回全部记录。
    """
    db路径 = Path(db文件路径)
    if not db路径.is_file():
        return []

    精确字段集 = set(精确匹配字段 or [])
    like关键词 = f"%{关键词}%"

    try:
        conn = sqlite3.connect(str(db路径))
        conn.text_factory = str
        cur = conn.cursor()

        表类型 = _检测表类型(cur)
        if 表类型 is None:
            return []

        # 根据精确匹配字段动态构建 WHERE 条件
        if not 精确字段集 or not 关键词:
            # 全模糊：使用原始 SQL 模板
            sql = _SQL_CONTACT if 表类型 == "contact" else _SQL_CONTACT_CONFIG
            cur.execute(sql, {"keyword": like关键词})
        else:
            # 有精确匹配字段：动态构建 WHERE 子句
            # contact 表字段名映射
            字段映射_contact = {
                "nick_name": "nick_name",
                "alias":     "alias",
                "username":  "username",
            }
            # ContactConfigTable 字段名映射
            字段映射_config = {
                "nick_name": "Nick",
                "alias":     "Number",
                "username":  "WxID",
            }
            字段映射 = 字段映射_contact if 表类型 == "contact" else 字段映射_config

            if 表类型 == "contact":
                # contact 表所有搜索字段（模糊 + 精确）
                全部搜索字段 = [
                    ("username",              "username"),
                    ("alias",                 "alias"),
                    ("remark",                "remark"),
                    ("nick_name",             "nick_name"),
                    ("remark_quan_pin",       "remark_quan_pin"),
                    ("remark_pin_yin_initial","remark_pin_yin_initial"),
                    ("pin_yin_initial",       "pin_yin_initial"),
                    ("quan_pin",              "quan_pin"),
                ]
                select部分 = "SELECT username, alias, remark, nick_name, small_head_url FROM contact"
            else:
                全部搜索字段 = [
                    ("username", "WxID"),
                    ("alias",    "Number"),
                    ("remark",   "Remark"),
                    ("nick_name","Nick"),
                ]
                select部分 = "SELECT WxID, Number, Remark, Nick, SmallHeader FROM ContactConfigTable"

            条件列表 = []
            参数字典 = {"keyword": 关键词, "like_keyword": like关键词}
            for 内部key, db字段 in 全部搜索字段:
                if 内部key in 精确字段集:
                    # 精确模式：只搜勾选字段，使用大小写不敏感的等值匹配
                    条件列表.append(f"{db字段} = :keyword")  # 精确匹配区分大小写
                # 精确模式下不再对其他字段做模糊匹配，直接跳过

            if not 条件列表:
                # 精确模式下没有勾选任何字段，返回空结果
                return []
            where部分 = " OR ".join(条件列表)
            order部分 = "ORDER BY remark, nick_name, username" if 表类型 == "contact" else "ORDER BY Remark, Nick, WxID"
            sql动态 = f"{select部分} WHERE {where部分} {order部分}"
            cur.execute(sql动态, 参数字典)
        行列表 = cur.fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    def 清理文本(值: str) -> str:
        """去除首尾空白，把字段内换行符替换成空格，防止表格行被撑高。"""
        return (值 or "").strip().replace("\r\n", " ").replace("\n", " ").replace("\r", " ")

    def 解析remark编号和姓名(remark文本: str):
        """
        从 remark 中提取编号（binah）和孩子中文全名。
        支持格式：
          - 前缀任意字符 + 连续6位数字 + '-' + 姓名（如 '¿¿¿000111-张三'）
          - 前缀任意字符 + 连续6位数字 + '-' + 姓名（如 '!!!00123-李四 王五'）
        规则：找到第一段连续6位数字作为编号，'-'后的所有字符作为姓名。
        若不符合格式则返回 ('', '')。
        """
        import re
        # 匹配：任意前缀 + 恰好6位连续数字 + '-' + 至少1个字符
        m = re.search(r'(\d{6})-(.+)', remark文本)
        if m:
            return m.group(1), m.group(2).strip()
        return "", ""

    结果 = []
    for col0, col1, col2, col3, col4 in 行列表:
        # 两种表的列顺序相同：(username/WxID, alias/Number, remark/Remark, nick/Nick, head_url/SmallHeader)
        remark值 = 清理文本(col2)
        编号, 孩子姓名 = 解析remark编号和姓名(remark值)
        结果.append({
            "username":       清理文本(col0),
            "alias":          清理文本(col1),
            "remark":         remark值,
            "nick_name":      清理文本(col3),
            "small_head_url": (col4 or "").strip(),
            "_db_source":     db路径.name,
            "_binah":         编号,
            "_child_name":    孩子姓名,
        })
    return 结果


def _db来源优先级(db文件名: str) -> int:
    """
    根据数据库文件名返回优先级数值（越小越优先）。
    按 数据库优先级 配置中的关键字做包含匹配，未匹配到则返回 999。
    """
    for 关键字, 优先级 in 数据库优先级.items():
        if 关键字 in db文件名:
            return 优先级
    return 999


def 跨库搜索(
    db路径列表: List[str],
    关键词: str,
    精确匹配字段: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """
    遍历所有数据库，按 username 去重合并。
    同一联系人出现在多个库时，按 数据库优先级 配置保留优先级最高的那条；
    优先级相同时，保留 remark 更长的那条。
    同时过滤掉 username 以 过滤username后缀 中任意后缀结尾的记录（如群聊、openim）。
    关键词为空时返回全部记录（不加 LIKE 过滤）。
    """
    已合并: Dict[str, Dict[str, str]] = {}

    for db路径 in db路径列表:
        for 条目 in 从单个数据库搜索(db路径, 关键词, 精确匹配字段=精确匹配字段):
            # 过滤 username 以指定后缀结尾的记录
            username小写 = 条目["username"].lower()
            if any(username小写.endswith(后缀) for 后缀 in 过滤username后缀):
                continue

            key = 条目["username"] or 条目["alias"]
            if not key:
                已合并[str(id(条目))] = 条目
                continue

            已有 = 已合并.get(key)
            if 已有 is None:
                已合并[key] = 条目
            else:
                # 比较优先级，优先级数值更小的胜出
                新优先级 = _db来源优先级(条目.get("_db_source", ""))
                旧优先级 = _db来源优先级(已有.get("_db_source", ""))
                if 新优先级 < 旧优先级:
                    # 新条目优先级更高，直接替换
                    已合并[key] = 条目
                elif 新优先级 == 旧优先级:
                    # 优先级相同，保留 remark 更长的
                    if len(条目.get("remark") or "") > len(已有.get("remark") or ""):
                        已合并[key] = 条目
                # 新优先级更低，保留原有条目，不做任何操作

    return list(已合并.values())


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


def 构建群成员索引(db路径列表: List[str]) -> Dict[str, Dict[str, List[str]]]:
    """
    从内部专用 contact.db 构建：微信原始ID(username) → 所在群名列表。

    返回结构：
        {
            "wxid_xxx": {
                "internal_live": ["内部直播群⑰", ...],
                "exclusive_lead": ["专属带领群-adisi-¡¡¡003719", ...],
            },
            ...
        }
    同一 wxid 在多个内部库出现时，群名去重合并。
    """
    索引: Dict[str, Dict[str, set]] = {}
    群成员库列表 = _解析群成员数据库路径(db路径列表)

    # 一次 SQL 拉出成员 wxid 与其所在内部直播群 / 专属带领群昵称
    _SQL = """
        SELECT
            c_member.username,
            cg.nick_name
        FROM chatroom_member cm
        JOIN chat_room cr ON cr.id = cm.room_id
        JOIN contact cg ON cg.username = cr.username
        JOIN contact c_member ON c_member.id = cm.member_id
        WHERE cg.nick_name LIKE '内部直播群%'
           OR cg.nick_name LIKE '专属带领群%'
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
                桶 = 索引.setdefault(wxid, {"internal_live": set(), "exclusive_lead": set()})
                if 群名.startswith("内部直播群"):
                    桶["internal_live"].add(群名)
                elif 群名.startswith("专属带领群"):
                    桶["exclusive_lead"].add(群名)
            conn.close()
        except sqlite3.Error:
            continue

    # set → 排序后的 list，便于表格展示稳定
    结果: Dict[str, Dict[str, List[str]]] = {}
    for wxid, 桶 in 索引.items():
        结果[wxid] = {
            "internal_live": sorted(桶["internal_live"]),
            "exclusive_lead": sorted(桶["exclusive_lead"]),
        }
    return 结果


def 填充联系人群信息(
    结果列表: List[Dict[str, str]],
    群索引: Dict[str, Dict[str, List[str]]],
) -> None:
    """
    就地给每条搜索结果补充 _internal_live_group / _exclusive_lead_group 字段。
    通过联系人的 username（微信原始ID）在群索引中查找；未加入任何群则留空。
    """
    for 条目 in 结果列表:
        wxid = (条目.get("username") or "").strip()
        群信息 = 群索引.get(wxid) or {}
        条目["_internal_live_group"] = "、".join(群信息.get("internal_live") or [])
        条目["_exclusive_lead_group"] = "、".join(群信息.get("exclusive_lead") or [])


# ============================================================
# 三、拷贝逻辑（复用拷贝脚本核心逻辑）
# ============================================================

def 获取文件信息(文件路径: str) -> Dict:
    """读取文件的大小和修改时间。"""
    stat = os.stat(文件路径)
    return {"size": stat.st_size, "mtime": stat.st_mtime}


def 等待源文件稳定(文件路径: str, 检查间隔秒: float = 1.0, 最大重试次数: int = 5) -> Dict:
    """
    连续两次检查文件大小和修改时间均一致，认为文件已稳定可安全复制。
    超过最大重试次数仍不稳定则抛出异常。
    """
    for _ in range(最大重试次数):
        第一次 = 获取文件信息(文件路径)
        time.sleep(检查间隔秒)
        第二次 = 获取文件信息(文件路径)
        if 第一次["size"] == 第二次["size"] and 第一次["mtime"] == 第二次["mtime"]:
            return 第二次
    raise RuntimeError(f"源文件长时间不稳定，无法安全复制：{文件路径}")


def 执行单个拷贝任务(源文件路径: str, 目标文件路径: str) -> str:
    """
    执行一次文件拷贝：
      1. 检查源文件存在
      2. 等待源文件稳定
      3. 确保目标目录存在
      4. 拷贝文件并校验大小
    返回描述结果的字符串（成功/失败）。
    """
    if not os.path.exists(源文件路径):
        return f"[跳过] 源文件不存在：{源文件路径}"

    try:
        源信息 = 等待源文件稳定(源文件路径)
    except RuntimeError as e:
        return f"[失败] {e}"

    目标目录 = os.path.dirname(目标文件路径)
    if 目标目录 and not os.path.exists(目标目录):
        os.makedirs(目标目录, exist_ok=True)

    shutil.copyfile(源文件路径, 目标文件路径)

    目标信息 = 获取文件信息(目标文件路径)
    if 源信息["size"] != 目标信息["size"]:
        return (
            f"[警告] 大小不一致：源={源信息['size']}字节，"
            f"目标={目标信息['size']}字节 — {目标文件路径}"
        )

    return f"[成功] {Path(源文件路径).name} → {目标文件路径}"


def _替换盘符前缀(路径: str, 盘符: str, 新根路径: str) -> str:
    """
    将路径开头的盘符（如 Z:、Y:）替换为本地根目录，保留其后相对路径。
    支持 Z:\\、Z:/、y:/ 等写法。
    """
    路径 = 路径.strip()
    if not 路径:
        return 路径

    盘符规范 = 盘符.upper().rstrip(":") + ":"
    if not 路径.upper().startswith(盘符规范):
        return 路径

    相对部分 = 路径[len(盘符规范):].lstrip("\\/")
    新根 = 新根路径.rstrip("\\/")
    if 相对部分:
        return f"{新根}\\{相对部分.replace('/', '\\')}"
    return 新根


def 根据设备生成拷贝任务(设备: str) -> List[List[str]]:
    """
    按设备选项从 默认拷贝任务列表 生成拷贝任务。
    仅改写源路径盘符，目标路径（Desktop 下的 db）不变。
    """
    需替换盘符 = 设备盘符映射.get(设备)
    任务列表: List[List[str]] = []

    for 源路径, 目标路径 in 默认拷贝任务列表:
        新源 = 源路径
        if 需替换盘符:
            新源 = _替换盘符前缀(源路径, 需替换盘符, 设备本地路径根)
        任务列表.append([新源, 目标路径])

    return 任务列表


def _获取旧版配置文件路径() -> Path:
    """旧版 json 配置路径（仅迁移用）：exe/脚本同目录。"""
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / 旧版配置文件名
    return Path(__file__).resolve().parent / 旧版配置文件名


def _从注册表读取配置() -> Dict:
    """从 Windows 注册表读取界面配置 JSON。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            注册表根路径,
            0,
            winreg.KEY_READ,
        ) as key:
            原始, _ = winreg.QueryValueEx(key, 注册表配置项名)
        if isinstance(原始, str) and 原始.strip():
            解析结果 = json.loads(原始)
            if isinstance(解析结果, dict):
                return 解析结果
    except Exception:
        pass
    return {}


def _写入注册表配置(配置: Dict) -> None:
    """将界面配置 JSON 写入 Windows 注册表。"""
    try:
        import winreg

        内容 = json.dumps(配置, ensure_ascii=False)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 注册表根路径) as key:
            winreg.SetValueEx(key, 注册表配置项名, 0, winreg.REG_SZ, 内容)
    except Exception:
        pass


def _从旧版json迁移配置() -> Dict:
    """
    若存在旧版 contact_search_config.json，读取后写入注册表并删除 json。
    避免用户升级后丢失已有配置，同时不再保留单独文件。
    """
    旧路径 = _获取旧版配置文件路径()
    if not 旧路径.is_file():
        return {}

    try:
        原始 = json.loads(旧路径.read_text(encoding="utf-8"))
        if isinstance(原始, dict):
            _写入注册表配置(原始)
            try:
                旧路径.unlink()
            except Exception:
                pass
            return 原始
    except Exception:
        pass
    return {}


def 加载界面配置() -> Dict:
    """
    读取上次保存的界面配置。
    优先读 Windows 注册表；若无则尝试从旧版 json 迁移。
    """
    配置 = _从注册表读取配置()
    if 配置:
        return 配置
    return _从旧版json迁移配置()


def 保存界面配置(配置: Dict) -> None:
    """将界面配置写入 Windows 注册表（不创建额外文件）。"""
    _写入注册表配置(配置)


def 从配置解析设备(配置: Dict) -> str:
    """读取并校验设备选项，非法值回退为「其他」。"""
    设备 = str(配置.get("设备", "其他")).strip()
    if 设备 not in 设备选项列表:
        return "其他"
    return 设备


def 从配置解析拷贝任务(配置: Dict, 设备: str) -> List[List[str]]:
    """
    优先使用配置里保存的拷贝任务；没有保存时再按设备规则生成默认任务。
    """
    已保存 = 配置.get("拷贝任务")
    if isinstance(已保存, list) and 已保存:
        任务列表: List[List[str]] = []
        for 条目 in 已保存:
            if isinstance(条目, (list, tuple)) and len(条目) >= 2:
                源 = str(条目[0]).strip()
                目标 = str(条目[1]).strip()
                if 源 and 目标:
                    任务列表.append([源, 目标])
        if 任务列表:
            return 任务列表
    return 根据设备生成拷贝任务(设备)


def 从配置解析数据库路径(配置: Dict) -> List[str]:
    """优先使用配置里保存的数据库路径列表。"""
    已保存 = 配置.get("数据库路径")
    if isinstance(已保存, list) and 已保存:
        路径列表 = [str(p).strip() for p in 已保存 if str(p).strip()]
        if 路径列表:
            return 路径列表
    return list(默认数据库列表)


# ============================================================
# 四、主界面
# ============================================================

class 搜索联系人界面:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("联系人搜索工具")
        self.root.geometry("1060x760")
        self.root.state("zoomed")  # Windows 下默认最大化窗口
        self.root.minsize(700, 500)

        # 读取上次保存的界面配置（设备 / 拷贝任务 / 数据库列表）
        self._已保存配置 = 加载界面配置()
        初始设备 = 从配置解析设备(self._已保存配置)

        # 搜索数据库列表
        self.db路径列表: List[str] = 从配置解析数据库路径(self._已保存配置)

        # 拷贝任务列表，每项为 [源路径, 目标路径]
        self.拷贝任务列表: List[List[str]] = 从配置解析拷贝任务(self._已保存配置, 初始设备)

        self._列ID列表 = [列[0] for 列 in 表格列定义]
        self._db面板已展开 = False
        self._拷贝面板已展开 = False

        # 分页状态：_全部结果 存放完整搜索结果，_已加载数量 记录当前表格已显示多少条
        self._全部结果: List[Dict[str, str]] = []
        self._已加载数量: int = 0

        # 自动搜索防抖计时器 ID（after 返回值），用于取消上一次未触发的计时
        self._防抖计时器: Optional[str] = None

        # 搜索模式：'模糊' 或 '精确'
        # 模糊模式：所有字段 LIKE，忽略下方字段勾选
        # 精确模式：只搜勾选的字段，且使用 = 精确匹配
        self._搜索模式 = tk.StringVar(value="模糊")

        # 设备选择：影响拷贝任务源路径中 Z:/Y: 是否映射到本机目录
        self._设备选择 = tk.StringVar(value=初始设备)

        # 精确模式下参与搜索的字段勾选（仅在精确模式时生效）
        self._精确_昵称   = tk.BooleanVar(value=True)
        self._精确_微信号 = tk.BooleanVar(value=True)
        self._精确_微信ID = tk.BooleanVar(value=True)

        # 头像图片内存缓存：key = username的MD5，value = tk.PhotoImage
        # 用 username 的 MD5 作 key，与本地磁盘文件名保持一致
        self._头像缓存: Dict[str, tk.PhotoImage] = {}

        # username → 上次缓存时的头像 URL，用于检测头像 URL 是否变化
        # URL 变化时强制重新下载并覆盖本地文件
        self._username头像url: Dict[str, str] = {}

        # 占位图（灰色小方块），用于头像未加载时显示
        self._占位图: Optional[tk.PhotoImage] = None

        # ── Canvas 表格专用状态 ──────────────────────────────────
        # 预先计算好的各列 x 起始坐标（表头绘制后填充）
        self._列x坐标: List[int] = []
        # 表头高度（固定）
        self._表头高度: int = 行高
        # Canvas 上已绘制的行数（用于像素定位）
        self._canvas已绘行数: int = 0
        # 每行实际高度（动态行高，默认至少为 行高）
        self._行高度列表: List[int] = []
        # 按字号缓存 Font 对象（含专属带领群等自定义字号列）
        self._字号字体缓存: Dict[int, tkfont.Font] = {}
        # 当前选中行的序号（-1 = 无选中）
        self._选中行序号: int = -1
        # 每行在 Canvas 上对应的背景矩形 item id，用于高亮选中
        self._行背景items: List[int] = []
        # 每行每列的文字 item id，list[行][列] = canvas item id
        self._行文字items: List[List[int]] = []
        # 每行的头像 item id
        self._行头像items: List[int] = []

        # 复制成功浮层提示（无边框 Toplevel + 半透明淡蓝，自动关闭）
        self._复制提示框: Optional[tk.Toplevel] = None
        self._复制提示定时器: Optional[str] = None

        self._构建界面()
        self._初始化占位图()
        # 关闭窗口前自动保存当前配置
        self.root.protocol("WM_DELETE_WINDOW", self._退出并保存)

    def _保存界面配置(self) -> None:
        """把当前设备、拷贝任务、数据库列表写入 Windows 注册表。"""
        if hasattr(self, "_拷贝任务行列表"):
            拷贝任务 = [[源, 目标] for 源, 目标 in self._读取当前拷贝任务()]
        else:
            拷贝任务 = [list(条目) for 条目 in self.拷贝任务列表]

        保存界面配置({
            "设备": self._设备选择.get(),
            "拷贝任务": 拷贝任务,
            "数据库路径": list(self.db路径列表),
        })

    def _退出并保存(self) -> None:
        """关闭窗口前先保存配置，再销毁主窗口。"""
        self._保存界面配置()
        self.root.destroy()

    def _初始化占位图(self):
        """生成一张灰色小方块作为头像未加载时的占位图。"""
        try:
            from PIL import Image, ImageTk
            占位 = Image.new("RGBA", 头像尺寸, (200, 200, 200, 180))
            self._占位图 = ImageTk.PhotoImage(占位)
        except Exception:
            self._占位图 = None

    # ----------------------------------------------------------
    # 界面构建
    # ----------------------------------------------------------

    def _构建界面(self):

        # ══════════════════════════════════════════════════════
        # 区域 A：搜索数据库折叠面板
        # ══════════════════════════════════════════════════════
        self._db标题行 = tk.Frame(self.root, bg="#ECEFF1")
        self._db标题行.pack(fill="x", padx=10, pady=(10, 0))

        self._db展开变量 = tk.StringVar(value="▶ 搜索数据库列表（点击展开）")
        tk.Button(
            self._db标题行,
            textvariable=self._db展开变量,
            font=("微软雅黑", 10),
            bg="#ECEFF1", fg="#37474F",
            activebackground="#CFD8DC",
            relief="flat", anchor="w", cursor="hand2",
            command=self._切换db面板,
        ).pack(side="left", fill="x", expand=True)

        self._db面板 = tk.Frame(self.root, bg="#F5F5F5", bd=1, relief="groove")
        self._构建db面板内容(self._db面板, 是拷贝面板=False)

        # ══════════════════════════════════════════════════════
        # 区域 B：拷贝任务折叠面板
        # ══════════════════════════════════════════════════════
        self._拷贝标题行 = tk.Frame(self.root, bg="#E8F5E9")
        self._拷贝标题行.pack(fill="x", padx=10, pady=(4, 0))

        self._拷贝展开变量 = tk.StringVar(value="▶ 拷贝任务配置（点击展开）")
        tk.Button(
            self._拷贝标题行,
            textvariable=self._拷贝展开变量,
            font=("微软雅黑", 10),
            bg="#E8F5E9", fg="#2E7D32",
            activebackground="#C8E6C9",
            relief="flat", anchor="w", cursor="hand2",
            command=self._切换拷贝面板,
        ).pack(side="left", fill="x", expand=True)

        self._拷贝面板 = tk.Frame(self.root, bg="#F1F8E9", bd=1, relief="groove")
        self._构建拷贝面板内容(self._拷贝面板)

        # ══════════════════════════════════════════════════════
        # 区域 B2：设备选择（影响拷贝任务源路径盘符）
        # ══════════════════════════════════════════════════════
        设备行 = tk.Frame(self.root, bg="#E8F4FD", pady=5)
        设备行.pack(fill="x", padx=10, pady=(2, 0))

        tk.Label(
            设备行, text="设备：", font=("微软雅黑", 字号),
            bg="#E8F4FD", fg="#555",
        ).pack(side="left")

        for 设备名 in 设备选项列表:
            说明 = ""
            if 设备名 == "内部专用":
                说明 = f"（Z:→{设备本地路径根}）"
            elif 设备名 == "意向专用":
                说明 = f"（Y:→{设备本地路径根}）"
            tk.Radiobutton(
                设备行,
                text=f"{设备名}{说明}",
                variable=self._设备选择,
                value=设备名,
                font=("微软雅黑", 11),
                bg="#E8F4FD",
                cursor="hand2",
                indicatoron=0,
                relief="groove",
                padx=10,
                pady=3,
                selectcolor="#1976D2",
                fg="#333",
                activeforeground="white",
                command=self._切换设备,
            ).pack(side="left", padx=4)

        # ══════════════════════════════════════════════════════
        # 区域 C：搜索行
        # ══════════════════════════════════════════════════════
        搜索框 = tk.Frame(self.root, pady=10)
        搜索框.pack(fill="x", padx=10)

        tk.Label(搜索框, text="搜索关键词：", font=("微软雅黑", 字号)).pack(side="left")

        self.关键词输入 = tk.Entry(搜索框, font=("微软雅黑", 字号), width=28)
        self.关键词输入.pack(side="left", padx=(4, 8))
        # 回车立即搜索；任意键入后等待防抖延迟再自动搜索
        self.关键词输入.bind("<Return>", lambda e: self._触发搜索(立即=True))
        self.关键词输入.bind("<KeyRelease>", lambda e: self._触发搜索(立即=False))
        self.关键词输入.focus()

        tk.Button(
            搜索框, text="搜 索",
            font=("微软雅黑", 字号), width=7,
            bg="#1976D2", fg="white",
            activebackground="#1565C0", activeforeground="white",
            relief="flat", cursor="hand2",
            command=lambda: self._触发搜索(立即=True),
        ).pack(side="left")

        # 刷新数据库按钮：执行拷贝任务后自动重新搜索
        tk.Button(
            搜索框, text="🔄 刷新数据库",
            font=("微软雅黑", 字号), padx=8,
            bg="#388E3C", fg="white",
            activebackground="#2E7D32", activeforeground="white",
            relief="flat", cursor="hand2",
            command=self._刷新数据库并搜索,
        ).pack(side="left", padx=(10, 0))

        # 搜索模式区：模糊 / 精确 单选 + 精确模式下的字段多选
        匹配区 = tk.Frame(搜索框)
        匹配区.pack(side="left", padx=(12, 0))

        tk.Label(匹配区, text="匹配方式：", font=("微软雅黑", 字号), fg="#555").pack(side="left")

        # 模糊 / 精确 单选按钮
        for 模式文字 in ["模糊", "精确"]:
            tk.Radiobutton(
                匹配区, text=模式文字, variable=self._搜索模式, value=模式文字,
                font=("微软雅黑", 字号), cursor="hand2",
                indicatoron=0, relief="groove",
                padx=10, pady=4,
                selectcolor="#1976D2", fg="#333", activeforeground="white",
                command=self._切换搜索模式,
            ).pack(side="left", padx=3)

        # 分隔符
        self._匹配分隔符 = tk.Label(匹配区, text="|", fg="#bbb", font=("微软雅黑", 字号))

        # 搜索列标签 + 三个字段按钮（精确模式才显示）
        self._搜索列标签 = tk.Label(匹配区, text="搜索列：", font=("微软雅黑", 字号), fg="#555")
        self._搜索列按钮列表 = []
        for 文字, 变量 in [
            ("昵称",      self._精确_昵称),
            ("微信号",    self._精确_微信号),
            ("微信原始ID", self._精确_微信ID),
        ]:
            btn = tk.Checkbutton(
                匹配区, text=文字, variable=变量,
                font=("微软雅黑", 字号), cursor="hand2",
                indicatoron=0, relief="groove",
                padx=10, pady=4,
                selectcolor="#388E3C", fg="#333",
                command=lambda: self._触发搜索(立即=True),
            )
            self._搜索列按钮列表.append(btn)

        # 初始化显示状态（模糊模式默认隐藏搜索列）
        self._更新搜索列显示()

        self.状态变量 = tk.StringVar(value="请输入关键词后按回车或点击搜索")
        tk.Label(
            搜索框, textvariable=self.状态变量,
            fg="#666", font=("微软雅黑", 11),
        ).pack(side="left", padx=12)

        # ══════════════════════════════════════════════════════
        # 区域 E：底部提示（必须在表格框之前 pack，才能先占底部位置）
        # ══════════════════════════════════════════════════════
        tk.Label(
            self.root,
            text="提示：单击表格中任意单元格，即可复制该格内容到剪贴板",
            fg="#999", font=("微软雅黑", 9), anchor="w",
        ).pack(fill="x", side="bottom", padx=12, pady=(0, 6))

        # ══════════════════════════════════════════════════════
        # 区域 D：Canvas 结果表格（支持像素级平滑滚动）
        # 布局：表格框（外层）
        #   ├─ 表头Frame（固定，不滚动）
        #   └─ 内容框（Canvas + 纵/横向滚动条）
        # ══════════════════════════════════════════════════════
        表格框 = tk.Frame(self.root)
        表格框.pack(fill="both", expand=True, padx=10, pady=(0, 0))

        # 表头容器：用于裁剪超出宽度的表头 Frame（overflow hidden 效果）
        # 固定在上方，高度 = 表头高度，不随内容滚动
        表头容器 = tk.Frame(表格框, height=self._表头高度, bg=表头背景色)
        表头容器.pack(fill="x", side="top")
        表头容器.pack_propagate(False)

        # 表头 Frame：用 place 布局，横向滚动时修改 x 偏移实现联动
        self._表头frame = tk.Frame(表头容器, bg=表头背景色, height=self._表头高度)
        self._表头frame.place(x=0, y=0, width=self._canvas总宽(), height=self._表头高度)
        # 表头内容会在 _canvas绘制表头() 中填充

        # 内容区：Canvas + 滚动条
        内容框 = tk.Frame(表格框)
        内容框.pack(fill="both", expand=True, side="top")

        self._canvas = tk.Canvas(内容框, bg=偶数行背景, highlightthickness=0)
        纵向滚动条 = ttk.Scrollbar(内容框, orient="vertical",   command=self._canvas.yview)
        横向滚动条 = ttk.Scrollbar(内容框, orient="horizontal", command=self._canvas.xview)
        self._canvas.configure(
            yscrollcommand=纵向滚动条.set,
            xscrollcommand=横向滚动条.set,
        )

        self._canvas.grid(row=0, column=0, sticky="nsew")
        纵向滚动条.grid(row=0, column=1, sticky="ns")
        横向滚动条.grid(row=1, column=0, sticky="ew")
        内容框.rowconfigure(0, weight=1)
        内容框.columnconfigure(0, weight=1)

        # 鼠标滚轮：Canvas 原生支持像素级滚动
        self._canvas.bind("<MouseWheel>", self._canvas滚轮)
        self._canvas.bind("<Button-4>",   self._canvas滚轮)
        self._canvas.bind("<Button-5>",   self._canvas滚轮)
        self._canvas.bind("<ButtonRelease-1>", self._canvas单击)
        # 窗口尺寸变化时同步表头宽度
        self._canvas.bind("<Configure>", self._canvas尺寸变化)
        # 横向滚动时，同步移动表头 x 偏移，使列标题与数据列保持对齐
        横向滚动条.configure(command=self._横向滚动命令)

        # Canvas 表格初始化：等 Canvas 创建完毕后绘制表头
        self.root.after(50, self._canvas绘制表头)

    # ----------------------------------------------------------
    # 搜索数据库面板内容（复用于 db 面板）
    # ----------------------------------------------------------

    def _构建db面板内容(self, 父框: tk.Frame, 是拷贝面板: bool):
        """在给定父框架内构建数据库列表框 + 操作按钮。"""
        列表区 = tk.Frame(父框, bg="#F5F5F5")
        列表区.pack(fill="both", expand=True, padx=8, pady=(6, 2))

        滚动条 = tk.Scrollbar(列表区)
        滚动条.pack(side="right", fill="y")

        self._db列表框 = tk.Listbox(
            列表区, font=("微软雅黑", 11),
            selectmode="single", activestyle="dotbox",
            yscrollcommand=滚动条.set, height=4,
            bg="#FAFAFA", relief="flat", bd=1,
        )
        self._db列表框.pack(side="left", fill="both", expand=True)
        滚动条.config(command=self._db列表框.yview)
        self._db列表框.bind("<Double-Button-1>", self._双击编辑db路径)

        # 就地编辑输入框（平时隐藏）
        self._db编辑框 = tk.Entry(父框, font=("微软雅黑", 11))
        self._db编辑索引: int = -1
        self._db编辑框.bind("<Return>",   self._提交db编辑)
        self._db编辑框.bind("<Escape>",   self._取消db编辑)
        self._db编辑框.bind("<FocusOut>", self._提交db编辑)

        操作行 = tk.Frame(父框, bg="#F5F5F5", pady=4)
        操作行.pack(fill="x", padx=8)

        def 小按钮(文字, 命令):
            return tk.Button(
                操作行, text=文字, font=("微软雅黑", 10),
                relief="flat", cursor="hand2", command=命令, padx=6,
            )

        小按钮("📂 选择文件", self._db选择文件).pack(side="left", padx=3)
        小按钮("🗑 删除选中",  self._db删除选中).pack(side="left", padx=3)
        小按钮("↩ 恢复默认",  self._db恢复默认).pack(side="left", padx=3)
        tk.Label(操作行, text="双击路径可就地编辑", font=("微软雅黑", 9),
                 fg="#999", bg="#F5F5F5").pack(side="left", padx=10)

        self._刷新db列表框()

    # ----------------------------------------------------------
    # 拷贝任务面板内容
    # ----------------------------------------------------------

    def _构建拷贝面板内容(self, 父框: tk.Frame):
        """在给定父框架内构建拷贝任务列表（每行：序号 + 源路径输入框 + → + 目标路径输入框 + 删除按钮）。"""
        # 说明行
        tk.Label(
            父框,
            text="每行配置一个拷贝任务：源文件（左）→ 目标文件（右）",
            font=("微软雅黑", 10), fg="#555", bg="#F1F8E9", anchor="w",
        ).pack(fill="x", padx=8, pady=(6, 2))

        # 任务行容器（用 Frame 包裹，支持动态增删行）
        self._拷贝任务容器 = tk.Frame(父框, bg="#F1F8E9")
        self._拷贝任务容器.pack(fill="x", padx=8, pady=(0, 2))

        # 操作按钮行
        操作行 = tk.Frame(父框, bg="#F1F8E9", pady=4)
        操作行.pack(fill="x", padx=8)

        tk.Button(
            操作行, text="➕ 添加任务",
            font=("微软雅黑", 10), relief="flat", cursor="hand2", padx=6,
            command=self._拷贝添加任务,
        ).pack(side="left", padx=3)

        # 渲染已有任务
        self._拷贝任务行列表: List[Dict] = []  # 每项存该行的输入框控件引用
        for 源, 目标 in self.拷贝任务列表:
            self._拷贝渲染一行(源, 目标)

    def _拷贝渲染一行(self, 源路径: str = "", 目标路径: str = ""):
        """在拷贝任务容器中渲染一行：序号 + 源输入框 + 浏览按钮 + → + 目标输入框 + 浏览按钮 + 删除按钮。"""
        行框 = tk.Frame(self._拷贝任务容器, bg="#F1F8E9", pady=2)
        行框.pack(fill="x")

        序号 = len(self._拷贝任务行列表) + 1
        序号标签 = tk.Label(行框, text=f"{序号}.", font=("微软雅黑", 10),
                            bg="#F1F8E9", width=2, anchor="e")
        序号标签.pack(side="left", padx=(0, 4))

        # 源路径输入框
        源变量 = tk.StringVar(value=源路径)
        源输入框 = tk.Entry(行框, textvariable=源变量, font=("微软雅黑", 10), width=36)
        源输入框.pack(side="left", padx=(0, 2))
        源输入框.bind("<FocusOut>", lambda _e: self._保存界面配置())

        tk.Button(
            行框, text="📂", font=("微软雅黑", 9), relief="flat", cursor="hand2", padx=2,
            command=lambda v=源变量: self._拷贝浏览文件(v),
        ).pack(side="left", padx=(0, 6))

        tk.Label(行框, text="→", font=("微软雅黑", 11), bg="#F1F8E9").pack(side="left", padx=4)

        # 目标路径输入框
        目标变量 = tk.StringVar(value=目标路径)
        目标输入框 = tk.Entry(行框, textvariable=目标变量, font=("微软雅黑", 10), width=36)
        目标输入框.pack(side="left", padx=(6, 2))
        目标输入框.bind("<FocusOut>", lambda _e: self._保存界面配置())

        tk.Button(
            行框, text="📂", font=("微软雅黑", 9), relief="flat", cursor="hand2", padx=2,
            command=lambda v=目标变量: self._拷贝浏览保存(v),
        ).pack(side="left", padx=(0, 6))

        # 删除按钮
        行引用 = {"行框": 行框, "源变量": 源变量, "目标变量": 目标变量, "序号标签": 序号标签}
        tk.Button(
            行框, text="🗑", font=("微软雅黑", 9), relief="flat", cursor="hand2",
            fg="#c62828",
            command=lambda ref=行引用: self._拷贝删除行(ref),
        ).pack(side="left")

        self._拷贝任务行列表.append(行引用)

    def _拷贝重建任务列表(self) -> None:
        """按当前设备选择，清空并重新渲染拷贝任务面板中的所有行。"""
        if not hasattr(self, "_拷贝任务行列表"):
            return

        for 行引用 in list(self._拷贝任务行列表):
            行引用["行框"].destroy()
        self._拷贝任务行列表.clear()

        self.拷贝任务列表 = 根据设备生成拷贝任务(self._设备选择.get())
        for 源路径, 目标路径 in self.拷贝任务列表:
            self._拷贝渲染一行(源路径, 目标路径)

    def _切换设备(self) -> None:
        """
        切换设备选项时，按规则改写拷贝任务源路径并刷新面板：
          - 内部专用：Z: → 设备本地路径根
          - 意向专用：Y: → 设备本地路径根
          - 其他：恢复默认 Z:/Y: 路径
        """
        设备 = self._设备选择.get()
        self._拷贝重建任务列表()

        if 设备 == "其他":
            提示 = "已切换为「其他」：拷贝任务保持 Z:/Y: 原路径"
        else:
            盘符 = 设备盘符映射.get(设备, "")
            提示 = f"已切换为「{设备}」：拷贝源路径 {盘符} 已改为 {设备本地路径根}"
        self.状态变量.set(提示)
        self._保存界面配置()

    def _拷贝添加任务(self):
        """添加一行空白拷贝任务。"""
        self._拷贝任务行列表  # 确保列表存在
        self._拷贝渲染一行("", "")
        self._保存界面配置()

    def _拷贝浏览文件(self, 变量: tk.StringVar):
        """打开文件选择器，选择源文件路径。"""
        路径 = filedialog.askopenfilename(
            parent=self.root, title="选择源文件",
            filetypes=[("SQLite 数据库", "*.db *.db3"), ("所有文件", "*.*")],
        )
        if 路径:
            变量.set(路径)
            self._保存界面配置()

    def _拷贝浏览保存(self, 变量: tk.StringVar):
        """打开文件保存选择器，选择目标文件路径。"""
        路径 = filedialog.asksaveasfilename(
            parent=self.root, title="选择目标路径",
            defaultextension=".db",
            filetypes=[("SQLite 数据库", "*.db *.db3"), ("所有文件", "*.*")],
        )
        if 路径:
            变量.set(路径)
            self._保存界面配置()

    def _拷贝删除行(self, 行引用: Dict):
        """删除指定的拷贝任务行，并重新编号。"""
        行引用["行框"].destroy()
        self._拷贝任务行列表 = [r for r in self._拷贝任务行列表 if r is not 行引用]
        # 重新编号
        for i, r in enumerate(self._拷贝任务行列表):
            r["序号标签"].config(text=f"{i + 1}.")
        self._保存界面配置()

    def _读取当前拷贝任务(self) -> List[Tuple[str, str]]:
        """从界面输入框读取当前所有有效的拷贝任务（源和目标均非空）。"""
        任务列表 = []
        for r in self._拷贝任务行列表:
            源 = r["源变量"].get().strip().strip('"')
            目标 = r["目标变量"].get().strip().strip('"')
            if 源 and 目标:
                任务列表.append((源, 目标))
        return 任务列表

    # ----------------------------------------------------------
    # 折叠面板切换
    # ----------------------------------------------------------

    def _切换db面板(self):
        if self._db面板已展开:
            self._db面板.pack_forget()
            self._db展开变量.set("▶ 搜索数据库列表（点击展开）")
            self._db面板已展开 = False
        else:
            self._db面板.pack(fill="x", padx=10, pady=(2, 0))
            self._db展开变量.set("▼ 搜索数据库列表（点击收起）")
            self._db面板已展开 = True

    def _切换拷贝面板(self):
        if self._拷贝面板已展开:
            self._拷贝面板.pack_forget()
            self._拷贝展开变量.set("▶ 拷贝任务配置（点击展开）")
            self._拷贝面板已展开 = False
        else:
            self._拷贝面板.pack(fill="x", padx=10, pady=(2, 0))
            self._拷贝展开变量.set("▼ 拷贝任务配置（点击收起）")
            self._拷贝面板已展开 = True

    # ----------------------------------------------------------
    # 搜索数据库列表管理
    # ----------------------------------------------------------

    def _刷新db列表框(self):
        self._db列表框.delete(0, "end")
        for i, p in enumerate(self.db路径列表):
            self._db列表框.insert("end", f"{i + 1}.  {p}")

    def _db选择文件(self):
        路径列表 = filedialog.askopenfilenames(
            parent=self.root, title="选择数据库文件（.db / .db3）",
            filetypes=[("SQLite 数据库", "*.db *.db3"), ("所有文件", "*.*")],
        )
        新增 = 0
        for 路径 in 路径列表:
            归一化 = os.path.normcase(os.path.abspath(路径))
            已有 = [os.path.normcase(os.path.abspath(p)) for p in self.db路径列表]
            if 归一化 not in 已有:
                self.db路径列表.append(路径)
                新增 += 1
        if 新增:
            self._刷新db列表框()
            self.状态变量.set(f"已添加 {新增} 个数据库，共 {len(self.db路径列表)} 个")
            self._保存界面配置()

    def _db删除选中(self):
        选中 = self._db列表框.curselection()
        if not 选中:
            messagebox.showinfo("提示", "请先单击选中要删除的项。", parent=self.root)
            return
        被删 = self.db路径列表.pop(选中[0])
        self._刷新db列表框()
        self.状态变量.set(f"已删除：{Path(被删).name}")
        self._保存界面配置()

    def _db恢复默认(self):
        self.db路径列表 = list(默认数据库列表)
        self._刷新db列表框()
        self.状态变量.set("已恢复默认数据库配置")
        self._保存界面配置()

    def _双击编辑db路径(self, event):
        选中 = self._db列表框.curselection()
        if not 选中:
            return
        idx = 选中[0]
        bbox = self._db列表框.bbox(idx)
        if not bbox:
            return
        x, y, 宽, 高 = bbox
        self._db编辑索引 = idx
        self._db编辑框.place(in_=self._db列表框, x=x, y=y, width=宽, height=高)
        self._db编辑框.delete(0, "end")
        self._db编辑框.insert(0, self.db路径列表[idx])
        self._db编辑框.selection_range(0, "end")
        self._db编辑框.focus()

    def _提交db编辑(self, event=None):
        if self._db编辑索引 < 0:
            return
        新路径 = self._db编辑框.get().strip().strip('"').strip("'")
        if 新路径:
            self.db路径列表[self._db编辑索引] = 新路径
            self._刷新db列表框()
            self.状态变量.set(f"路径已更新：{Path(新路径).name}")
            self._保存界面配置()
        self._db编辑框.place_forget()
        self._db编辑索引 = -1

    def _取消db编辑(self, event=None):
        self._db编辑框.place_forget()
        self._db编辑索引 = -1

    # ----------------------------------------------------------
    # 刷新数据库（拷贝 + 重新搜索）
    # ----------------------------------------------------------

    def _刷新数据库并搜索(self):
        """
        点击"刷新数据库"按钮时：
          1. 读取拷贝任务面板中的任务
          2. 在后台线程中执行拷贝（防止界面卡死）
          3. 拷贝完成后回到主线程自动触发搜索
        """
        任务列表 = self._读取当前拷贝任务()
        if not 任务列表:
            # 没有拷贝任务，直接重新搜索
            self._执行搜索()
            return

        self.状态变量.set(f"正在拷贝 {len(任务列表)} 个文件……")
        self.root.update_idletasks()

        def 后台拷贝():
            日志行 = []
            for 源, 目标 in 任务列表:
                结果 = 执行单个拷贝任务(源, 目标)
                日志行.append(结果)

            # 拷贝完成，切回主线程更新界面
            成功数 = sum(1 for r in 日志行 if r.startswith("[成功]"))
            失败数 = len(日志行) - 成功数
            摘要 = f"拷贝完成：{成功数} 成功 / {失败数} 失败。"

            # 如果有失败，弹出详情
            if 失败数:
                详情 = "\n".join(日志行)
                self.root.after(0, lambda: messagebox.showwarning(
                    "拷贝结果", f"{摘要}\n\n{详情}", parent=self.root
                ))

            # 回主线程：更新状态并重新搜索
            self.root.after(0, lambda: self.状态变量.set(摘要 + " 正在重新搜索……"))
            self.root.after(0, self._执行搜索)

        threading.Thread(target=后台拷贝, daemon=True).start()

    # ----------------------------------------------------------
    # 搜索（防抖触发 + 分页）
    # ----------------------------------------------------------

    def _更新搜索列显示(self):
        """根据当前搜索模式，显示或隐藏「搜索列」分隔符、标签和字段按钮。"""
        if self._搜索模式.get() == "精确":
            self._匹配分隔符.pack(side="left", padx=6)
            self._搜索列标签.pack(side="left")
            for btn in self._搜索列按钮列表:
                btn.pack(side="left", padx=3)
        else:
            self._匹配分隔符.pack_forget()
            self._搜索列标签.pack_forget()
            for btn in self._搜索列按钮列表:
                btn.pack_forget()

    def _切换搜索模式(self):
        """模式切换时：更新搜索列显示状态，并立即重新搜索。"""
        self._更新搜索列显示()
        self._触发搜索(立即=True)

    def _触发搜索(self, 立即: bool = False):
        """
        统一的搜索触发入口。
        - 立即=True（按回车或点搜索按钮）：取消防抖计时，立刻执行搜索
        - 立即=False（键盘输入）：取消上一个防抖计时，延迟 自动搜索延迟毫秒 后执行
        """
        if self._防抖计时器 is not None:
            self.root.after_cancel(self._防抖计时器)
            self._防抖计时器 = None

        if 立即:
            self._执行搜索()
        else:
            self._防抖计时器 = self.root.after(自动搜索延迟毫秒, self._执行搜索)

    def _执行搜索(self):
        """执行搜索：获取全部结果存入内存，只向表格写入前 分页批次大小 条。"""
        self._防抖计时器 = None  # 清除计时器引用

        if not self.db路径列表:
            messagebox.showwarning("未配置数据库", "请先展开数据库面板并添加至少一个数据库。")
            return

        关键词 = self.关键词输入.get().strip()

        self.状态变量.set("搜索中……")
        self.root.update_idletasks()

        # 全量搜索并过滤，结果存入内存
        # 根据搜索模式和字段勾选状态构建参数
        # 模糊模式：精确字段列表传 None，全字段模糊匹配
        # 精确模式：只搜勾选的字段，使用精确匹配（= 关键词）
        if self._搜索模式.get() == "精确":
            精确字段 = []
            if self._精确_昵称.get():
                精确字段.append("nick_name")
            if self._精确_微信号.get():
                精确字段.append("alias")
            if self._精确_微信ID.get():
                精确字段.append("username")
            传入精确字段 = 精确字段 if 精确字段 else None
        else:
            传入精确字段 = None  # 模糊模式不限制字段

        self._全部结果 = 跨库搜索(self.db路径列表, 关键词, 精确匹配字段=传入精确字段)
        # 从内部专用库回填群信息（内部直播群 / 专属带领群）
        self._群成员索引 = 构建群成员索引(self.db路径列表)
        填充联系人群信息(self._全部结果, self._群成员索引)
        self._已加载数量 = 0

        # 清空 Canvas 表格，清空头像缓存
        self._canvas清空表格()
        self._头像缓存.clear()
        self._username头像url.clear()
        self._追加加载一批()

        总数 = len(self._全部结果)
        已显示 = min(self._已加载数量, 总数)
        if 总数 == 0:
            提示 = f'未找到包含"{关键词}"的联系人' if 关键词 else "数据库中没有记录"
        elif 已显示 < 总数:
            提示 = f'共 {总数} 条结果，已显示 {已显示} 条，向下滚动加载更多'
            if 关键词:
                提示 += f'（关键词："{关键词}"）'
        else:
            提示 = f'共 {总数} 条结果，已全部显示'
            if 关键词:
                提示 += f'（关键词："{关键词}"）'
        self.状态变量.set(提示)

    def _追加加载一批(self):
        """从 _全部结果 中取下一批（分页批次大小条），追加绘制到 Canvas，并触发头像异步下载。"""
        总数 = len(self._全部结果)
        起始 = self._已加载数量
        结束 = min(起始 + 分页批次大小, 总数)

        for 序号 in range(起始, 结束):
            条目 = self._全部结果[序号]
            self._canvas绘制一行(序号, 条目)

            url = 条目.get("small_head_url", "")
            if url:
                self._异步下载头像(序号, url, username=条目.get("username", ""))

        self._已加载数量 = 结束
        self._canvas更新scrollregion()

    # ----------------------------------------------------------
    # Canvas 表格绘制
    # ----------------------------------------------------------

    def _列内容字号(self, 列key: str) -> int:
        """返回指定列的正文字号；未单独配置的列使用全局 字号。"""
        return 列内容字号.get(列key, 字号)

    def _字号字体(self, 目标字号: int) -> tkfont.Font:
        """按字号获取 Font 对象（懒加载并缓存）。"""
        if 目标字号 not in self._字号字体缓存:
            self._字号字体缓存[目标字号] = tkfont.Font(family="微软雅黑", size=目标字号)
        return self._字号字体缓存[目标字号]

    def _列字体(self, 列key: str) -> tkfont.Font:
        """按列 key 取对应字号的 Font。"""
        return self._字号字体(self._列内容字号(列key))

    def _表格字体(self) -> tkfont.Font:
        """默认表格正文字体（全局 字号）。"""
        return self._字号字体(字号)

    def _估算换行行数(self, 文本: str, 可用宽: int, 字体: tkfont.Font) -> int:
        """
        按像素宽度估算文本换行后的行数（中文按单字测量，兼容无空格长串）。
        可用宽 <= 0 时视为单行，避免除零。
        """
        if not 文本:
            return 1
        if 可用宽 <= 0:
            return 1
        行数 = 1
        当前宽 = 0.0
        for ch in 文本:
            字宽 = 字体.measure(ch)
            if 当前宽 + 字宽 > 可用宽 and 当前宽 > 0:
                行数 += 1
                当前宽 = 字宽
            else:
                当前宽 += 字宽
        return 行数

    def _计算行高(self, 条目: Dict[str, str]) -> int:
        """
        根据各列文本在列宽内换行后的最大行数，计算该行所需高度。
        各列使用各自字号估算换行（专属带领群等为 8 号字）。
        """
        实际列宽 = self._实际列宽列表()
        需要高 = 行高
        for i, (列key, _, _) in enumerate(表格列定义):
            文本 = 条目.get(列key, "") or ""
            可用宽 = 实际列宽[i] - 12
            列字体 = self._列字体(列key)
            行数 = self._估算换行行数(文本, 可用宽, 列字体)
            行距 = 列字体.metrics("linespace")
            列需要高 = int(行数 * 行距 + 2 * 单元格内边距)
            需要高 = max(需要高, 列需要高)
        return 需要高

    def _canvas行y(self, 行序号: int) -> int:
        """计算第 行序号 行（0-based）的 Canvas y 坐标（累加前面各行动态高度）。"""
        if 行序号 <= 0:
            return 0
        if 行序号 <= len(self._行高度列表):
            return sum(self._行高度列表[:行序号])
        return 行序号 * 行高

    def _canvas根据y找行序号(self, canvas_y: float) -> int:
        """根据 Canvas y 坐标反查行序号（支持动态行高）。"""
        if canvas_y < 0 or not self._行高度列表:
            return -1
        累计 = 0
        for 序号, 高 in enumerate(self._行高度列表):
            if canvas_y < 累计 + 高:
                return 序号
            累计 += 高
        return -1

    def _canvas重绘已加载数据行(self) -> None:
        """
        窗口变宽/变窄后，列宽变化会导致换行行数变化，需按新列宽重算行高并重绘。
        保留头像内存缓存，重绘后贴回已下载头像。
        """
        if self._已加载数量 <= 0:
            return
        已加载 = self._已加载数量
        self._canvas.delete("数据行")
        self._行背景items.clear()
        self._行文字items.clear()
        self._行头像items.clear()
        self._行高度列表.clear()
        self._canvas已绘行数 = 0
        self._选中行序号 = -1

        for 序号 in range(已加载):
            self._canvas绘制一行(序号, self._全部结果[序号])
            条目 = self._全部结果[序号]
            username = 条目.get("username", "")
            if username:
                md5key = self._计算md5(username)
                if md5key in self._头像缓存:
                    self._更新canvas行头像(序号, self._头像缓存[md5key])

        self._canvas更新scrollregion()

    def _实际列宽列表(self) -> List[int]:
        """
        计算各数据列的实际显示宽度。
        规则：当 Canvas 可见宽度 > 所有列最小宽度之和时，按比例等比例放大各列；
        否则使用配置中的最小列宽。
        头像列宽固定不参与放大。
        """
        最小列宽列表 = [列宽 for _, _, 列宽 in 表格列定义]
        最小数据列总宽 = sum(最小列宽列表)
        # Canvas 未创建或尚未渲染时直接返回最小列宽
        if not hasattr(self, "_canvas"):
            return 最小列宽列表
        canvas宽 = self._canvas.winfo_width()
        可用宽 = canvas宽 - 头像列宽  # 去掉头像列后剩余宽度

        if canvas宽 > 1 and 可用宽 > 最小数据列总宽:
            # 按比例放大：各列宽 × (可用宽 / 最小总宽)
            比例 = 可用宽 / 最小数据列总宽
            放大后 = [int(w * 比例) for w in 最小列宽列表]
            # 修正最后一列，补齐因取整丢失的像素
            误差 = 可用宽 - sum(放大后)
            放大后[-1] += 误差
            return 放大后
        return 最小列宽列表

    def _canvas总宽(self) -> int:
        """所有列宽之和（头像列 + 数据列实际宽度）。"""
        return 头像列宽 + sum(self._实际列宽列表())

    def _canvas绘制表头(self):
        """
        填充固定表头 Frame 的内容（头像列 + 各数据列标题）。
        表头 Frame 独立于 Canvas 之外，不随内容滚动。
        窗口尺寸变化或横向滚动时会重新调用以刷新布局。
        """
        # 清除旧的表头子控件
        for w in self._表头frame.winfo_children():
            w.destroy()
        # 更新表头 Frame 总宽（列数固定，宽度固定）
        self._表头frame.place(width=self._canvas总宽())

        # 头像列表头
        tk.Label(
            self._表头frame, text="头像", bg=表头背景色, fg=表头文字色,
            font=("微软雅黑", 字号, "bold"),
            relief="flat", anchor="center",
        ).place(x=0, y=0, width=头像列宽, height=self._表头高度)

        # 各数据列表头（使用实际列宽，窗口宽时等比放大）
        实际列宽 = self._实际列宽列表()
        x = 头像列宽
        for i, (_, 列标题, _) in enumerate(表格列定义):
            列宽 = 实际列宽[i]
            tk.Label(
                self._表头frame, text=列标题, bg=表头背景色, fg=表头文字色,
                font=("微软雅黑", 字号, "bold"),
                relief="flat", anchor="w", padx=6,
            ).place(x=x, y=0, width=列宽, height=self._表头高度)
            tk.Frame(self._表头frame, bg=分隔线颜色).place(
                x=x - 1, y=4, width=1, height=self._表头高度 - 8
            )
            x += 列宽

    def _canvas清空表格(self):
        """清除 Canvas 上所有数据行的绘制内容，重置状态。"""
        self._canvas.delete("数据行")
        self._行背景items.clear()
        self._行文字items.clear()
        self._行头像items.clear()
        self._行高度列表.clear()
        self._canvas已绘行数 = 0
        self._选中行序号 = -1
        self._canvas.configure(scrollregion=(0, 0, self._canvas总宽(), 0))
        self._canvas.yview_moveto(0)

    def _canvas绘制一行(self, 行序号: int, 条目: Dict[str, str]):
        """
        在 Canvas 上绘制一行数据：
          - 背景矩形（交替色）
          - 头像占位图
          - 各列文字（左上对齐，列宽内自动换行；行高随内容增高）
          - 底部分隔线
        """
        当前行高 = self._计算行高(条目)
        self._行高度列表.append(当前行高)

        y上 = self._canvas行y(行序号)
        y下 = y上 + 当前行高
        总宽 = self._canvas总宽()
        背景色 = 偶数行背景 if 行序号 % 2 == 0 else 奇数行背景

        # 背景矩形
        背景id = self._canvas.create_rectangle(
            0, y上, 总宽, y下,
            fill=背景色, outline="", tags="数据行"
        )
        self._行背景items.append(背景id)

        # 头像（先放占位图，异步加载后替换；垂直居中于当前行动态行高）
        头像id = self._canvas.create_image(
            头像列宽 // 2, y上 + 当前行高 // 2,
            image=self._占位图, anchor="center", tags="数据行"
        )
        self._行头像items.append(头像id)

        # 各列文字：左上对齐 + width 换行，完整显示在单元格内
        实际列宽 = self._实际列宽列表()
        文字id列表: List[int] = []
        x = 头像列宽
        for i, (列key, _, _) in enumerate(表格列定义):
            列宽 = 实际列宽[i]
            文本 = 条目.get(列key, "")
            单元格字号 = self._列内容字号(列key)
            tid = self._canvas.create_text(
                x + 8, y上 + 单元格内边距,
                text=文本, anchor="nw",
                font=("微软雅黑", 单元格字号),
                fill="#222222",
                width=列宽 - 12,
                tags="数据行",
            )
            文字id列表.append(tid)
            x += 列宽

        self._行文字items.append(文字id列表)

        # 行底部分隔线
        self._canvas.create_line(
            0, y下, 总宽, y下,
            fill=分隔线颜色, tags="数据行"
        )

        self._canvas已绘行数 += 1

    def _canvas更新scrollregion(self):
        """根据已绘行数与各行动态高度，更新 Canvas 的可滚动区域。"""
        if self._行高度列表:
            总高 = sum(self._行高度列表)
        else:
            总高 = self._canvas已绘行数 * 行高
        总宽 = self._canvas总宽()
        self._canvas.configure(scrollregion=(0, 0, 总宽, 总高))

    def _canvas尺寸变化(self, event):
        """
        窗口宽度变化时：
          1. 重绘表头（列宽重新按比例计算）
          2. 按新列宽重算行高并重绘已加载数据行
        """
        self._canvas绘制表头()
        self._canvas重绘已加载数据行()

    def _canvas重新布局数据行(self):
        """兼容旧调用：统一走重绘逻辑（动态行高需整行重算）。"""
        self._canvas重绘已加载数据行()

    def _横向滚动命令(self, *args):
        """
        横向滚动条命令：先让 Canvas 横向滚动，
        再根据 xview 左侧比例同步移动表头 Frame 的 x 偏移，
        使列标题始终与下方数据列对齐。
        """
        self._canvas.xview(*args)
        left_frac = self._canvas.xview()[0]
        总宽 = self._canvas总宽()
        偏移 = -int(left_frac * 总宽)
        # 通过修改表头内第一个子控件（头像列）的 place x 不够，
        # 直接把整个表头 Frame 的起始 x 设为负偏移即可
        self._表头frame.place(x=偏移, y=0, width=总宽, height=self._表头高度)

    # ----------------------------------------------------------
    # Canvas 滚动与交互
    # ----------------------------------------------------------

    def _canvas滚轮(self, event):
        """
        Canvas 鼠标滚轮：用 yview_moveto 按像素比例滚动，实现平滑效果。
        每次滚动固定像素数（每次 20px），通过换算成比例后调用 yview_moveto。
        """
        # 获取 scrollregion 总高度
        region = self._canvas.cget("scrollregion")
        if not region:
            return
        try:
            总高 = float(str(region).split()[3])
        except (IndexError, ValueError):
            return
        if 总高 <= 0:
            return

        每次滚动像素 = 滚动速度像素  # 顶部配置区可调整

        if event.num == 4:
            方向 = -1
        elif event.num == 5:
            方向 = 1
        else:
            方向 = -1 if event.delta > 0 else 1

        当前top, _ = self._canvas.yview()
        新top = max(0.0, min(1.0, 当前top + 方向 * 每次滚动像素 / 总高))
        self._canvas.yview_moveto(新top)
        self._检查是否滚动到底部()

    def _检查是否滚动到底部(self, event=None):
        """滚动到底部时追加加载下一批数据。"""
        if self._已加载数量 >= len(self._全部结果):
            return

        _, bottom = self._canvas.yview()
        if bottom < 0.99:
            return

        self._追加加载一批()

        总数 = len(self._全部结果)
        已显示 = self._已加载数量
        关键词 = self.关键词输入.get().strip()
        if 已显示 >= 总数:
            提示 = f'共 {总数} 条结果，已全部显示'
        else:
            提示 = f'共 {总数} 条结果，已显示 {已显示} 条，向下滚动加载更多'
        if 关键词:
            提示 += f'（关键词："{关键词}"）'
        self.状态变量.set(提示)

    def _canvas单击(self, event):
        """
        单击 Canvas：
          - 识别点击的行序号和列序号
          - 高亮选中行
          - 复制对应单元格内容到剪贴板（头像列不复制）
        """
        # Canvas 的 event.y 是视口坐标，需要加上滚动偏移换算成 Canvas 坐标
        canvas_y = self._canvas.canvasy(event.y)
        canvas_x = self._canvas.canvasx(event.x)

        # 计算行序号（0-based，支持动态行高）
        行序号 = self._canvas根据y找行序号(canvas_y)
        if 行序号 < 0 or 行序号 >= self._canvas已绘行数:
            return

        # 高亮选中行（恢复上一行背景色，设置新选中行）
        if self._选中行序号 >= 0 and self._选中行序号 < len(self._行背景items):
            旧背景色 = 偶数行背景 if self._选中行序号 % 2 == 0 else 奇数行背景
            self._canvas.itemconfig(self._行背景items[self._选中行序号], fill=旧背景色)
        self._canvas.itemconfig(self._行背景items[行序号], fill=选中行背景)
        self._选中行序号 = 行序号

        # 判断点击的列（头像列 or 数据列）
        if canvas_x < 头像列宽:
            return  # 点击头像列，不复制

        列序号 = -1
        x = 头像列宽
        for i, (_, _, 列宽) in enumerate(表格列定义):
            if canvas_x < x + 列宽:
                列序号 = i
                break
            x += 列宽

        if 列序号 < 0:
            return

        # 取对应行的数据
        if 行序号 >= len(self._全部结果):
            return
        条目 = self._全部结果[行序号]
        列key = 表格列定义[列序号][0]
        列标题 = 表格列定义[列序号][1]
        单元格内容 = 条目.get(列key, "")

        if not 单元格内容:
            self.状态变量.set("该单元格为空")
            self._显示复制提示("该单元格为空", "", event, 成功=False)
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(单元格内容)
        self.状态变量.set(f'已复制 [{列标题}]：{单元格内容}')
        self._显示复制提示(列标题, 单元格内容, event, 成功=True)

    def _关闭复制提示(self) -> None:
        """关闭并销毁复制浮层，避免多次点击叠加多个提示框。"""
        if self._复制提示定时器:
            try:
                self.root.after_cancel(self._复制提示定时器)
            except Exception:
                pass
            self._复制提示定时器 = None
        if self._复制提示框 is not None:
            try:
                self._复制提示框.destroy()
            except Exception:
                pass
            self._复制提示框 = None

    def _绑定浮层单击关闭(self, *控件列表: tk.Widget) -> None:
        """为复制提示浮层及其子控件绑定单击关闭，并显示手型光标。"""
        def _单击关闭(_event=None) -> None:
            self._关闭复制提示()

        for 控件 in 控件列表:
            控件.bind("<ButtonRelease-1>", _单击关闭)
            try:
                控件.configure(cursor="hand2")
            except tk.TclError:
                pass

    def _显示复制提示(
        self,
        列标题: str,
        内容: str,
        event: Optional[tk.Event] = None,
        *,
        成功: bool = True,
    ) -> None:
        """
        在窗口顶部居中弹出半透明淡蓝复制反馈浮层。
        使用 Toplevel + -alpha 实现半透明；单击浮层可立即关闭。
        """
        self._关闭复制提示()

        背景色 = 复制提示背景色
        边框色 = 复制提示边框色
        主字色 = 复制提示主字色
        副字色 = 复制提示副字色

        if 成功:
            主文案 = "✓  已复制到剪贴板"
            if len(内容) > 复制提示预览最大字数:
                预览 = 内容[: 复制提示预览最大字数 - 3] + "..."
            else:
                预览 = 内容
            副文案 = f"[{列标题}]  {预览}"
        else:
            主文案 = "⚠  无法复制"
            副文案 = 列标题 or "该单元格没有内容"

        # 无边框 Toplevel：支持整体半透明，置顶显示
        浮层 = tk.Toplevel(self.root)
        浮层.withdraw()
        浮层.overrideredirect(True)
        浮层.configure(bg=边框色)
        try:
            浮层.attributes("-topmost", True)
            浮层.attributes("-alpha", 复制提示透明度)
        except tk.TclError:
            pass

        外框 = tk.Frame(浮层, bg=边框色, bd=0)
        外框.pack(padx=2, pady=2)
        内框 = tk.Frame(外框, bg=背景色)
        内框.pack(fill="both", expand=True)

        主标签 = tk.Label(
            内框, text=主文案, fg=主字色, bg=背景色,
            font=("微软雅黑", 15, "bold"), padx=24,
        )
        主标签.pack(anchor="w", pady=(14, 6))
        副标签 = tk.Label(
            内框, text=副文案, fg=副字色, bg=背景色,
            font=("微软雅黑", 11), padx=24,
            wraplength=460, justify="left",
        )
        副标签.pack(anchor="w", pady=(0, 14))

        # 单击浮层任意位置可立即关闭（不必等自动消失）
        self._绑定浮层单击关闭(浮层, 外框, 内框, 主标签, 副标签)

        self.root.update_idletasks()
        浮层.update_idletasks()
        浮层宽 = 浮层.winfo_reqwidth()
        浮层高 = 浮层.winfo_reqheight()
        根x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - 浮层宽) // 2)
        根y = self.root.winfo_rooty() + 复制提示距顶像素
        浮层.geometry(f"{浮层宽}x{浮层高}+{根x}+{根y}")
        浮层.deiconify()
        浮层.lift()

        self._复制提示框 = 浮层
        self._复制提示定时器 = self.root.after(复制提示显示毫秒, self._关闭复制提示)

    # ----------------------------------------------------------
    # 头像异步下载（基于 username MD5 做唯一标识和变更检测）
    # ----------------------------------------------------------

    @staticmethod
    def _计算md5(文本: str) -> str:
        """对任意字符串做 MD5，返回 32 位小写十六进制字符串。"""
        return hashlib.md5(文本.encode("utf-8")).hexdigest()

    def _异步下载头像(self, 行序号: int, url: str, username: str = ""):
        """
        下载头像的统一入口：
          - 本地磁盘文件名 = username 的 MD5
          - 内存缓存 key   = username 的 MD5
          - URL 变化时强制重新下载并覆盖本地文件
          - 内存已缓存且 URL 未变化，直接更新 Canvas item
          - 否则后台线程：优先读本地文件，否则从网络下载
        """
        if not username:
            return

        username_md5 = self._计算md5(username)
        url_变化 = self._username头像url.get(username) != url
        self._username头像url[username] = url

        if username_md5 in self._头像缓存 and not url_变化:
            self._更新canvas行头像(行序号, self._头像缓存[username_md5])
            return

        if url_变化:
            self._头像缓存.pop(username_md5, None)

        def 下载():
            try:
                from PIL import Image, ImageTk
                头像缓存目录.mkdir(parents=True, exist_ok=True)
                本地文件 = 头像缓存目录 / f"{username_md5}.png"

                if 本地文件.is_file() and not url_变化:
                    img = Image.open(本地文件).convert("RGBA")
                else:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        数据 = resp.read()
                    img = Image.open(io.BytesIO(数据)).convert("RGBA")
                    img = img.resize(头像尺寸, Image.LANCZOS)
                    img.save(本地文件, format="PNG")

                photo = ImageTk.PhotoImage(img)
                self.root.after(0, lambda: self._缓存并更新canvas头像(行序号, username_md5, photo))
            except Exception:
                pass

        threading.Thread(target=下载, daemon=True).start()

    def _缓存并更新canvas头像(self, 行序号: int, md5key: str, photo: tk.PhotoImage):
        """存入内存缓存并更新 Canvas 对应行的头像 item。"""
        self._头像缓存[md5key] = photo
        self._更新canvas行头像(行序号, photo)

    def _更新canvas行头像(self, 行序号: int, photo: tk.PhotoImage):
        """更新 Canvas 中指定行的头像图片。行可能已被清空，需捕获异常。"""
        try:
            if 行序号 < len(self._行头像items):
                self._canvas.itemconfig(self._行头像items[行序号], image=photo)
        except Exception:
            pass


# ============================================================
# 五、入口
# ============================================================

def 获取图标路径() -> Optional[str]:
    """
    查找图标文件。
    - 打包为 exe 后，PyInstaller 会把资源文件解压到 sys._MEIPASS 目录
    - 直接运行 .py 时，使用脚本同目录
    图标文件名固定为 icon.ico，找不到则返回 None（使用系统默认图标）。
    """
    import sys
    候选目录列表 = [
        Path(getattr(sys, "_MEIPASS", "")),   # PyInstaller 打包后的临时目录
        Path(__file__).resolve().parent,       # 脚本所在目录
    ]
    for 目录 in 候选目录列表:
        候选路径 = 目录 / "icon.ico"
        if 候选路径.is_file():
            return str(候选路径)
    return None


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    root.option_add("*Font", f"微软雅黑 {字号}")

    # 设置窗口图标（左上角 + 任务栏）
    图标路径 = 获取图标路径()
    if 图标路径:
        try:
            root.iconbitmap(图标路径)
        except Exception:
            pass  # 图标设置失败不影响程序运行

    # Scrollbar 保持系统默认样式即可，不需要 Treeview 专属样式

    搜索联系人界面(root)
    root.mainloop()


if __name__ == "__main__":
    main()
