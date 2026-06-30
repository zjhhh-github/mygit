# -*- coding: utf-8 -*-
"""
内部备注导入脚本（外置版）
==========================

职责：
    从本机微信 contact.db 读取符合规则的「内部备注」，按 wechat_id 做 UPSERT
    写入 Supabase 的 internal_notes 表。

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

上传规则：
    - postgrest（默认，推荐）：
        POST /rest/v1/internal_notes?on_conflict=wechat_id
        Prefer: resolution=merge-duplicates,return=minimal
        → wechat_id 不存在 → INSERT；存在 → UPDATE 全部字段（含 internal_note）。
    - edge：
        走 /functions/v1/import-internal-notes（Edge Function 模式，备用通路）。

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


def _deduplicate_items(items: list, *, log=None) -> list:
    """多库读取合并后去重：先按 internal_note，再按 wechat_id（不区分大小写）。

    与上传阶段 _sync_increment 的规则一致（first-wins），避免双库重复记录进入 JSON 与 UPSERT。
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

    return sorted(candidates, key=lambda r: r.get("internal_note") or "")


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
        # alias（微信ID）优先；alias 为空时退回 username（微信号）
        total = alias_val if alias_val else username_val
        wechat_id_effective = alias_val if alias_val else username_val
        items.append({
            "internal_note": remark_clean,
            "wechat_id": wechat_id_effective,
            "wechat_number": username_val,
            "total_wechat_number": total,
            "_zh": {
                "内部备注": remark_clean,
                "微信ID": alias_val,
                "微信号": username_val,
                "总微信号": total,
            },
        })
    return items


def load_from_db(db_path: Union[str, list[str]], *, log=None) -> list:
    """
    从一个或多个 contact.db 读取符合规则的内部备注，合并后先去重再返回。

    去重顺序：
        1. 跳过 internal_note / wechat_id / total_wechat_number 为空的记录
        2. 按 internal_note 去重（first-wins）
        3. 再按 wechat_id 去重（不区分大小写，first-wins）

    db_path 支持：
        - 单个路径字符串
        - 分号/竖线分隔的多路径字符串（如 "a.db;b.db"）
        - 路径列表

    返回值：
        list[dict]，每条形如：
        {
            "internal_note":       清洗后的备注本体,
            "wechat_id":           上传用的微信 ID（alias 优先，没有则 username）,
            "wechat_number":       微信号（username 原值），
            "total_wechat_number": 总微信号（同 wechat_id 兜底逻辑），
            "_zh": { 中文键的副本，仅用于导出 JSON 给人看 }
        }
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
    """生成发给 PostgREST 的标准记录（UPSERT 用这一份）。"""
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
) -> tuple:
    """分页拉取远端 internal_notes，构建两个索引：

    - by_note  : { internal_note: [id, ...] }   # 按备注命中
    - by_wxid  : { wechat_id_lower: id }        # 按微信 ID 命中（小写归一）
                 表上有 internal_notes_wechat_id_key 唯一约束，
                 一个 wechat_id 最多对应一条记录，直接存单值。

    用于事前估算「本次会触发多少条 INSERT / UPDATE」，不影响实际请求。
    """
    base = f"{supabase_url}/rest/v1/{table_name}"
    url = f"{base}?select=id,internal_note,wechat_id&order=id.asc"
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    page_size = 1000
    by_note: dict = {}
    by_wxid: dict = {}
    offset = 0
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
            note_key = item.get("internal_note") or ""
            wxid = (item.get("wechat_id") or "").strip()
            rid = item.get("id")
            by_note.setdefault(note_key, []).append(rid)
            if wxid and rid is not None:
                # 唯一约束保证不会重复；以防万一只保留首条 id
                by_wxid.setdefault(wxid.lower(), rid)
        if len(batch) < page_size:
            break
        offset += page_size

    total = sum(len(v) for v in by_note.values())
    log.info(
        f"[内部备注] 远端已有 {total} 条，internal_note 去重种数 {len(by_note)}，"
        f"wechat_id 索引 {len(by_wxid)}"
    )
    return by_note, by_wxid


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
) -> dict:
    """批量 UPSERT 写入 internal_notes。

    核心写法：
        POST /rest/v1/internal_notes?on_conflict=wechat_id
        Prefer: resolution=merge-duplicates,return=minimal
        body  : [_insert_payload(r), ...]   # 一次最多 batch_size 条

    服务端语义：
        - wechat_id 不存在 → INSERT
        - wechat_id 已存在 → UPDATE 该行所有字段（含 internal_note 一并覆盖）
                             这同时覆盖了"按 internal_note 命中"和"按 wechat_id
                             兜底命中"两类场景，且彻底规避 23505 duplicate key。
    """
    base = f"{supabase_url}/rest/v1/{table_name}"
    common_headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    session = _build_session()

    try:
        existing_by_note, existing_by_wxid = _pull_existing(
            token,
            supabase_url=supabase_url,
            anon_key=anon_key,
            table_name=table_name,
            request_timeout=request_timeout,
            log=log,
        )
    except Exception as e:
        # 远端拉不下来时不阻断，仍允许尝试 UPSERT（merge 行为不依赖 existing 索引）
        log.warning(f"[内部备注] 拉取远端索引失败，将盲跑批量 UPSERT：{e}")
        existing_by_note, existing_by_wxid = {}, {}

    # ── 本地按 internal_note 去重（first-wins，与历史行为一致）──────
    local_by_note: dict = {}
    local_dropped = 0
    local_dup_note = 0
    for r in rows:
        if not r.get("internal_note") or not r.get("wechat_id") or not r.get("total_wechat_number"):
            local_dropped += 1
            continue
        k = r["internal_note"]
        if k in local_by_note:
            local_dup_note += 1
            continue
        local_by_note[k] = r
    if local_dropped:
        log.warning(f"[内部备注] 本地跳过 {local_dropped} 条（关键字段为空）")
    if local_dup_note:
        log.info(f"[内部备注] 本地相同 internal_note 合并去重 {local_dup_note} 条")

    # ── 再按 wechat_id 去重 ────────────────────────────────────────
    # 原因：UPSERT 同批不能含相同 conflict 列，否则 PostgREST 会整批 23505 失败
    candidates: list = []
    seen_wxid: set = set()
    local_dup_wxid = 0
    plan_insert = 0
    plan_update_note = 0
    plan_update_wxid = 0
    for note, r in local_by_note.items():
        wxid = (r.get("wechat_id") or "").strip()
        wxid_lc = wxid.lower()
        if not wxid:
            local_dropped += 1
            continue
        if wxid_lc in seen_wxid:
            local_dup_wxid += 1
            continue
        seen_wxid.add(wxid_lc)
        candidates.append(r)

        # 仅日志估算，不影响实际请求
        ids = existing_by_note.get(note)
        if ids and len(ids) == 1:
            plan_update_note += 1
        elif wxid_lc in existing_by_wxid:
            plan_update_wxid += 1
        else:
            plan_insert += 1
    if local_dup_wxid:
        log.info(f"[内部备注] 本地相同 wechat_id 合并去重 {local_dup_wxid} 条")

    log.info(
        f"[内部备注] 计划（基于远端索引估算）：新增 {plan_insert}，"
        f"按备注更新 {plan_update_note}，按微信ID兜底更新 {plan_update_wxid}，"
        f"待处理总数 {len(candidates)}"
    )

    if not candidates:
        log.info("[内部备注] 无可处理记录")
        return {"inserted": 0, "updated": 0, "failed": 0, "skipped": 0}

    # ── 批量 UPSERT ────────────────────────────────────────────────
    upsert_url = f"{base}?on_conflict={conflict_column}"
    headers_upsert = dict(common_headers)
    headers_upsert["Prefer"] = "resolution=merge-duplicates,return=minimal"

    def _upsert_one_by_one(batch: list) -> tuple:
        """整批失败时降级为逐条 UPSERT，定位是哪几条出错。"""
        ok = 0
        fail = 0
        for r in batch:
            try:
                resp = session.post(
                    upsert_url, json=[_insert_payload(r)], headers=headers_upsert,
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

    size = max(1, int(batch_size))
    total = len(candidates)
    total_batches = max(1, math.ceil(total / size))
    log.info(f"[内部备注] 批量 UPSERT 开始：共 {total} 条 / {total_batches} 批")

    ok = 0
    fail = 0
    for i in range(total_batches):
        batch = candidates[i * size : (i + 1) * size]
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
                ok += len(batch)
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
                ok += s
                fail += f
        except requests.RequestException as e:
            log.warning(f"[内部备注][UPSERT 批 {i+1}] 异常 {e}，逐条重试")
            s, f = _upsert_one_by_one(batch)
            ok += s
            fail += f
        if i < total_batches - 1:
            time.sleep(batch_interval)

    log.info(
        f"[内部备注] 完成：UPSERT 成功 {ok} / 失败 {fail}（合并 INSERT 与 UPDATE）"
    )
    return {
        "inserted": ok,    # UPSERT 不区分新增 vs 更新，一并计入 inserted（保持旧字段语义）
        "updated": 0,
        "failed": fail,
        "skipped": local_dup_wxid,
    }


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
) -> dict:
    """走 /functions/v1/import-internal-notes，规则由 Edge Function 决定。"""
    url = f"{supabase_url}/functions/v1/{import_function}"
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": anon_key,
        "Content-Type": "application/json",
    }
    total = len(rows)
    total_batches = max(1, math.ceil(total / batch_size))
    log.info(f"[内部备注] Edge Function → {url}，共 {total} / {total_batches} 批")

    ok_batches = 0
    fail_batches = 0
    added_sum = 0
    for i in range(total_batches):
        batch = rows[i * batch_size : (i + 1) * batch_size]
        try:
            t0 = time.time()
            resp = requests.post(url, json=batch, headers=headers, timeout=request_timeout)
            elapsed = time.time() - t0
            body = (resp.text or "")[:300]
            if 200 <= resp.status_code < 300:
                ok_batches += 1
                try:
                    added_sum += int((resp.json() or {}).get("added", 0) or 0)
                except Exception:
                    pass
                log.info(
                    f"[内部备注][批 {i+1}/{total_batches}] OK {resp.status_code} "
                    f"耗时 {elapsed:.2f}s body={body}"
                )
            else:
                fail_batches += 1
                log.warning(
                    f"[内部备注][批 {i+1}/{total_batches}] HTTP {resp.status_code}：{body}"
                )
        except requests.RequestException as e:
            fail_batches += 1
            log.warning(f"[内部备注][批 {i+1}] 异常 {e}")

        if i < total_batches - 1:
            time.sleep(batch_interval)

    log.info(
        f"[内部备注] Edge 完成：ok_batches={ok_batches} fail_batches={fail_batches} added={added_sum}"
    )
    return {"ok_batches": ok_batches, "fail_batches": fail_batches, "added": added_sum}


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
        items = load_from_db(db_paths, log=log)
        log.info(f"[内部备注] 双库读取合并去重后 {len(items)} 条")
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
            )
        else:
            _sync_increment(
                items, token,
                supabase_url=supabase_url, anon_key=anon_key,
                table_name=table_name, conflict_column=conflict_column,
                batch_size=batch_size, batch_interval=batch_interval,
                request_timeout=request_timeout, log=log,
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
