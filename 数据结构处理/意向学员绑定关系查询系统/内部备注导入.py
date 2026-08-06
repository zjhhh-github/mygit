# -*- coding: utf-8 -*-
"""
内部备注导入脚本（外置版）
==========================

职责：
    从本机微信 contact.db 读取符合规则的「内部备注」，写入 Supabase 的
    internal_notes 表。

字段语义（与库表 chk_wechat_id_not_equal_number 一致）：
    - wechat_id           = 微信原始 ID（username，永久身份）
    - wechat_number       = 微信号（alias，可变化）
    - total_wechat_number = alias 存在时用 alias，否则用 username
    - 禁止 wechat_id 与 wechat_number 非空且相等（字段污染，不允许导入）

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

上传规则（postgrest，默认）：
    匹配优先级：
        1. wechat_id（微信原始 ID）→ POST upsert on_conflict=wechat_id
        2. wechat_number（微信号）  → PATCH 按远端行 id 更新（纠正历史脏数据）
        3. 均未命中               → INSERT
    禁止用微信号覆盖微信原始 ID；payload 中 wechat_id 始终来自 username。

    edge 模式：走 /functions/v1/import-internal-notes（备用通路）。

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
from typing import Callable, Optional, Union

import requests


# ─────────────────────────────── 默认参数（可被 run_pipeline 覆盖）───────────────────────────────
DEFAULT_BATCH_SIZE = 100              # 批量 UPSERT / Edge Function 每批条数
DEFAULT_BATCH_INTERVAL = 0.3          # 批与批之间的间隔（秒），缓解限流 / 抖动
DEFAULT_REQUEST_TIMEOUT = 120         # 单次 HTTP 请求超时（秒）
DEFAULT_TABLE_NAME = "internal_notes" # 远端表名
DEFAULT_CONFLICT_COLUMN = "wechat_id" # UPSERT 冲突列（唯一约束所在列）
DEFAULT_IMPORT_FUNCTION = "import-internal-notes"  # Edge Function 名

# 备注尾部「(非) / （非）」清洗正则
# 适配半角与全角两种括号，去掉后再 rstrip 清掉残余空白
_NON_SUFFIX_RE = re.compile(r"[（(]非[）)]\s*$")


# ─────────────────────────────── 工具函数 ───────────────────────────────
def _clean_remark(s: str) -> str:
    """清洗 remark 字段：去掉末尾的「(非)/（非）」标记。"""
    if not s:
        return s
    return _NON_SUFFIX_RE.sub("", s).rstrip()


def _is_field_polluted(wechat_id: str, wechat_number: str) -> bool:
    """字段污染：wechat_id 与 wechat_number 均非空且相等（违反库表 CHECK 约束）。

    不做 wxid_ 前缀等格式猜测；仅比较两字段是否非空且相同（大小写不敏感）。
    """
    wx = (wechat_id or "").strip()
    num = (wechat_number or "").strip()
    if not wx or not num:
        return False
    return wx.lower() == num.lower()


def _build_session() -> requests.Session:
    """构建一个带连接池复用 + 自动重试的 Session。

    背景：旧版逐条 PATCH 大量请求时偶发 SSLEOFError / 连接被对端复位，
    用 urllib3.Retry 加上 3 次指数退避，提高稳定性。
    """
    session = requests.Session()
    try:
        from urllib3.util.retry import Retry
        retry = Retry(
            total=3, connect=3, read=3, status=3,
            backoff_factor=0.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST", "PATCH", "PUT", "DELETE"]),
            raise_on_status=False,
        )
        adapter = requests.adapters.HTTPAdapter(
            max_retries=retry, pool_connections=4, pool_maxsize=4,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    except Exception:
        # urllib3 版本差异时优雅回退：返回普通 Session（至少有 keep-alive）
        pass
    return session


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
    """多库读取合并后去重：先按 internal_note，再按 wechat_id（不区分大小写）。

    返回 (去重后列表, 统计 dict)，统计含 duplicate / dropped 供汇总日志使用。
    """
    log = _get_logger(log)
    dropped = 0
    dup_note = 0
    dup_wxid = 0

    by_note: dict[str, dict] = {}
    for item in items:
        note = (item.get("internal_note") or "").strip()
        wxid = (item.get("wechat_id") or "").strip()
        total = (item.get("total_wechat_number") or "").strip()
        if not note or not wxid or not total:
            dropped += 1
            continue
        if note in by_note:
            dup_note += 1
            continue
        by_note[note] = item

    candidates: list = []
    seen_wxid: set[str] = set()
    for note in sorted(by_note.keys()):
        item = by_note[note]
        wxid_lc = (item.get("wechat_id") or "").strip().lower()
        if not wxid_lc:
            dropped += 1
            continue
        if wxid_lc in seen_wxid:
            dup_wxid += 1
            continue
        seen_wxid.add(wxid_lc)
        candidates.append(item)

    if dropped:
        log.warning(f"[内部备注] 合并去重跳过 {dropped} 条（关键字段为空）")
    if dup_note:
        log.info(f"[内部备注] 合并去重：相同 internal_note 去掉 {dup_note} 条")
    if dup_wxid:
        log.info(f"[内部备注] 合并去重：相同 wechat_id 去掉 {dup_wxid} 条")

    stats = {"duplicate": dup_note + dup_wxid, "dropped": dropped}
    return sorted(candidates, key=lambda r: r.get("internal_note") or ""), stats


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

    items: list = []
    for remark, username, alias in rows:
        remark_clean = _clean_remark(remark or "")
        username_val = (username or "").strip()
        alias_val = (alias or "").strip()
        # contact.db：username=微信原始ID，alias=微信号
        # wechat_id 必须用 username，禁止把 alias 写入 wechat_id
        total = alias_val if alias_val else username_val
        items.append({
            "internal_note": remark_clean,
            "wechat_id": username_val,
            "wechat_number": alias_val,
            "total_wechat_number": total,
            "_zh": {
                "内部备注": remark_clean,
                "微信原始ID": username_val,
                "微信号": alias_val,
                "总微信号": total,
            },
        })
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


# ─────────────────────────────── 上传 payload 工厂 ───────────────────────────────
def _insert_payload(r: dict) -> dict:
    """生成发给 PostgREST 的标准记录；wechat_id 必须为微信原始 ID（username）。"""
    return {
        "internal_note": r["internal_note"],
        "wechat_id": r["wechat_id"],
        "wechat_number": r["wechat_number"],
        "total_wechat_number": r["total_wechat_number"],
    }


# ─────────────────────────────── 远端索引拉取 ───────────────────────────────
def _pull_existing(
    token: str,
    *,
    supabase_url: str,
    anon_key: str,
    table_name: str,
    request_timeout: int,
    log,
) -> tuple[dict, dict]:
    """分页拉取远端 internal_notes，构建两个匹配索引（值均为远端行 id）：

    - by_wxid   : { wechat_id_lower: id }      第一优先匹配键
    - by_number : { wechat_number_lower: id }  第二优先匹配键（微信号）
    """
    base = f"{supabase_url}/rest/v1/{table_name}"
    url = f"{base}?select=id,wechat_id,wechat_number&order=id.asc"
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    page_size = 1000
    by_wxid: dict = {}
    by_number: dict = {}
    offset = 0
    total_rows = 0
    while True:
        h = dict(headers)
        h["Range-Unit"] = "items"
        h["Range"] = f"{offset}-{offset + page_size - 1}"
        resp = requests.get(url, headers=h, timeout=request_timeout)
        if resp.status_code not in (200, 206):
            raise RuntimeError(
                f"拉取远端 {table_name} 失败 HTTP {resp.status_code}: "
                f"{(resp.text or '')[:200]}"
            )
        batch = resp.json() or []
        if not batch:
            break
        for item in batch:
            rid = item.get("id")
            if rid is None:
                continue
            total_rows += 1
            wxid = (item.get("wechat_id") or "").strip()
            num = (item.get("wechat_number") or "").strip()
            if wxid:
                by_wxid.setdefault(wxid.lower(), rid)
            if num:
                by_number.setdefault(num.lower(), rid)
        if len(batch) < page_size:
            break
        offset += page_size

    log.info(
        f"[内部备注] 远端已有 {total_rows} 条，"
        f"wechat_id 索引 {len(by_wxid)}，wechat_number 索引 {len(by_number)}"
    )
    return by_wxid, by_number


def _resolve_remote_match(
    r: dict,
    by_wxid: dict,
    by_number: dict,
) -> tuple[str, object]:
    """决定本条记录的远端匹配方式。

    返回 (mode, remote_id)：
        - ("upsert", None)       按 wechat_id 命中或全新插入 → POST upsert
        - ("patch",  remote_id)  仅按 wechat_number 命中     → PATCH 按 id 更新
    """
    wxid_lc = (r.get("wechat_id") or "").strip().lower()
    num_lc = (r.get("wechat_number") or "").strip().lower()

    if wxid_lc and wxid_lc in by_wxid:
        return "upsert", None
    if num_lc and num_lc in by_number:
        return "patch", by_number[num_lc]
    return "upsert", None


def _prepare_upload_candidates(
    rows: list,
    *,
    log,
) -> tuple[list, dict]:
    """上传前本地去重 + 字段污染校验。

    返回 (candidates, stats)，stats 含 duplicate / polluted / dropped。
    """
    local_by_note: dict = {}
    local_dropped = 0
    local_dup_note = 0
    polluted = 0

    for r in rows:
        wxid = (r.get("wechat_id") or "").strip()
        total = (r.get("total_wechat_number") or "").strip()
        note = (r.get("internal_note") or "").strip()
        number = (r.get("wechat_number") or "").strip()

        if not note or not wxid or not total:
            local_dropped += 1
            continue
        if _is_field_polluted(wxid, number):
            polluted += 1
            log.warning(
                f"[内部备注][字段污染] internal_note='{note}' "
                f"wechat_id='{wxid}' wechat_number='{number}' "
                f"两者非空且相等，已跳过"
            )
            continue
        if note in local_by_note:
            local_dup_note += 1
            continue
        local_by_note[note] = r

    candidates: list = []
    seen_wxid: set[str] = set()
    local_dup_wxid = 0
    for note in sorted(local_by_note.keys()):
        r = local_by_note[note]
        wxid_lc = (r.get("wechat_id") or "").strip().lower()
        if not wxid_lc:
            local_dropped += 1
            continue
        if wxid_lc in seen_wxid:
            local_dup_wxid += 1
            continue
        seen_wxid.add(wxid_lc)
        candidates.append(r)

    if local_dropped:
        log.warning(f"[内部备注] 上传前跳过 {local_dropped} 条（关键字段为空）")
    if local_dup_note:
        log.info(f"[内部备注] 上传前相同 internal_note 去重 {local_dup_note} 条")
    if local_dup_wxid:
        log.info(f"[内部备注] 上传前相同 wechat_id 去重 {local_dup_wxid} 条")

    duplicate = local_dup_note + local_dup_wxid
    stats = {"duplicate": duplicate, "polluted": polluted, "dropped": local_dropped}
    return candidates, stats


# ─────────────────────────────── 增量 UPSERT 主流程 ───────────────────────────────
def _sync_increment(
    rows: list,
    token: str,
    *,
    supabase_url: str,
    anon_key: str,
    table_name: str,
    conflict_column: str,
    batch_size: int,
    batch_interval: float,
    request_timeout: int,
    log,
    merge_duplicate: int = 0,
) -> dict:
    """按 wechat_id 优先、wechat_number 次之写入 internal_notes。

    - 第一优先 wechat_id：POST upsert（on_conflict=wechat_id）
    - 第二优先 wechat_number：PATCH 按远端 id（payload 中 wechat_id 仍为 username）
    - 导入前拦截字段污染（wechat_id == wechat_number 且均非空）
    """
    base = f"{supabase_url}/rest/v1/{table_name}"
    common_headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    session = _build_session()

    candidates, prep_stats = _prepare_upload_candidates(rows, log=log)
    polluted = prep_stats["polluted"]
    duplicate = merge_duplicate + prep_stats["duplicate"]

    if not candidates:
        log.info("[内部备注] 无可上传记录")
        _log_import_summary(log, success=0, failed=0, polluted=polluted, duplicate=duplicate)
        return {
            "success": 0,
            "failed": 0,
            "polluted": polluted,
            "duplicate": duplicate,
        }

    try:
        by_wxid, by_number = _pull_existing(
            token,
            supabase_url=supabase_url,
            anon_key=anon_key,
            table_name=table_name,
            request_timeout=request_timeout,
            log=log,
        )
    except Exception as e:
        log.warning(f"[内部备注] 拉取远端索引失败，将盲跑 upsert（无法按微信号兜底）：{e}")
        by_wxid, by_number = {}, {}

    upsert_rows: list = []
    patch_rows: list = []
    plan_upsert = 0
    plan_patch = 0
    plan_insert = 0

    for r in candidates:
        mode, remote_id = _resolve_remote_match(r, by_wxid, by_number)
        if mode == "patch" and remote_id is not None:
            patch_rows.append((remote_id, r))
            plan_patch += 1
        else:
            upsert_rows.append(r)
            wxid_lc = (r.get("wechat_id") or "").strip().lower()
            if wxid_lc in by_wxid:
                plan_upsert += 1
            else:
                plan_insert += 1

    log.info(
        f"[内部备注] 计划：新增 {plan_insert}，按 wechat_id 更新 {plan_upsert}，"
        f"按 wechat_number 兜底 PATCH {plan_patch}，待处理 {len(candidates)} 条"
    )

    upsert_url = f"{base}?on_conflict={conflict_column}"
    headers_upsert = dict(common_headers)
    headers_upsert["Prefer"] = "resolution=merge-duplicates,return=minimal"
    headers_patch = dict(common_headers)
    headers_patch["Prefer"] = "return=minimal"

    def _upsert_one_by_one(batch: list) -> tuple[int, int]:
        ok = 0
        fail = 0
        for r in batch:
            try:
                resp = session.post(
                    upsert_url,
                    json=[_insert_payload(r)],
                    headers=headers_upsert,
                    timeout=request_timeout,
                )
                if 200 <= resp.status_code < 300:
                    ok += 1
                else:
                    fail += 1
                    log.warning(
                        f"[内部备注][UPSERT 单条] internal_note='{r.get('internal_note','')}' "
                        f"wechat_id='{r.get('wechat_id','')}' "
                        f"HTTP {resp.status_code}: {(resp.text or '')[:200]}"
                    )
            except requests.RequestException as e:
                fail += 1
                log.warning(
                    f"[内部备注][UPSERT 单条] internal_note='{r.get('internal_note','')}' 异常 {e}"
                )
        return ok, fail

    def _patch_one(remote_id, r: dict) -> bool:
        """按微信号命中远端行时 PATCH；wechat_id 写 username，禁止写入 alias。"""
        url = f"{base}?id=eq.{remote_id}"
        try:
            resp = session.patch(
                url,
                json=_insert_payload(r),
                headers=headers_patch,
                timeout=request_timeout,
            )
            if 200 <= resp.status_code < 300:
                return True
            log.warning(
                f"[内部备注][PATCH 单条] id={remote_id} internal_note='{r.get('internal_note','')}' "
                f"wechat_id='{r.get('wechat_id','')}' wechat_number='{r.get('wechat_number','')}' "
                f"HTTP {resp.status_code}: {(resp.text or '')[:200]}"
            )
        except requests.RequestException as e:
            log.warning(
                f"[内部备注][PATCH 单条] id={remote_id} internal_note='{r.get('internal_note','')}' 异常 {e}"
            )
        return False

    success = 0
    failed = 0

    # ── 批量 POST upsert（新增 + 按 wechat_id 更新）────────────────
    size = max(1, int(batch_size))
    total_upsert = len(upsert_rows)
    total_batches = max(1, math.ceil(total_upsert / size)) if total_upsert else 0
    if total_upsert:
        log.info(f"[内部备注] 批量 UPSERT 开始：共 {total_upsert} 条 / {total_batches} 批")
    for i in range(total_batches):
        batch = upsert_rows[i * size : (i + 1) * size]
        try:
            t0 = time.time()
            resp = session.post(
                upsert_url,
                json=[_insert_payload(x) for x in batch],
                headers=headers_upsert,
                timeout=request_timeout,
            )
            elapsed = time.time() - t0
            if 200 <= resp.status_code < 300:
                success += len(batch)
                log.info(
                    f"[内部备注][UPSERT 批 {i+1}/{total_batches}] OK {len(batch)} 条，{elapsed:.2f}s"
                )
            else:
                body = (resp.text or "")[:300]
                log.warning(
                    f"[内部备注][UPSERT 批 {i+1}/{total_batches}] "
                    f"HTTP {resp.status_code}: {body}，逐条重试"
                )
                s, f = _upsert_one_by_one(batch)
                success += s
                failed += f
        except requests.RequestException as e:
            log.warning(f"[内部备注][UPSERT 批 {i+1}] 异常 {e}，逐条重试")
            s, f = _upsert_one_by_one(batch)
            success += s
            failed += f
        if i < total_batches - 1:
            time.sleep(batch_interval)

    # ── 逐条 PATCH（第二优先：按 wechat_number 命中）──────────────
    if patch_rows:
        log.info(f"[内部备注] 按 wechat_number 兜底 PATCH 开始：共 {len(patch_rows)} 条")
    for remote_id, r in patch_rows:
        if _patch_one(remote_id, r):
            success += 1
        else:
            failed += 1
        time.sleep(batch_interval)

    _log_import_summary(log, success=success, failed=failed, polluted=polluted, duplicate=duplicate)
    return {
        "success": success,
        "failed": failed,
        "polluted": polluted,
        "duplicate": duplicate,
    }


def _log_import_summary(
    log,
    *,
    success: int,
    failed: int,
    polluted: int,
    duplicate: int,
) -> None:
    """每次导入结束输出统一汇总行。"""
    log.info(
        f"[内部备注] 导入汇总：成功 {success}，失败 {failed}，"
        f"字段污染 {polluted}，重复 {duplicate}"
    )


# ─────────────────────────────── Edge Function 上传（备用）───────────────────────────────
def _edge_upload(
    rows: list,
    token: str,
    *,
    supabase_url: str,
    anon_key: str,
    import_function: str,
    batch_size: int,
    batch_interval: float,
    request_timeout: int,
    log,
    merge_duplicate: int = 0,
) -> dict:
    """走 /functions/v1/import-internal-notes；上传前同样做字段污染拦截。"""
    candidates, prep_stats = _prepare_upload_candidates(rows, log=log)
    polluted = prep_stats["polluted"]
    duplicate = merge_duplicate + prep_stats["duplicate"]

    if not candidates:
        _log_import_summary(log, success=0, failed=0, polluted=polluted, duplicate=duplicate)
        return {"success": 0, "failed": 0, "polluted": polluted, "duplicate": duplicate}

    url = f"{supabase_url}/functions/v1/{import_function}"
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": anon_key,
        "Content-Type": "application/json",
    }
    payload_rows = [_insert_payload(r) for r in candidates]
    total = len(payload_rows)
    total_batches = max(1, math.ceil(total / batch_size))
    log.info(f"[内部备注] Edge Function → {url}，共 {total} / {total_batches} 批")

    success = 0
    failed = 0
    for i in range(total_batches):
        batch = payload_rows[i * batch_size : (i + 1) * batch_size]
        try:
            t0 = time.time()
            resp = requests.post(url, json=batch, headers=headers, timeout=request_timeout)
            elapsed = time.time() - t0
            body = (resp.text or "")[:300]
            if 200 <= resp.status_code < 300:
                success += len(batch)
                log.info(
                    f"[内部备注][Edge 批 {i+1}/{total_batches}] OK {len(batch)} 条 "
                    f"耗时 {elapsed:.2f}s body={body}"
                )
            else:
                failed += len(batch)
                log.warning(
                    f"[内部备注][Edge 批 {i+1}/{total_batches}] HTTP {resp.status_code}：{body}"
                )
        except requests.RequestException as e:
            failed += len(batch)
            log.warning(f"[内部备注][Edge 批 {i+1}] 异常 {e}")

        if i < total_batches - 1:
            time.sleep(batch_interval)

    _log_import_summary(log, success=success, failed=failed, polluted=polluted, duplicate=duplicate)
    return {
        "success": success,
        "failed": failed,
        "polluted": polluted,
        "duplicate": duplicate,
    }


# ─────────────────────────────── 对外入口 ───────────────────────────────
def run_pipeline(
    *,
    db_path: Union[str, list[str]],
    out_json: str,
    upload: bool = True,
    upload_mode: str = "postgrest",
    supabase_url: Optional[str] = None,
    anon_key: Optional[str] = None,
    get_token: Optional[Callable[[], str]] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    batch_interval: float = DEFAULT_BATCH_INTERVAL,
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
    table_name: str = DEFAULT_TABLE_NAME,
    conflict_column: str = DEFAULT_CONFLICT_COLUMN,
    import_function: str = DEFAULT_IMPORT_FUNCTION,
    log=None,
) -> bool:
    """
    内部备注导入流水线主入口。

    流程：
        1. load_from_db(db_path)             从一个或多个 contact.db 读取并合并备注
        2. dump_json(items, out_json)        导出一份中文键 JSON 快照（人工可读）
        3. if not upload  → 结束（仅导出模式）
        4. 调用方注入 token / supabase 信息 → 走 _sync_increment 或 _edge_upload

    返回：True 表示流水线顺利跑完（业务上不一定 100% 写成功，详见日志）。
    """
    log = _get_logger(log)
    try:
        log.info("=" * 60)
        log.info("内部备注导入流水线启动（外置脚本）")
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

        token = get_token()
        mode = (upload_mode or "postgrest").lower()
        if mode == "edge":
            _edge_upload(
                items, token,
                supabase_url=supabase_url, anon_key=anon_key,
                import_function=import_function,
                batch_size=batch_size, batch_interval=batch_interval,
                request_timeout=request_timeout, log=log,
                merge_duplicate=merge_duplicate,
            )
        else:
            _sync_increment(
                items, token,
                supabase_url=supabase_url, anon_key=anon_key,
                table_name=table_name, conflict_column=conflict_column,
                batch_size=batch_size, batch_interval=batch_interval,
                request_timeout=request_timeout, log=log,
                merge_duplicate=merge_duplicate,
            )
        return True
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
            "UPLOAD_MODE":       "postgrest",
            "SUPABASE_URL":      "https://xxx.supabase.co",
            "ANON_KEY":          "...",
            "TOKEN":             "...",        // 可选；为空则仅导出，不上传
            "BATCH_SIZE":        100,
            "BATCH_INTERVAL":    0.3,
            "REQUEST_TIMEOUT":   120,
            "TABLE_NAME":        "internal_notes",
            "CONFLICT_COLUMN":   "wechat_id",
            "IMPORT_FUNCTION":   "import-internal-notes"
        }
    """
    from pathlib import Path
    cfg_path = Path(__file__).resolve().parent / "内部备注导入.config.json"
    if not cfg_path.exists():
        print(f"[内部备注] 未找到配置文件：{cfg_path}")
        print("如需独立运行，请在脚本同目录下创建该文件（见 main() 注释）")
        return
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    token_val = (cfg.get("TOKEN") or "").strip()
    db_cfg = cfg.get("DB_PATHS") or cfg.get("DB_PATH")
    ok = run_pipeline(
        db_path=db_cfg,
        out_json=cfg["OUT_JSON"],
        upload=bool(cfg.get("UPLOAD", True)) and bool(token_val),
        upload_mode=cfg.get("UPLOAD_MODE", "postgrest"),
        supabase_url=cfg.get("SUPABASE_URL"),
        anon_key=cfg.get("ANON_KEY"),
        get_token=(lambda: token_val) if token_val else None,
        batch_size=int(cfg.get("BATCH_SIZE", DEFAULT_BATCH_SIZE)),
        batch_interval=float(cfg.get("BATCH_INTERVAL", DEFAULT_BATCH_INTERVAL)),
        request_timeout=int(cfg.get("REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT)),
        table_name=cfg.get("TABLE_NAME", DEFAULT_TABLE_NAME),
        conflict_column=cfg.get("CONFLICT_COLUMN", DEFAULT_CONFLICT_COLUMN),
        import_function=cfg.get("IMPORT_FUNCTION", DEFAULT_IMPORT_FUNCTION),
        log=None,
    )
    print("OK" if ok else "FAIL")


if __name__ == "__main__":
    main()
