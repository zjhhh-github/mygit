# -*- coding: utf-8 -*-
"""
内部备注导入脚本（外置版，经 Edge Function 写入）
====================================================

架构（2026-08-07 改造：脚本不再直接读写数据库表）：

    contact.db
        ↓
    内部备注导入.py（本脚本）
        ↓
    Supabase Edge Function：import-internal-notes
        ↓
    internal_notes 表

脚本职责只有 4 件事：
    1. 读取本机微信 contact.db
    2. 生成接口需要的 JSON（中文字段）
    3. 调用 Edge Function import-internal-notes
    4. 打印 / 落盘导入结果

按微信原始ID匹配、新增大号、新增小号、更新微信号、冲突检测、
污染防护（微信号 == 微信原始ID 时自动清空微信号）等业务逻辑，
全部交给 Edge Function 处理，本脚本不重复实现，也不直接连表。

contact.db 字段映射（不变）：
    - username = 微信原始ID（唯一身份键，不可变）
    - alias     = 微信号（可变，可能为空——合法数据，不能过滤）

发给 Edge Function 的记录格式（中文字段）：
    {
      "内部备注": "¿¿¿011880-肖子辰",
      "微信原始ID": "wxid_wex1khezgs8k21",
      "微信号": "luke870420",
      "总微信号": "luke870420"
    }
    总微信号规则：微信号非空则用微信号，否则用微信原始ID。

为什么独立成一个脚本？
    - 让"导入控制台" exe 不再内嵌这套逻辑，**修改这个脚本不需要重新打包 exe**。
    - 控制台启动时通过 importlib 动态加载本脚本，调用下方 run_pipeline() 即可。
    - 也支持独立运行：把 内部备注导入.config.json 放到本脚本同目录，
      然后执行 `python 内部备注导入.py` 即可。

筛选规则（SQL WHERE）：
    备注必须满足下列任一前缀格式：
        ¿¿¿ + 6 位数字 + - + 任意尾段
        ¡¡¡ + 6 位数字 + - + 任意尾段
    且尾段（去空白后）不能是「空」或「删除」。
    Python 层会额外把备注尾部的「(非)/（非）」清洗掉。

上传前本地校验（_prepare_edge_candidates）：
    - 必须非空：内部备注、微信原始ID
    - 允许为空：微信号（合法数据，不能过滤）
    - 字段污染（微信号 == 微信原始ID）：只统计数量并打印日志，
      不清空、不修改、不丢弃——交给 Edge Function 自动清理

调用方负责注入：
    - supabase_url / anon_key ：Supabase 服务地址与匿名 key
    - get_token              ：返回当前可用 JWT 的回调
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional, Union

import requests


# ─────────────────────────────── 默认参数（可被 run_pipeline 覆盖）───────────────────────────────
DEFAULT_EDGE_BATCH_SIZE = 500         # 调用 Edge Function 每批条数
DEFAULT_BATCH_INTERVAL = 0.3          # 批与批之间的间隔（秒），缓解限流 / 抖动
DEFAULT_REQUEST_TIMEOUT = 120         # 单次 HTTP 请求超时（秒）
DEFAULT_IMPORT_FUNCTION = "import-internal-notes"  # Edge Function 名
DEFAULT_EDGE_CONCURRENCY = 1          # 同时发出的批次请求数，1 = 原来的串行行为

# 备注尾部「(非) / （非）」清洗正则
# 适配半角与全角两种括号，去掉后再 rstrip 清掉残余空白
_NON_SUFFIX_RE = re.compile(r"[（(]非[）)]\s*$")


# ─────────────────────────────── 工具函数 ───────────────────────────────
def _clean_remark(s: str) -> str:
    """清洗 remark 字段：去掉末尾的「(非)/（非）」标记。"""
    if not s:
        return s
    return _NON_SUFFIX_RE.sub("", s).rstrip()


def _is_field_polluted(wechat_id: str, wechat_original_id: str) -> bool:
    """字段污染：wechat_id（微信号）与 wechat_original_id（微信原始ID）均非空且相等。

    微信号和微信原始ID是两个不同维度的字段，正常情况下不应该相等；
    如果相等，大概率是采集/映射环节把同一个值塞进了两个字段，属于脏数据。

    注意：这里只用于本地统计污染数量并打印日志，不会因为污染而丢弃记录、
    也不会修改数据——真正的清理动作交给 Edge Function 自动处理。
    """
    wx = (wechat_id or "").strip()
    oid = (wechat_original_id or "").strip()
    if not wx or not oid:
        return False
    return wx.lower() == oid.lower()


def _build_session(pool_size: int = 4) -> requests.Session:
    """构建一个带连接池复用 + 自动重试的 Session，供调用 Edge Function 时复用连接。

    注意：read=0（读超时不做内部重试）。
    Edge Function 处理一批数据可能耗时几十秒到一百多秒，属于正常的"慢"，
    不是网络抽风。如果对读超时也自动重试，会出现"外层日志一直不打印，
    但其实 urllib3 在偷偷重试"的假死现象，还会让本已很慢的服务端再收到
    一次重复请求、雪上加霜。因此读超时直接失败，交给外层重试/汇总逻辑处理，
    这样超时时能第一时间看到日志，问题排查更直观。
    connect / status 出现网络抖动或 5xx 时仍保留少量自动重试。
    """
    session = requests.Session()
    try:
        from urllib3.util.retry import Retry
        retry = Retry(
            total=3, connect=3, read=0, status=3,
            backoff_factor=0.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST", "PATCH", "PUT", "DELETE"]),
            raise_on_status=False,
        )
        adapter = requests.adapters.HTTPAdapter(
            max_retries=retry, pool_connections=pool_size, pool_maxsize=pool_size,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    except Exception:
        # urllib3 版本差异时优雅回退：返回普通 Session（至少有 keep-alive）
        pass
    return session


def _login_supabase_token(
    *, supabase_url: str, anon_key: str, email: str, password: str,
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> str:
    """
    用邮箱 + 密码登录 Supabase，换取一个短期有效的 access_token（JWT）。

    独立运行本脚本时，静态 TOKEN 很容易过期，配置邮箱/密码后每次运行
    都会自动换取一个新的可用 token，不需要手动去控制台复制。

    入参：
        supabase_url / anon_key ：Supabase 项目地址与匿名 key
        email / password       ：登录账号（与"导入控制台"里使用的账号一致）
    返回：
        access_token 字符串
    异常：
        登录失败（账号密码错误、网络异常等）时抛出异常，交给调用方处理
    """
    url = f"{supabase_url.rstrip('/')}/auth/v1/token?grant_type=password"
    headers = {"apikey": anon_key, "Content-Type": "application/json"}
    payload = {"email": email, "password": password}
    resp = requests.post(url, json=payload, headers=headers, timeout=request_timeout)
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise ValueError("登录响应中未找到 access_token，请检查邮箱/密码或 ANON_KEY 是否正确")
    return token


def _get_logger(log) -> logging.Logger:
    """统一日志：调用方传 logger 直接用；传 None 时回退到模块默认 logger。"""
    if log is not None:
        return log
    logger = logging.getLogger("内部备注导入")
    if not logger.handlers:
        # 独立运行时给一个最小可读的控制台输出
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# ─────────────────────────────── 数据读取（contact.db → items）───────────────────────────────
_CONTACT_QUERY_SQL = """
    SELECT remark, username, alias
    FROM contact
    WHERE (
            remark GLOB '¿¿¿[0-9][0-9][0-9][0-9][0-9][0-9]-*'
            OR
            remark GLOB '!!![0-9][0-9][0-9][0-9][0-9][0-9]-*'
          )
      AND TRIM(SUBSTR(remark, 11)) NOT IN ('空', '删除')
    ORDER BY remark;
"""


def _normalize_db_paths(db_path: Union[str, list[str]]) -> list[str]:
    """把单路径、分号/竖线分隔串或路径列表统一成去重后的路径列表。"""
    if isinstance(db_path, list):
        raw_parts = db_path
    else:
        # 兼容更多分隔写法：分号/竖线/逗号/换行（含中文标点）
        raw_parts = re.split(r"[;|,\n，；]+", db_path or "")
    paths: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        # 兼容用户手工填写时带引号的情况：例如 "C:\a.db";"C:\b.db"
        p = (part or "").strip().strip('"').strip("'")
        if not p:
            continue
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            continue
        seen.add(key)
        paths.append(p)
    return paths


def _deduplicate_items(items: list, *, log=None) -> tuple[list, dict]:
    """多库读取合并后去重：先按 internal_note，再按 wechat_original_id（不区分大小写）。

    唯一身份键是 wechat_original_id（微信原始ID，不会变），wechat_id（微信号）允许为空，
    因此去重和"关键字段是否为空"的校验都必须以 wechat_original_id 为准，不能用 wechat_id。

    返回 (去重后列表, 统计 dict)，统计含 duplicate / dropped 供汇总日志使用。
    """
    log = _get_logger(log)
    dropped = 0
    dup_note = 0
    dup_oid = 0

    by_note: dict[str, dict] = {}
    for item in items:
        note = (item.get("internal_note") or "").strip()
        original_id = (item.get("wechat_original_id") or "").strip()
        if not note or not original_id:
            dropped += 1
            continue
        if note in by_note:
            dup_note += 1
            continue
        by_note[note] = item

    candidates: list = []
    seen_oid: set[str] = set()
    for note in sorted(by_note.keys()):
        item = by_note[note]
        oid_lc = (item.get("wechat_original_id") or "").strip().lower()
        if not oid_lc:
            dropped += 1
            continue
        if oid_lc in seen_oid:
            dup_oid += 1
            continue
        seen_oid.add(oid_lc)
        candidates.append(item)

    if dropped:
        log.warning(f"[内部备注] 合并去重跳过 {dropped} 条（关键字段为空）")
    if dup_note:
        log.info(f"[内部备注] 合并去重：相同 internal_note 去掉 {dup_note} 条")
    if dup_oid:
        log.info(f"[内部备注] 合并去重：相同 wechat_original_id 去掉 {dup_oid} 条")

    stats = {"duplicate": dup_note + dup_oid, "dropped": dropped}
    return sorted(candidates, key=lambda r: r.get("internal_note") or ""), stats


def _build_item_from_contact_row(remark: str, username: str, alias: str) -> dict:
    """把 contact.db 一行数据映射为待上传 item（内部用英文键，方便本地处理）。

    contact.db 字段含义：
        - username = 微信原始ID（永久不变，唯一身份键）
        - alias     = 微信号（可变，可能为空）

    映射规则（不修改）：
        - wechat_id           = alias（微信号）
        - wechat_original_id  = username（微信原始ID，唯一身份键）
        - wechat_number       = username（旧兼容字段，与 wechat_original_id 保持一致）

    _zh 是给人工预览用的中文键快照（写入 out_json），跟真正发给
    Edge Function 的字段（见 _edge_payload_fields）含义一致，但这里的
    "总微信号"只是本地预估展示，最终结果以 Edge Function 返回为准。

    单独抽成这个纯函数是为了方便离线单测（不依赖 sqlite 文件）。
    """
    remark_clean = _clean_remark(remark or "")
    username_val = (username or "").strip()
    alias_val = (alias or "").strip()
    estimated_total = alias_val if alias_val else username_val
    return {
        "internal_note": remark_clean,
        "wechat_id": alias_val,
        "wechat_original_id": username_val,
        "wechat_number": username_val,
        "_zh": {
            "内部备注": remark_clean,
            "微信号": alias_val,
            "微信原始ID": username_val,
            "总微信号(预估)": estimated_total,
        },
    }


def _load_single_db(db_path: str) -> list:
    """从单个 contact.db 读取符合规则的内部备注。"""
    db_path = (db_path or "").strip().strip('"').strip("'")
    if os.path.isdir(db_path):
        raise IsADirectoryError(f"数据库路径指向了目录而不是文件：{db_path}")
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"未找到数据库文件：{db_path}")

    # Windows 下如果文件正被定时拷贝覆盖，sqlite3.connect 可能短暂报
    # "unable to open database file"。这里做小次数重试，降低偶发失败。
    last_exc: Exception | None = None
    conn = None
    for _ in range(5):
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            break
        except sqlite3.OperationalError as exc:
            last_exc = exc
            time.sleep(0.6)
    if conn is None:
        raise sqlite3.OperationalError(
            f"无法打开数据库文件：{db_path}；请检查路径是否可访问，或稍后重试。原始错误：{last_exc}"
        )
    conn.text_factory = str
    try:
        cur = conn.cursor()
        cur.execute(_CONTACT_QUERY_SQL)
        rows = cur.fetchall()
    finally:
        conn.close()

    items: list = [
        _build_item_from_contact_row(remark, username, alias)
        for remark, username, alias in rows
    ]
    return items


def load_from_db(db_path: Union[str, list[str]], *, log=None) -> tuple[list, dict]:
    """
    从一个或多个 contact.db 读取符合规则的内部备注，合并后先去重再返回。

    返回值：
        (items, stats)
        items: list[dict]，字段映射见 _load_single_db
        stats: {"duplicate": int, "dropped": int} 合并去重统计
    """
    paths = _normalize_db_paths(db_path)
    if not paths:
        raise ValueError("未提供有效的数据库路径")

    all_items: list = []
    for path in paths:
        all_items.extend(_load_single_db(path))

    return _deduplicate_items(all_items, log=log)


def dump_json(items: list, out_path: str) -> None:
    """导出中文键 JSON 快照，便于人工肉眼检查本次将上传的数据。"""
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump([r["_zh"] for r in items], f, ensure_ascii=False, indent=2)


# ─────────────────────────────── Edge Function payload 工厂 ───────────────────────────────
def _edge_payload_fields(r: dict) -> dict:
    """把内部 item（英文键）转换成 Edge Function 需要的中文字段格式。

    示例：
        contact.db: username=wxid_wex1khezgs8k21, alias=luke870420
        → {"内部备注": "...", "微信原始ID": "wxid_wex1khezgs8k21",
           "微信号": "luke870420", "总微信号": "luke870420"}

        contact.db: username=kangye9876, alias=""（微信号为空，合法数据）
        → {"内部备注": "...", "微信原始ID": "kangye9876",
           "微信号": "", "总微信号": "kangye9876"}
    """
    wechat_id = r.get("wechat_id") or ""
    wechat_original_id = r.get("wechat_original_id") or ""
    return {
        "内部备注": r.get("internal_note") or "",
        "微信原始ID": wechat_original_id,
        "微信号": wechat_id,
        "总微信号": wechat_id if wechat_id else wechat_original_id,
    }


def _prepare_edge_candidates(rows: list, *, log) -> tuple[list, dict]:
    """上传前本地去重 + 字段污染统计（不因污染丢弃记录，也不修改数据）。

    校验规则（唯一身份键是 wechat_original_id，不是 wechat_id）：
        - 必须非空：internal_note、wechat_original_id
        - 允许为空：wechat_id（微信号）——合法数据，不能过滤
        - 字段污染：wechat_id 与 wechat_original_id 均非空且相等——只统计数量、
          打印日志，不清空、不修改、不丢弃，交给 Edge Function 自动清理

    返回 (candidates, stats)，stats 含 duplicate / polluted / dropped。
    """
    local_by_note: dict = {}
    local_dropped = 0
    local_dup_note = 0
    polluted = 0

    for r in rows:
        wxid = (r.get("wechat_id") or "").strip()
        original_id = (r.get("wechat_original_id") or "").strip()
        note = (r.get("internal_note") or "").strip()

        if not note or not original_id:
            local_dropped += 1
            continue
        if _is_field_polluted(wxid, original_id):
            polluted += 1
            log.warning(
                f"[内部备注][字段污染] internal_note='{note}' "
                f"wechat_id='{wxid}' wechat_original_id='{original_id}' "
                f"两者非空且相等；不修改数据，交给 Edge Function 自动清理"
            )
        if note in local_by_note:
            local_dup_note += 1
            continue
        local_by_note[note] = r

    candidates: list = []
    seen_oid: set[str] = set()
    local_dup_oid = 0
    for note in sorted(local_by_note.keys()):
        r = local_by_note[note]
        oid_lc = (r.get("wechat_original_id") or "").strip().lower()
        if oid_lc in seen_oid:
            local_dup_oid += 1
            continue
        seen_oid.add(oid_lc)
        candidates.append(r)

    if local_dropped:
        log.warning(f"[内部备注] 上传前跳过 {local_dropped} 条（关键字段为空）")
    if local_dup_note:
        log.info(f"[内部备注] 上传前相同 internal_note 去重 {local_dup_note} 条")
    if local_dup_oid:
        log.info(f"[内部备注] 上传前相同 wechat_original_id 去重 {local_dup_oid} 条")
    if polluted:
        log.warning(
            f"[内部备注] 发现字段污染 {polluted} 条（微信号 == 微信原始ID），"
            f"已随批次一起上传，由 Edge Function 自动清理"
        )

    duplicate = local_dup_note + local_dup_oid
    stats = {"duplicate": duplicate, "polluted": polluted, "dropped": local_dropped}
    return candidates, stats


# ─────────────────────────────── Edge Function 调用 ───────────────────────────────
# Edge Function 返回的 summary 字段（英文键，与接口约定一致）及对应中文展示文案
_SUMMARY_KEYS = [
    "total",
    "new_main",
    "new_sub",
    "matched_by_original",
    "wechat_id_updated",
    "auto_converted_sub",
    "conflict",
    "skipped",
    "error",
]
_SUMMARY_LABELS = {
    "total": "总数量",
    "new_main": "新增大号",
    "new_sub": "新增小号",
    "matched_by_original": "按原始ID匹配",
    "wechat_id_updated": "微信号更新",
    "auto_converted_sub": "自动转换小号",
    "conflict": "冲突",
    "skipped": "跳过",
    "error": "错误",
}


def _print_edge_summary(log, aggregated: dict, *, polluted: int, duplicate: int) -> None:
    """按约定格式打印 Edge Function 导入汇总（多批结果已聚合）。"""
    log.info("=" * 40)
    log.info("导入完成")
    for key in _SUMMARY_KEYS:
        log.info(f"{_SUMMARY_LABELS[key]}: {aggregated.get(key, 0)}")
    log.info(f"字段污染(交由Edge Function自动清理): {polluted}")
    log.info(f"本地重复(未上传): {duplicate}")


def _post_one_batch(
    session: "requests.Session",
    url: str,
    headers: dict,
    batch_rows: list,
    *,
    batch_index: int,
    total_batches: int,
    request_timeout: int,
    log,
) -> dict:
    """发送单个批次到 Edge Function，返回这一批的原始结果（不做汇总，汇总交给主线程做）。

    之所以把"发请求"和"汇总结果"拆开：并发发送多个批次时，每个批次跑在
    不同的线程里，如果在线程里直接去改 aggregated/all_details 这些共享字典，
    多线程同时写同一个 dict 容易出问题；所以这里只负责请求 + 解析，
    返回结果后统一回到主线程里串行汇总，既能并发提速，又不用担心线程安全。
    """
    payload_rows = [_edge_payload_fields(r) for r in batch_rows]
    log.info(f"[Edge Function] 批次: {batch_index + 1}/{total_batches}  数量: {len(batch_rows)}")
    t0 = time.time()
    try:
        resp = session.post(
            url,
            json={"data": payload_rows},
            headers=headers,
            timeout=request_timeout,
        )
        elapsed = time.time() - t0
        try:
            body = resp.json()
        except ValueError:
            body = None
        return {
            "index": batch_index, "batch_len": len(batch_rows), "elapsed": elapsed,
            "status_code": resp.status_code, "text": resp.text, "body": body, "exc": None,
        }
    except requests.RequestException as e:
        elapsed = time.time() - t0
        return {
            "index": batch_index, "batch_len": len(batch_rows), "elapsed": elapsed,
            "status_code": None, "text": None, "body": None, "exc": e,
        }


def _apply_batch_result(
    result: dict, *, total_batches: int, aggregated: dict, all_details: list, log,
) -> bool:
    """把单个批次的原始结果合并进总汇总（aggregated/all_details），并打印日志。

    返回值：这一批是否成功（False 会让整体 overall_success 变成 False）。
    """
    i = result["index"]
    batch_len = result["batch_len"]
    tag = f"[Edge Function][批次 {i + 1}/{total_batches}]"

    if result["exc"] is not None:
        aggregated["error"] += batch_len
        log.warning(f"{tag} 异常 {result['exc']}")
        return False

    status_code = result["status_code"]
    body = result["body"]
    if not (200 <= status_code < 300) or not isinstance(body, dict):
        aggregated["error"] += batch_len
        log.warning(f"{tag} HTTP {status_code}: {(result['text'] or '')[:300]}")
        return False

    ok = bool(body.get("success"))
    summary = body.get("summary") or {}
    for k in _SUMMARY_KEYS:
        aggregated[k] += int(summary.get(k) or 0)
    details = body.get("details")
    if isinstance(details, list):
        all_details.extend(details)

    if not ok:
        log.warning(f"{tag} success=false，body={body}")
        return False

    log.info(f"{tag} OK，耗时 {result['elapsed']:.2f}s，summary={summary}")
    return True


def _edge_function_upload(
    candidates: list,
    token: str,
    *,
    supabase_url: str,
    anon_key: str,
    import_function: str,
    batch_size: int,
    batch_interval: float,
    request_timeout: int,
    details_out_path: Optional[str] = None,
    log,
    polluted: int = 0,
    duplicate: int = 0,
    concurrency: int = DEFAULT_EDGE_CONCURRENCY,
) -> dict:
    """把候选记录分批 POST 给 Edge Function import-internal-notes。

    请求：
        POST {supabase_url}/functions/v1/{import_function}
        Headers: Authorization: Bearer {token} / apikey: {anon_key} /
                 Content-Type: application/json
        Body:    {"data": [ {"内部备注":..., "微信原始ID":..., "微信号":..., "总微信号":...}, ... ]}

    Edge Function 已经负责：按微信原始ID匹配 / 新增大号 / 新增小号 /
    更新微信号 / 冲突检测 / 污染防护，本函数只管发请求、聚合多批返回的
    summary、打印结果、把 details 落盘，不做任何业务判断。

    concurrency（并发批次数）：
        Edge Function 目前是逐条处理记录的，量大时单批耗时可能到一两分钟，
        如果一批批串行等，总耗时会随批次数线性叠加。concurrency > 1 时会
        同时发出多个批次的请求，让 Edge Function 并行处理多批，从而缩短
        总耗时。默认值较保守（见 DEFAULT_EDGE_CONCURRENCY），如担心把服务端
        压垮，可以在配置文件里把 EDGE_CONCURRENCY 调回 1（等价于原来的串行行为）。
    """
    aggregated = {k: 0 for k in _SUMMARY_KEYS}
    all_details: list = []
    overall_success = True

    if not candidates:
        log.info("[Edge Function] 无可上传记录")
        _print_edge_summary(log, aggregated, polluted=polluted, duplicate=duplicate)
        return {
            "success": True,
            "summary": aggregated,
            "polluted": polluted,
            "duplicate": duplicate,
        }

    url = f"{supabase_url.rstrip('/')}/functions/v1/{import_function}"
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": anon_key,
        "Content-Type": "application/json",
    }

    size = max(1, int(batch_size))
    total = len(candidates)
    total_batches = max(1, math.ceil(total / size))
    concurrency = max(1, int(concurrency))
    session = _build_session(pool_size=max(4, concurrency))

    if concurrency <= 1:
        # 并发数为 1：保持原来的串行行为，逐批发送、批间可选间隔，最省资源、最稳妥
        for i in range(total_batches):
            batch = candidates[i * size : (i + 1) * size]
            result = _post_one_batch(
                session, url, headers, batch,
                batch_index=i, total_batches=total_batches,
                request_timeout=request_timeout, log=log,
            )
            ok = _apply_batch_result(
                result, total_batches=total_batches,
                aggregated=aggregated, all_details=all_details, log=log,
            )
            if not ok:
                overall_success = False
            if i < total_batches - 1:
                time.sleep(batch_interval)
    else:
        # 并发数 > 1：同时发出多个批次的请求，让 Edge Function 并行处理，缩短总耗时。
        # 每个批次结果收集回主线程后再统一汇总，避免多线程同时写共享字典。
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(
                    _post_one_batch, session, url, headers,
                    candidates[i * size : (i + 1) * size],
                    batch_index=i, total_batches=total_batches,
                    request_timeout=request_timeout, log=log,
                )
                for i in range(total_batches)
            ]
            for future in as_completed(futures):
                result = future.result()
                ok = _apply_batch_result(
                    result, total_batches=total_batches,
                    aggregated=aggregated, all_details=all_details, log=log,
                )
                if not ok:
                    overall_success = False

    if details_out_path and all_details:
        try:
            Path(details_out_path).write_text(
                json.dumps(all_details, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log.info(f"[Edge Function] details 已写入：{details_out_path}（共 {len(all_details)} 条）")
        except Exception as e:
            log.warning(f"[Edge Function] details 写入失败：{e}")

    _print_edge_summary(log, aggregated, polluted=polluted, duplicate=duplicate)
    return {
        "success": overall_success,
        "summary": aggregated,
        "polluted": polluted,
        "duplicate": duplicate,
        "details_count": len(all_details),
    }


def _default_details_path(out_json: str) -> str:
    """按 out_json 派生一个 details 落盘路径：xxx_result.json → xxx_result_edge_details.json。"""
    p = Path(out_json)
    return str(p.with_name(p.stem + "_edge_details" + p.suffix))


# ─────────────────────────────── 对外入口 ───────────────────────────────
def run_pipeline(
    *,
    db_path: Union[str, list[str]],
    out_json: str,
    upload: bool = True,
    upload_mode: Optional[str] = None,
    supabase_url: Optional[str] = None,
    anon_key: Optional[str] = None,
    get_token: Optional[Callable[[], str]] = None,
    batch_size: int = DEFAULT_EDGE_BATCH_SIZE,
    batch_interval: float = DEFAULT_BATCH_INTERVAL,
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
    concurrency: int = DEFAULT_EDGE_CONCURRENCY,
    table_name: Optional[str] = None,
    conflict_column: Optional[str] = None,
    import_function: str = DEFAULT_IMPORT_FUNCTION,
    details_out_json: Optional[str] = None,
    log=None,
) -> bool:
    """
    内部备注导入流水线主入口（经 Edge Function 写入，脚本不直接操作表）。

    流程：
        1. load_from_db(db_path)              从一个或多个 contact.db 读取并合并备注
        2. dump_json(items, out_json)          导出一份中文键 JSON 快照（人工可读）
        3. if not upload  → 结束（仅导出模式）
        4. _prepare_edge_candidates(items)     本地去重 + 统计字段污染（不丢弃、不改数据）
        5. _edge_function_upload(candidates)   分批调用 Edge Function，业务判断全部交给它

    兼容说明：
        upload_mode / table_name / conflict_column 三个参数已不再使用
        （旧版直接操作数据库表的逻辑已删除），仅保留在签名里避免破坏
        导入控制台.py 现有的调用方式；继续照旧传参不会报错，但不会生效。

    返回：True 表示流水线跑完且 Edge Function 全部批次 success=true；
         业务上不一定 100% 全部记录成功（个别冲突 / 跳过属于正常情况），
         具体数字见日志汇总和返回的 summary。
    """
    _ = (upload_mode, table_name, conflict_column)  # 已废弃，仅保留兼容旧调用签名
    log = _get_logger(log)
    try:
        log.info("=" * 60)
        log.info("内部备注导入流水线启动（外置脚本，经 Edge Function 写入）")
        db_paths = _normalize_db_paths(db_path)
        log.info(f"[内部备注] 数据源：{len(db_paths)} 个数据库")
        for p in db_paths:
            log.info(f"  - {p}")
        items, load_stats = load_from_db(db_paths, log=log)
        merge_duplicate = load_stats.get("duplicate", 0)
        log.info(f"[内部备注] 双库读取合并去重后 {len(items)} 条（合并阶段重复 {merge_duplicate} 条）")
        dump_json(items, out_json)
        log.info(f"[内部备注] JSON 已导出：{out_json}")

        if not upload:
            log.info("[内部备注] 仅导出，已跳过上传")
            return True
        if not items:
            log.info("[内部备注] 无数据可上传")
            return True

        if get_token is None or not supabase_url or not anon_key:
            raise RuntimeError(
                "上传需要 supabase_url / anon_key / get_token 三个参数，请由调用方注入"
            )

        candidates, prep_stats = _prepare_edge_candidates(items, log=log)
        duplicate = merge_duplicate + prep_stats["duplicate"]

        token = get_token()
        result = _edge_function_upload(
            candidates, token,
            supabase_url=supabase_url, anon_key=anon_key,
            import_function=import_function,
            batch_size=batch_size, batch_interval=batch_interval,
            request_timeout=request_timeout,
            details_out_path=details_out_json or _default_details_path(out_json),
            log=log,
            polluted=prep_stats["polluted"],
            duplicate=duplicate,
            concurrency=concurrency,
        )
        return bool(result.get("success"))
    except Exception as e:
        log.error(f"[内部备注] 流水线失败：{e}", exc_info=True)
        return False


# ─────────────────────────────── 独立运行入口 ───────────────────────────────
def main() -> None:
    """
    独立运行：读取脚本同目录下的 内部备注导入.config.json 取参数后跑一遍。

    配置文件示例：
        {
            "DB_PATH":           "C:/Users/LENOVO/Desktop/contact_内部专用.db;C:/Users/LENOVO/Desktop/contact_内部专用2.db",
            "DB_PATHS":          ["C:/Users/LENOVO/Desktop/contact_内部专用.db", "C:/Users/LENOVO/Desktop/contact_内部专用2.db"],
            "OUT_JSON":          "C:/Users/LENOVO/Desktop/contact_result.json",
            "UPLOAD":            true,
            "UPLOAD_MODE":       "edge",
            "IMPORT_FUNCTION":   "import-internal-notes",
            "SUPABASE_URL":      "https://xxx.supabase.co",
            "ANON_KEY":          "...",
            "TOKEN":             "...",        // 可选；填了就直接用，优先级最高
            "EMAIL":             "...",        // 可选；TOKEN 为空时，用邮箱+密码自动登录换取 token
            "PASSWORD":          "...",        // 可选；与 EMAIL 搭配使用
            "EDGE_BATCH_SIZE":   500,
            "EDGE_CONCURRENCY":  3,          // 同时发出的批次请求数，1=串行（默认更稳），>1 能明显提速
            "BATCH_INTERVAL":    0.3,
            "REQUEST_TIMEOUT":   120
        }

    Token 获取优先级：
        1. TOKEN 非空 → 直接用（注意 Supabase 的 JWT 一般几小时后过期，过期需手动更新）
        2. TOKEN 为空、EMAIL + PASSWORD 都非空 → 自动登录换取一个新 token（推荐，免维护）
        3. 都为空 → 仅导出 JSON，跳过上传
    """
    cfg_path = Path(__file__).resolve().parent / "内部备注导入.config.json"
    if not cfg_path.exists():
        print(f"[内部备注] 未找到配置文件：{cfg_path}")
        print("如需独立运行，请在脚本同目录下创建该文件（见 main() 注释）")
        return
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    supabase_url = cfg.get("SUPABASE_URL")
    anon_key = cfg.get("ANON_KEY")
    request_timeout = int(cfg.get("REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT))

    token_val = (cfg.get("TOKEN") or "").strip()
    email_val = (cfg.get("EMAIL") or "").strip()
    password_val = (cfg.get("PASSWORD") or "").strip()

    get_token_fn: Optional[Callable[[], str]] = None
    if token_val:
        # 优先级 1：配置里直接给了 token，原样使用
        get_token_fn = lambda: token_val
    elif email_val and password_val and supabase_url and anon_key:
        # 优先级 2：用邮箱 + 密码自动登录换取 token，避免手动维护短期有效的 TOKEN
        def get_token_fn():
            print("[内部备注] TOKEN 为空，正在用 EMAIL/PASSWORD 自动登录换取 token ...")
            return _login_supabase_token(
                supabase_url=supabase_url, anon_key=anon_key,
                email=email_val, password=password_val,
                request_timeout=request_timeout,
            )

    db_cfg = cfg.get("DB_PATHS") or cfg.get("DB_PATH")
    ok = run_pipeline(
        db_path=db_cfg,
        out_json=cfg["OUT_JSON"],
        upload=bool(cfg.get("UPLOAD", True)) and get_token_fn is not None,
        upload_mode=cfg.get("UPLOAD_MODE", "edge"),
        supabase_url=supabase_url,
        anon_key=anon_key,
        get_token=get_token_fn,
        batch_size=int(cfg.get("EDGE_BATCH_SIZE", DEFAULT_EDGE_BATCH_SIZE)),
        batch_interval=float(cfg.get("BATCH_INTERVAL", DEFAULT_BATCH_INTERVAL)),
        request_timeout=request_timeout,
        concurrency=int(cfg.get("EDGE_CONCURRENCY", DEFAULT_EDGE_CONCURRENCY)),
        import_function=cfg.get("IMPORT_FUNCTION", DEFAULT_IMPORT_FUNCTION),
        log=None,
    )
    print("OK" if ok else "FAIL")


if __name__ == "__main__":
    main()
