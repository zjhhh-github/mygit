# -*- coding: utf-8 -*-
"""
contact.db → 内部备注 JSON → 批量上传到 internal_notes

默认走 PostgREST upsert（与前端 supabase.from('internal_notes').upsert 等价）：
  POST {SUPABASE_URL}/rest/v1/internal_notes?on_conflict=wechat_id
  Prefer: resolution=merge-duplicates,return=representation

如确认服务端存在 import-internal-notes Edge Function，可把 UPLOAD_MODE 改为 "edge"。
"""

import json
import math
import os
import re
import sqlite3
import sys
import time

import requests


# ─────────────────────────── 集中配置 ───────────────────────────
CONFIG = {
    # Supabase 项目 URL（不含尾部斜杠）
    "SUPABASE_URL": "https://backend.appmiaoda.com/projects/supabase293970823448936448",
    # Supabase anon key
    "ANON_KEY": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoyMDg5NTE1MzA2LCJpc3MiOiJzdXBhYmFzZSIsInJvbGUiOiJhbm9uIiwic3ViIjoiYW5vbiJ9.Z19rhe7D6v4pXthoontMmG_C1U3yW6DTTSyFOKYvs54",
    # 登录邮箱 / 密码
    "EMAIL": "15648230994@miaoda.com",
    "PASSWORD": "028056hQ@",
    # 本地数据源
    "DB_PATH": r"C:\Users\LENOVO\Desktop\contact.db",
    # 导出 JSON 路径（中文字段，便于人工核对/Excel 导入）
    "OUT_JSON": r"C:\Users\LENOVO\Desktop\contact_result.json",
    # 上传控制
    "UPLOAD": True,
    "UPLOAD_MODE": "postgrest",  # "postgrest" 或 "edge"
    "BATCH_SIZE": 100,           # 与前端保持一致
    "BATCH_INTERVAL": 0.3,
    "REQUEST_TIMEOUT": 120,
    "MAX_RETRIES": 3,
    "RETRY_INTERVAL": 2,
    # PostgREST 目标表
    "TABLE_NAME": "internal_notes",
    "CONFLICT_COLUMN": "wechat_id",
    # Edge Function 名称（UPLOAD_MODE=edge 时使用）
    "IMPORT_FUNCTION": "import-internal-notes",
}


# ─────────────────────────── 数据清洗 ───────────────────────────
_NON_SUFFIX_RE = re.compile(r"[（(]非[）)]\s*$")


def clean_remark(remark: str) -> str:
    if not remark:
        return remark
    return _NON_SUFFIX_RE.sub("", remark).rstrip()


def resolve_total_wx(username: str, alias: str) -> str:
    if alias and alias.strip():
        return alias.strip()
    return (username or "").strip()


# ─────────────────────────── 数据源 ───────────────────────────
def load_from_db(db_path: str) -> list:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"未找到数据库文件：{db_path}")

    sql = """
        SELECT remark, username, alias
        FROM contact
        WHERE remark LIKE '¿¿¿%-%'
          AND INSTR(SUBSTR(remark, 4), '-') > 0
          AND TRIM(SUBSTR(remark, INSTR(SUBSTR(remark, 4), '-') + 4)) NOT IN ('空', '删除')
        ORDER BY remark;
    """

    conn = sqlite3.connect(db_path)
    conn.text_factory = str
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
    finally:
        conn.close()

    results = []
    for remark, username, alias in rows:
        remark_clean = clean_remark(remark or "")
        username_val = (username or "").strip()
        alias_val = (alias or "").strip()
        results.append({
            "内部备注": remark_clean,
            "微信ID": alias_val,
            "微信号": username_val,
            "总微信号": resolve_total_wx(username_val, alias_val),
        })
    return results


def dump_json(payload: list, out_path: str) -> None:
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def to_en_rows(items: list) -> list:
    """中文键 → 英文蛇形键。

    注意：internal_notes.wechat_id 在库里是 NOT NULL UNIQUE，
    当 alias（微信ID）为空时，回退用 username（微信号）作为 wechat_id，
    与业务侧"总微信号 = alias or username"的回退逻辑保持一致，避免漏导入。
    """
    rows = []
    for r in items:
        alias = (r.get("微信ID") or "").strip()
        username = (r.get("微信号") or "").strip()
        wechat_id_effective = alias if alias else username
        rows.append({
            "internal_note": (r.get("内部备注") or "").strip(),
            "wechat_id": wechat_id_effective,
            "wechat_number": username,
            "total_wechat_number": (r.get("总微信号") or "").strip(),
        })
    return rows


# ─────────────────────────── 登录 ───────────────────────────
def get_token() -> str:
    url = f"{CONFIG['SUPABASE_URL']}/auth/v1/token?grant_type=password"
    headers = {"apikey": CONFIG["ANON_KEY"], "Content-Type": "application/json"}
    payload = {"email": CONFIG["EMAIL"], "password": CONFIG["PASSWORD"]}
    print("[登录] 正在获取 Supabase Token ...")
    resp = requests.post(url, json=payload, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise ValueError("登录响应中未找到 access_token，请检查邮箱/密码或 anon key。")
    print("[登录] Token 获取成功")
    return token


# ─────────────────────────── PostgREST upsert ─────────────────────
def _postgrest_headers(token: str, prefer: str) -> dict:
    return {
        "apikey": CONFIG["ANON_KEY"],
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def postgrest_upsert(rows: list, token: str) -> dict:
    """走 PostgREST 的 upsert：等价前端 .upsert(batch, { onConflict: 'wechat_id' })。
    批量失败时对本批逐条降级重试（与前端 importInternalNotes 一致）。"""
    table = CONFIG["TABLE_NAME"]
    conflict = CONFIG["CONFLICT_COLUMN"]
    url = f"{CONFIG['SUPABASE_URL']}/rest/v1/{table}?on_conflict={conflict}"
    headers_batch = _postgrest_headers(token, "resolution=merge-duplicates,return=minimal")
    headers_single = _postgrest_headers(token, "resolution=merge-duplicates,return=minimal")

    total = len(rows)
    batch_size = CONFIG["BATCH_SIZE"]
    total_batches = max(1, math.ceil(total / batch_size))
    print(f"[上传/PostgREST] 目标表 {table}，on_conflict={conflict}")
    print(f"[上传/PostgREST] 共 {total} 条，分 {total_batches} 批，每批最多 {batch_size} 条")

    success = 0
    failed = 0
    errors_preview = []

    for i in range(total_batches):
        batch = rows[i * batch_size : (i + 1) * batch_size]
        valid_batch = [
            r for r in batch
            if r.get("wechat_id") and r.get("total_wechat_number") and r.get("internal_note")
        ]
        dropped = len(batch) - len(valid_batch)
        if dropped:
            print(
                f"[批次 {i+1}/{total_batches}] 跳过 {dropped} 条"
                "（wechat_id/total_wechat_number/internal_note 任一为空）"
            )

        if not valid_batch:
            continue

        t0 = time.time()
        try:
            resp = requests.post(
                url, json=valid_batch, headers=headers_batch, timeout=CONFIG["REQUEST_TIMEOUT"]
            )
            elapsed = time.time() - t0

            if 200 <= resp.status_code < 300:
                success += len(valid_batch)
                print(f"[批次 {i+1}/{total_batches}] OK {len(valid_batch)} 条，耗时 {elapsed:.2f}s")
            else:
                body = (resp.text or "")[:300]
                print(f"[批次 {i+1}/{total_batches}] 批量失败 HTTP {resp.status_code}，降级逐条。响应：{body}")
                s, f, errs = _postgrest_upsert_one_by_one(valid_batch, url, headers_single)
                success += s
                failed += f
                errors_preview.extend(errs[:5])
        except requests.RequestException as e:
            print(f"[批次 {i+1}/{total_batches}] 批量异常：{e}，降级逐条")
            s, f, errs = _postgrest_upsert_one_by_one(valid_batch, url, headers_single)
            success += s
            failed += f
            errors_preview.extend(errs[:5])

        if i < total_batches - 1:
            time.sleep(CONFIG["BATCH_INTERVAL"])

    print(f"[上传完成] success={success} failed={failed}")
    if errors_preview:
        print("[错误预览] 前若干条：")
        for msg in errors_preview[:10]:
            print("  - " + msg)
    return {"success": success, "failed": failed, "errors": errors_preview}


def _postgrest_upsert_one_by_one(rows: list, url: str, headers: dict) -> tuple:
    success = 0
    failed = 0
    errors = []
    for r in rows:
        try:
            resp = requests.post(url, json=[r], headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
            if 200 <= resp.status_code < 300:
                success += 1
            else:
                failed += 1
                errors.append(f"{r.get('wechat_id','')} HTTP {resp.status_code}: {(resp.text or '')[:200]}")
        except requests.RequestException as e:
            failed += 1
            errors.append(f"{r.get('wechat_id','')} 异常: {e}")
    return success, failed, errors


# ─────────────────────────── Edge Function 模式 ─────────────────
def edge_upload(rows: list, token: str) -> dict:
    url = f"{CONFIG['SUPABASE_URL']}/functions/v1/{CONFIG['IMPORT_FUNCTION']}"
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": CONFIG["ANON_KEY"],
        "Content-Type": "application/json",
    }
    total = len(rows)
    batch_size = CONFIG["BATCH_SIZE"]
    total_batches = max(1, math.ceil(total / batch_size))
    print(f"[上传/Edge] {url}")
    print(f"[上传/Edge] 共 {total} 条，分 {total_batches} 批")

    agg = {"success_batches": 0, "failed_batches": 0, "total_added": 0}
    for i in range(total_batches):
        batch = rows[i * batch_size : (i + 1) * batch_size]
        last_err = None
        for attempt in range(1, CONFIG["MAX_RETRIES"] + 1):
            try:
                t0 = time.time()
                resp = requests.post(url, json=batch, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
                elapsed = time.time() - t0
                body = (resp.text or "")[:300]
                if 200 <= resp.status_code < 300:
                    try:
                        result = resp.json()
                    except Exception:
                        result = {}
                    added = int(result.get("added", 0) or 0)
                    agg["success_batches"] += 1
                    agg["total_added"] += added
                    print(
                        f"[批次 {i+1}/{total_batches}] OK status={resp.status_code} "
                        f"added={added} 耗时 {elapsed:.2f}s body={body}"
                    )
                    break
                else:
                    last_err = f"HTTP {resp.status_code}: {body}"
                    print(f"[批次 {i+1}] 第 {attempt} 次失败 {last_err}")
                    if attempt < CONFIG["MAX_RETRIES"]:
                        time.sleep(CONFIG["RETRY_INTERVAL"])
            except requests.RequestException as e:
                last_err = str(e)
                print(f"[批次 {i+1}] 第 {attempt} 次异常 {e}")
                if attempt < CONFIG["MAX_RETRIES"]:
                    time.sleep(CONFIG["RETRY_INTERVAL"])
        else:
            agg["failed_batches"] += 1
            print(f"[批次 {i+1}] 最终失败：{last_err}")

        if i < total_batches - 1:
            time.sleep(CONFIG["BATCH_INTERVAL"])

    print(f"[上传完成] success_batches={agg['success_batches']} failed_batches={agg['failed_batches']} total_added={agg['total_added']}")
    return agg


# ─────────────────────────── 主流程 ───────────────────────────
def main() -> int:
    try:
        results = load_from_db(CONFIG["DB_PATH"])
    except FileNotFoundError as e:
        print(f"[错误] {e}")
        return 1

    print(f"[查询] 共 {len(results)} 条记录")
    if results:
        print("-" * 90)
        print(f"{'内部备注':<30} {'微信号':<22} {'微信ID':<22} {'总微信号':<22}")
        print("-" * 90)
        for r in results[:10]:
            print(f"{r['内部备注']:<30} {r['微信号']:<22} {r['微信ID']:<22} {r['总微信号']:<22}")
        if len(results) > 10:
            print(f"...（仅预览前 10 条）")

    dump_json(results, CONFIG["OUT_JSON"])
    print(f"[导出] 已保存：{CONFIG['OUT_JSON']}")

    if not CONFIG["UPLOAD"]:
        print("[提示] UPLOAD=False，已跳过上传")
        return 0
    if not results:
        print("[提示] 无数据可上传")
        return 0

    try:
        token = get_token()
    except Exception as e:
        print(f"[错误] 登录失败：{e}")
        return 2

    rows_en = to_en_rows(results)

    mode = CONFIG.get("UPLOAD_MODE", "postgrest").lower()
    if mode == "edge":
        edge_upload(rows_en, token)
    else:
        postgrest_upsert(rows_en, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
