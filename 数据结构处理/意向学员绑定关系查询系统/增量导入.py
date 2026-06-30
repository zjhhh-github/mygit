# -*- coding: utf-8 -*-
"""
增量导入一体化流水线
流程：原始Excel → 数据处理 → 查重清洗 → 导出CSV报告 → 调用增量导入API
"""

import csv
import json
import locale
import logging
import math
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

import pandas as pd
import requests
from tqdm import tqdm


# ─────────────────────────── 无控制台兼容 ────────────────────────
# PyInstaller --windowed 模式下 sys.stdout / sys.stderr 为 None，
# 任何向其写入的操作（print、logging.StreamHandler、tqdm 进度条）都会抛错。
# 此处统一重定向到 os.devnull，保证打包后脚本不会因此崩溃。
if sys.stdout is None or sys.stderr is None:
    import os
    _devnull = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = _devnull
    if sys.stderr is None:
        sys.stderr = _devnull


# ─────────────────────────── 集中配置 ───────────────────────────
CONFIG = {
    # Supabase 项目 URL（不含尾部斜杠）
    "SUPABASE_URL": "https://backend.appmiaoda.com/projects/supabase293970823448936448",
    # Supabase anon key
    "ANON_KEY": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoyMDg5NTE1MzA2LCJpc3MiOiJzdXBhYmFzZSIsInJvbGUiOiJhbm9uIiwic3ViIjoiYW5vbiJ9.Z19rhe7D6v4pXthoontMmG_C1U3yW6DTTSyFOKYvs54",
    # 登录邮箱 / 密码
    "EMAIL": "15648230994@miaoda.com",
    "PASSWORD": "028056hQ@",
    # 原始意向专用通讯录导出 Excel 所在目录（自动选取形如"意向专用通讯录导出*.xlsx"中文件名升序最后一个）
    "EXCEL_DIR": r"X:\backup\ProspectiveContacts",
    "EXCEL_PREFIX": "意向专用通讯录导出",
    "EXCEL_EXT": ".xlsx",
    # 报名备注 SQLite 数据库路径（支持分号/竖线分隔多路径）
    "CONTACT_DB_PATH": (
        r"C:\Users\LENOVO\Desktop\contact_内部专用.db;"
        r"C:\Users\LENOVO\Desktop\contact_内部专用2.db"
    ),
    # 或使用数组形式指定多个数据库（优先级高于 CONTACT_DB_PATH）
    "CONTACT_DB_PATHS": [
        r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
        r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
    ],
    # CSV 报告输出目录与日志目录（EXCEL_DIR\LOG\；见下方派生项）
    # 每批最多条数（与 Edge Function 侧 MAX_BATCH=200 对齐；超出会被截断计入 ignored）
    "BATCH_SIZE": 200,
    # 批次间隔（秒）
    "BATCH_INTERVAL": 1,
    # 单次请求超时（秒，Edge Function 处理 200 条约 <10s，给予宽裕 buffer）
    "REQUEST_TIMEOUT": 120,
    # 最大重试次数
    "MAX_RETRIES": 3,
    # 重试间隔（秒）
    "RETRY_INTERVAL": 2,
    # 导入前跳过"意向学员查询系统"中绑定状态=有绑定 或 报名状态=已报名 的学员
    "SKIP_ENROLLED_OR_BOUND": True,
    # 跳过匹配时忽略大小写（微信号 ID 在不同来源可能大小写不一致）
    "SKIP_CASE_INSENSITIVE": True,
    # 定向诊断：列出的微信号（大小写不敏感）会在拉取/过滤阶段打印详细日志，方便排查
    "DEBUG_WXIDS": [],
}
# ─────────────────────────── 本地可编辑配置 ──────────────────────
# 允许在 GUI 中覆盖的路径会被持久化到脚本/exe 同级目录下的 config.local.json，
# 启动时读取合并进 CONFIG，空值或缺失则回退到派生默认值。
def _app_dir() -> Path:
    """脚本或 PyInstaller 可执行文件所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


LOCAL_CONFIG_FILE = _app_dir() / "config.local.json"
LOCAL_CONFIG_KEYS = ("OUTPUT_DIR",)


def load_local_config() -> None:
    """从 LOCAL_CONFIG_FILE 读取白名单键，非空时覆盖 CONFIG。"""
    try:
        if not LOCAL_CONFIG_FILE.exists():
            return
        data = json.loads(LOCAL_CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        for key in LOCAL_CONFIG_KEYS:
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                CONFIG[key] = val.strip()
    except Exception:
        # 配置读取失败不应阻塞主流程，静默回退到默认值
        pass


def save_local_config() -> None:
    """将当前白名单键写入 LOCAL_CONFIG_FILE。"""
    data = {key: CONFIG.get(key, "") for key in LOCAL_CONFIG_KEYS}
    LOCAL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_CONFIG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


load_local_config()

# 派生路径：未显式配置 OUTPUT_DIR 时回退到 EXCEL_DIR\LOG；日志文件名每次运行带时间戳
if not CONFIG.get("OUTPUT_DIR"):
    CONFIG["OUTPUT_DIR"] = str(Path(CONFIG["EXCEL_DIR"]) / "LOG")
CONFIG["LOG_FILE"] = str(
    Path(CONFIG["OUTPUT_DIR"]) / f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
)


# ─────────────────────────── 日志 ───────────────────────────────
def setup_logger() -> logging.Logger:
    logger = logging.getLogger("incremental_import")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    Path(CONFIG["LOG_FILE"]).parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(CONFIG["LOG_FILE"], encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


logger = setup_logger()


def 安全打印(内容: object) -> None:
    文本 = str(内容)
    控制台编码 = locale.getpreferredencoding(False) or "utf-8"
    print(文本.encode(控制台编码, errors="replace").decode(控制台编码, errors="replace"))


# ─────────────────────────── Token 获取 ─────────────────────────
def get_token() -> str:
    """通过邮箱+密码登录 Supabase，返回 access_token。"""
    url = f"{CONFIG['SUPABASE_URL']}/auth/v1/token?grant_type=password"
    headers = {
        "apikey": CONFIG["ANON_KEY"],
        "Content-Type": "application/json",
    }
    payload = {"email": CONFIG["EMAIL"], "password": CONFIG["PASSWORD"]}

    logger.info("正在获取 Supabase Token ...")
    resp = requests.post(url, json=payload, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise ValueError("登录响应中未找到 access_token，请检查邮箱/密码或 anon key。")
    logger.info("Token 获取成功。")
    return token


# ─────────────────────────── 数据处理层 ─────────────────────────
def 提取首个非空值(行数据: "pd.Series", 字段列表: List[str]) -> str:
    """按字段优先级提取首个非空值，全部为空时返回空字符串。"""
    for 字段名 in 字段列表:
        值 = 行数据.get(字段名)
        if pd.isna(值):
            continue
        文本值 = str(值).strip()
        if 文本值:
            return 文本值
    return ""


def 格式化绑定日期(原始值: object) -> str:
    """将意向学员(添加时间)转换为 YYYY/MM/DD HH:MM:SS，无法解析则返回空字符串。"""
    if pd.isna(原始值):
        return ""
    时间戳 = pd.to_datetime(原始值, errors="coerce")
    if pd.isna(时间戳):
        return ""
    return 时间戳.strftime("%Y/%m/%d %H:%M:%S")


def 解析绑定周期天数(行数据: "pd.Series") -> int:
    """读取绑定周期字段（天），缺失或非法则默认 90 天。"""
    候选字段 = ["绑定周期", "意向学员(绑定周期)", "来源(绑定周期)"]
    原始周期 = 提取首个非空值(行数据, 候选字段)
    if not 原始周期:
        return 90
    try:
        周期天数 = int(float(原始周期))
    except ValueError:
        return 90
    return 周期天数 if 周期天数 >= 0 else 90


def 解析绑定日期_dt(text: str) -> Optional[datetime]:
    """
    兼容多格式日期字符串 → datetime。
    支持：YYYYMMDDHHMMSS / YYYYMMDD / YYYY-MM-DD HH:MM:SS / YYYY/MM/DD HH:MM:SS 等。
    """
    value = str(text).strip()
    if not value:
        return None
    for fmt in (
        "%Y%m%d%H%M%S",
        "%Y%m%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def 计算解绑日期(绑定日期: str, 绑定周期天数: int) -> str:
    """绑定日期 + 绑定周期天数 → YYYYMMDD，绑定日期无效时返回空字符串。"""
    绑定日期对象 = 解析绑定日期_dt(绑定日期)
    if 绑定日期对象 is None:
        return ""
    return (绑定日期对象 + timedelta(days=绑定周期天数)).strftime("%Y%m%d")


def 判定是否报名(行数据: "pd.Series", 报名备注集合: Set[str]) -> str:
    """意向学员(内部备注) 去空格后存在于集合中 → 已报名，否则 → 未报名。"""
    内部备注 = 行数据.get("意向学员(内部备注)")
    if pd.isna(内部备注):
        return "未报名"
    标准备注 = str(内部备注).strip()
    if not 标准备注:
        return "未报名"
    return "已报名" if 标准备注 in 报名备注集合 else "未报名"


def 统一计算绑定状态(学员映射: Dict[str, dict]) -> None:
    """
    同一学员多来源中，当前时间处于有效窗口内的来源里，
    仅绑定日期最早的一条标记为"有绑定"，其余保持"无绑定"。
    """
    当前时间 = datetime.now()
    for 学员数据 in 学员映射.values():
        来源列表 = 学员数据.get("来源", [])
        有效来源候选: List[Tuple[datetime, int]] = []
        for 索引, 来源项 in enumerate(来源列表):
            来源项["绑定状态"] = "无绑定"
            绑定日期对象 = 解析绑定日期_dt(str(来源项.get("绑定日期", "")).strip())
            if 绑定日期对象 is None:
                continue
            解绑日期文本 = str(来源项.get("解绑日期", "")).strip()
            try:
                解绑日期对象 = datetime.strptime(解绑日期文本, "%Y%m%d")
            except ValueError:
                continue
            if 绑定日期对象 <= 当前时间 <= 解绑日期对象:
                有效来源候选.append((绑定日期对象, 索引))
        if 有效来源候选:
            有效来源候选.sort(key=lambda 项: (项[0], 项[1]))
            来源列表[有效来源候选[0][1]]["绑定状态"] = "有绑定"


def 解析最新Excel路径(目录: str, 前缀: str, 扩展名: str) -> str:
    """
    扫描目录下形如 "{前缀}*{扩展名}" 的文件，按文件名升序返回最后一个完整路径。
    - 不递归子目录
    - 不区分大小写匹配扩展名
    - 自动忽略以 "~$" 开头的 Excel 临时锁文件
    - 目录不存在或无匹配时抛 FileNotFoundError
    """
    目录路径 = Path(目录)
    if not 目录路径.is_dir():
        raise FileNotFoundError(f"Excel 目录不存在：{目录}")
    候选 = [
        p for p in 目录路径.iterdir()
        if p.is_file()
        and p.name.startswith(前缀)
        and not p.name.startswith("~$")
        and p.suffix.lower() == 扩展名.lower()
    ]
    if not 候选:
        raise FileNotFoundError(
            f'目录 {目录} 下未找到以 "{前缀}" 开头的 {扩展名} 文件'
        )
    候选.sort(key=lambda p: p.name)
    选中 = 候选[-1]
    logger.info(
        f"扫描到 {len(候选)} 个候选 Excel，自动选择（文件名最大）：{选中.name}"
    )
    return str(选中)


def 读取售前通讯录数据(excel_path: str) -> "pd.DataFrame":
    """读取原始售前通讯录 Excel，返回 DataFrame。"""
    路径 = Path(excel_path)
    if not 路径.exists():
        raise FileNotFoundError(f"未找到 Excel 文件：{excel_path}")
    logger.info(f"读取原始 Excel：{excel_path}")
    df = pd.read_excel(路径, sheet_name=0)
    logger.info(f"读取完成：{df.shape[0]} 行，{df.shape[1]} 列。")
    return df


def _normalize_contact_db_paths(db_path: Union[str, List[str]]) -> List[str]:
    """把单路径、分号/竖线分隔串或路径列表统一成去重后的路径列表。"""
    if isinstance(db_path, list):
        raw_parts = db_path
    else:
        raw_parts = re.split(r"[;|]", str(db_path or ""))
    paths: List[str] = []
    seen: Set[str] = set()
    for part in raw_parts:
        p = str(part or "").strip()
        if not p:
            continue
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            continue
        seen.add(key)
        paths.append(p)
    return paths


def 解析通讯录数据库路径() -> List[str]:
    """从 CONFIG 解析一个或多个通讯录数据库路径。"""
    paths = CONFIG.get("CONTACT_DB_PATHS")
    if paths:
        return _normalize_contact_db_paths(paths)
    return _normalize_contact_db_paths(CONFIG.get("CONTACT_DB_PATH", ""))


def _读取单库报名备注集合(db_path: str) -> Set[str]:
    """从单个 SQLite contact.remark 构建报名备注集合。"""
    路径 = Path(db_path)
    if not 路径.exists():
        raise FileNotFoundError(f"未找到数据库文件：{db_path}")
    logger.info(f"读取报名备注集合：{db_path}")
    备注集合: Set[str] = set()
    连接 = sqlite3.connect(str(路径))
    try:
        游标 = 连接.cursor()
        try:
            游标.execute("SELECT remark FROM contact")
            for (备注值,) in 游标.fetchall():
                if 备注值 is None:
                    continue
                标准备注 = str(备注值).strip()
                if 标准备注:
                    备注集合.add(标准备注)
        finally:
            游标.close()
    finally:
        连接.close()
    logger.info(f"  本库备注 {len(备注集合)} 条")
    return 备注集合


def 读取报名备注集合(db_path: Union[str, List[str]]) -> Set[str]:
    """从一个或多个 SQLite contact.remark 构建报名备注集合并去重。"""
    paths = _normalize_contact_db_paths(db_path)
    if not paths:
        raise ValueError("未提供有效的通讯录数据库路径")
    备注集合: Set[str] = set()
    for path in paths:
        备注集合 |= _读取单库报名备注集合(path)
    logger.info(
        f"报名备注集合大小：{len(备注集合)}（合并 {len(paths)} 个数据库）"
    )
    return 备注集合


# ─────────────────────────── 微信原始ID 兜底映射 ─────────────────────────
# 增量导入新增"微信原始ID"长期维护字段：当 Excel 未提供该字段时，
# 用 contact.db 中的 (alias / username) → username 关系做回填：
#   - contact.username 即微信原始ID（wxid_xxx 格式，登录态下不可改）
#   - contact.alias    即用户可见的"微信号"（可改），未设置时为空
# 因此本项目里的"总微信号"实际取值规则为 alias if alias else username。
# 我们把 总微信号(小写) 与 username(小写) 都作为映射键，两条路都可命中，
# 全部统一返回 contact.username 作为该联系人对应的"微信原始ID"。
def _读取单库微信原始ID映射(db_path: str) -> Dict[str, str]:
    """从单个 contact.db 构建 总微信号(小写) → 微信原始ID 的回填映射。"""
    映射: Dict[str, str] = {}
    路径 = Path(db_path)
    if not 路径.exists():
        logger.warning(
            f"读取微信原始ID映射失败：未找到数据库 {db_path}，跳过本库"
        )
        return 映射

    logger.info(f"读取微信原始ID映射：{db_path}")
    连接 = sqlite3.connect(str(路径))
    try:
        游标 = 连接.cursor()
        try:
            游标.execute("SELECT username, alias FROM contact")
            for username, alias in 游标.fetchall():
                username_v = (username or "").strip()
                alias_v = (alias or "").strip() if alias is not None else ""
                if not username_v:
                    continue
                total = alias_v if alias_v else username_v
                key_total = total.lower()
                if key_total and key_total not in 映射:
                    映射[key_total] = username_v
                key_user = username_v.lower()
                if key_user and key_user not in 映射:
                    映射[key_user] = username_v
        finally:
            游标.close()
    finally:
        连接.close()
    logger.info(f"  本库映射 {len(映射)} 条键")
    return 映射


def 读取微信原始ID映射(db_path: Union[str, List[str]]) -> Dict[str, str]:
    """从一个或多个 contact.db 构建 总微信号(小写) → 微信原始ID 的回填映射。

    使用场景：意向学员或来源在 Excel 中没有"微信原始ID"，
    但能匹配到 contact.db 中的某个联系人时，用 contact.username 回填。

    返回：dict，键为小写后的"总微信号"或"微信原始ID"，值为原始大小写的 username。
    若所有数据库均不存在或读取失败，返回空 dict（不抛异常，不阻塞主流程）。
    """
    paths = _normalize_contact_db_paths(db_path)
    if not paths:
        logger.warning("未提供有效的通讯录数据库路径，将跳过 contact 回填")
        return {}

    映射: Dict[str, str] = {}
    found = 0
    for path in paths:
        单库映射 = _读取单库微信原始ID映射(path)
        if not 单库映射:
            continue
        found += 1
        for key, value in 单库映射.items():
            if key not in 映射:
                映射[key] = value

    if found == 0:
        logger.warning("所有通讯录数据库均未读取到映射，将跳过 contact 回填")
    else:
        logger.info(
            f"微信原始ID映射构建完成：{len(映射)} 条键（合并 {found} 个数据库）"
        )
    return 映射


def 回填微信原始ID(总微信号: str, 微信原始ID映射: Dict[str, str]) -> str:
    """按"总微信号"在 contact 映射中查找对应的微信原始ID；查不到返回空串。"""
    if not 总微信号 or not 微信原始ID映射:
        return ""
    return 微信原始ID映射.get(总微信号.strip().lower(), "")


def 转换为目标结构(
    数据表: "pd.DataFrame",
    报名备注集合: Set[str],
    微信原始ID映射: Optional[Dict[str, str]] = None,
) -> List[dict]:
    """
    原始表格 → 学员+来源列表结构，同时计算绑定状态。
    空学员微信号行不丢弃，使用"未知学员_行号"保底。

    新增字段：
        - 意向学员微信原始ID：优先取 Excel 列"意向学员(微信原始ID)"，
          缺失时用"意向学员总微信号"在 contact.db 映射中回填，仍找不到留空。
        - 来源微信原始ID    ：优先取 Excel 列"来源(微信原始ID)"，
          缺失时用"来源总微信号"在 contact.db 映射中回填，仍找不到留空。
    若同一意向学员多次出现，按"原值为空时才补值"的规则做补更新，
    避免新批次的空值覆盖已有原始ID。
    """
    学员映射: Dict[str, dict] = {}
    映射 = 微信原始ID映射 or {}

    for 行号, 行数据 in 数据表.iterrows():
        意向学员微信号 = 提取首个非空值(
            行数据, ["意向学员(总微信号)", "意向学员(微信号)", "意向学员(微信ID)"]
        )
        if not 意向学员微信号:
            意向学员微信号 = f"未知学员_{行号 + 1}"

        # 先取 Excel 列；若列不存在或为空，再用 contact 映射按"总微信号"回填
        意向学员微信原始ID = 提取首个非空值(行数据, ["意向学员(微信原始ID)"])
        if not 意向学员微信原始ID:
            意向学员微信原始ID = 回填微信原始ID(意向学员微信号, 映射)

        来源微信号 = 提取首个非空值(
            行数据, ["来源(总微信号)", "来源(微信号)", "来源(微信ID)"]
        )
        来源微信原始ID = 提取首个非空值(行数据, ["来源(微信原始ID)"])
        if not 来源微信原始ID:
            来源微信原始ID = 回填微信原始ID(来源微信号, 映射)

        绑定日期 = 格式化绑定日期(行数据.get("意向学员(添加时间)"))
        绑定周期天数 = 解析绑定周期天数(行数据)
        解绑日期 = 计算解绑日期(绑定日期, 绑定周期天数)
        是否报名 = 判定是否报名(行数据, 报名备注集合)

        来源项 = {
            "来源微信号": 来源微信号,
            "来源微信原始ID": 来源微信原始ID,
            "绑定日期": 绑定日期,
            "绑定周期": 绑定周期天数,
            "解绑日期": 解绑日期,
            "绑定状态": "无绑定",
        }

        if 意向学员微信号 not in 学员映射:
            学员映射[意向学员微信号] = {
                "意向学员微信号": 意向学员微信号,
                "意向学员微信原始ID": 意向学员微信原始ID,
                "是否报名": 是否报名,
                "来源": [],
            }
        else:
            existing = 学员映射[意向学员微信号]
            existing["是否报名"] = 是否报名
            # 只在原值为空时补更新，避免新批次的空值覆盖已有的 ID
            if not existing.get("意向学员微信原始ID") and 意向学员微信原始ID:
                existing["意向学员微信原始ID"] = 意向学员微信原始ID

        学员映射[意向学员微信号]["来源"].append(来源项)

    统一计算绑定状态(学员映射)
    records = list(学员映射.values())
    logger.info(f"数据转换完成：共 {len(records)} 条意向学员记录。")
    return records


# ─────────────────────────── 查重清洗层 ─────────────────────────
def apply_same_day_earliest_dedup(records: List[dict]) -> Tuple[List[dict], List[dict]]:
    """
    同日最早去重规则（仅作用于"已报名"学员）：
    - 以 (意向学员微信号, 绑定日期的日期部分) 分组；
    - 同组内按时间升序排序，仅保留最早一条来源；
    - 其余来源从 records 中移除，并写入返回的 removed_rows 明细。
    无法解析日期的来源不参与分组，原样保留。
    必须在 apply_dedup_and_clean 之前调用（那时绑定日期仍带时间）。
    """
    removed_rows: List[dict] = []

    for rec in records:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("是否报名", "")).strip() != "已报名":
            continue
        sources = rec.get("来源", [])
        if not isinstance(sources, list) or len(sources) < 2:
            continue

        groups: Dict[object, List[Tuple[datetime, int, dict]]] = defaultdict(list)
        for idx, src in enumerate(sources):
            if not isinstance(src, dict):
                continue
            dt = 解析绑定日期_dt(str(src.get("绑定日期", "")).strip())
            if dt is None:
                continue
            groups[dt.date()].append((dt, idx, src))

        drop_indices: Set[int] = set()
        intent_wx = str(rec.get("意向学员微信号", "")).strip()
        for _date_key, items in groups.items():
            if len(items) < 2:
                continue
            items.sort(key=lambda x: (x[0], x[1]))
            _kept_dt, _kept_idx, kept_src = items[0]
            for _later_dt, later_idx, later_src in items[1:]:
                drop_indices.add(later_idx)
                removed_rows.append(
                    {
                        "意向学员微信号": intent_wx,
                        "删除来源微信号": str(later_src.get("来源微信号", "")).strip(),
                        "删除绑定日期": str(later_src.get("绑定日期", "")).strip(),
                        "保留来源微信号": str(kept_src.get("来源微信号", "")).strip(),
                        "保留绑定日期": str(kept_src.get("绑定日期", "")).strip(),
                        "规则": "同意向学员+已报名+同日保留最早",
                    }
                )

        if drop_indices:
            rec["来源"] = [s for i, s in enumerate(sources) if i not in drop_indices]

    logger.info(f"同日最早去重完成：删除 {len(removed_rows)} 条（仅已报名学员）。")
    return records, removed_rows


def 格式化绑定日期为8位(text: str) -> str:
    """将绑定日期统一格式化为 YYYYMMDD；不可解析时保持原值。"""
    dt = 解析绑定日期_dt(text)
    if dt is None:
        return str(text).strip()
    return dt.strftime("%Y%m%d")


def apply_dedup_and_clean(records: List[dict]) -> Tuple[List[dict], List[dict]]:
    """
    执行查重清洗：
    1. 对"已报名"学员，删除绑定时间相差约 8 小时（±1分钟）的较晚来源；
    2. 将所有来源的绑定日期统一格式化为 YYYYMMDD。
    修改在原列表上原地进行，返回 (records, removed_rows)。
    """
    target_delta = timedelta(hours=8)
    tolerance = timedelta(minutes=1)
    min_delta = target_delta - tolerance
    max_delta = target_delta + tolerance

    # 构建索引：仅已报名学员
    source_refs_by_intent: Dict[str, list] = defaultdict(list)
    for rec_idx, item in enumerate(records):
        if not isinstance(item, dict):
            continue
        if str(item.get("是否报名", "")).strip() != "已报名":
            continue
        intent_wx = str(item.get("意向学员微信号", "")).strip()
        if not intent_wx:
            continue
        for src_idx, source_item in enumerate(item.get("来源", [])):
            if not isinstance(source_item, dict):
                continue
            bind_dt = 解析绑定日期_dt(str(source_item.get("绑定日期", "")).strip())
            if bind_dt is None:
                continue
            source_refs_by_intent[intent_wx].append(
                {
                    "rec_idx": rec_idx,
                    "src_idx": src_idx,
                    "source_wx": str(source_item.get("来源微信号", "")).strip(),
                    "bind_raw": str(source_item.get("绑定日期", "")).strip(),
                    "bind_dt": bind_dt,
                }
            )

    to_remove_keys: set = set()
    removed_rows: List[dict] = []

    for intent_wx, refs in source_refs_by_intent.items():
        if len(refs) < 2:
            continue
        refs_sorted = sorted(refs, key=lambda x: x["bind_dt"])
        for i in range(len(refs_sorted)):
            later = refs_sorted[i]
            for j in range(i):
                earlier = refs_sorted[j]
                delta = later["bind_dt"] - earlier["bind_dt"]
                if min_delta <= delta <= max_delta:
                    key = (later["rec_idx"], later["src_idx"])
                    if key not in to_remove_keys:
                        to_remove_keys.add(key)
                        removed_rows.append(
                            {
                                "意向学员微信号": intent_wx,
                                "删除来源微信号": later["source_wx"],
                                "删除绑定日期": later["bind_raw"],
                                "参考来源微信号": earlier["source_wx"],
                                "参考绑定日期": earlier["bind_raw"],
                                "规则": "同意向学员+已报名+晚8小时(±1分钟)删除",
                            }
                        )
                    break

    # 原地删除标记来源
    for rec_idx, item in enumerate(records):
        if not isinstance(item, dict):
            continue
        sources = item.get("来源", [])
        if not isinstance(sources, list):
            continue
        item["来源"] = [
            src for src_idx, src in enumerate(sources)
            if (rec_idx, src_idx) not in to_remove_keys
        ]

    # 绑定日期统一为 YYYYMMDD
    normalized_count = 0
    for item in records:
        if not isinstance(item, dict):
            continue
        for source_item in item.get("来源", []):
            if not isinstance(source_item, dict):
                continue
            old = str(source_item.get("绑定日期", "")).strip()
            new = 格式化绑定日期为8位(old)
            if new != old:
                normalized_count += 1
            source_item["绑定日期"] = new

    logger.info(
        f'查重清洗完成：删除"晚8小时"来源 {len(removed_rows)} 条，'
        f"绑定日期格式化 {normalized_count} 条。"
    )

    return records, removed_rows


def build_csv_reports(
    records: List[dict],
    removed_rows: List[dict],
    same_day_removed_rows: Optional[List[dict]] = None,
) -> dict:
    """
    遍历清洗后的 records，生成 5 类统计数据：
    - pair_rows: 意向学员_来源微信号明细
    - duplicate_source_rows: 来源微信号重复统计
    - duplicate_triple_rows: 三字段重复统计
    - removed_rows: 晚8小时删除明细（由 apply_dedup_and_clean 返回）
    - same_day_removed_rows: 同日非最早删除明细（由 apply_same_day_earliest_dedup 返回）
    """
    if same_day_removed_rows is None:
        same_day_removed_rows = []
    pair_rows: List[dict] = []
    source_counter: Counter = Counter()
    source_to_intent_set: Dict[str, set] = defaultdict(set)
    triple_counter: Counter = Counter()

    for item in records:
        if not isinstance(item, dict):
            continue
        intent_wx = str(item.get("意向学员微信号", "")).strip()
        if not intent_wx:
            continue
        signup_status = str(item.get("是否报名", "")).strip()
        intent_orig = str(item.get("意向学员微信原始ID", "")).strip()

        for source_item in item.get("来源", []):
            if not isinstance(source_item, dict):
                continue
            source_wx = str(source_item.get("来源微信号", "")).strip()
            bind_date = str(source_item.get("绑定日期", "")).strip()
            source_orig = str(source_item.get("来源微信原始ID", "")).strip()

            triple_counter[(intent_wx, signup_status, bind_date)] += 1

            if not source_wx:
                continue

            pair_rows.append(
                {
                    "意向学员微信号": intent_wx,
                    "意向学员微信原始ID": intent_orig,
                    "来源微信号": source_wx,
                    "来源微信原始ID": source_orig,
                    "是否报名": signup_status,
                    "绑定日期": bind_date,
                    "解绑日期": str(source_item.get("解绑日期", "")).strip(),
                    "绑定状态": str(source_item.get("绑定状态", "")).strip(),
                }
            )
            source_counter[source_wx] += 1
            source_to_intent_set[source_wx].add(intent_wx)

    duplicate_source_rows = sorted(
        [
            {
                "来源微信号": wx,
                "出现次数": cnt,
                "关联意向学员数量": len(source_to_intent_set[wx]),
                "关联意向学员微信号列表": " | ".join(sorted(source_to_intent_set[wx])),
            }
            for wx, cnt in source_counter.items()
            if cnt > 1
        ],
        key=lambda x: x["出现次数"],
        reverse=True,
    )

    duplicate_triple_rows = sorted(
        [
            {
                "意向学员微信号": iw,
                "是否报名": ss,
                "绑定日期": bd,
                "出现次数": cnt,
            }
            for (iw, ss, bd), cnt in triple_counter.items()
            if cnt > 1
        ],
        key=lambda x: x["出现次数"],
        reverse=True,
    )

    logger.info(
        f"报告统计：明细 {len(pair_rows)} 条，"
        f"重复来源 {len(duplicate_source_rows)} 种，"
        f"三字段重复 {len(duplicate_triple_rows)} 种，"
        f"晚8小时删除 {len(removed_rows)} 条，"
        f"同日非最早删除 {len(same_day_removed_rows)} 条。"
    )

    return {
        "pair_rows": pair_rows,
        "duplicate_source_rows": duplicate_source_rows,
        "duplicate_triple_rows": duplicate_triple_rows,
        "removed_rows": removed_rows,
        "same_day_removed_rows": same_day_removed_rows,
    }


def _获取可写路径(path: Path) -> Path:
    """若目标文件被占用，自动追加时间戳后缀。"""
    try:
        with path.open("a", encoding="utf-8"):
            pass
        return path
    except PermissionError:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return path.with_name(f"{path.stem}_{ts}{path.suffix}")


def export_csv_reports(reports: dict, output_dir: str) -> None:
    """将 4 类统计数据写入 CSV 文件，输出目录自动创建。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    files = {
        "意向学员_来源微信号明细.csv": (
            reports["pair_rows"],
            [
                "意向学员微信号",
                "意向学员微信原始ID",
                "来源微信号",
                "来源微信原始ID",
                "是否报名",
                "绑定日期",
                "解绑日期",
                "绑定状态",
            ],
        ),
        "来源微信号重复统计.csv": (
            reports["duplicate_source_rows"],
            ["来源微信号", "出现次数", "关联意向学员数量", "关联意向学员微信号列表"],
        ),
        "三字段重复统计.csv": (
            reports["duplicate_triple_rows"],
            ["意向学员微信号", "是否报名", "绑定日期", "出现次数"],
        ),
        "删除_晚8小时_明细.csv": (
            reports["removed_rows"],
            ["意向学员微信号", "删除来源微信号", "删除绑定日期", "参考来源微信号", "参考绑定日期", "规则"],
        ),
        "删除_同日非最早_明细.csv": (
            reports.get("same_day_removed_rows", []),
            ["意向学员微信号", "删除来源微信号", "删除绑定日期", "保留来源微信号", "保留绑定日期", "规则"],
        ),
    }

    for filename, (rows, fieldnames) in files.items():
        target = _获取可写路径(out / filename)
        with target.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        logger.info(f"已导出：{target}（{len(rows)} 行）")


# ─────────────────────────── API 对接层 ─────────────────────────
def build_api_payload(records: List[dict]) -> List[dict]:
    """
    清洗后的 records → API 所需 JSON。
    每条来源保留 来源微信号 / 来源微信原始ID / 绑定日期，过滤空来源微信号。

    新增字段：
        - 顶层 意向学员微信原始ID：用于服务端新增/补更新该意向学员的原始 wxid；
        - 每条来源 来源微信原始ID：用于服务端新增/补更新该来源的原始 wxid；
    若值为空字符串，则表示本次没有提供该字段，服务端按"原值为空时才补"
    的规则处理（不会用空值覆盖已有原始ID）。
    """
    payload: List[dict] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        intent_wx = str(item.get("意向学员微信号", "")).strip()
        if not intent_wx or intent_wx.startswith("未知学员_"):
            continue
        sources = [
            {
                "来源微信号": str(s.get("来源微信号", "")).strip(),
                "来源微信原始ID": str(s.get("来源微信原始ID", "")).strip(),
                "绑定日期": str(s.get("绑定日期", "")).strip(),
            }
            for s in item.get("来源", [])
            if isinstance(s, dict) and str(s.get("来源微信号", "")).strip()
        ]
        if not sources:
            continue
        payload.append(
            {
                "意向学员微信号": intent_wx,
                "意向学员微信原始ID": str(item.get("意向学员微信原始ID", "")).strip(),
                "是否报名": str(item.get("是否报名", "")).strip(),
                "来源": sources,
            }
        )
    logger.info(f"API Payload 构建完成：{len(payload)} 条有效学员记录。")
    return payload


def dedup_payload(payload: List[dict]) -> List[dict]:
    """
    对 payload 按 (意向学员微信号, 来源微信号, 绑定日期) 三元组去重：
    - 同一三元组只保留首次出现的来源条目；
    - 学员下所有来源均被去重后，整条学员记录丢弃；
    - 不修改其他字段，原地更新 item["来源"]。

    微信原始ID 去重规则：
    - 顶层 意向学员微信原始ID：保留首次出现的非空值；后续同学员若提供
      非空值，可补回填到首次条目（保证回填能力，又不会被空值覆盖）；
    - 来源微信原始ID 同理：保留首次出现的非空值，后续可补回填。
    """
    seen: Dict[tuple, dict] = {}            # 三元组 → 已保留的 src 引用
    student_kept: Dict[str, dict] = {}      # 学员微信号 → 已保留的 item 引用
    filtered: List[dict] = []
    total_src_before = 0
    total_src_after = 0

    for item in payload:
        if not isinstance(item, dict):
            continue
        student = str(item.get("意向学员微信号", "")).strip()
        intent_orig = str(item.get("意向学员微信原始ID", "")).strip()
        new_sources = []
        for src in item.get("来源", []):
            if not isinstance(src, dict):
                continue
            total_src_before += 1
            source = str(src.get("来源微信号", "")).strip()
            date = str(src.get("绑定日期", "")).strip()
            src_orig = str(src.get("来源微信原始ID", "")).strip()
            key = (student, source, date)
            if key in seen:
                # 已存在：尝试用本次非空值补回填首次条目的"来源微信原始ID"
                kept = seen[key]
                if not kept.get("来源微信原始ID") and src_orig:
                    kept["来源微信原始ID"] = src_orig
                continue
            seen[key] = src
            new_sources.append(src)
        if new_sources:
            item["来源"] = new_sources
            # 同学员多次出现时，把后续的非空意向学员微信原始ID 回填到首次条目
            if student in student_kept:
                kept_item = student_kept[student]
                if not kept_item.get("意向学员微信原始ID") and intent_orig:
                    kept_item["意向学员微信原始ID"] = intent_orig
                # 来源仍按首次条目保留，不重复追加
            else:
                student_kept[student] = item
                filtered.append(item)
            total_src_after += len(new_sources)

    logger.info(
        f"Payload 去重完成：学员 {len(payload)} → {len(filtered)}，"
        f"来源 {total_src_before} → {total_src_after}，"
        f"去重 {total_src_before - total_src_after} 条。"
    )
    return filtered


def fetch_skip_wxids(token: str) -> Tuple[Set[str], Set[str]]:
    """拉取应跳过的意向学员微信号集合：已报名 ∪ 学员侧有绑定 ∪ 来源侧有绑定。

    返回 (skip_raw, skip_lc)：
    - skip_raw：原始大小写，仅用于日志展示；
    - skip_lc ：lowercase + strip 后的集合，用于真正的匹配比较。

    使用 requests 的 params 自动进行 URL 编码，避免中文筛选值未编码导致
    PostgREST 解析失败而返回空列表的坑。
    """
    debug_targets_lc: Set[str] = {
        str(x).strip().lower()
        for x in (CONFIG.get("DEBUG_WXIDS") or [])
        if str(x).strip()
    }
    base = f"{CONFIG['SUPABASE_URL']}/rest/v1"
    headers = {
        "apikey": CONFIG["ANON_KEY"],
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    def _paged_get(path: str, params: dict) -> list:
        page_size = 1000
        offset = 0
        all_items: list = []
        first_call = True
        while True:
            q = dict(params)
            q["limit"] = page_size
            q["offset"] = offset
            resp = requests.get(
                f"{base}{path}", params=q, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"]
            )
            if first_call:
                logger.info(
                    f"[跳过清单] GET {path} 首请求 → HTTP {resp.status_code}，"
                    f"URL={resp.url}"
                )
                first_call = False
            if resp.status_code not in (200, 206):
                raise RuntimeError(
                    f"GET {path} 失败 HTTP {resp.status_code}: {(resp.text or '')[:200]}"
                )
            try:
                batch = resp.json() or []
            except Exception:
                batch = []
            if not isinstance(batch, list):
                logger.warning(
                    f"[跳过清单] {path} 返回非数组：{str(batch)[:200]}"
                )
                break
            if not batch:
                break
            all_items.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return all_items

    # 一次性拉全 students（id / wechat_id / enrollment_status），本地做 join；
    # 避免 id=in.(...) 长 URL 被上游网关拒绝（曾触发 502 Bad Gateway）。
    students_rows = _paged_get(
        "/students",
        {"select": "id,wechat_id,enrollment_status"},
    )
    id_to_wxid: dict = {}
    enrolled: Set[str] = set()
    for r in students_rows:
        sid = r.get("id")
        wx = (r.get("wechat_id") or "").strip()
        if sid and wx:
            id_to_wxid[sid] = wx
        if wx and (r.get("enrollment_status") or "").strip() == "已报名":
            enrolled.add(wx)
    logger.info(
        f"[跳过清单] students 全表拉取 {len(students_rows)} 行，"
        f"可用 id→wxid {len(id_to_wxid)}，已报名 {len(enrolled)}"
    )

    # 仅按"学员侧有绑定"过滤：拉回 student_id，本地 join 学员 wechat_id。
    # 来源侧（source_wechat_id）不纳入跳过集合，避免把"仅作为介绍人活跃"的
    # 微信号误判为不应作为新意向学员导入。
    bound_rows = _paged_get(
        "/sources_with_status",
        {"select": "student_id", "bind_status": "eq.有绑定"},
    )
    sid_set: Set[str] = {r.get("student_id") for r in bound_rows if r.get("student_id")}
    logger.info(
        f"[跳过清单] sources_with_status 有绑定 拉取 {len(bound_rows)} 行，"
        f"学员侧 student_id {len(sid_set)}"
    )

    student_side_wxids: Set[str] = set()
    missing_sid = 0
    for sid in sid_set:
        wx = id_to_wxid.get(sid)
        if wx:
            student_side_wxids.add(wx)
        else:
            missing_sid += 1
    if missing_sid:
        logger.info(
            f"[跳过清单] 有 {missing_sid} 个 student_id 在 students 表未命中（可能已删除），已忽略"
        )

    skip = enrolled | student_side_wxids
    skip_lc: Set[str] = {s.lower() for s in skip}
    logger.info(
        f"[跳过清单] 汇总：已报名 {len(enrolled)} | "
        f"学员侧有绑定 {len(student_side_wxids)} | "
        f"合并去重后 {len(skip)}（大小写归一后 {len(skip_lc)}）"
    )

    # 定向诊断：逐个展示 debug 目标在 skip 集合里的命中情况
    if debug_targets_lc:
        for t in debug_targets_lc:
            hits_enrolled = [w for w in enrolled if w.lower() == t]
            hits_student = [w for w in student_side_wxids if w.lower() == t]
            students_hit = [
                (r.get("wechat_id"), r.get("enrollment_status"), r.get("id"))
                for r in students_rows
                if (r.get("wechat_id") or "").strip().lower() == t
            ]
            logger.info(
                f"[诊断] 目标 {t!r}："
                f"已报名命中={hits_enrolled}，学员侧有绑定命中={hits_student}，"
                f"students 表命中={students_hit}"
            )
            if not students_hit:
                logger.info(
                    f"[诊断] 目标 {t!r} 在 students 表中未找到（可能：学员尚未录入，"
                    f"或 wechat_id 含不可见字符；注意本脚本已按大小写不敏感匹配）"
                )
    return skip, skip_lc


def filter_skip_enrolled_or_bound(payload: list, token: str) -> list:
    """根据远端 students/sources_with_status 过滤 payload，默认按 CONFIG 开关判定。"""
    if not CONFIG.get("SKIP_ENROLLED_OR_BOUND", True):
        logger.info("[跳过清单] 开关已关闭，不执行过滤。")
        return payload
    if not payload:
        return payload
    try:
        skip, skip_lc = fetch_skip_wxids(token)
    except Exception as e:
        logger.warning(f"[跳过清单] 拉取远端状态失败，保守起见本次不过滤：{e}")
        return payload
    if not skip:
        logger.info("[跳过清单] 远端暂无已报名/有绑定学员，不过滤。")
        return payload

    case_insensitive = bool(CONFIG.get("SKIP_CASE_INSENSITIVE", True))
    debug_targets_lc: Set[str] = {
        str(x).strip().lower()
        for x in (CONFIG.get("DEBUG_WXIDS") or [])
        if str(x).strip()
    }

    before = len(payload)
    kept = []
    removed_wxids: list = []
    for p in payload:
        wx = (p.get("意向学员微信号") or "").strip()
        wx_lc = wx.lower()
        hit = bool(wx) and (
            (wx in skip) or (case_insensitive and wx_lc in skip_lc)
        )
        if wx_lc and wx_lc in debug_targets_lc:
            logger.info(
                f"[诊断] payload 中目标 {wx!r}："
                f"严格命中={wx in skip}，大小写归一命中={wx_lc in skip_lc}，"
                f"case_insensitive={case_insensitive} → {'跳过' if hit else '保留'}"
            )
        if hit:
            removed_wxids.append(wx)
        else:
            kept.append(p)
    removed = before - len(kept)
    logger.info(
        f"[跳过清单] 过滤完成：原 {before} → 剩 {len(kept)}，跳过 {removed} 条"
    )
    if removed:
        preview = ", ".join(removed_wxids[:10])
        more = f"（仅展示前 10 个，共 {removed} 个）" if removed > 10 else ""
        logger.info(f"[跳过清单] 被跳过的微信号示例：{preview}{more}")
    return kept


def call_import_api(batch: list, token: str, batch_index: int, total_batches: int) -> dict:
    """调用增量导入 API，发送单批数据，最多重试 MAX_RETRIES 次。"""
    url = f"{CONFIG['SUPABASE_URL']}/functions/v1/incremental-import"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    DUP_KEY_FLAG = "duplicate key value"

    last_error = None
    for attempt in range(1, CONFIG["MAX_RETRIES"] + 1):
        try:
            t0 = time.time()
            resp = requests.post(
                url, json=batch, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"]
            )
            elapsed = time.time() - t0

            body_text = resp.text or ""
            if DUP_KEY_FLAG in body_text:
                logger.warning(
                    f"[批次 {batch_index}/{total_batches}] 检测到 duplicate key，跳过该批次不重试。"
                    f" 响应片段：{body_text[:200]}"
                )
                return {
                    "error": "duplicate_key_skip",
                    "detail": body_text[:300],
                    "_elapsed": elapsed,
                }

            if resp.status_code >= 500:
                raise requests.HTTPError(
                    f"服务端错误 {resp.status_code}: {body_text[:200]}", response=resp
                )

            result: dict = {}
            try:
                result = resp.json()
            except Exception:
                result = {"raw": body_text}

            logger.info(
                f"[批次 {batch_index}/{total_batches}] "
                f"total={result.get('total', len(batch))} "
                f"added={result.get('added', '-')} "
                f"ignored={result.get('ignored', '-')} "
                f"errors={result.get('errors', '-')} "
                f"耗时: {elapsed:.2f}s"
            )

            if resp.status_code >= 400:
                logger.warning(
                    f"[批次 {batch_index}] HTTP {resp.status_code}，响应：{body_text[:300]}"
                )

            result["_elapsed"] = elapsed
            return result

        except (requests.exceptions.RequestException, requests.HTTPError) as e:
            if DUP_KEY_FLAG in str(e):
                logger.warning(
                    f"[批次 {batch_index}/{total_batches}] 异常含 duplicate key，跳过不重试：{e}"
                )
                return {"error": "duplicate_key_skip", "detail": str(e)[:300]}
            last_error = e
            logger.warning(
                f"[批次 {batch_index}] 第 {attempt} 次请求失败：{e}，"
                f"{'将重试...' if attempt < CONFIG['MAX_RETRIES'] else '已达最大重试次数，跳过。'}"
            )
            if attempt < CONFIG["MAX_RETRIES"]:
                time.sleep(CONFIG["RETRY_INTERVAL"])

    logger.error(f"[批次 {batch_index}] 最终失败，错误：{last_error}")
    return {"error": str(last_error)}


def run_import(payload: list, token: str) -> None:
    """
    将 payload 按 BATCH_SIZE 分批调用 API，使用 tqdm 渲染实时导入进度条。
    - 进度条总量按"记录条数"推进，右侧实时展示累计 added/ignored/errors 等
    - 每批成功后通过 tqdm.write 打印简短摘要，避免与进度条互相覆盖
    - 导入过程中，控制台 logger 临时提升为 WARNING，文件 logger 仍保留全量记录
    """
    batch_size = CONFIG["BATCH_SIZE"]
    total = len(payload)
    total_batches = math.ceil(total / batch_size)
    logger.info(
        f"开始导入：共 {total} 条，分 {total_batches} 批，每批最多 {batch_size} 条。"
    )

    # 临时抬升控制台 handler 级别，避免 logger.info 打断 tqdm
    console_handler = None
    original_level = None
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            console_handler = h
            original_level = h.level
            h.setLevel(logging.WARNING)
            break

    agg = {
        "added": 0,
        "ignored": 0,
        "errors": 0,
        "failed_batches": 0,
        "success_batches": 0,
        "dup_skip_batches": 0,
    }
    global_start = time.time()

    try:
        with tqdm(total=total, desc="导入进度", unit="条", dynamic_ncols=True) as pbar:
            for i in range(total_batches):
                batch = payload[i * batch_size : (i + 1) * batch_size]
                result = call_import_api(batch, token, i + 1, total_batches)
                elapsed = result.pop("_elapsed", 0.0) if isinstance(result, dict) else 0.0

                if "error" in result:
                    if result.get("error") == "duplicate_key_skip":
                        agg["dup_skip_batches"] += 1
                        tqdm.write(
                            f"[批次 {i+1}/{total_batches}] 重复数据跳过（不重试） "
                            f"耗时 {elapsed:.2f}s | 进度 {i+1}/{total_batches}"
                        )
                    else:
                        agg["failed_batches"] += 1
                        tqdm.write(
                            f"[批次 {i+1}/{total_batches}] 失败：{str(result.get('error', ''))[:200]} "
                            f"| 进度 {i+1}/{total_batches}"
                        )
                else:
                    agg["success_batches"] += 1
                    for k in ("added", "ignored", "errors"):
                        v = result.get(k)
                        if isinstance(v, int):
                            agg[k] += v
                    tqdm.write(
                        f"[批次 {i+1}/{total_batches}] "
                        f"total={result.get('total', len(batch))} "
                        f"added={result.get('added', '-')} "
                        f"ignored={result.get('ignored', '-')} "
                        f"errors={result.get('errors', '-')} "
                        f"耗时 {elapsed:.2f}s | 进度 {i+1}/{total_batches}"
                    )

                pbar.update(len(batch))
                pbar.set_postfix(
                    added=agg["added"],
                    ignored=agg["ignored"],
                    errors=agg["errors"],
                    failed=agg["failed_batches"],
                    dup_skip=agg["dup_skip_batches"],
                )

                if i < total_batches - 1:
                    time.sleep(CONFIG["BATCH_INTERVAL"])
    finally:
        if console_handler is not None and original_level is not None:
            console_handler.setLevel(original_level)

    total_elapsed = time.time() - global_start
    logger.info(
        f"导入完成 | 成功批次: {agg['success_batches']} | 失败批次: {agg['failed_batches']} "
        f"| 重复跳过批次: {agg['dup_skip_batches']} "
        f"| 累计 added={agg['added']} ignored={agg['ignored']} errors={agg['errors']} "
        f"| 总耗时: {total_elapsed:.2f}s"
    )


# ─────────────────────────── 流水线 ─────────────────────────────
def run_pipeline_once() -> bool:
    """
    执行一次完整流水线（Token → Excel → 转换 → 清洗 → 导出 → 导入）。
    返回：
        True  全流程执行完毕（含"payload 为空而跳过导入"这种业务上的正常结束）
        False 流程中抛出异常被捕获
    """
    logger.info("=" * 60)
    logger.info("增量导入一体化流水线启动")
    logger.info("=" * 60)

    try:
        token = get_token()

        excel_path = 解析最新Excel路径(
            CONFIG["EXCEL_DIR"], CONFIG["EXCEL_PREFIX"], CONFIG["EXCEL_EXT"]
        )
        df = 读取售前通讯录数据(excel_path)
        contact_db_paths = 解析通讯录数据库路径()
        logger.info(f"通讯录数据源：{len(contact_db_paths)} 个数据库")
        for p in contact_db_paths:
            logger.info(f"  - {p}")
        enrolled_set = 读取报名备注集合(contact_db_paths)
        # 微信原始ID 兜底映射：当 Excel 未提供"微信原始ID"时，用该映射回填
        wechat_orig_map = 读取微信原始ID映射(contact_db_paths)

        records = 转换为目标结构(df, enrolled_set, wechat_orig_map)
        records, same_day_removed_rows = apply_same_day_earliest_dedup(records)
        records, removed_rows = apply_dedup_and_clean(records)

        reports = build_csv_reports(records, removed_rows, same_day_removed_rows)
        export_csv_reports(reports, CONFIG["OUTPUT_DIR"])

        payload = build_api_payload(records)
        payload = dedup_payload(payload)

        # 备份用：记录"过滤前"的 payload 快照
        # 后面会与"过滤后"的 payload 做差集，算出本次被「跳过清单」筛掉的学员
        # 这里用浅拷贝即可：差集只比较顶层「意向学员微信号」字段
        payload_before_skip = list(payload)

        payload = filter_skip_enrolled_or_bound(payload, token)

        try:
            json_dir = Path(CONFIG["OUTPUT_DIR"])
            json_dir.mkdir(parents=True, exist_ok=True)
            ts_str = datetime.now().strftime('%Y%m%d_%H%M')

            # ── 备份 1：实际上传到 incremental-import 接口的 payload ─────────
            # 文件名保持不变，避免影响任何下游对接 / 既有运维脚本
            json_name = f"意向学员数据_{ts_str}.json"
            json_path = _获取可写路径(json_dir / json_name)
            with json_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存上传 JSON：{json_path}（{len(payload)} 条）")

            # ── 备份 2：本次被「跳过清单」过滤掉的学员，仅诊断用，不上传 ─────
            # 计算逻辑：过滤前 - 过滤后（按"意向学员微信号"作为唯一键做差集）
            # 用途：方便事后核对"某个学员为什么没在导入备份里" → 查这份就能定位
            kept_keys = {
                str(it.get("意向学员微信号", "")).strip()
                for it in payload
                if isinstance(it, dict)
            }
            skipped_payload = [
                it for it in payload_before_skip
                if isinstance(it, dict)
                and str(it.get("意向学员微信号", "")).strip() not in kept_keys
            ]
            skipped_name = f"意向学员数据_{ts_str}_skipped.json"
            skipped_path = _获取可写路径(json_dir / skipped_name)
            with skipped_path.open("w", encoding="utf-8") as f:
                json.dump(skipped_payload, f, ensure_ascii=False, indent=2)
            logger.info(
                f"已保存被跳过学员 JSON：{skipped_path}"
                f"（{len(skipped_payload)} 条，仅用于诊断，不会上传）"
            )
        except Exception as e:
            logger.warning(f"保存上传 JSON 失败（不影响后续导入）：{e}")

        if not payload:
            logger.warning("有效导入数据为空，跳过 API 调用。")
        else:
            run_import(payload, token)

        logger.info("=" * 60)
        logger.info("流水线执行完毕。")
        logger.info("=" * 60)
        return True

    except Exception as e:
        logger.error(f"流水线执行异常：{e}", exc_info=True)
        logger.info("=" * 60)
        logger.info("流水线执行结束（异常）。")
        logger.info("=" * 60)
        return False


# ─────────────────────────── 调度器 ─────────────────────────────
import threading


class Scheduler:
    """
    固定间隔调度器：
    - 启动后每 INTERVAL_SEC 秒触发一次 run_pipeline_once。
    - 防重入：若上一次任务尚未结束，本次触发直接跳过并告警。
    - 立即运行：支持用户手动触发一次，同样受防重入约束。
    """

    def __init__(self, interval_sec: int = 15 * 60) -> None:
        self.interval_sec = interval_sec
        self._stop_event = threading.Event()
        self._wakeup_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._busy = threading.Lock()
        self._next_run_ts: float = 0.0
        self._last_run_ts: float = 0.0
        self._last_result: Optional[bool] = None
        self._skip_count: int = 0
        self._run_count: int = 0
        self._on_run_start: Optional[callable] = None  # type: ignore[assignment]
        self._on_run_end: Optional[callable] = None  # type: ignore[assignment]

    # ── 回调注入（供 GUI 刷新状态） ────────────────────────────
    def set_callbacks(self, on_start=None, on_end=None) -> None:
        self._on_run_start = on_start
        self._on_run_end = on_end

    # ── 状态查询 ─────────────────────────────────────────────
    def is_running(self) -> bool:
        return self._busy.locked()

    def is_scheduled(self) -> bool:
        if self._stop_event.is_set():
            return False
        return self._thread is not None and self._thread.is_alive()

    def next_run_in_sec(self) -> int:
        if not self.is_scheduled():
            return -1
        return max(0, int(self._next_run_ts - time.time()))

    def stats(self) -> dict:
        return {
            "scheduled": self.is_scheduled(),
            "running": self.is_running(),
            "next_run_in": self.next_run_in_sec(),
            "last_run_ts": self._last_run_ts,
            "last_result": self._last_result,
            "run_count": self._run_count,
            "skip_count": self._skip_count,
            "interval_sec": self.interval_sec,
        }

    # ── 控制 ─────────────────────────────────────────────────
    def start(self) -> None:
        if self.is_scheduled():
            return
        self._stop_event.clear()
        self._next_run_ts = time.time() + self.interval_sec
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()
        logger.info(f"定时调度已启动：每 {self.interval_sec} 秒触发一次。")

    def set_interval(self, interval_sec: int) -> None:
        """动态调整触发周期（秒）。调度运行中会立即按新周期重排下一次触发时间。"""
        if interval_sec <= 0:
            raise ValueError("interval_sec 必须为正整数")
        self.interval_sec = int(interval_sec)
        if self.is_scheduled():
            self._next_run_ts = time.time() + self.interval_sec
            self._wakeup_event.set()
        logger.info(f"调度周期已更新为 {self.interval_sec} 秒。")

    def stop(self) -> None:
        if not self.is_scheduled():
            return
        self._stop_event.set()
        self._wakeup_event.set()
        self._next_run_ts = 0.0
        logger.info("定时调度已请求停止。")

    def trigger_once(self) -> bool:
        """立即异步触发一次执行。已在运行时返回 False。"""
        if self.is_running():
            logger.warning("已有任务在执行中，本次立即执行请求被跳过。")
            return False
        threading.Thread(target=self._run_job, name="manual-run", daemon=True).start()
        return True

    # ── 内部 ─────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop_event.is_set():
            wait = max(0.0, self._next_run_ts - time.time())
            woken = self._wakeup_event.wait(timeout=wait)
            if self._stop_event.is_set():
                break
            if woken:
                # 被 set_interval 唤醒：周期已被外部刷新，重新进入等待
                self._wakeup_event.clear()
                continue
            self._next_run_ts = time.time() + self.interval_sec
            if self.is_running():
                self._skip_count += 1
                logger.warning(
                    f"上一次任务仍在执行，跳过本次定时触发（累计跳过 {self._skip_count} 次）。"
                )
                continue
            self._run_job()

    def _run_job(self) -> None:
        if not self._busy.acquire(blocking=False):
            logger.warning("任务锁未获取到，跳过本次执行。")
            return
        try:
            if self._on_run_start:
                try:
                    self._on_run_start()
                except Exception:
                    pass
            self._run_count += 1
            ok = run_pipeline_once()
            self._last_run_ts = time.time()
            self._last_result = ok
        finally:
            self._busy.release()
            if self._on_run_end:
                try:
                    self._on_run_end()
                except Exception:
                    pass


# ─────────────────────────── GUI ────────────────────────────────
import queue


class TkLogHandler(logging.Handler):
    """将 logger 的输出塞入 queue，供 Tk 主线程轮询消费。"""

    def __init__(self, q: "queue.Queue[str]") -> None:
        super().__init__()
        self.queue = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(self.format(record))
        except Exception:
            pass


def _enable_windows_dpi_awareness() -> None:
    """开启 Windows DPI 感知，避免 Tk 在高分屏下字体模糊。失败时静默回退。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # Windows 8.1+: Per-Monitor DPI Aware
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            return
        except Exception:
            pass
        # Windows Vista+: System DPI Aware
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def launch_gui() -> None:
    _enable_windows_dpi_awareness()

    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox, filedialog
    from tkinter import font as tkfont

    log_queue: "queue.Queue[str]" = queue.Queue()
    gui_handler = TkLogHandler(log_queue)
    gui_handler.setLevel(logging.INFO)
    gui_handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(gui_handler)

    scheduler = Scheduler(interval_sec=15 * 60)

    root = tk.Tk()
    root.title("增量导入控制台")
    root.geometry("1000x620")
    root.minsize(820, 500)

    # ── 字体放大与 Tk 缩放 ────────────────────────────────
    try:
        # 根据系统真实 DPI 放大 Tk 全局缩放，Windows 默认 DPI 为 96
        dpi = root.winfo_fpixels("1i")
        root.tk.call("tk", "scaling", max(1.25, dpi / 72.0))
    except Exception:
        pass

    _ui_font_family = "Microsoft YaHei UI"
    _mono_font_family = "Consolas"
    _ui_size = 11
    _log_size = 11

    # 统一调整 ttk 命名字体，使全局控件（Label/Button/Entry/Frame 等）放大
    for _named in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            _f = tkfont.nametofont(_named)
            _f.configure(family=_ui_font_family, size=_ui_size)
        except Exception:
            pass

    style = ttk.Style(root)
    style.configure("TLabel", font=(_ui_font_family, _ui_size))
    style.configure("TButton", font=(_ui_font_family, _ui_size), padding=(10, 6))
    style.configure("TLabelframe.Label", font=(_ui_font_family, _ui_size, "bold"))
    style.configure("TEntry", padding=4)

    status_bold_font = (_ui_font_family, _ui_size + 1, "bold")
    status_font = (_ui_font_family, _ui_size)

    # ── 顶部状态栏 ─────────────────────────────────────────
    status_frame = ttk.Frame(root, padding=(12, 10))
    status_frame.pack(fill=tk.X)

    status_var = tk.StringVar(value="状态：空闲")
    next_var = tk.StringVar(value="下次：未启动")
    stats_var = tk.StringVar(value="运行 0 次 / 跳过 0 次")
    last_var = tk.StringVar(value="上次：—")

    ttk.Label(status_frame, textvariable=status_var, font=status_bold_font).grid(row=0, column=0, sticky="w", padx=(0, 18))
    ttk.Label(status_frame, textvariable=next_var, font=status_font).grid(row=0, column=1, sticky="w", padx=(0, 18))
    ttk.Label(status_frame, textvariable=last_var, font=status_font).grid(row=0, column=2, sticky="w", padx=(0, 18))
    ttk.Label(status_frame, textvariable=stats_var, font=status_font).grid(row=0, column=3, sticky="w")

    # ── 间隔设置区 ────────────────────────────────────────
    interval_frame = ttk.Frame(root, padding=(12, 2))
    interval_frame.pack(fill=tk.X)

    ttk.Label(interval_frame, text="触发间隔（分钟）：").grid(row=0, column=0, sticky="w")
    interval_var = tk.StringVar(value="15")
    # 使用 tk.Spinbox 而非 ttk.Spinbox：后者在 Python 3.7+ 才加入，这里保持 3.6 兼容
    spin_interval = tk.Spinbox(
        interval_frame, from_=1, to=1440, textvariable=interval_var, width=6, font=(_ui_font_family, _ui_size)
    )
    spin_interval.grid(row=0, column=1, padx=(6, 6))
    btn_apply_interval = ttk.Button(interval_frame, text="应用间隔")
    btn_apply_interval.grid(row=0, column=2, padx=(0, 12))

    # ── 输出目录设置区 ────────────────────────────────────
    path_frame = ttk.Frame(root, padding=(12, 2))
    path_frame.pack(fill=tk.X)
    path_frame.columnconfigure(1, weight=1)

    ttk.Label(path_frame, text="输出目录（CSV/JSON）：").grid(row=0, column=0, sticky="w")
    output_dir_var = tk.StringVar(value=CONFIG.get("OUTPUT_DIR", ""))
    entry_output_dir = ttk.Entry(path_frame, textvariable=output_dir_var, font=(_ui_font_family, _ui_size))
    entry_output_dir.grid(row=0, column=1, sticky="we", padx=(6, 6))
    btn_browse_output = ttk.Button(path_frame, text="浏览...")
    btn_browse_output.grid(row=0, column=2, padx=(0, 6))
    btn_apply_output = ttk.Button(path_frame, text="应用并保存")
    btn_apply_output.grid(row=0, column=3, padx=(0, 12))

    # ── 按钮区 ────────────────────────────────────────────
    btn_frame = ttk.Frame(root, padding=(12, 6))
    btn_frame.pack(fill=tk.X)

    btn_start = ttk.Button(btn_frame, text="启动定时")
    btn_stop = ttk.Button(btn_frame, text="停止定时")
    btn_run_now = ttk.Button(btn_frame, text="立即运行一次")
    btn_clear = ttk.Button(btn_frame, text="清空日志")

    btn_start.grid(row=0, column=0, padx=4, pady=4)
    btn_stop.grid(row=0, column=1, padx=4, pady=4)
    btn_run_now.grid(row=0, column=2, padx=4, pady=4)
    btn_clear.grid(row=0, column=3, padx=4, pady=4)

    # ── 日志区 ────────────────────────────────────────────
    log_frame = ttk.LabelFrame(root, text="运行日志", padding=(6, 4))
    log_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 12))
    log_text = scrolledtext.ScrolledText(
        log_frame, wrap=tk.NONE, state="disabled", font=(_mono_font_family, _log_size)
    )
    log_text.pack(fill=tk.BOTH, expand=True)

    def append_log(line: str) -> None:
        log_text.configure(state="normal")
        log_text.insert(tk.END, line + "\n")
        log_text.see(tk.END)
        log_text.configure(state="disabled")

    def clear_log() -> None:
        log_text.configure(state="normal")
        log_text.delete("1.0", tk.END)
        log_text.configure(state="disabled")

    def refresh_status() -> None:
        st = scheduler.stats()
        if st["running"]:
            status_var.set("状态：运行中")
        elif st["scheduled"]:
            status_var.set("状态：等待中")
        else:
            status_var.set("状态：空闲")

        if st["scheduled"]:
            sec = st["next_run_in"]
            mm, ss = divmod(max(0, sec), 60)
            next_var.set(f"下次：{mm:02d}:{ss:02d} 后")
        else:
            next_var.set("下次：未启动")

        if st["last_run_ts"] > 0:
            ts = datetime.fromtimestamp(st["last_run_ts"]).strftime("%H:%M:%S")
            tag = "成功" if st["last_result"] else "异常"
            last_var.set(f"上次：{ts} ({tag})")

        stats_var.set(f"运行 {st['run_count']} 次 / 跳过 {st['skip_count']} 次")

    def pump_logs() -> None:
        try:
            while True:
                line = log_queue.get_nowait()
                append_log(line)
        except queue.Empty:
            pass
        refresh_status()
        root.after(500, pump_logs)

    def do_start() -> None:
        scheduler.start()
        refresh_status()

    def do_stop() -> None:
        scheduler.stop()
        refresh_status()

    def do_run_now() -> None:
        if not scheduler.trigger_once():
            messagebox.showinfo("提示", "当前已有任务在执行，请稍后再试。")
        refresh_status()

    def do_apply_interval() -> None:
        raw = interval_var.get().strip()
        try:
            minutes = int(raw)
            if minutes < 1 or minutes > 1440:
                raise ValueError
        except ValueError:
            messagebox.showwarning("间隔无效", "请输入 1 ~ 1440 之间的整数（分钟）。")
            return
        scheduler.set_interval(minutes * 60)
        refresh_status()

    def do_browse_output() -> None:
        init_dir = output_dir_var.get().strip() or CONFIG.get("OUTPUT_DIR", "")
        chosen = filedialog.askdirectory(title="选择输出目录", initialdir=init_dir)
        if chosen:
            output_dir_var.set(chosen)

    def do_apply_output() -> None:
        raw = output_dir_var.get().strip()
        default_dir = str(Path(CONFIG["EXCEL_DIR"]) / "LOG")
        target = raw if raw else default_dir
        try:
            Path(target).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showwarning("路径无效", f"无法创建或访问目录：\n{target}\n\n{e}")
            return
        CONFIG["OUTPUT_DIR"] = target
        output_dir_var.set(target)
        try:
            save_local_config()
            logger.info(f"输出目录已更新并保存：{target}")
            messagebox.showinfo(
                "已应用",
                f"输出目录已更新为：\n{target}\n\n"
                f"说明：本次运行的日志文件仍写入旧目录，新 CSV/JSON 将输出到新目录；"
                f"下次启动后日志也会写入新目录。",
            )
        except Exception as e:
            logger.warning(f"保存本地配置失败：{e}")
            messagebox.showwarning("保存失败", f"本次已应用，但写入配置文件失败：\n{e}")

    btn_start.configure(command=do_start)
    btn_stop.configure(command=do_stop)
    btn_run_now.configure(command=do_run_now)
    btn_clear.configure(command=clear_log)
    btn_apply_interval.configure(command=do_apply_interval)
    btn_browse_output.configure(command=do_browse_output)
    btn_apply_output.configure(command=do_apply_output)

    def on_close() -> None:
        scheduler.stop()
        root.after(100, root.destroy)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(300, pump_logs)
    logger.info("GUI 已就绪，可点击『启动定时』或『立即运行一次』。")
    root.mainloop()


# ─────────────────────────── 入口 ───────────────────────────────
def main() -> None:
    if "--no-gui" in sys.argv:
        ok = run_pipeline_once()
        sys.exit(0 if ok else 1)
    launch_gui()


if __name__ == "__main__":
    main()
