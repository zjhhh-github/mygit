# -*- coding: utf-8 -*-
"""
独立脚本：调用 export-students-flat 接口并写入飞书多维表格。

使用说明（PowerShell）：
1) 先设置环境变量（必须）：
   $env:SUPABASE_EXPORT_URL="https://xxx.supabase.co/functions/v1/export-students-flat"
   $env:SUPABASE_KEY="your_anon_or_service_role_key"
   $env:FEISHU_APP_ID="cli_xxx"
   $env:FEISHU_APP_SECRET="xxx"
   $env:FEISHU_APP_TOKEN="xxx"
   $env:FEISHU_TABLE_ID="tblxxx"

2) 执行脚本：
   python .\\export_students_flat_to_feishu.py --clear-existing
"""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


REQUEST_TIMEOUT = 30
DEFAULT_LIMIT = 500
DEFAULT_FEISHU_DELETE_BATCH_SIZE = 1000
DEFAULT_FEISHU_INSERT_BATCH_SIZE = 1000
# 拉取 record_id 时只请求 1 个业务字段，减小分页响应体积（record_id 仍在 item 根上返回）
FEISHU_LIST_ID_FIELD_NAMES = ["意向学员总微信号"]

# 与现有脚本保持一致：默认导出接口与密钥（环境变量可覆盖）
DEFAULT_SUPABASE_EXPORT_URL = (
    "https://backend.appmiaoda.com/projects/"
    "supabase293970823448936448/functions/v1/export-students-flat"
)
DEFAULT_SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoyMDg5NTE1MzA2LCJpc3MiOiJzdXBhYmFzZSIs"
    "InJvbGUiOiJhbm9uIiwic3ViIjoiYW5vbiJ9."
    "Z19rhe7D6v4pXthoontMmG_C1U3yW6DTTSyFOKYvs54"
)
DEFAULT_SUPABASE_BASE_URL = "https://backend.appmiaoda.com/projects/supabase293970823448936448"
DEFAULT_SUPABASE_EMAIL = "15648230994@miaoda.com"
DEFAULT_SUPABASE_PASSWORD = "028056hQ@"

# 与现有脚本保持一致：默认飞书配置（环境变量可覆盖）
DEFAULT_FEISHU_APP_ID = "cli_a96f36ed1538dbcf"
DEFAULT_FEISHU_APP_SECRET = "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB"
DEFAULT_FEISHU_APP_TOKEN = "Zk05bwki2abD8XsBBOccaFsPn8e"
DEFAULT_FEISHU_TABLE_ID = "tblNIWZ1EsDyZ1ug"
DEFAULT_FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

# 兼容导入控制台注入：mod.DRY_RUN / mod.log
DRY_RUN = False
log = print
# 导入控制台直调入口默认“先删后写”；如需保留旧数据，可在宿主侧注入 False 覆盖。
SYNC_CLEAR_EXISTING_DEFAULT = True
SCRIPT_DIR = Path(__file__).resolve().parent
REWRITE_PREVIEW_PATH = SCRIPT_DIR / "rewrite_preview.json"
RECORDS_PREVIEW_PATH = SCRIPT_DIR / "records_preview.json"
PREVIEW_PRINT_LIMIT = 30
BACKUP_DIR = Path(r"X:\backup\秒哒导出意向通讯录")


def _env_or_default(name: str, default_value: str) -> str:
    """环境变量优先，否则使用脚本默认值。"""
    value = (os.getenv(name) or default_value or "").strip()
    if not value:
        raise RuntimeError(f"配置缺失：{name}")
    return value


def _emit(msg: str) -> None:
    """统一日志输出，兼容导入控制台注入的 log 回调。"""
    try:
        if callable(log):
            log(str(msg))
            return
    except Exception:
        pass
    print(msg)


def _load_contact_rows_from_db(db_path: Path) -> List[Dict[str, str]]:
    """读取通讯录库 contact 表的 alias/username/remark。"""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    rows: List[Dict[str, str]] = []
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


def _build_referrer_rewrite_mapping() -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    构建来源微信号改写映射：
    1) source_to_marker: 来源微信号(alias/username) -> 目标备注标识（由 !!!xxxxxx... 转成 ¿¿¿xxxxxx...）
    2) marker_to_target: 目标备注标识 -> 目标微信号（alias 优先，否则 username）
    """
    desktop = Path.home() / "Desktop"
    db_files = [
        desktop / "contact_内部专用.db",
        desktop / "contact_内部专用2.db",
    ]
    all_rows: List[Dict[str, str]] = []
    for db in db_files:
        try:
            rows = _load_contact_rows_from_db(db)
            all_rows.extend(rows)
            _emit("已读取通讯录：{0}，记录 {1} 条".format(db, len(rows)))
        except Exception as exc:
            _emit("读取通讯录失败：{0} -> {1}".format(db, exc))

    source_to_marker: Dict[str, str] = {}
    marker_to_target: Dict[str, str] = {}
    source_remark_pattern = re.compile(r"^!!!(\d{6})(.*)$")
    target_remark_pattern = re.compile(r"^(?:¿¿¿|？？？|\?\?\?)(\d{6})(.*)$")

    # 第一步：建立 来源微信号 -> marker
    for row in all_rows:
        m = source_remark_pattern.match(row.get("remark", ""))
        if not m:
            continue
        marker = "¿¿¿{0}{1}".format(m.group(1), m.group(2) or "")
        alias = row.get("alias", "")
        username = row.get("username", "")
        if alias:
            source_to_marker[alias.lower()] = marker
        if username:
            source_to_marker[username.lower()] = marker

    # 第二步：建立 marker -> 目标微信号（alias 优先）
    for row in all_rows:
        m = target_remark_pattern.match(row.get("remark", ""))
        if not m:
            continue
        marker = "¿¿¿{0}{1}".format(m.group(1), m.group(2) or "")
        alias = row.get("alias", "")
        username = row.get("username", "")
        if alias:
            marker_to_target[marker] = alias
        elif username and marker not in marker_to_target:
            marker_to_target[marker] = username

    return source_to_marker, marker_to_target


def _resolve_referrer_wechat(row: Dict[str, Any]) -> str:
    """
    从 export-students-flat 一行数据中解析推荐人总微信号。

    API 常见情况：source_wechat_id 为空，但 referrer_total_wechat_no
    或 source_wechat_original_id 仍有值，需按优先级兜底读取。
    """
    return str(
        row.get("referrer_total_wechat_no")
        or row.get("source_wechat_id")
        or row.get("source_wechat_original_id")
        or row.get("来源微信号")
        or row.get("推荐人总微信号")
        or ""
    ).strip()


def _set_referrer_wechat(row: Dict[str, Any], value: str) -> None:
    """改写推荐人微信号后，同步写入 API 标准字段，避免后续映射仍读到旧值。"""
    row["referrer_total_wechat_no"] = value
    row["source_wechat_id"] = value
    if "来源微信号" in row:
        row["来源微信号"] = value
    if "推荐人总微信号" in row:
        row["推荐人总微信号"] = value


def _apply_referrer_wechat_rewrite(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    对 flat 导出行做来源微信号改写（小号替换大号）：
    - 命中来源号 alias/username 且其备注为 !!!xxxxxx...
    - 再根据 ¿¿¿xxxxxx... 找到目标 alias/username
    """
    source_to_marker, marker_to_target = _build_referrer_rewrite_mapping()
    if not source_to_marker:
        _emit("预览：未找到 remark=!!!xxxxxx 的匹配规则，本次不改写。")
        return rows

    changed: List[Dict[str, str]] = []
    unresolved: List[Dict[str, str]] = []
    rewritten: List[Dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        new_row = dict(row)
        student_wechat = str(
            new_row.get("student_wechat_id")
            or new_row.get("意向学员微信号")
            or new_row.get("wechat_id")
            or ""
        ).strip()
        old_ref = _resolve_referrer_wechat(new_row)
        if not old_ref:
            rewritten.append(new_row)
            continue

        marker = source_to_marker.get(old_ref.lower())
        if not marker:
            rewritten.append(new_row)
            continue

        new_ref = marker_to_target.get(marker)
        if not new_ref:
            unresolved.append(
                {
                    "意向学员微信号": student_wechat,
                    "原来源微信号": old_ref,
                    "编号": marker[3:9] if len(marker) >= 9 else "",
                    "说明": "未在 contact.remark 命中：{0}".format(marker),
                }
            )
            rewritten.append(new_row)
            continue

        if new_ref != old_ref:
            _set_referrer_wechat(new_row, new_ref)
            changed.append(
                {
                    "意向学员微信号": student_wechat,
                    "原来源微信号": old_ref,
                    "新来源微信号": new_ref,
                    "编号": marker[3:9] if len(marker) >= 9 else "",
                }
            )
        rewritten.append(new_row)

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
    _emit("预览：来源微信号改写 {0} 条，未命中目标 {1} 条".format(len(changed), len(unresolved)))
    _emit("预览文件已写入：{0}".format(REWRITE_PREVIEW_PATH))
    if changed:
        _emit("改写样例（最多前 {0} 条）：".format(PREVIEW_PRINT_LIMIT))
        for item in changed[:PREVIEW_PRINT_LIMIT]:
            _emit(
                "学员={0} | {1} -> {2} | 编号={3}".format(
                    item.get("意向学员微信号", ""),
                    item.get("原来源微信号", ""),
                    item.get("新来源微信号", ""),
                    item.get("编号", ""),
                )
            )
    if unresolved:
        _emit("未命中样例（最多前 {0} 条）：".format(PREVIEW_PRINT_LIMIT))
        for item in unresolved[:PREVIEW_PRINT_LIMIT]:
            _emit(
                "学员={0} | 原来源={1} | 编号={2} | {3}".format(
                    item.get("意向学员微信号", ""),
                    item.get("原来源微信号", ""),
                    item.get("编号", ""),
                    item.get("说明", ""),
                )
            )
    return rewritten


def _print_records_preview(records: List[Dict[str, Any]]) -> None:
    """
    打印“准备写入飞书”的样例并落盘全量 JSON。
    说明：
    - 终端只打印前 PREVIEW_PRINT_LIMIT 条，避免刷屏；
    - 完整记录写入 records_preview.json，便于人工全量核对。
    """
    try:
        RECORDS_PREVIEW_PATH.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _emit("飞书写入预览文件已写入：{0}".format(RECORDS_PREVIEW_PATH))
    except Exception as exc:
        _emit("写入飞书预览文件失败：{0}".format(exc))

    _emit("准备写入飞书记录总数：{0}".format(len(records)))
    if not records:
        return
    _emit("准备写入飞书样例（最多前 {0} 条）：".format(PREVIEW_PRINT_LIMIT))
    for idx, rec in enumerate(records[:PREVIEW_PRINT_LIMIT], start=1):
        _emit("[{0}] {1}".format(idx, json.dumps(rec, ensure_ascii=False)))


def _backup_records_for_write(records: List[Dict[str, Any]]) -> Path:
    """
    备份即将写入飞书的记录。
    目标路径：X:\\backup\\秒哒导出意向通讯录\\秒哒导出意向通讯录到飞书_yyyymmdd.json
    """
    date_str = datetime.now().strftime("%Y%m%d")
    backup_file = BACKUP_DIR / "秒哒导出意向通讯录到飞书_{0}.json".format(date_str)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    _emit("写入内容备份完成：{0}".format(backup_file))
    return backup_file


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    """统一 POST JSON 请求，返回 JSON 字典。"""
    resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP 请求失败：{resp.status_code} {resp.text[:400]}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"响应不是合法 JSON：{resp.text[:200]}") from exc
    if not isinstance(body, dict):
        raise RuntimeError(f"响应不是 JSON 对象：{body}")
    return body


def _supabase_auth_url(supabase_base_url: str) -> str:
    return f"{supabase_base_url.rstrip('/')}/auth/v1/token"


def _login_supabase_user_token(supabase_base_url: str, supabase_key: str, email: str, password: str) -> str:
    """使用账号密码换取用户 JWT（与现有脚本逻辑一致）。"""
    resp = requests.post(
        _supabase_auth_url(supabase_base_url),
        params={"grant_type": "password"},
        headers={"apikey": supabase_key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=REQUEST_TIMEOUT,
    )
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(f"秒哒登录失败：HTTP {resp.status_code} {resp.text[:300]}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"秒哒登录响应不是 JSON：{resp.text[:200]}") from exc
    token = str((body or {}).get("access_token") or "").strip()
    if not token:
        raise RuntimeError(f"秒哒登录成功但 access_token 为空：{body}")
    return token


def fetch_students_flat(
    export_url: str,
    supabase_key: str,
    supabase_base_url: str,
    supabase_email: str,
    supabase_password: str,
    limit: int,
    sleep_seconds: float,
    max_pages: int,
) -> List[Dict[str, Any]]:
    """
    按接口约定执行游标翻页，直到 has_more=False。
    接口响应结构：
    {
      "success": true,
      "data": [...],
      "meta": {"last_created_at": "...", "last_id": "...", "has_more": true}
    }
    """
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key,
        "Content-Type": "application/json",
    }

    all_rows: List[Dict[str, Any]] = []
    last_created_at: Optional[str] = None
    last_id: Optional[str] = None
    has_more = True
    page = 0

    _emit("开始拉取 export-students-flat 数据...")
    # 参数名自适应：默认使用 limit；若命中 414，则切换到 page_size 并保持到任务结束。
    page_size_param_name = "limit"
    use_page_size_param = True
    # 分页大小降级策略：优先 500，失败后逐级降到 200/100/50，兼顾速度与稳定性。
    page_size_candidates = [500, 200, 100, 50]
    current_page_size = limit
    if current_page_size not in page_size_candidates:
        page_size_candidates.insert(0, current_page_size)

    def _next_smaller_page_size(size: int) -> Optional[int]:
        for candidate in page_size_candidates:
            if candidate < size:
                return candidate
        return None
    while has_more:
        if 0 < max_pages <= page:
            _emit("达到 max_pages={0}，提前停止翻页。".format(max_pages))
            break

        # 单页请求重试循环：处理 401（刷新 token）与 414（参数名/页大小降级）。
        retried_auth = False
        timeout_retry_count = 0
        max_timeout_retry = 3
        while True:
            payload: Dict[str, Any] = {}
            if use_page_size_param:
                payload[page_size_param_name] = current_page_size
            if last_created_at:
                payload["last_created_at"] = last_created_at
            if last_id:
                payload["last_id"] = last_id
            try:
                result = _post_json(export_url, headers, payload)
                break
            except RuntimeError as exc:
                err_text = str(exc)
                if "HTTP 请求失败：401" in err_text and not retried_auth:
                    _emit("检测到 401，尝试使用账号密码换取用户 token 后重试...")
                    user_token = _login_supabase_user_token(
                        supabase_base_url=supabase_base_url,
                        supabase_key=supabase_key,
                        email=supabase_email,
                        password=supabase_password,
                    )
                    headers["Authorization"] = "Bearer {0}".format(user_token)
                    retried_auth = True
                    continue

                if "414 Request-URI Too Large" in err_text:
                    if page_size_param_name == "limit":
                        page_size_param_name = "page_size"
                        _emit("检测到 414，自动切换参数名为 page_size 重试...")
                        continue
                    next_size = _next_smaller_page_size(current_page_size)
                    if next_size is not None:
                        current_page_size = next_size
                        _emit("检测到 414，自动降级分页大小为 {0} 重试...".format(current_page_size))
                        continue
                    if use_page_size_param:
                        use_page_size_param = False
                        _emit("检测到 414，分页大小参数已移除，改为仅传游标重试...")
                        continue
                    raise RuntimeError(
                        "分页大小已降到最小且移除分页参数后仍触发 414，请后端放宽查询或优化视图。"
                    )
                if "statement timeout" in err_text.lower():
                    timeout_retry_count += 1
                    if timeout_retry_count > max_timeout_retry:
                        raise RuntimeError("秒哒查询连续超时，已超过最大重试次数，请稍后重试。")
                    wait_seconds = 2 ** timeout_retry_count
                    _emit("检测到秒哒查询超时，{0}s 后重试当前页...".format(wait_seconds))
                    time.sleep(wait_seconds)
                    continue
                raise
        if not bool(result.get("success")):
            raise RuntimeError(f"接口返回 success=false：{result}")

        data = result.get("data", [])
        if not isinstance(data, list):
            raise RuntimeError("接口返回 data 不是数组")
        page_rows = [x for x in data if isinstance(x, dict)]

        meta = result.get("meta", {})
        if not isinstance(meta, dict):
            meta = {}
        has_more = bool(meta.get("has_more", False))
        next_last_created_at = str(meta.get("last_created_at") or "").strip()
        next_last_id = str(meta.get("last_id") or "").strip()

        page += 1
        all_rows.extend(page_rows)
        _emit("第 {0} 页拉取完成：{1} 条，累计 {2} 条。".format(page, len(page_rows), len(all_rows)))

        # 游标推进保护：接口标记 has_more=True 但游标为空时，避免死循环。
        if has_more and (not next_last_created_at or not next_last_id):
            raise RuntimeError("接口返回 has_more=true，但 meta.last_created_at/last_id 缺失，已中止避免死循环。")

        last_created_at = next_last_created_at or None
        last_id = next_last_id or None

        if has_more and sleep_seconds > 0:
            time.sleep(sleep_seconds)

    _emit("拉取完成，总记录数：{0}".format(len(all_rows)))
    return all_rows


def _to_feishu_record(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把一条 flat 数据映射为飞书多维表格 record。"""
    student_wechat = str(
        row.get("student_wechat_id")
        or row.get("意向学员微信号")
        or row.get("wechat_id")
        or ""
    ).strip()
    if not student_wechat:
        return None

    fields: Dict[str, Any] = {
        "意向学员总微信号": student_wechat,
    }

    source_wechat = _resolve_referrer_wechat(row)
    bind_date = str(row.get("bind_date") or row.get("绑定日期") or "").strip()
    unbind_date = str(row.get("unbind_date") or row.get("解绑日期") or "").strip()
    bind_status = str(
        row.get("bind_status")
        or row.get("绑定状态")
        or row.get("student_enrollment_status")
        or ""
    ).strip()

    if source_wechat:
        fields["推荐人总微信号"] = source_wechat
    if bind_date:
        fields["绑定日期"] = bind_date
    if unbind_date and unbind_date.lower() != "none":
        fields["解绑日期"] = unbind_date
    if bind_status:
        fields["绑定状态"] = bind_status

    return {"fields": fields}


def _feishu_token(
    api_base: str,
    app_id: str,
    app_secret: str,
    session: Optional[requests.Session] = None,
) -> str:
    url = f"{api_base}/auth/v3/tenant_access_token/internal"
    body = _post_json(url, {"Content-Type": "application/json"}, {"app_id": app_id, "app_secret": app_secret})
    _ = session
    if body.get("code") != 0:
        raise RuntimeError(f"获取飞书 tenant_access_token 失败：{body}")
    token = str(body.get("tenant_access_token") or "").strip()
    if not token:
        raise RuntimeError("飞书 tenant_access_token 为空")
    return token


def _format_feishu_field_names(field_names: List[str]) -> str:
    """飞书 GET records 要求 field_names 为 JSON 数组字符串。"""
    return "[" + ",".join(['"{0}"'.format(name) for name in field_names]) + "]"


def _extract_record_ids_from_items(items: Any) -> List[str]:
    """从飞书 records 分页 items 中提取 record_id 列表。"""
    ids: List[str] = []
    if not isinstance(items, list):
        return ids
    for item in items:
        if isinstance(item, dict):
            rid = str(item.get("record_id") or "").strip()
            if rid:
                ids.append(rid)
    return ids


def _emit_record_id_list_progress(loaded: int, total: int, page_num: int) -> None:
    """打印拉取 record_id 的分页进度，避免长时间无输出。"""
    if total > 0:
        percent = min(100.0, loaded * 100.0 / total)
        _emit(
            "飞书拉取 record_id 进度：{0}/{1}（第 {2} 页，{3:.1f}%）".format(
                loaded, total, page_num, percent
            )
        )
    else:
        _emit("飞书拉取 record_id 进度：已读取 {0} 条（第 {1} 页）".format(loaded, page_num))


def _feishu_request(
    method: str,
    url: str,
    token: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    requester = session if session is not None else requests
    resp = requester.request(
        method, url, headers=headers, params=params, json=payload, timeout=REQUEST_TIMEOUT
    )
    try:
        body = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"飞书响应不是合法 JSON：{resp.text[:200]}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"飞书 HTTP 失败：{resp.status_code} {body}")
    if not isinstance(body, dict):
        raise RuntimeError(f"飞书响应不是 JSON 对象：{body}")
    if body.get("code") != 0:
        raise RuntimeError(f"飞书业务失败：{body}")
    return body


def _list_all_record_ids(
    api_base: str,
    app_token: str,
    table_id: str,
    token: str,
    session: Optional[requests.Session] = None,
) -> List[str]:
    """分页读取当前表全部 record_id（轻量模式 + 进度日志）。"""
    url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    ids: List[str] = []
    page_token = ""
    page_size_candidates = [500, 200, 100, 50]
    current_page_size = 500
    page_num = 0
    total_hint = 0

    def _next_smaller_page_size(size: int) -> Optional[int]:
        for candidate in page_size_candidates:
            if candidate < size:
                return candidate
        return None

    def _build_list_params() -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "page_size": current_page_size,
            # 从第一页开始就启用轻量模式，减少返回字段体积。
            "automatic_fields": False,
            "field_names": _format_feishu_field_names(FEISHU_LIST_ID_FIELD_NAMES),
        }
        if page_token:
            params["page_token"] = page_token
        return params

    while True:
        page_num += 1
        try:
            body = _feishu_request("GET", url, token, params=_build_list_params(), session=session)
        except RuntimeError as exc:
            err_text = str(exc)
            # 飞书在数据量较大时可能返回 RecordExceedLimit（1254103），此时自动降级 page_size 重试。
            if "RecordExceedLimit" in err_text or "1254103" in err_text:
                next_size = _next_smaller_page_size(current_page_size)
                if next_size is not None:
                    _emit(
                        "飞书分页触发 RecordExceedLimit，自动降级 page_size：{0} -> {1}".format(
                            current_page_size, next_size
                        )
                    )
                    current_page_size = next_size
                    continue
                _emit("飞书分页在最小分页仍超限，自动切换到 records/search 兜底读取 record_id。")
                return _list_all_record_ids_via_search(api_base, app_token, table_id, token, session=session)
            raise
        data = body.get("data") or {}
        if not total_hint:
            total_hint = int(data.get("total") or 0)
        page_ids = _extract_record_ids_from_items(data.get("items"))
        ids.extend(page_ids)
        _emit_record_id_list_progress(len(ids), total_hint, page_num)
        has_more = bool(data.get("has_more"))
        page_token = str(data.get("page_token") or "").strip()
        if not has_more:
            break
        if has_more and not page_token:
            raise RuntimeError("飞书分页返回 has_more=true 但 page_token 为空")
    return ids


def _list_all_record_ids_via_search(
    api_base: str,
    app_token: str,
    table_id: str,
    token: str,
    session: Optional[requests.Session] = None,
) -> List[str]:
    """
    兜底方案：使用 records/search 分页拉取 record_id。
    当 records GET 接口持续触发 RecordExceedLimit 时，切换到该方案。
    """
    url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
    ids: List[str] = []
    page_token = ""
    page_size_candidates = [500, 200, 100, 50]
    current_page_size = 500
    page_num = 0
    total_hint = 0

    def _next_smaller_page_size(size: int) -> Optional[int]:
        for candidate in page_size_candidates:
            if candidate < size:
                return candidate
        return None

    while True:
        page_num += 1
        params: Dict[str, Any] = {"page_size": current_page_size}
        if page_token:
            params["page_token"] = page_token
        # search 接口用 body 传 automatic_fields / field_names，尽量降低返回体积。
        payload: Dict[str, Any] = {
            "automatic_fields": False,
            "field_names": FEISHU_LIST_ID_FIELD_NAMES,
        }
        try:
            body = _feishu_request("POST", url, token, params=params, payload=payload, session=session)
        except RuntimeError as exc:
            err_text = str(exc)
            if "RecordExceedLimit" in err_text or "1254103" in err_text:
                next_size = _next_smaller_page_size(current_page_size)
                if next_size is None:
                    raise RuntimeError(
                        "飞书 search 分页仍触发 RecordExceedLimit，page_size 已降到最小仍失败。"
                    ) from exc
                _emit(
                    "飞书 search 分页触发 RecordExceedLimit，自动降级 page_size：{0} -> {1}".format(
                        current_page_size, next_size
                    )
                )
                current_page_size = next_size
                continue
            raise

        data = body.get("data") or {}
        if not total_hint:
            total_hint = int(data.get("total") or 0)
        page_ids = _extract_record_ids_from_items(data.get("items"))
        ids.extend(page_ids)
        _emit_record_id_list_progress(len(ids), total_hint, page_num)
        has_more = bool(data.get("has_more"))
        page_token = str(data.get("page_token") or "").strip()
        if not has_more:
            break
        if has_more and not page_token:
            raise RuntimeError("飞书 search 分页返回 has_more=true 但 page_token 为空")
    return ids


def _list_record_ids_batch(
    api_base: str,
    app_token: str,
    table_id: str,
    token: str,
    *,
    batch_size: int = 1000,
    session: Optional[requests.Session] = None,
) -> List[str]:
    """
    只读取飞书表第一页 record_id，最多 batch_size 条。
    用于「读一批删一批」循环清空：每次删完后再读第一页，无需 page_token。
    """
    url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    page_size_candidates: List[int] = []
    seen: set[int] = set()
    for size in (batch_size, 500, 200, 100, 50):
        if size <= batch_size and size not in seen:
            seen.add(size)
            page_size_candidates.append(size)

    for current_page_size in page_size_candidates:
        params: Dict[str, Any] = {
            "page_size": current_page_size,
            "automatic_fields": False,
            "field_names": _format_feishu_field_names(FEISHU_LIST_ID_FIELD_NAMES),
        }
        try:
            body = _feishu_request("GET", url, token, params=params, session=session)
            data = body.get("data") or {}
            return _extract_record_ids_from_items(data.get("items"))
        except RuntimeError as exc:
            err_text = str(exc)
            if "RecordExceedLimit" in err_text or "1254103" in err_text:
                _emit(
                    "飞书读批次触发 RecordExceedLimit，降级 page_size：{0}".format(current_page_size)
                )
                continue
            raise

    # GET 仍失败时，用 search 只读第一页。
    search_url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
    search_payload: Dict[str, Any] = {
        "automatic_fields": False,
        "field_names": FEISHU_LIST_ID_FIELD_NAMES,
    }
    search_candidates: List[int] = []
    search_seen: set[int] = set()
    for size in (min(batch_size, 500), 200, 100, 50):
        if size not in search_seen:
            search_seen.add(size)
            search_candidates.append(size)
    for current_page_size in search_candidates:
        try:
            body = _feishu_request(
                "POST",
                search_url,
                token,
                params={"page_size": current_page_size},
                payload=search_payload,
                session=session,
            )
            data = body.get("data") or {}
            return _extract_record_ids_from_items(data.get("items"))
        except RuntimeError as exc:
            err_text = str(exc)
            if "RecordExceedLimit" in err_text or "1254103" in err_text:
                _emit(
                    "飞书 search 读批次触发 RecordExceedLimit，降级 page_size：{0}".format(
                        current_page_size
                    )
                )
                continue
            raise

    raise RuntimeError("飞书读批次 record_id 失败：page_size 已降到最小仍失败。")


def _clear_all_records_in_batches(
    api_base: str,
    app_token: str,
    table_id: str,
    token: str,
    *,
    read_batch_size: int = 1000,
    session: Optional[requests.Session] = None,
) -> int:
    """
    循环清空飞书表：先读最多 read_batch_size 条 record_id，够 1000 删 1000，不够有几条删几条，
    重复直到表内无记录。始终读第一页，避免全量拉 ID 与删后翻页 token 失效。
    """
    deleted = 0
    round_num = 0
    delete_url = (
        f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
    )

    while True:
        round_num += 1
        batch_ids = _list_record_ids_batch(
            api_base,
            app_token,
            table_id,
            token,
            batch_size=read_batch_size,
            session=session,
        )
        if not batch_ids:
            if deleted > 0:
                _emit("飞书表已清空，共删除 {0} 条。".format(deleted))
            break

        _feishu_request(
            "POST",
            delete_url,
            token,
            payload={"records": batch_ids},
            session=session,
        )
        deleted += len(batch_ids)
        _emit(
            "飞书删除进度：第 {0} 轮，本轮删除 {1} 条，累计 {2} 条".format(
                round_num, len(batch_ids), deleted
            )
        )

    return deleted


def _batch_delete(
    api_base: str,
    app_token: str,
    table_id: str,
    token: str,
    record_ids: List[str],
    batch_size: int,
    session: Optional[requests.Session] = None,
) -> int:
    """按批次删除旧记录。"""
    if not record_ids:
        return 0
    url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
    deleted = 0
    for i in range(0, len(record_ids), batch_size):
        chunk = record_ids[i : i + batch_size]
        _feishu_request("POST", url, token, payload={"records": chunk}, session=session)
        deleted += len(chunk)
        _emit("飞书删除进度：{0}/{1}".format(deleted, len(record_ids)))
    return deleted


def _batch_insert(
    api_base: str,
    app_token: str,
    table_id: str,
    token: str,
    records: List[Dict[str, Any]],
    batch_size: int,
    session: Optional[requests.Session] = None,
) -> int:
    """按批次写入新记录。"""
    if not records:
        return 0
    url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
    inserted = 0
    for i in range(0, len(records), batch_size):
        chunk = records[i : i + batch_size]
        _feishu_request("POST", url, token, payload={"records": chunk}, session=session)
        inserted += len(chunk)
        _emit("飞书写入进度：{0}/{1}".format(inserted, len(records)))
    return inserted


def _sync_records_to_feishu_table(
    cfg: Dict[str, str],
    token: str,
    records: List[Dict[str, Any]],
    *,
    should_clear_existing: bool,
    delete_batch_size: int,
    insert_batch_size: int,
) -> Tuple[int, int]:
    """
    使用同一个 requests.Session 完成读删循环 / 写入，复用 TCP 连接。
    清空时采用「读一批删一批」循环，不先全量拉 record_id。
    返回：(deleted, inserted)
    """
    session = requests.Session()
    deleted = 0
    try:
        if should_clear_existing:
            _emit(
                "开始循环清空飞书旧数据（每轮最多读 {0} 条，有几条删几条）...".format(
                    delete_batch_size
                )
            )
            deleted = _clear_all_records_in_batches(
                cfg["api_base"],
                cfg["app_token"],
                cfg["table_id"],
                token,
                read_batch_size=delete_batch_size,
                session=session,
            )
        inserted = _batch_insert(
            cfg["api_base"],
            cfg["app_token"],
            cfg["table_id"],
            token,
            records,
            batch_size=insert_batch_size,
            session=session,
        )
        return deleted, inserted
    finally:
        session.close()


def _load_runtime_config() -> Dict[str, str]:
    """集中读取运行配置，供命令行与导入控制台函数入口复用。"""
    export_url = _env_or_default("SUPABASE_EXPORT_URL", DEFAULT_SUPABASE_EXPORT_URL)
    supabase_key = _env_or_default("SUPABASE_KEY", DEFAULT_SUPABASE_KEY)
    supabase_base_url = _env_or_default("SUPABASE_BASE_URL", DEFAULT_SUPABASE_BASE_URL)
    supabase_email = _env_or_default("SUPABASE_EMAIL", DEFAULT_SUPABASE_EMAIL)
    supabase_password = _env_or_default("SUPABASE_PASSWORD", DEFAULT_SUPABASE_PASSWORD)
    api_base = _env_or_default("FEISHU_API_BASE", DEFAULT_FEISHU_API_BASE).rstrip("/")
    app_id = _env_or_default("FEISHU_APP_ID", DEFAULT_FEISHU_APP_ID)
    app_secret = _env_or_default("FEISHU_APP_SECRET", DEFAULT_FEISHU_APP_SECRET)
    app_token = _env_or_default("FEISHU_APP_TOKEN", DEFAULT_FEISHU_APP_TOKEN)
    table_id = _env_or_default("FEISHU_TABLE_ID", DEFAULT_FEISHU_TABLE_ID)
    return {
        "export_url": export_url,
        "supabase_key": supabase_key,
        "supabase_base_url": supabase_base_url,
        "supabase_email": supabase_email,
        "supabase_password": supabase_password,
        "api_base": api_base,
        "app_id": app_id,
        "app_secret": app_secret,
        "app_token": app_token,
        "table_id": table_id,
    }


def export_students(filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    导入控制台直调入口：拉取秒哒 flat 数据并返回 rows。
    保持函数签名兼容，filter 当前保留以便后续扩展。
    """
    _ = filter
    cfg = _load_runtime_config()
    rows = fetch_students_flat(
        export_url=cfg["export_url"],
        supabase_key=cfg["supabase_key"],
        supabase_base_url=cfg["supabase_base_url"],
        supabase_email=cfg["supabase_email"],
        supabase_password=cfg["supabase_password"],
        limit=500,
        sleep_seconds=0.0,
        max_pages=0,
    )
    return rows


def sync_to_feishu(rows: Any) -> Dict[str, int]:
    """导入控制台直调入口：将 flat rows 写入飞书并返回统一统计。"""
    cfg = _load_runtime_config()
    normalized_rows = rows if isinstance(rows, list) else []
    normalized_rows = _apply_referrer_wechat_rewrite(normalized_rows)
    records: List[Dict[str, Any]] = []
    exported = len(normalized_rows)
    deleted = 0
    for row in normalized_rows:
        rec = _to_feishu_record(row)
        if rec is not None:
            records.append(rec)
    prepared = len(records)
    _backup_records_for_write(records)
    _emit("可写入飞书记录数：{0}".format(prepared))

    if bool(DRY_RUN):
        _emit("DRY RUN 模式：不执行飞书写入。")
        _print_records_preview(records)
        return {
            "deleted": 0,
            "exported": exported,
            "prepared": prepared,
            "inserted": 0,
            "failed": 0,
        }

    token = _feishu_token(cfg["api_base"], cfg["app_id"], cfg["app_secret"])
    if bool(SYNC_CLEAR_EXISTING_DEFAULT):
        _emit("导入控制台模式：默认先删除飞书旧数据。")
    else:
        _emit("导入控制台模式：已关闭默认删除，仅追加写入。")
    deleted, inserted = _sync_records_to_feishu_table(
        cfg,
        token,
        records,
        should_clear_existing=bool(SYNC_CLEAR_EXISTING_DEFAULT),
        delete_batch_size=DEFAULT_FEISHU_DELETE_BATCH_SIZE,
        insert_batch_size=DEFAULT_FEISHU_INSERT_BATCH_SIZE,
    )
    failed = max(prepared - inserted, 0)
    return {
        "deleted": deleted,
        "exported": exported,
        "prepared": prepared,
        "inserted": inserted,
        "failed": failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="拉取 export-students-flat 并写入飞书多维表格")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="每页拉取数量（固定按 500 执行）")
    parser.add_argument("--sleep", type=float, default=0.5, help="分页间隔秒数，默认 0.5")
    parser.add_argument("--max-pages", type=int, default=0, help="最多拉取页数，0 表示不限")
    parser.add_argument(
        "--clear-existing",
        action="store_true",
        help="兼容旧参数：当前默认已先清空飞书表旧数据，无需额外传入",
    )
    parser.add_argument(
        "--no-clear-existing",
        action="store_true",
        help="跳过清空飞书表旧数据，直接追加写入",
    )
    parser.add_argument(
        "--delete-batch-size",
        type=int,
        choices=[500, 1000],
        default=DEFAULT_FEISHU_DELETE_BATCH_SIZE,
        help="飞书删除批次，仅支持 500 或 1000，默认 1000",
    )
    parser.add_argument(
        "--insert-batch-size",
        type=int,
        choices=[500, 1000],
        default=DEFAULT_FEISHU_INSERT_BATCH_SIZE,
        help="飞书写入批次，仅支持 500 或 1000，默认 1000",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅拉取并转换，不写入飞书")
    parser.add_argument("--output-json", type=str, default="", help="将拉取原始数据保存到指定 JSON 文件")
    args = parser.parse_args()

    cfg = _load_runtime_config()
    if args.limit != 500:
        _emit("已按固定策略强制使用秒哒每页 500 条，忽略你传入的 --limit。")
    export_limit = 500
    rows = fetch_students_flat(
        export_url=cfg["export_url"],
        supabase_key=cfg["supabase_key"],
        supabase_base_url=cfg["supabase_base_url"],
        supabase_email=cfg["supabase_email"],
        supabase_password=cfg["supabase_password"],
        limit=export_limit,
        sleep_seconds=max(args.sleep, 0.0),
        max_pages=max(args.max_pages, 0),
    )
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        _emit("已输出原始 JSON：{0}".format(args.output_json))

    rows = _apply_referrer_wechat_rewrite(rows)
    records: List[Dict[str, Any]] = []
    for row in rows:
        rec = _to_feishu_record(row)
        if rec is not None:
            records.append(rec)
    _backup_records_for_write(records)
    _emit("可写入飞书记录数：{0}".format(len(records)))

    if bool(args.dry_run):
        _emit("DRY RUN 模式：不执行飞书写入。")
        _print_records_preview(records)
        return 0

    token = _feishu_token(cfg["api_base"], cfg["app_id"], cfg["app_secret"])
    should_clear_existing = not bool(args.no_clear_existing)
    if should_clear_existing:
        _emit("默认策略：写入前先清空飞书旧数据。")
    else:
        _emit("已按参数关闭清空：本次将直接追加写入。")
    deleted, inserted = _sync_records_to_feishu_table(
        cfg,
        token,
        records,
        should_clear_existing=should_clear_existing,
        delete_batch_size=args.delete_batch_size,
        insert_batch_size=args.insert_batch_size,
    )
    _emit("执行完成：")
    _emit("- 删除旧记录：{0}".format(deleted))
    _emit("- 写入新记录：{0}".format(inserted))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
