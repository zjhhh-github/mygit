# -*- coding: utf-8 -*-
"""
秒哒导出 + 飞书同步（控制台兼容版，重写版）。

设计目标：
1) 先保证“秒哒导出结果”可单独验证（--only-export）。
2) 保留既有 API / token 获取逻辑，不改变外部调用入口。
3) 与导入控制台兼容：提供 export_students()/sync_to_feishu()。
4) 导出改为异步任务模式：create_export_task/process_export_task/task_status。

说明：
- 飞书同步复用 manjike-tools/prospect/同步意向学员到飞书.py 主脚本执行，
  这样可以复用其字段映射、批量写入、重试等成熟逻辑。
"""

from __future__ import annotations

import base64
import csv
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import requests


# ==============================
# 一、秒哒导出配置（保留原有 API 信息）
# ==============================
EXPORT_URL = (
    "https://backend.appmiaoda.com/projects/"
    "supabase293970823448936448/functions/v1/export-students-flat"
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
EXPORT_PAGE_SIZE = 50
MAX_IN_MEMORY_RECORDS = 5000
MAX_WORKER_RETRY = 3
EXPORT_HTTP_RETRY = 3


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
EXPORT_TASK_DB_PATH = SCRIPT_DIR / "export_tasks.db"

CSV_HEADERS = ["意向学员微信号", "来源_json"]

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


def _now_ts() -> int:
    """返回当前 Unix 秒级时间戳。"""
    return int(time.time())


def _init_export_task_db() -> None:
    """初始化导出任务表，用于异步任务模式。"""
    conn = sqlite3.connect(str(EXPORT_TASK_DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS export_tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                filter_json TEXT NOT NULL DEFAULT '{}',
                file_path TEXT NOT NULL DEFAULT '',
                file_url TEXT NOT NULL DEFAULT '',
                total_count INTEGER NOT NULL DEFAULT 0,
                processed_count INTEGER NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _execute_export_task_sql(sql: str, params: tuple[Any, ...] = ()) -> None:
    conn = sqlite3.connect(str(EXPORT_TASK_DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _fetch_export_task(task_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(str(EXPORT_TASK_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM export_tasks WHERE task_id = ?", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"导出任务不存在：{task_id}")
        return dict(row)
    finally:
        conn.close()


def _update_export_task(task_id: str, **kwargs: Any) -> None:
    if not kwargs:
        return
    fields: list[str] = []
    values: list[Any] = []
    for key, value in kwargs.items():
        fields.append(f"{key} = ?")
        values.append(value)
    fields.append("updated_at = ?")
    values.append(_now_ts())
    values.append(task_id)
    sql = f"UPDATE export_tasks SET {', '.join(fields)} WHERE task_id = ?"
    _execute_export_task_sql(sql, tuple(values))


def create_export_task(filter: dict[str, Any] | None = None) -> str:
    """
    创建导出任务，仅写入 export_tasks，不执行导出。
    返回 task_id 供 worker 异步处理。
    """
    _init_export_task_db()
    task_id = str(uuid.uuid4())
    now = _now_ts()
    filter_json = json.dumps(filter or {}, ensure_ascii=False)
    _execute_export_task_sql(
        """
        INSERT INTO export_tasks
        (task_id, status, progress, filter_json, created_at, updated_at)
        VALUES (?, 'pending', 0, ?, ?, ?)
        """,
        (task_id, filter_json, now, now),
    )
    return task_id


def _emit(msg: str) -> None:
    """统一日志输出，兼容导入控制台注入的 log 回调。"""
    try:
        if callable(log):
            log(msg)
            return
    except Exception:
        pass
    try:
        print(msg)
    except UnicodeEncodeError:
        # 某些 Windows 终端是 GBK，遇到 emoji 会报编码错误；降级为可打印文本。
        print(str(msg).encode("gbk", errors="ignore").decode("gbk", errors="ignore"))


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


def _normalize_students_payload(data: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    统一解析 flat API 响应结构：
    {
      "data": [],
      "meta": {
        "last_created_at": "...",
        "last_id": "...",
        "has_more": true
      }
    }
    """
    if not isinstance(data, dict):
        raise RuntimeError("秒哒 flat 导出响应不是 JSON 对象")
    # 新接口约定 success=true 才算业务成功；旧接口未返回 success 时保持兼容。
    if "success" in data and not bool(data.get("success")):
        raise RuntimeError(f"秒哒 flat 导出响应 success=false：{data}")

    items_raw = data.get("data")
    if not isinstance(items_raw, list):
        raise RuntimeError("秒哒 flat 导出响应缺少 data 数组")
    items = [x for x in items_raw if isinstance(x, dict)]

    meta_raw = data.get("meta")
    meta = dict(meta_raw) if isinstance(meta_raw, dict) else {}
    meta.setdefault("last_created_at", "")
    meta.setdefault("last_id", "")
    meta["has_more"] = bool(meta.get("has_more"))
    return items, meta


def _extract_cursor_value(item: dict[str, Any]) -> tuple[str, str]:
    """
    从单条学生记录中提取游标值：
    - 主游标优先新字段 student_id，其次兼容旧字段 student_original_id/id
    - 辅助游标优先新字段 student_created_at，其次兼容 created_at/create_time
    """
    raw_id = item.get("student_id")
    if raw_id is None or raw_id == "":
        raw_id = item.get("id")
    if raw_id is None or raw_id == "":
        raw_id = item.get("student_original_id")
    cursor_id = str(raw_id or "").strip()
    cursor_time = str(
        item.get("student_created_at")
        or item.get("created_at")
        or item.get("create_time")
        or ""
    ).strip()
    return cursor_id, cursor_time


def _is_retryable_export_error(resp: requests.Response | None, err: Exception | None) -> bool:
    """判断导出请求是否属于可重试错误（HTTP500/timeout）。"""
    if resp is not None and resp.status_code >= 500:
        return True
    if err is None:
        return False
    msg = str(err).lower()
    return "timeout" in msg or "statement timeout" in msg


def _fetch_students_page(
    token: str,
    last_id: str,
    last_create_time: str,
    page_size: int,
    filter_payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool, str, str]:
    """基于 flat API + cursor 调用秒哒导出接口。"""
    payload = dict(filter_payload)
    payload["limit"] = page_size
    if last_id:
        payload["last_id"] = last_id
    if last_create_time:
        payload["last_created_at"] = last_create_time

    wait_seconds = 1
    for attempt in range(1, EXPORT_HTTP_RETRY + 1):
        resp: requests.Response | None = None
        err: Exception | None = None
        try:
            resp = requests.post(
                EXPORT_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": EXPORT_API_KEY,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            if not (200 <= resp.status_code < 300):
                text = (resp.text or "")[:600]
                if resp.status_code == 401:
                    raise RuntimeError(f"秒哒导出未授权(401)：{text}")
                raise RuntimeError(f"秒哒导出失败 HTTP {resp.status_code}：{text}")

            try:
                data = resp.json()
            except ValueError:
                raise RuntimeError("秒哒导出响应不是合法 JSON")

            items, meta = _normalize_students_payload(data)
            next_last_id = str(meta.get("last_id") or "").strip()
            next_last_create_time = str(meta.get("last_created_at") or "").strip()
            has_more = bool(meta.get("has_more"))

            # meta 缺失时，用当前页最后一条兜底推进 cursor。
            if items and (not next_last_id or not next_last_create_time):
                fallback_last_id, fallback_last_created_at = _extract_cursor_value(items[-1])
                if not next_last_id:
                    next_last_id = fallback_last_id
                if not next_last_create_time:
                    next_last_create_time = fallback_last_created_at

            _emit(
                f"[秒哒导出] flat分页：last_created_at={last_create_time or '-'} "
                f"last_id={last_id or '-'} page_size={page_size} returned={len(items)}"
            )
            return items, has_more, next_last_id, next_last_create_time
        except Exception as exc:
            err = exc
            if attempt >= EXPORT_HTTP_RETRY or not _is_retryable_export_error(resp, err):
                raise
            _emit(
                f"[秒哒导出] 分页失败重试 {attempt}/{EXPORT_HTTP_RETRY - 1}：{exc}，"
                f"{wait_seconds}s 后重试（last_created_at={last_create_time or '-'} "
                f"last_id={last_id or '-'} page_size={page_size}）"
            )
            time.sleep(wait_seconds)
            wait_seconds *= 2

    raise RuntimeError("秒哒导出分页重试失败")


def upload_file(file_path: str) -> str:
    """
    mock 上传函数：返回 file:// URL。
    后续接入真实 storage 时只需替换此函数实现。
    """
    path = Path(file_path).resolve()
    return path.as_uri()


def process_export_task(task_id: str) -> str:
    """
    处理导出任务：
    - 分页拉取秒哒数据（禁止一次性加载）
    - CSV 流式写入
    - 上传到 storage（mock）
    - 更新任务状态和 file_url
    """
    _emit(f"[秒哒导出] worker 开始处理任务：{task_id}")
    _init_export_task_db()
    task = _fetch_export_task(task_id)
    if task["status"] == "success" and str(task.get("file_url") or "").strip():
        _emit(f"[秒哒导出] 任务已完成，直接复用 file_url：{task.get('file_url')}")
        return str(task["file_url"])

    filter_payload = {}
    try:
        filter_payload = json.loads(str(task.get("filter_json") or "{}"))
        if not isinstance(filter_payload, dict):
            filter_payload = {}
    except Exception:
        filter_payload = {}

    _update_export_task(task_id, status="processing", progress=1, error_message="")
    token = get_user_token()
    csv_path = SCRIPT_DIR / f"export_task_{task_id}.csv"

    attempts = 0
    while attempts < MAX_WORKER_RETRY:
        try:
            total_count = 0
            processed = 0
            last_id = ""
            last_create_time = ""
            with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
                writer.writeheader()

                while True:
                    prev_last_id = last_id
                    prev_last_created_at = last_create_time
                    page_items, has_more, next_last_id, next_last_create_time = _fetch_students_page(
                        token=token,
                        last_id=last_id,
                        last_create_time=last_create_time,
                        page_size=EXPORT_PAGE_SIZE,
                        filter_payload=filter_payload,
                    )
                    if len(page_items) > MAX_IN_MEMORY_RECORDS:
                        raise RuntimeError("单页数据超过 5000 条，违反内存约束，任务终止。")

                    for stu in page_items:
                        # 兼容新老导出字段：优先 student_wechat_id，其次 wechat_id/意向学员微信号。
                        student_wx = str(
                            stu.get("student_wechat_id")
                            or stu.get("wechat_id")
                            or stu.get("意向学员微信号")
                            or ""
                        ).strip()
                        # flat 接口一行就是一个“学员-来源关系”；导出到 CSV 时转换为来源数组，兼容后续同步逻辑。
                        source_wechat = str(
                            stu.get("source_wechat_id")
                            or stu.get("来源微信号")
                            or ""
                        ).strip()
                        bind_date = str(stu.get("bind_date") or stu.get("绑定日期") or "").strip()
                        unbind_date = str(stu.get("unbind_date") or stu.get("解绑日期") or "").strip()
                        bind_status = str(
                            stu.get("bind_status")
                            or stu.get("绑定状态")
                            or stu.get("student_enrollment_status")
                            or stu.get("enrollment_status")
                            or ""
                        ).strip()
                        source_item = {}
                        if source_wechat:
                            source_item["来源微信号"] = source_wechat
                        if bind_date:
                            source_item["绑定日期"] = bind_date
                        if unbind_date:
                            source_item["解绑日期"] = unbind_date
                        if bind_status:
                            source_item["绑定状态"] = bind_status
                        source_list = [source_item] if source_item else []
                        row = {
                            "意向学员微信号": student_wx,
                            "来源_json": json.dumps(source_list, ensure_ascii=False),
                        }
                        writer.writerow(row)
                        processed += 1

                    if total_count > 0:
                        progress = max(1, min(int(processed * 100 / total_count), 99))
                    else:
                        progress = 50 if processed > 0 else 1
                    _update_export_task(
                        task_id,
                        progress=progress,
                        processed_count=processed,
                        total_count=total_count,
                        retry_count=attempts,
                    )

                    # 强制按“当前页最后一条记录”推进游标，避免重复拉取同一页。
                    if page_items:
                        page_last_id = str(
                            page_items[-1].get("student_id")
                            or page_items[-1].get("student_original_id")
                            or page_items[-1].get("id")
                            or ""
                        ).strip()
                        last_id = page_last_id or next_last_id
                    else:
                        last_id = next_last_id
                    last_create_time = next_last_create_time

                    # 死循环保护：游标未前进则立即终止分页，避免无限重复同一页。
                    if not page_items:
                        _emit("⚠️ 检测到cursor未推进，终止防死循环（当前页为空）")
                        break
                    if last_id == prev_last_id or last_create_time == prev_last_created_at:
                        _emit("⚠️ 检测到cursor未推进，终止防死循环")
                        break
                    if not page_items or not has_more:
                        break

            file_url = upload_file(str(csv_path))
            _update_export_task(
                task_id,
                status="success",
                progress=100,
                file_path=str(csv_path),
                file_url=file_url,
                processed_count=processed,
                total_count=total_count,
                retry_count=attempts,
                error_message="",
            )
            _emit(f"[秒哒导出] worker 完成任务：{task_id}")
            return file_url
        except Exception as exc:
            attempts += 1
            if attempts >= MAX_WORKER_RETRY:
                _update_export_task(
                    task_id,
                    status="failed",
                    progress=0,
                    retry_count=attempts,
                    error_message=str(exc),
                )
                raise
            sleep_seconds = 2 ** attempts
            _update_export_task(
                task_id,
                status="retrying",
                retry_count=attempts,
                error_message=f"{exc}（{sleep_seconds} 秒后重试）",
            )
            time.sleep(sleep_seconds)

    raise RuntimeError(f"任务处理失败：{task_id}")


def export_students(filter: dict[str, Any] | None = None) -> str:
    """
    兼容导入控制台旧入口：
    - 现在只创建任务并返回 task_id；
    - 不再直接返回导出全量数据。
    """
    task_id = create_export_task(filter or {})
    _emit(f"[秒哒导出] 已创建异步任务：{task_id}")
    return task_id


def run_worker_loop() -> None:
    """
    后台常驻 worker：
    - while True 无限循环
    - 每 2 秒轮询 pending 任务
    - 逐个调用 process_export_task(task_id)
    """
    _emit("[秒哒导出] worker 模式已启动，开始轮询 pending 任务。")
    _init_export_task_db()
    while True:
        pending_task_ids: list[str] = []
        conn = sqlite3.connect(str(EXPORT_TASK_DB_PATH))
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT task_id
                FROM export_tasks
                WHERE status = 'pending'
                ORDER BY created_at ASC
                """
            )
            rows = cur.fetchall()
            pending_task_ids = [str(row[0]) for row in rows if row and row[0]]
        except Exception as exc:
            _emit(f"[秒哒导出] worker 查询 pending 任务失败：{exc}")
        finally:
            conn.close()

        for task_id in pending_task_ids:
            try:
                _emit(f"[秒哒导出] worker 领取任务：{task_id}")
                process_export_task(task_id)
            except Exception as exc:
                _emit(f"[秒哒导出] worker 处理任务失败：task_id={task_id} error={exc}")

        time.sleep(2)


def task_status(task_id: str) -> dict[str, Any]:
    """查询任务状态：status / progress / file_url。"""
    task = _fetch_export_task(task_id)
    return {
        "task_id": task_id,
        "status": str(task.get("status") or ""),
        "progress": int(task.get("progress") or 0),
        "file_url": str(task.get("file_url") or ""),
        "error_message": str(task.get("error_message") or ""),
    }


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


def _build_feishu_records_from_flat(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    flat API 数据直接转飞书 records。
    仅做字段适配，不做 join/拼接计算。
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        student_wx = str(
            row.get("意向学员总微信号")
            or row.get("意向学员微信号")
            or row.get("student_wechat_id")
            or row.get("wechat_id")
            or ""
        ).strip()
        if not student_wx:
            continue

        fields: dict[str, Any] = {"意向学员总微信号": student_wx}
        ref_wx = str(
            row.get("推荐人总微信号")
            or row.get("来源微信号")
            or row.get("source_wechat_id")
            or ""
        ).strip()
        bind_date = str(row.get("绑定日期") or row.get("bind_date") or "").strip()
        unbind_date = str(row.get("解绑日期") or row.get("unbind_date") or "").strip()
        bind_status = str(
            row.get("绑定状态")
            or row.get("bind_status")
            or row.get("student_enrollment_status")
            or row.get("enrollment_status")
            or ""
        ).strip()

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


def _download_csv_from_file_url(file_url: str) -> Path:
    """
    根据 file_url 下载/定位 CSV 文件：
    - file:// 直接映射本地路径
    - http(s) 下载到脚本目录
    """
    if not file_url:
        raise RuntimeError("file_url 为空，无法下载导出结果")
    if file_url.startswith("file://"):
        local_path = Path(file_url.replace("file:///", "").replace("file://", ""))
        if not local_path.exists():
            raise RuntimeError(f"CSV 文件不存在：{local_path}")
        return local_path

    if file_url.startswith("http://") or file_url.startswith("https://"):
        target = SCRIPT_DIR / f"download_{int(time.time())}.csv"
        resp = requests.get(file_url, timeout=REQUEST_TIMEOUT)
        if not (200 <= resp.status_code < 300):
            raise RuntimeError(f"下载 CSV 失败 HTTP {resp.status_code}")
        target.write_bytes(resp.content)
        return target

    local_path = Path(file_url)
    if local_path.exists():
        return local_path
    raise RuntimeError(f"不支持的 file_url：{file_url}")


def _load_students_from_csv(csv_path: Path) -> list[dict[str, Any]]:
    """
    从任务产出的 CSV 还原 students 结构，供原飞书同步逻辑复用。
    CSV 单行一个学生，不在内存中保留原始超大导出结构。
    """
    students: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if len(students) >= MAX_IN_MEMORY_RECORDS:
                raise RuntimeError("同步前加载 CSV 超过 5000 条，请改为分批同步模式。")
            student_wx = str(row.get("意向学员微信号") or "").strip()
            raw_sources = str(row.get("来源_json") or "[]").strip()
            try:
                source_list = json.loads(raw_sources)
                if not isinstance(source_list, list):
                    source_list = []
            except Exception:
                source_list = []
            students.append({"意向学员微信号": student_wx, "来源": source_list})
    return students


def sync_to_feishu(students: Any) -> dict[str, int]:
    """
    兼容导入控制台旧接口：
    - 本脚本侧先提供 export 结果统计；
    - 真正写飞书委托给 prospect 主脚本，保持既有写入逻辑稳定。
    """
    if isinstance(students, str):
        status = task_status(students)
        file_url = str(status.get("file_url") or "").strip()
        if not file_url:
            raise RuntimeError(
                f"任务尚未产出 file_url，当前状态：{status.get('status')}，进度：{status.get('progress')}"
            )
        csv_path = _download_csv_from_file_url(file_url)
        normalized_students = _load_students_from_csv(csv_path)
    elif isinstance(students, dict) and "file_url" in students:
        csv_path = _download_csv_from_file_url(str(students.get("file_url") or ""))
        normalized_students = _load_students_from_csv(csv_path)
    else:
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


def run_export_worker() -> None:
    """
    新导出主流程：
    - flat API cursor 分页拉取
    - 飞书流式写入（分页写入）
    - 防死循环保护
    """
    _emit("[秒哒导出] run_export_worker 启动")
    token = get_user_token()
    conf = _get_feishu_runtime_config()
    feishu_token = _feishu_tenant_token(conf)

    record_ids = _list_all_record_ids(conf, feishu_token)
    deleted = _batch_delete_records(conf, feishu_token, record_ids)
    _emit(f"[飞书同步] 已清空记录数：{deleted}")

    last_created_at = ""
    last_id = ""
    total_exported = 0
    total_inserted = 0
    page_index = 0

    while True:
        prev_last_created_at = last_created_at
        prev_last_id = last_id
        rows, has_more, next_last_id, next_last_created_at = _fetch_students_page(
            token=token,
            last_id=last_id,
            last_create_time=last_created_at,
            page_size=EXPORT_PAGE_SIZE,
            filter_payload={},
        )

        page_index += 1
        page_count = len(rows)
        total_exported += page_count
        _emit(f"[秒哒导出] 第 {page_index} 页导出：{page_count} 条")

        if page_count == 0:
            _emit("⚠️ 检测到cursor未推进，终止防死循环（当前页为空）")
            break

        records = _build_feishu_records_from_flat(rows)
        inserted = _batch_insert_records(conf, feishu_token, records)
        total_inserted += inserted
        _emit(f"[飞书同步] 第 {page_index} 页写入：{inserted}/{len(records)} 条")

        last_created_at = next_last_created_at
        last_id = next_last_id
        if last_created_at == prev_last_created_at or last_id == prev_last_id:
            _emit("⚠️ 检测到cursor未推进，终止防死循环")
            break

        if not has_more:
            break

    _emit("[秒哒导出] ════════ 执行汇总 ════════")
    _emit(f"[秒哒导出] 导出总数：{total_exported}")
    _emit(f"[飞书同步] 写入总数：{total_inserted}")


def main() -> int:
    run_export_worker()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
