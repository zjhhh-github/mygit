# -*- coding: utf-8 -*-

"""
补充飞书推送3：对比 contact.db 最大编号与飞书多维表格编号列，自动补齐缺失编号。

影刀用法：把整个 main.py 粘贴到影刀「Python 代码」模块中直接运行。
    只需修改下方「影刀运行配置」里的 是否写入 / 是否全量扫描。
    运行后 拉专属带领群_全部_所有记录 即为拉取到的完整飞书记录列表。

命令行用法（本地调试时把 影刀直接运行 改为 False）：
    python main.py
    python main.py --write
    python main.py --full-scan
"""

import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# ========== 影刀运行配置（影刀里直接改这里）==========
影刀直接运行 = False
是否写入 = True
是否全量扫描 = False  # True=对比阶段全量分页拉编号列（慢，仅核对用）

# 对比阶段快速取 max：search 按编号降序，默认只请求 1 条（失败自动扩大到 50 条）
FAST_NUMBER_PAGE_SIZE = 1
FAST_NUMBER_FALLBACK_PAGE_SIZE = 50

# ────────────────────────── 配置 ──────────────────────────
_DEFAULT_APP_ID = "cli_a96f36ed1538dbcf"
_DEFAULT_APP_SECRET = "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB"

APP_ID = os.environ.get("FEISHU_APP_ID", "").strip() or _DEFAULT_APP_ID
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "").strip() or _DEFAULT_APP_SECRET

# 飞书多维表格定位
APP_TOKEN = "QggBwb85cid8Opk6eywcEFsrn6b"
TABLE_ID = "tblgWQUZZ5lgp9V7"
VIEW_ID = "vewLS7j77H"

# contact 数据库路径
DB_PATH = Path(
    r"C:\Users\LENOVO\Documents\chatlog\wxid_7u0rihcbbpbz12_ec5a"
    r"\db_storage\contact\contact.db"
)

# 飞书字段名
FIELD_NUMBER = "编号"

# 编号位数（与 SQL SUBSTR(remark, 4, 6) 保持一致）
NUMBER_WIDTH = 6

FEISHU_HOST = "https://open.feishu.cn"
REQUEST_TIMEOUT = 60
RETRY_TIMES = 5
# 飞书业务错误码：内部错误 / 数据未就绪 / 限流 / 写冲突，可重试
FEISHU_RETRY_CODES = {1254290, 1254291, 1254607, 1255001, 1255002, 1255005, 1255040}
# 影刀/系统环境常注入无效 HTTP 代理，导致飞书 API 报 ProxyError；默认直连
飞书请求禁用代理 = True
PAGE_SIZE = 500  # 全量分页大小（过大易触发 InternalError，建议 100）
PAGE_DELAY = 0.2  # 分页间隔（秒），降低 QPS 避免 1254290
WRITE后等待秒 = 3  # 补充写入后等待飞书落库，再拉全表
SEARCH_TOP_SIZE = 200  # fetch_feishu_data 全量扫描前的快速样本条数（兼容旧逻辑）
BATCH_SIZE = 10  # 飞书 batch_create 单次最多 500，保守取 10

# 拉取到的飞书完整记录（含全部字段），仅保存在内存变量中
拉专属带领群_全部_所有记录 = []  # type: List[Dict[str, Any]]

# 影刀可直接读取的运行结果变量
执行结果 = {}  # type: Dict[str, Any]
待新增编号 = []  # type: List[str]
数据库最大编号 = None  # type: Optional[str]
飞书最大编号 = None  # type: Optional[str]
是否成功 = False
执行消息 = ""


def _ensure_yingdao_globals():
    # type: () -> None
    """影刀 exec 环境可能未创建模块级中文变量，运行前补齐，避免 NameError。"""
    g = globals()
    if "拉专属带领群_全部_所有记录" not in g:
        g["拉专属带领群_全部_所有记录"] = []
    if "执行结果" not in g:
        g["执行结果"] = {}
    if "待新增编号" not in g:
        g["待新增编号"] = []
    if "数据库最大编号" not in g:
        g["数据库最大编号"] = None
    if "飞书最大编号" not in g:
        g["飞书最大编号"] = None
    if "是否成功" not in g:
        g["是否成功"] = False
    if "执行消息" not in g:
        g["执行消息"] = ""


def _sync_yingdao_result(result):
    # type: (Dict[str, Any]) -> None
    """把运行结果写回影刀可直接读取的模块变量（显式 global 赋值，影刀才能读到）。"""
    global 拉专属带领群_全部_所有记录
    global 执行结果, 待新增编号, 数据库最大编号, 飞书最大编号, 是否成功, 执行消息
    _ensure_yingdao_globals()
    执行结果 = result
    待新增编号 = result.get("待新增编号") or []
    数据库最大编号 = result.get("数据库最大编号")
    飞书最大编号 = result.get("飞书最大编号")
    是否成功 = bool(result.get("成功"))
    执行消息 = result.get("消息") or ""
    拉专属带领群_全部_所有记录 = list(result.get("拉专属带领群_全部_所有记录") or [])


def _get_拉专属带领群_全部_所有记录():
    # type: () -> List[Dict[str, Any]]
    global 拉专属带领群_全部_所有记录
    _ensure_yingdao_globals()
    return 拉专属带领群_全部_所有记录


def _set_拉专属带领群_全部_所有记录(records):
    # type: (List[Dict[str, Any]]) -> List[Dict[str, Any]]
    """写入 拉专属带领群_全部_所有记录，并返回副本供 result 同步。"""
    global 拉专属带领群_全部_所有记录
    _ensure_yingdao_globals()
    saved = list(records)
    拉专属带领群_全部_所有记录 = saved
    return list(saved)


def fetch_full_table_to_variable(token, wait_before=False):
    # type: (str, bool) -> Tuple[List[Dict[str, Any]], int]
    """拉取整表并保存到 拉专属带领群_全部_所有记录。"""
    if wait_before and WRITE后等待秒 > 0:
        print("  等待 {} 秒，确保飞书写入完成 ...".format(WRITE后等待秒))
        time.sleep(WRITE后等待秒)
    print("  拉取整表（全部字段） ...")
    records = list_all_records(token, field_names=None)
    saved_records = _set_拉专属带领群_全部_所有记录(records)
    print("  整表 {} 条 -> 拉专属带领群_全部_所有记录".format(len(saved_records)))
    return saved_records, len(saved_records)

# contact.db 查询：取 remark 中 ¿¿¿ + 6 位数字前缀的最大值
MAX_NO_SQL = """
SELECT COALESCE((
    SELECT SUBSTR(remark, 4, 6)
    FROM contact
    WHERE remark GLOB '¿¿¿[0-9][0-9][0-9][0-9][0-9][0-9]-*'
      AND TRIM(SUBSTR(remark, 11)) NOT IN ('', '空', '删除')
    ORDER BY CAST(SUBSTR(remark, 4, 6) AS INTEGER) DESC
    LIMIT 1
), '') AS max_no;
"""


def _build_http_session():
    # type: () -> requests.Session
    """创建 HTTP 会话；影刀环境下忽略系统代理，避免 ProxyError。"""
    session = requests.Session()
    if 飞书请求禁用代理:
        session.trust_env = False
        session.proxies.update({"http": None, "https": None})
    return session


_HTTP_SESSION = _build_http_session()


def _request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """带重试的 HTTP 请求。"""
    if 飞书请求禁用代理 and "proxies" not in kwargs:
        kwargs["proxies"] = {"http": None, "https": None}

    last_exc = None  # type: Optional[Exception]
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            resp = _HTTP_SESSION.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
            if resp.status_code >= 500 or resp.status_code == 429:
                last_exc = RuntimeError("HTTP {}: {}".format(resp.status_code, resp.text[:200]))
                if attempt < RETRY_TIMES:
                    time.sleep(min(2 ** attempt, 5))
                    continue
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < RETRY_TIMES:
                time.sleep(min(2 ** attempt, 5))
                continue
    raise RuntimeError("请求失败：{} {}，原因：{}".format(method, url, last_exc))


def _feishu_api_call(method, url, headers=None, params=None, json_body=None):
    # type: (str, str, Optional[Dict[str, str]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """调用飞书 API，HTTP 成功但 code!=0 时对可重试错误码自动重试。"""
    headers = headers or {}
    last_payload = None  # type: Optional[Dict[str, Any]]
    for attempt in range(1, RETRY_TIMES + 1):
        if method.upper() == "GET":
            resp = _request("GET", url, headers=headers, params=params)
        else:
            resp = _request("POST", url, headers=headers, params=params, json=json_body)
        payload = resp.json()
        code = payload.get("code")
        if code == 0:
            return payload.get("data") or {}

        last_payload = payload
        if code in FEISHU_RETRY_CODES and attempt < RETRY_TIMES:
            wait_s = min(2 ** attempt, 8)
            print("  飞书接口暂时失败 code={}，{}s 后重试 ({}/{})".format(
                code, wait_s, attempt, RETRY_TIMES - 1
            ))
            time.sleep(wait_s)
            continue
        raise RuntimeError("飞书接口失败：{}".format(payload))

    raise RuntimeError("飞书接口失败：{}".format(last_payload))


def _extract_text(value: Any) -> str:
    """把飞书字段值统一转为字符串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, list):
        parts = []  # type: List[str]
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")).strip())
            elif item is not None:
                parts.append(str(item).strip())
        return "".join(parts).strip()
    if isinstance(value, dict):
        return str(value.get("text", "")).strip()
    return str(value).strip()


def parse_number(text):
    # type: (str) -> Optional[int]
    """从编号文本中提取整数；无法解析时返回 None。"""
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    match = re.search(r"\d+", text)
    return int(match.group()) if match else None


def format_number(num):
    # type: (int) -> str
    """格式化为固定位数编号字符串。"""
    return str(num).zfill(NUMBER_WIDTH)


def fetch_db_max_no():
    # type: () -> Optional[int]
    """从 contact.db 查询 max_no，返回整数；无结果时返回 None。"""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"数据库不存在：{DB_PATH}")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute(MAX_NO_SQL).fetchone()
    finally:
        conn.close()

    raw = (row[0] or "").strip() if row else ""
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError(f"数据库 max_no 非纯数字：{raw!r}")
    return int(raw)


def get_tenant_access_token() -> str:
    """获取飞书 tenant_access_token。"""
    url = f"{FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal"
    resp = _request("POST", url, json={"app_id": APP_ID, "app_secret": APP_SECRET})
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 token 失败：{data}")
    return data["tenant_access_token"]


def _parse_numbers_from_records(records):
    # type: (List[Dict[str, Any]]) -> List[int]
    numbers = []  # type: List[int]
    for record in records:
        fields = record.get("fields") or {}
        num_text = _extract_text(fields.get(FIELD_NUMBER))
        parsed = parse_number(num_text)
        if parsed is not None:
            numbers.append(parsed)
    return numbers


def save_拉专属带领群_全部_所有记录(records=None):
    # type: (Optional[List[Dict[str, Any]]]) -> int
    """把拉取结果写入模块变量 拉专属带领群_全部_所有记录（仅内存）。"""
    if records is not None:
        return len(_set_拉专属带领群_全部_所有记录(records))
    return len(_get_拉专属带领群_全部_所有记录())


def fetch_feishu_numbers_only(token, full_scan=False):
    # type: (str, bool) -> Tuple[Optional[int], int, List[Dict[str, Any]]]
    """
    获取飞书编号最大值，用于对比。

    默认：search 按编号降序只拉 1 条（约 1 次请求，极快）。
    full_scan=True：分页拉取视图内全部编号列（慢，仅核对用）。
    """
    if full_scan:
        print("  全量扫描编号列（分页） ...")
        records = list_all_records(token, field_names=[FIELD_NUMBER])
        numbers = _parse_numbers_from_records(records)
        feishu_max = max(numbers) if numbers else None
        print("  编号列全量完成：{} 条，有效编号 {} 条".format(len(records), len(numbers)))
        return feishu_max, len(records), records

    try:
        feishu_max, total, _mode, records = fetch_feishu_max_number_fast(token)
        stat = total if total is not None else len(records)
        print(
            "  快速完成：max={}，表内约 {} 条".format(
                format_number(feishu_max) if feishu_max is not None else "(无)",
                stat,
            )
        )
        return feishu_max, stat, records
    except RuntimeError as exc:
        print("  快速模式失败，回退全量编号列：{}".format(exc))

    records = list_all_records(token, field_names=[FIELD_NUMBER])
    numbers = _parse_numbers_from_records(records)
    feishu_max = max(numbers) if numbers else None
    print("  编号列回退全量完成：{} 条，有效编号 {} 条".format(len(records), len(numbers)))
    return feishu_max, len(records), records


def fetch_feishu_max_number_fast(token, page_size=None):
    # type: (str, Optional[int]) -> Tuple[Optional[int], Optional[int], str, List[Dict[str, Any]]]
    """
    快速获取飞书编号最大值：search 按编号降序取一页（仅编号列）。

    默认 page_size=1，只取最大编号那条；若首条无有效编号则自动扩大到 FAST_NUMBER_FALLBACK_PAGE_SIZE。
    返回 (max_number, total_or_none, mode_label, records)。
    """
    if page_size is None:
        page_size = FAST_NUMBER_PAGE_SIZE

    url = (
        "{}/open-apis/bitable/v1/apps/{}/tables/{}/records/search".format(
            FEISHU_HOST, APP_TOKEN, TABLE_ID
        )
    )
    headers = {
        "Authorization": "Bearer {}".format(token),
        "Content-Type": "application/json",
    }
    body = {
        "view_id": VIEW_ID,
        "field_names": [FIELD_NUMBER],
        "sort": [{"field_name": FIELD_NUMBER, "desc": True}],
        "page_size": page_size,
        "automatic_fields": False,
    }

    print("  search 降序取编号，page_size={} ...".format(page_size))
    data = _feishu_api_call("POST", url, headers=headers, json_body=body)
    items = data.get("items") or []
    numbers = _parse_numbers_from_records(items)

    # 首条可能为空编号，自动扩大样本再试一次
    if not numbers and page_size < FAST_NUMBER_FALLBACK_PAGE_SIZE:
        print("  本页无有效编号，扩大到 {} 条重试 ...".format(FAST_NUMBER_FALLBACK_PAGE_SIZE))
        return fetch_feishu_max_number_fast(token, page_size=FAST_NUMBER_FALLBACK_PAGE_SIZE)

    if not numbers:
        return None, data.get("total"), "search_numbers", items

    return max(numbers), data.get("total"), "search_numbers", items


def list_all_records_search(token, field_names=None):
    # type: (str, Optional[List[str]]) -> List[Dict[str, Any]]
    """通过 search 接口分页拉取记录；field_names 指定时只拉指定列。"""
    url = (
        "{}/open-apis/bitable/v1/apps/{}/tables/{}/records/search".format(
            FEISHU_HOST, APP_TOKEN, TABLE_ID
        )
    )
    headers = {
        "Authorization": "Bearer {}".format(token),
        "Content-Type": "application/json",
    }
    body = {
        "view_id": VIEW_ID,
        "automatic_fields": field_names is None,
    }  # type: Dict[str, Any]
    if field_names:
        body["field_names"] = field_names

    label = "编号列" if field_names == [FIELD_NUMBER] else "整表"
    all_records = []  # type: List[Dict[str, Any]]
    page_token = None  # type: Optional[str]
    page_index = 0

    while True:
        page_index += 1
        params = {"page_size": PAGE_SIZE}  # type: Dict[str, Any]
        if page_token:
            params["page_token"] = page_token

        data = _feishu_api_call("POST", url, headers=headers, params=params, json_body=body)
        items = data.get("items") or []
        all_records.extend(items)
        if page_index == 1 or page_index % 10 == 0:
            print("  {}拉取第 {} 页，累计 {} 条 ...".format(label, page_index, len(all_records)))

        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
        time.sleep(PAGE_DELAY)

    return all_records


def list_all_records(token, field_names=None):
    # type: (str, Optional[List[str]]) -> List[Dict[str, Any]]
    """拉取视图内记录；field_names 为空时拉全部字段，否则只拉指定列。"""
    try:
        return list_all_records_search(token, field_names=field_names)
    except RuntimeError as exc:
        print("  search 拉取失败，回退 GET list：{}".format(exc))

    all_records = []  # type: List[Dict[str, Any]]
    page_token = None  # type: Optional[str]
    page_index = 0
    url = (
        "{}/open-apis/bitable/v1/apps/{}/tables/{}/records".format(
            FEISHU_HOST, APP_TOKEN, TABLE_ID
        )
    )
    headers = {"Authorization": "Bearer {}".format(token)}
    label = "编号列" if field_names == [FIELD_NUMBER] else "整表"

    while True:
        page_index += 1
        params = {
            "page_size": PAGE_SIZE,
            "view_id": VIEW_ID,
        }  # type: Dict[str, Any]
        if field_names:
            params["field_names"] = field_names
        if page_token:
            params["page_token"] = page_token

        data = _feishu_api_call("GET", url, headers=headers, params=params)
        items = data.get("items") or []
        all_records.extend(items)
        if page_index == 1 or page_index % 10 == 0:
            print("  GET {} 第 {} 页，累计 {} 条 ...".format(label, page_index, len(all_records)))

        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
        time.sleep(PAGE_DELAY)

    return all_records


def fetch_feishu_data(token, full_scan=False):
    # type: (str, bool) -> Tuple[Optional[int], Optional[int], str, List[Dict[str, Any]]]
    """
    拉取飞书记录并计算编号最大值。

    - full_scan=True：分页拉取视图内全部记录
    - 否则：search 快速模式
    """
    if full_scan:
        records = list_all_records(token)
        numbers = _parse_numbers_from_records(records)
        if not numbers:
            return None, len(records), "full", records
        print("  全量拉取完成：有效编号 {}/{} 条".format(len(numbers), len(records)))
        return max(numbers), len(records), "full", records

    try:
        return fetch_feishu_max_number_fast(token, page_size=SEARCH_TOP_SIZE)
    except RuntimeError as exc:
        print("  快速模式失败，回退全量拉取：{}".format(exc))

    records = list_all_records(token)
    numbers = _parse_numbers_from_records(records)
    if not numbers:
        return None, len(records), "full", records
    print("  全量拉取完成：有效编号 {}/{} 条".format(len(numbers), len(records)))
    return max(numbers), len(records), "full", records


def fetch_feishu_max_number(token, full_scan=False):
    # type: (str, bool) -> Tuple[Optional[int], Optional[int], str, List[Dict[str, Any]]]
    """兼容旧函数名。"""
    return fetch_feishu_data(token, full_scan=full_scan)


def refresh_拉专属带领群_全部_所有记录(token):
    # type: (str) -> int
    """补充写入后等待落库，再拉取整表（全部字段）写入变量。"""
    _records, saved_count = fetch_full_table_to_variable(token, wait_before=True)
    return saved_count


def build_new_numbers(feishu_max, db_max):
    # type: (int, int) -> List[str]
    """生成待新增编号列表（feishu_max+1 .. db_max，步长 1）。"""
    if db_max <= feishu_max:
        return []
    return [format_number(n) for n in range(feishu_max + 1, db_max + 1)]


def batch_create_records(token, numbers):
    # type: (str, List[str]) -> int
    """批量写入飞书，返回成功写入条数。"""
    url = (
        f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{APP_TOKEN}"
        f"/tables/{TABLE_ID}/records/batch_create"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    created = 0
    total_batches = (len(numbers) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx, start in enumerate(range(0, len(numbers), BATCH_SIZE), start=1):
        batch = numbers[start : start + BATCH_SIZE]
        payload = {
            "records": [{"fields": {FIELD_NUMBER: num}} for num in batch],
        }
        resp = _request("POST", url, headers=headers, json=payload)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"批次 {batch_idx}/{total_batches} 写入失败：{data}")
        created += len(batch)
        print(f"  已写入批次 {batch_idx}/{total_batches}，本批 {len(batch)} 条")

    return created


def build_report(
    db_max=None,
    feishu_max=None,
    feishu_stat=None,
    read_mode="",
    record_count=0,
    new_numbers=None,
    dry_run=True,
):
    # type: (Optional[int], Optional[int], Optional[int], str, int, List[str], bool) -> str
    """生成对比报告文本。"""
    if new_numbers is None:
        new_numbers = []
    mode_label = {
        "numbers_only": "仅编号列",
        "full": "整表",
        "search_numbers": "快速编号",
    }.get(read_mode, read_mode)
    stat_text = str(feishu_stat) if feishu_stat is not None else "(未统计)"
    lines = [
        "=== 补充飞书推送3 对比报告 ===",
        f"数据库路径: {DB_PATH}",
        f"数据库 max_no: {format_number(db_max) if db_max is not None else '(无)'}",
        f"飞书 app_token: {APP_TOKEN}",
        f"飞书 table_id: {TABLE_ID}",
        f"飞书 view_id: {VIEW_ID}",
        f"飞书读取模式: {mode_label}",
        f"飞书统计值: {stat_text}",
        f"飞书编号最大值: {format_number(feishu_max) if feishu_max is not None else '(无)'}",
        f"飞书记录变量: 拉专属带领群_全部_所有记录（{record_count} 条，{mode_label}）",
        "",
    ]

    if db_max is None:
        lines.append("结论: 数据库未查到有效 max_no，无需补充。")
        return "\n".join(lines)

    if feishu_max is None:
        lines.append("结论: 飞书表无有效编号，请人工确认后再写入。")
        return "\n".join(lines)

    if db_max <= feishu_max:
        lines.append(
            f"结论: 数据库 max({format_number(db_max)}) "
            f"<= 飞书 max({format_number(feishu_max)})，无需补充。"
        )
        return "\n".join(lines)

    lines.extend(
        [
            f"结论: 数据库 max({format_number(db_max)}) "
            f"> 飞书 max({format_number(feishu_max)})，需补充 {len(new_numbers)} 条。",
            f"模式: {'DRY_RUN（仅预览）' if dry_run else 'WRITE（已写入）'}",
            "",
            f"--- 待新增编号（{len(new_numbers)} 条）---",
        ]
    )
    lines.extend(new_numbers)
    return "\n".join(lines)


def run_supplement(write=False, full_scan=False, dry_run=None):
    # type: (bool, bool, Optional[bool]) -> Dict[str, Any]
    """执行主流程，并同步更新影刀可直接读取的模块变量。"""
    _ensure_yingdao_globals()
    if dry_run is None:
        dry_run = not write

    result = {
        "成功": False,
        "消息": "",
        "报告": "",
        "数据库最大编号": None,
        "飞书最大编号": None,
        "待新增编号": [],
        "已写入条数": 0,
        "记录条数": 0,
        "读取模式": "",
        "拉专属带领群_全部_所有记录": [],
    }

    try:
        print("=== 补充飞书推送3 ===")
        print("模式: {}".format("WRITE（实际写入）" if not dry_run else "DRY_RUN（仅预览）"))
        print()

        print("[1/4] 查询 contact.db max_no ...")
        db_max = fetch_db_max_no()
        result["数据库最大编号"] = format_number(db_max) if db_max is not None else None
        print("  数据库 max_no = {}".format(result["数据库最大编号"] or "(无)"))
        print()

        print("[2/4] 快速获取飞书编号最大值 ...")
        token = get_tenant_access_token()
        feishu_max, number_count, _number_records = fetch_feishu_numbers_only(
            token, full_scan=full_scan
        )
        feishu_stat = number_count
        read_mode = "numbers_only" if full_scan else "search_numbers"
        result["飞书最大编号"] = format_number(feishu_max) if feishu_max is not None else None
        result["读取模式"] = read_mode
        print(
            "  飞书编号最大值 = {} （{}，表内约 {} 条）".format(
                result["飞书最大编号"] or "(无)",
                "全量编号列" if full_scan else "search 快速",
                number_count,
            )
        )
        print()

        print("[3/4] 对比并生成补充计划 ...")
        new_numbers = []
        if db_max is not None and feishu_max is not None:
            new_numbers = build_new_numbers(feishu_max, db_max)
        result["待新增编号"] = new_numbers

        report = build_report(
            db_max=db_max,
            feishu_max=feishu_max,
            feishu_stat=feishu_stat,
            read_mode=read_mode,
            record_count=len(result.get("拉专属带领群_全部_所有记录") or []),
            new_numbers=new_numbers,
            dry_run=dry_run,
        )
        result["报告"] = report
        print(report)
        print()

        if new_numbers and not dry_run:
            print()
            print("[4/4] 写入新编号 {} 条 ...".format(len(new_numbers)))
            created = batch_create_records(token, new_numbers)
            result["已写入条数"] = created
            print("写入完成，共 {} 条。".format(created))
            if created <= 0:
                result["成功"] = False
                result["消息"] = "写入失败，未拉取整表"
            else:
                result["成功"] = True
        elif new_numbers and dry_run:
            result["成功"] = True
            result["消息"] = "预览完成，待新增 {} 条，未写入".format(len(new_numbers))
        else:
            result["成功"] = True
            result["消息"] = "无需补充编号"

        # 流程成功时，统一拉整表并写入 拉专属带领群_全部_所有记录
        if result.get("成功"):
            print()
            step_label = "[5/5]" if new_numbers and not dry_run else "[4/4]"
            print("{} 拉取整表并保存到 拉专属带领群_全部_所有记录 ...".format(step_label))
            wait_before = bool(new_numbers and not dry_run and result.get("已写入条数"))
            saved_records, saved_count = fetch_full_table_to_variable(
                token, wait_before=wait_before
            )
            result["记录条数"] = saved_count
            result["读取模式"] = "full"
            result["拉专属带领群_全部_所有记录"] = saved_records
            refreshed_numbers = _parse_numbers_from_records(saved_records)
            if refreshed_numbers:
                result["飞书最大编号"] = format_number(max(refreshed_numbers))
            if result.get("已写入条数"):
                result["消息"] = "已写入 {} 条编号，整表 {} 条已保存到变量".format(
                    result["已写入条数"], saved_count
                )
            elif new_numbers and dry_run:
                result["消息"] = "预览完成，待新增 {} 条，整表 {} 条已保存到变量".format(
                    len(new_numbers), saved_count
                )
            else:
                result["消息"] = "无需补充编号，整表 {} 条已保存到变量".format(saved_count)

    except Exception as exc:
        result["成功"] = False
        result["消息"] = str(exc)
        print("[错误] {}".format(exc))

    _sync_yingdao_result(result)
    return result


def run_compare_only(full_scan=False, write=True):
    # type: (bool, bool) -> Dict[str, Any]
    """
    前三步：查库 max -> 快速取飞书 max -> 对比；默认把多出来的编号写入飞书。

    write=False（--preview）时仅对比，不写入。
    """
    dry_run = not write
    result = {
        "成功": False,
        "消息": "",
        "报告": "",
        "数据库最大编号": None,
        "飞书最大编号": None,
        "待新增编号": [],
        "已写入条数": 0,
        "记录条数": 0,
        "读取模式": "",
    }

    try:
        print("=== 补充飞书对比（前三步）===")
        print("模式: {}".format("写入飞书" if write else "预览（不写入）"))
        print()

        print("[1/3] 查询 contact.db max_no ...")
        db_max = fetch_db_max_no()
        result["数据库最大编号"] = format_number(db_max) if db_max is not None else None
        print("  数据库 max_no = {}".format(result["数据库最大编号"] or "(无)"))
        print()

        print("[2/3] 快速获取飞书编号最大值 ...")
        token = get_tenant_access_token()
        feishu_max, number_count, _number_records = fetch_feishu_numbers_only(
            token, full_scan=full_scan
        )
        read_mode = "numbers_only" if full_scan else "search_numbers"
        result["飞书最大编号"] = format_number(feishu_max) if feishu_max is not None else None
        result["读取模式"] = read_mode
        print(
            "  飞书编号最大值 = {} （{}，表内约 {} 条）".format(
                result["飞书最大编号"] or "(无)",
                "全量编号列" if full_scan else "search 快速",
                number_count,
            )
        )
        print()

        print("[3/3] 对比{} ...".format("并写入飞书" if write else "并生成补充计划"))
        new_numbers = []
        if db_max is not None and feishu_max is not None:
            new_numbers = build_new_numbers(feishu_max, db_max)
        result["待新增编号"] = new_numbers

        report = build_report(
            db_max=db_max,
            feishu_max=feishu_max,
            feishu_stat=number_count,
            read_mode=read_mode,
            record_count=0,
            new_numbers=new_numbers,
            dry_run=dry_run,
        )
        result["报告"] = report
        print(report)
        print()

        if new_numbers and write:
            print("  待写入 {} 条：{}".format(
                len(new_numbers),
                "、".join(new_numbers[:5]) + (" ..." if len(new_numbers) > 5 else ""),
            ))
            created = batch_create_records(token, new_numbers)
            result["已写入条数"] = created
            print("  写入完成，共 {} 条。".format(created))
            result["成功"] = created > 0
            if created > 0:
                result["消息"] = "已对比并写入 {} 条编号到飞书".format(created)
            else:
                result["消息"] = "写入失败"
        elif new_numbers:
            result["成功"] = True
            result["消息"] = "对比完成，待新增 {} 条（预览，未写入）".format(len(new_numbers))
        else:
            result["成功"] = True
            result["消息"] = "对比完成，无需补充编号"

    except Exception as exc:
        result["成功"] = False
        result["消息"] = str(exc)
        print("[错误] {}".format(exc))

    return result


def 补充飞书推送3(是否写入=False, 是否全量扫描=False):
    # type: (bool, bool) -> Dict[str, Any]
    return run_supplement(write=是否写入, full_scan=是否全量扫描)


def main():
    # type: () -> int
    parser = argparse.ArgumentParser(description="补充飞书推送3：对比并补齐编号")
    parser.add_argument(
        "--write",
        action="store_true",
        help="实际写入飞书（默认仅预览，不写入）",
    )
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help="强制全量扫描飞书表（较慢，仅用于核对）",
    )
    args = parser.parse_args()
    result = run_supplement(write=args.write, full_scan=args.full_scan)
    if not result.get("成功"):
        return 1
    if result.get("待新增编号") and not args.write:
        print()
        print("当前为预览模式，未写入飞书。确认后把 是否写入 改为 True 再运行。")
    return 0


def _should_use_cli():
    # type: () -> bool
    """影刀直接运行时走顶部配置；只有命令行带参数时才用 argparse。"""
    if __name__ != "__main__":
        return False
    if not 影刀直接运行:
        return True
    return len(sys.argv) > 1


if __name__ == "__main__":
    if _should_use_cli():
        raise SystemExit(main())
    else:
        _ensure_yingdao_globals()
        执行结果 = run_supplement(write=是否写入, full_scan=是否全量扫描)
        # 影刀后续步骤需在本模块命名空间显式读取这些变量
        拉专属带领群_全部_所有记录 = list(执行结果.get("拉专属带领群_全部_所有记录") or [])
        待新增编号 = 执行结果.get("待新增编号") or []
        数据库最大编号 = 执行结果.get("数据库最大编号")
        飞书最大编号 = 执行结果.get("飞书最大编号")
        是否成功 = bool(执行结果.get("成功"))
        执行消息 = 执行结果.get("消息") or ""
