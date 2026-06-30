# -*- coding: utf-8 -*-
"""
秒哒导出 + 飞书同步（控制台兼容版，重写版）。

设计目标：
1) 先保证“秒哒导出结果”可单独验证（--only-export）。
2) 保留既有 API / token 获取逻辑，不改变外部调用入口。
3) 与导入控制台兼容：提供 export_students()/sync_to_feishu()。

说明：
- 飞书同步复用 manjike-tools/prospect/同步意向学员到飞书.py 主脚本执行，
  这样可以复用其字段映射、批量写入、重试等成熟逻辑。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import requests


# ==============================
# 一、秒哒导出配置（保留原有 API 信息）
# ==============================
EXPORT_URL = (
    "https://backend.appmiaoda.com/projects/"
    "supabase293970823448936448/functions/v1/export-students"
)
EXPORT_API_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoyMDg5NTE1MzA2LCJpc3MiOiJzdXBhYmFzZSIs"
    "InJvbGUiOiJhbm9uIiwic3ViIjoiYW5vbiJ9."
    "Z19rhe7D6v4pXthoontMmG_C1U3yW6DTTSyFOKYvs54"
)
SUPABASE_BASE_URL = (
    "https://backend.appmiaoda.com/projects/supabase293970823448936448"
)
SUPABASE_EMAIL = os.getenv("SUPABASE_EMAIL", "15648230994@miaoda.com")
SUPABASE_PASSWORD = os.getenv("SUPABASE_PASSWORD", "028056hQ@")
REQUEST_TIMEOUT = 30


# ==============================
# 二、运行时变量（导入控制台会覆盖）
# ==============================
DRY_RUN = False
log = print
PREVIEW_REWRITE_ONLY = False

SCRIPT_DIR = Path(__file__).resolve().parent
TOKEN_CACHE_PATH = SCRIPT_DIR / "token.json"
RAW_EXPORT_PATH = SCRIPT_DIR / "export_raw.json"
SYNC_CONFIG_FILE = SCRIPT_DIR / "sync_to_feishu.config.json"
REWRITE_PREVIEW_PATH = SCRIPT_DIR / "rewrite_preview.json"
REWRITE_RESULT_PATH = SCRIPT_DIR / "rewrite_result.json"

# 飞书配置优先级：环境变量 > 同目录 sync_to_feishu.config.json > 默认值
DEFAULT_FEISHU_CONFIG = {
    "app_id": "cli_a96f36ed1538dbcf",
    "app_secret": "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB",
    "app_token": "Zk05bwki2abD8XsBBOccaFsPn8e",
    "table_id": "tblNIWZ1EsDyZ1ug",
    "api_base": "https://open.feishu.cn/open-apis",
}

# 兼容导入控制台“打开字段映射配置”按钮：
# 控制台会尝试读取 DEFAULT_FIELD_MAPPING/FIELD_MAP 生成模板。
DEFAULT_FIELD_MAPPING = {
    "student_id_field": "意向学员总微信号",
    "fields": [
        {"feishu": "意向学员总微信号", "source": "student.意向学员微信号"},
        {"feishu": "推荐人总微信号", "source": "source.来源微信号"},
        {"feishu": "绑定日期", "source": "source.绑定日期", "type_hint": "date"},
        {"feishu": "解绑日期", "source": "source.解绑日期", "type_hint": "date"},
        {"feishu": "绑定状态", "source": "source.绑定状态"},
    ],
}


def _emit(msg: str) -> None:
    """统一日志输出，兼容导入控制台注入的 log 回调。"""
    try:
        if callable(log):
            log(msg)
            return
    except Exception:
        pass
    print(msg)


def _jwt_expiry(token: str) -> int | None:
    """解析 JWT 过期时间（秒时间戳），解析失败返回 None。"""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        exp = payload.get("exp")
        return int(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


def _load_token_cache() -> dict[str, Any] | None:
    if not TOKEN_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _save_token_cache(access_token: str, refresh_token: str | None, email: str) -> None:
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token or "",
        "expires_at": _jwt_expiry(access_token) or int(time.time()) + 3500,
        "email": email,
    }
    TOKEN_CACHE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _auth_url() -> str:
    return f"{SUPABASE_BASE_URL.rstrip('/')}/auth/v1/token"


def _login_supabase(email: str, password: str) -> dict[str, Any]:
    """使用账号密码登录秒哒 Supabase，返回 token 结构。"""
    resp = requests.post(
        _auth_url(),
        params={"grant_type": "password"},
        headers={"apikey": EXPORT_API_KEY, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=REQUEST_TIMEOUT,
    )
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(f"秒哒登录失败 HTTP {resp.status_code}：{(resp.text or '')[:300]}")
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError("秒哒登录响应不是合法 JSON")
    if not data.get("access_token"):
        raise RuntimeError("秒哒登录成功但未返回 access_token")
    return data


def _refresh_token(refresh_token: str) -> dict[str, Any]:
    """使用 refresh_token 刷新 access_token。"""
    resp = requests.post(
        _auth_url(),
        params={"grant_type": "refresh_token"},
        headers={"apikey": EXPORT_API_KEY, "Content-Type": "application/json"},
        json={"refresh_token": refresh_token},
        timeout=REQUEST_TIMEOUT,
    )
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(f"秒哒 refresh 失败 HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError("秒哒 refresh 响应不是合法 JSON")
    if not data.get("access_token"):
        raise RuntimeError("秒哒 refresh 成功但未返回 access_token")
    return data


def get_user_token() -> str:
    """
    获取秒哒导出所需 JWT：
    1) 优先 USER_JWT_TOKEN 环境变量
    2) 再尝试 token 缓存
    3) 缓存不可用时走 refresh / 登录
    """
    token_env = os.getenv("USER_JWT_TOKEN", "").strip()
    if token_env:
        return token_env

    now = int(time.time())
    cached = _load_token_cache()
    if cached and cached.get("access_token"):
        expires_at = int(cached.get("expires_at") or 0)
        if expires_at - now > 60:
            return str(cached["access_token"])
        refresh_token = str(cached.get("refresh_token") or "").strip()
        if refresh_token:
            try:
                data = _refresh_token(refresh_token)
                _save_token_cache(
                    access_token=str(data["access_token"]),
                    refresh_token=str(data.get("refresh_token") or refresh_token),
                    email=str(cached.get("email") or SUPABASE_EMAIL),
                )
                return str(data["access_token"])
            except Exception:
                pass

    if not SUPABASE_EMAIL or not SUPABASE_PASSWORD:
        raise RuntimeError(
            "无法获取秒哒 token：请设置 USER_JWT_TOKEN 或 SUPABASE_EMAIL/SUPABASE_PASSWORD"
        )
    data = _login_supabase(SUPABASE_EMAIL, SUPABASE_PASSWORD)
    _save_token_cache(
        access_token=str(data["access_token"]),
        refresh_token=str(data.get("refresh_token") or ""),
        email=SUPABASE_EMAIL,
    )
    return str(data["access_token"])


def export_students() -> Any:
    """
    导出秒哒意向学员数据（核心测试入口）。
    """
    token = get_user_token()
    resp = requests.post(
        EXPORT_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "apikey": EXPORT_API_KEY,
            "Content-Type": "application/json",
        },
        json={},
        timeout=REQUEST_TIMEOUT,
    )
    if not (200 <= resp.status_code < 300):
        text = (resp.text or "")[:600]
        if resp.status_code == 401:
            raise RuntimeError(f"秒哒导出未授权(401)：{text}")
        raise RuntimeError(f"秒哒导出失败 HTTP {resp.status_code}：{text}")
    try:
        return resp.json()
    except ValueError:
        raise RuntimeError("秒哒导出响应不是合法 JSON")


def _coerce_to_list(data: Any) -> list[dict[str, Any]]:
    """把导出结果归一成 list[dict]，便于统计和回显。"""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "records", "list", "items"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
        return [data]
    return []


def _load_local_feishu_config() -> dict[str, Any]:
    """读取同目录 sync_to_feishu.config.json 的飞书配置节（不存在则返回空）。"""
    if not SYNC_CONFIG_FILE.exists():
        return {}
    try:
        root = json.loads(SYNC_CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(root, dict):
            return {}
        section = root.get("飞书")
        return dict(section) if isinstance(section, dict) else {}
    except Exception:
        return {}


def _get_feishu_runtime_config() -> dict[str, str]:
    """组装本次运行的飞书配置（环境变量优先）。"""
    local_cfg = _load_local_feishu_config()
    app_id = (os.environ.get("FEISHU_APP_ID") or local_cfg.get("app_id") or DEFAULT_FEISHU_CONFIG["app_id"]).strip()
    app_secret = (os.environ.get("FEISHU_APP_SECRET") or local_cfg.get("app_secret") or DEFAULT_FEISHU_CONFIG["app_secret"]).strip()
    app_token = (os.environ.get("FEISHU_APP_TOKEN") or local_cfg.get("app_token") or DEFAULT_FEISHU_CONFIG["app_token"]).strip()
    table_id = (os.environ.get("FEISHU_TABLE_ID") or local_cfg.get("table_id") or DEFAULT_FEISHU_CONFIG["table_id"]).strip()
    api_base = (
        os.environ.get("FEISHU_API_BASE")
        or local_cfg.get("api_base")
        or DEFAULT_FEISHU_CONFIG["api_base"]
    ).strip().rstrip("/")
    missing = [k for k, v in {
        "FEISHU_APP_ID": app_id,
        "FEISHU_APP_SECRET": app_secret,
        "FEISHU_APP_TOKEN": app_token,
        "FEISHU_TABLE_ID": table_id,
    }.items() if not v]
    if missing:
        raise RuntimeError(f"飞书配置缺失：{', '.join(missing)}")
    return {
        "app_id": app_id,
        "app_secret": app_secret,
        "app_token": app_token,
        "table_id": table_id,
        "api_base": api_base,
    }


def _feishu_tenant_token(conf: dict[str, str]) -> str:
    """用 app_id/app_secret 获取飞书 tenant_access_token。"""
    url = f"{conf['api_base']}/auth/v3/tenant_access_token/internal"
    resp = requests.post(
        url,
        json={"app_id": conf["app_id"], "app_secret": conf["app_secret"]},
        timeout=REQUEST_TIMEOUT,
    )
    body = resp.json()
    if resp.status_code != 200 or body.get("code") != 0:
        raise RuntimeError(f"获取飞书 token 失败：HTTP={resp.status_code} body={body}")
    token = str(body.get("tenant_access_token") or "").strip()
    if not token:
        raise RuntimeError("获取飞书 token 失败：tenant_access_token 为空")
    return token


def _feishu_request(
    method: str,
    url: str,
    token: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    max_retry: int = 5,
) -> dict[str, Any]:
    """飞书接口请求统一封装，包含 429/5xx 指数退避重试。"""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    backoff = 1.0
    last_error = ""
    for _ in range(max_retry):
        try:
            resp = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            body = resp.json()
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_error = f"http={resp.status_code}"
                time.sleep(backoff)
                backoff *= 2
                continue
            if not isinstance(body, dict):
                raise RuntimeError(f"飞书响应非 JSON 对象：{body}")
            return body
        except requests.RequestException as exc:
            last_error = str(exc)
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"飞书接口重试失败：{last_error}")


def _list_all_record_ids(conf: dict[str, str], token: str) -> list[str]:
    """分页查询飞书表内全部记录 ID。"""
    url = f"{conf['api_base']}/bitable/v1/apps/{conf['app_token']}/tables/{conf['table_id']}/records"
    out: list[str] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        body = _feishu_request("GET", url, token, params=params)
        if body.get("code") != 0:
            raise RuntimeError(f"查询飞书记录失败：{body}")
        data = body.get("data") or {}
        items = data.get("items") or []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    rid = str(item.get("record_id") or "").strip()
                    if rid:
                        out.append(rid)
        if not data.get("has_more"):
            break
        page_token = str(data.get("page_token") or "").strip()
        if not page_token:
            break
    return out


def _batch_delete_records(conf: dict[str, str], token: str, record_ids: list[str]) -> int:
    """批量删除飞书记录（只删记录，不动表结构）。"""
    if not record_ids:
        return 0
    url = f"{conf['api_base']}/bitable/v1/apps/{conf['app_token']}/tables/{conf['table_id']}/records/batch_delete"
    deleted = 0
    batch_size = 1000
    for i in range(0, len(record_ids), batch_size):
        batch = record_ids[i : i + batch_size]
        body = _feishu_request("POST", url, token, payload={"records": batch})
        if body.get("code") != 0:
            raise RuntimeError(f"删除飞书记录失败：{body}")
        deleted += len(batch)
        _emit(f"[飞书同步] 删除进度：{deleted}/{len(record_ids)}")
    return deleted


def _build_feishu_records(students: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把学员+来源展平为飞书 records。"""
    out: list[dict[str, Any]] = []
    for stu in students:
        student_wx = str(stu.get("意向学员微信号") or "").strip()
        if not student_wx:
            continue
        sources = stu.get("来源")
        src_list = sources if isinstance(sources, list) else []
        valid_sources = [s for s in src_list if isinstance(s, dict)]
        if not valid_sources:
            out.append({"fields": {"意向学员总微信号": student_wx}})
            continue
        for src in valid_sources:
            fields: dict[str, Any] = {"意向学员总微信号": student_wx}
            ref_wx = str(src.get("来源微信号") or "").strip()
            bind_date = str(src.get("绑定日期") or "").strip()
            unbind_date = str(src.get("解绑日期") or "").strip()
            bind_status = str(src.get("绑定状态") or "").strip()
            if ref_wx:
                fields["推荐人总微信号"] = ref_wx
            if bind_date:
                fields["绑定日期"] = bind_date
            if unbind_date:
                fields["解绑日期"] = unbind_date
            if bind_status:
                fields["绑定状态"] = bind_status
            out.append({"fields": fields})
    return out


def _load_contact_rows_from_db(db_path: Path) -> list[dict[str, str]]:
    """从单个 contact.db 读取 alias/username/remark。"""
    if not db_path.exists():
        return []
    rows: list[dict[str, str]] = []
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT alias, username, remark FROM contact")
        for alias, username, remark in cur.fetchall():
            rows.append(
                {
                    "alias": str(alias or "").strip(),
                    "username": str(username or "").strip(),
                    "remark": str(remark or "").strip(),
                }
            )
    finally:
        conn.close()
    return rows


def _build_remark_rewrite_mapping() -> tuple[dict[str, str], dict[str, str]]:
    """
    构建两类映射（按你给的规则）：
    1) source_to_marker：
       来源微信号(alias/username) -> 目标备注标识（由 !!!xxxxxx... 转成 ¿¿¿xxxxxx...）
    2) marker_to_target：
       目标备注标识（contact.remark）-> 目标微信号（alias 优先，否则 username）
    """
    desktop = Path.home() / "Desktop"
    db_files = [
        desktop / "contact_内部专用.db",
        desktop / "contact_内部专用2.db",
    ]

    all_rows: list[dict[str, str]] = []
    for db in db_files:
        try:
            rows = _load_contact_rows_from_db(db)
            all_rows.extend(rows)
            _emit(f"[飞书同步] 已读取通讯录：{db}，记录 {len(rows)} 条")
        except Exception as exc:
            _emit(f"[飞书同步] 读取通讯录失败：{db} -> {exc}")

    source_to_marker: dict[str, str] = {}
    marker_to_target: dict[str, str] = {}

    source_remark_pattern = re.compile(r"^!!!(\d{6})(.*)$")
    target_remark_pattern = re.compile(r"^(?:¿¿¿|？？？|\?\?\?)(\d{6})(.*)$")

    # 第一步：从 "!!!xxxxxx..." 建立 来源微信号 -> "¿¿¿xxxxxx..."
    for row in all_rows:
        m = source_remark_pattern.match(row.get("remark", ""))
        if not m:
            continue
        code = m.group(1)
        suffix = m.group(2) or ""
        marker = f"¿¿¿{code}{suffix}"
        alias = row.get("alias", "")
        username = row.get("username", "")
        if alias:
            source_to_marker[alias.lower()] = marker
        if username:
            source_to_marker[username.lower()] = marker

    # 第二步：建立 marker -> 目标微信号（alias 优先）
    for row in all_rows:
        remark = row.get("remark", "")
        m = target_remark_pattern.match(remark)
        if not m:
            continue
        code = m.group(1)
        suffix = m.group(2) or ""
        marker = f"¿¿¿{code}{suffix}"
        alias = row.get("alias", "")
        username = row.get("username", "")
        if alias:
            marker_to_target[marker] = alias
        elif username and marker not in marker_to_target:
            marker_to_target[marker] = username

    return source_to_marker, marker_to_target


def _apply_remark_rewrite(students: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    按规则改写来源微信号，并打印预览结果：
    - 来源微信号命中 alias/username，且其 remark 以 !!!123456 开头
    - 则来源微信号改成 ¿¿¿123456 开头的 alias/username（alias 优先）
    """
    source_to_marker, marker_to_target = _build_remark_rewrite_mapping()
    if not source_to_marker:
        _emit("[飞书同步] 预览：未找到 remark=!!!xxxxxx 的匹配规则。")
        return students

    changed: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []

    for stu in students:
        student_wx = str(stu.get("意向学员微信号") or "").strip()
        src_list = stu.get("来源")
        if not isinstance(src_list, list):
            continue
        for src in src_list:
            if not isinstance(src, dict):
                continue
            old_ref = str(src.get("来源微信号") or "").strip()
            if not old_ref:
                continue
            marker = source_to_marker.get(old_ref.lower())
            if not marker:
                continue
            new_ref = marker_to_target.get(marker)
            if not new_ref:
                unresolved.append(
                    {
                        "意向学员微信号": student_wx,
                        "原来源微信号": old_ref,
                        "编号": marker[3:9] if len(marker) >= 9 else "",
                        "说明": f"未在 contact.remark 命中：{marker}",
                    }
                )
                continue
            if new_ref != old_ref:
                src["来源微信号"] = new_ref
                changed.append(
                    {
                        "意向学员微信号": student_wx,
                        "原来源微信号": old_ref,
                        "新来源微信号": new_ref,
                        "编号": marker[3:9] if len(marker) >= 9 else "",
                    }
                )

    preview_payload = {
        "changed_count": len(changed),
        "unresolved_count": len(unresolved),
        "changed": changed,
        "unresolved": unresolved,
    }
    REWRITE_PREVIEW_PATH.write_text(
        json.dumps(preview_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _emit(f"[飞书同步] 预览：来源微信号改写 {len(changed)} 条，未命中目标 {len(unresolved)} 条")
    _emit(f"[飞书同步] 预览文件已写入：{REWRITE_PREVIEW_PATH}")
    if changed:
        _emit("[飞书同步] 改写样例（最多前 20 条）：")
        for item in changed[:20]:
            _emit(
                f"[飞书同步] 学员={item['意向学员微信号']} | "
                f"{item['原来源微信号']} -> {item['新来源微信号']} | 编号={item['编号']}"
            )
    if unresolved:
        _emit("[飞书同步] 未命中样例（最多前 20 条）：")
        for item in unresolved[:20]:
            _emit(
                f"[飞书同步] 学员={item['意向学员微信号']} | "
                f"原来源={item['原来源微信号']} | 编号={item['编号']} | {item['说明']}"
            )
    return students


def _write_rewrite_result(original_payload: Any, rewritten_students: list[dict[str, Any]]) -> None:
    """
    把改写后的结果按“原始导出结构”回写到文件：
    - 原始是 dict 且包含 data/records/list/items 任一数组键：替换该键
    - 原始是 list：直接写 list
    - 其他情况：降级写 {"data": [...]}
    """
    output: Any
    if isinstance(original_payload, dict):
        output = dict(original_payload)
        replaced = False
        for key in ("data", "records", "list", "items"):
            if isinstance(output.get(key), list):
                output[key] = rewritten_students
                replaced = True
                break
        if not replaced:
            output["data"] = rewritten_students
    elif isinstance(original_payload, list):
        output = rewritten_students
    else:
        output = {"data": rewritten_students}

    REWRITE_RESULT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _emit(f"[飞书同步] 改写后结果已写入：{REWRITE_RESULT_PATH}")


def _batch_insert_records(conf: dict[str, str], token: str, records: list[dict[str, Any]]) -> int:
    """批量写入飞书记录。"""
    if not records:
        return 0
    url = f"{conf['api_base']}/bitable/v1/apps/{conf['app_token']}/tables/{conf['table_id']}/records/batch_create"
    inserted = 0
    batch_size = 1000
    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        body = _feishu_request("POST", url, token, payload={"records": batch})
        if body.get("code") != 0:
            raise RuntimeError(f"写入飞书失败：{body}")
        inserted += len(batch)
        _emit(f"[飞书同步] 导入进度：{inserted}/{len(records)}")
    return inserted


def sync_to_feishu(students: Any) -> dict[str, int]:
    """
    兼容导入控制台旧接口：
    - 本脚本侧先提供 export 结果统计；
    - 真正写飞书委托给 prospect 主脚本，保持既有写入逻辑稳定。
    """
    normalized_students = _coerce_to_list(students)
    normalized_students = _apply_remark_rewrite(normalized_students)
    _write_rewrite_result(students, normalized_students)
    exported = len(normalized_students)
    records = _build_feishu_records(normalized_students)
    prepared = len(records)

    if PREVIEW_REWRITE_ONLY:
        _emit("[飞书同步] 当前为预览模式：仅打印改写结果，不执行飞书删除/写入。")
        return {
            "deleted": 0,
            "exported": exported,
            "prepared": prepared,
            "inserted": 0,
            "failed": 0,
        }

    conf = _get_feishu_runtime_config()
    token = _feishu_tenant_token(conf)
    record_ids = _list_all_record_ids(conf, token)

    if DRY_RUN:
        _emit("[飞书同步] DRY_RUN=true，本次仅统计不写入")
        _emit(f"[飞书同步] 当前飞书记录数：{len(record_ids)}")
        _emit(f"[飞书同步] 预计写入记录数：{prepared}")
        return {
            "deleted": 0,
            "exported": exported,
            "prepared": prepared,
            "inserted": 0,
            "failed": 0,
        }

    deleted = _batch_delete_records(conf, token, record_ids)
    inserted = _batch_insert_records(conf, token, records)
    failed = max(prepared - inserted, 0)
    return {
        "deleted": deleted,
        "exported": exported,
        "prepared": prepared,
        "inserted": inserted,
        "failed": failed,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="秒哒导出 + 飞书同步（控制台兼容版）")
    parser.add_argument("--only-export", action="store_true", help="只测试秒哒导出，不写飞书")
    parser.add_argument("--dry-run", action="store_true", help="同步阶段只走 dry-run")
    parser.add_argument("--execute", action="store_true", help="同步阶段真实执行")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global DRY_RUN
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.execute:
        DRY_RUN = False
    elif args.dry_run:
        DRY_RUN = True

    # 第一步：先导出（满足“先测试从秒哒导出的结果”）
    students = export_students()
    students_list = _coerce_to_list(students)
    RAW_EXPORT_PATH.write_text(
        json.dumps(students, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _emit(f"[秒哒导出] 成功，学员数：{len(students_list)}")
    _emit(f"[秒哒导出] 原始结果已写入：{RAW_EXPORT_PATH}")

    if args.only_export:
        return 0

    # 第二步：按 DRY_RUN/EXECUTE 走飞书同步
    summary = sync_to_feishu(students)
    _emit("[飞书同步] ════════ 执行汇总 ════════")
    _emit(f"[飞书同步] 已清空记录数        ：{summary['deleted']}")
    _emit(f"[飞书同步] 导出接口学员总数    ：{summary['exported']}")
    _emit(f"[飞书同步] 展平后待导入记录数  ：{summary['prepared']}")
    _emit(f"[飞书同步] 已导入记录数        ：{summary['inserted']}")
    _emit(f"[飞书同步] 导入失败记录数      ：{summary['failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
