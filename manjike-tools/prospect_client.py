#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prospect 独立系统 API 客户端（导入任务、登录会话等）。

【模块功能说明】
封装 Prospect 独立系统的登录、导入文件上传（curl multipart）与任务进度轮询，
供 internal/、prospect/ 下各上传脚本复用，统一服务地址解析与 [IMPORT] 日志格式。

本模块供 internal/、prospect/ 下脚本共用，统一：
  - 服务地址解析（环境变量 > 脚本配置 > config.json）
  - Prospect 登录与会话
  - 导入任务文件上传（curl -F）与进度轮询
  - [IMPORT] 统一日志格式

注意：Prospect 接口必须访问 http://localhost（或 18081），
不能访问主系统 http://localhost:8080。
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 允许从 manjike-tools 根目录 import common
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common import (  # noqa: E402
    DualLogger,
    http_request,
    http_upload_multipart,
    log_business_error,
    normalize_host,
    resolve_prospect_config,
    warn_if_main_backend_for_prospect,
)

# 与前端 importTask.ts、ProspectImportTaskController 保持一致
PROSPECT_IMPORT_TASK_BASE = "/api/prospect/import/tasks"

# 导入任务终态
IMPORT_TERMINAL_STATUSES = frozenset({"SUCCESS", "FAILED", "CANCELLED", "COMPLETED"})


def resolve_prospect_server(
    script_server: Optional[Dict[str, Any]] = None,
    logger: Optional[DualLogger] = None,
) -> Dict[str, str]:
    """解析 Prospect 服务端连接信息（host / account / password）。"""
    cfg = resolve_prospect_config(script_server)
    host = normalize_host(cfg.get("host") or "http://localhost")
    warn_if_main_backend_for_prospect(host, logger)
    return {
        "host": host,
        "account": str(cfg.get("account") or "").strip(),
        "password": str(cfg.get("password") or "").strip(),
    }


def login_prospect_session(
    host: str,
    account: str,
    password: str,
    logger: DualLogger,
) -> Dict[str, Any]:
    """登录 Prospect 独立后端，返回 token 与 userInfo。"""
    logger.info("[AUTH] 登录 Prospect：{}".format(host))
    status, body = http_request(
        url="{}/api/prospect/auth/login".format(host),
        method="POST",
        json_body={"username": account, "password": password},
    )
    if status != 200 or body.get("code") != 0:
        log_business_error(logger, body, prefix="Prospect 登录失败")
        logger.error(
            "请确认 host 指向 Prospect 独立服务（http://localhost 或 http://localhost:18081），"
            "并使用 prospect_user 表账号，不是主系统 admin。"
        )
        sys.exit(1)

    data = body.get("data") or {}
    token = data.get("token") or data.get("access_token")
    if not token:
        logger.error("Prospect 登录响应无 token：{}".format(json.dumps(body, ensure_ascii=False)))
        sys.exit(1)

    user_info = {
        "userId": data.get("userId"),
        "username": data.get("username"),
        "roleCode": data.get("roleCode"),
        "displayName": data.get("displayName"),
    }
    logger.info("[AUTH] 登录成功 user={} token={}...".format(
        user_info.get("username") or account,
        token[:12],
    ))
    return {"token": token, "userInfo": user_info}


def parse_result_summary(progress: Dict[str, Any]) -> Dict[str, Any]:
    """解析导入进度里的 resultSummary（可能是 JSON 字符串）。"""
    raw = progress.get("resultSummary")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except (json.JSONDecodeError, TypeError):
        return {}


def summary_count(summary: Dict[str, Any], *keys: str) -> int:
    """从 resultSummary 读取计数，兼容 inserted / insertedCount 等字段名。"""
    for key in keys:
        if key in summary and summary.get(key) is not None:
            return int(summary.get(key) or 0)
    return 0


def normalize_contact_mode(mode: str) -> str:
    """内部通讯录导入 mode：CONTACT_INCREMENTAL / CONTACT_OVERWRITE。"""
    key = (mode or "CONTACT_INCREMENTAL").strip().upper()
    aliases = {
        "INCREMENTAL": "CONTACT_INCREMENTAL",
        "CONTACT_INCREMENTAL": "CONTACT_INCREMENTAL",
        "FULL": "CONTACT_OVERWRITE",
        "OVERWRITE": "CONTACT_OVERWRITE",
        "CONTACT_OVERWRITE": "CONTACT_OVERWRITE",
    }
    if key in aliases:
        return aliases[key]
    if key == "APPEND":
        raise ValueError("内部通讯录已下线 APPEND，请用 CONTACT_INCREMENTAL 或 CONTACT_OVERWRITE")
    raise ValueError("不支持的 mode={!r}，请用 CONTACT_INCREMENTAL 或 CONTACT_OVERWRITE".format(mode))


def normalize_student_mode(mode: str) -> str:
    """意向学员导入 mode：FULL / INCREMENTAL / APPEND。"""
    key = (mode or "INCREMENTAL").strip().upper()
    allowed = {"FULL", "INCREMENTAL", "APPEND"}
    if key in allowed:
        return key
    raise ValueError("不支持的 mode={!r}，请用 FULL / INCREMENTAL / APPEND".format(mode))


def normalize_user_mode(mode: str) -> str:
    """Prospect 用户管理导入 mode：目前仅 USER_INCREMENTAL。"""
    key = (mode or "USER_INCREMENTAL").strip().upper()
    if key in {"USER_INCREMENTAL", "INCREMENTAL"}:
        return "USER_INCREMENTAL"
    raise ValueError("不支持的 mode={!r}，用户导入请用 USER_INCREMENTAL".format(mode))


def log_import_progress(
    logger: DualLogger,
    task_id: str,
    progress: Dict[str, Any],
    log_prefix: str = "",
) -> None:
    """统一 [IMPORT] 进度日志。"""
    status = str(progress.get("status") or "")
    total = progress.get("total") or 0
    processed = progress.get("processed") or 0
    ok_count = progress.get("okCount")
    error_count = progress.get("errorCount")
    summary = parse_result_summary(progress)
    extra = ""
    if summary:
        extra = " inserted={} updated={} skipped={}".format(
            summary_count(summary, "insertedCount", "inserted"),
            summary_count(summary, "updatedCount", "updated"),
            summary_count(summary, "skippedCount", "skipped"),
        )
    logger.info(
        "{}[IMPORT] taskId={} status={} total={} processed={} ok={} error={}{}".format(
            log_prefix, task_id, status, total, processed, ok_count, error_count, extra
        )
    )


def poll_import_task(
    host: str,
    token: str,
    task_id: str,
    max_wait_seconds: int,
    poll_interval: int,
    logger: DualLogger,
    log_prefix: str = "",
) -> Dict[str, Any]:
    """轮询 GET /api/prospect/import/tasks/{taskId} 直到终态。"""
    logger.info("{}[IMPORT] 轮询 taskId={}（最长 {}s）".format(log_prefix, task_id, max_wait_seconds))
    started = time.time()
    last_signature = ""
    while True:
        if time.time() - started > max_wait_seconds:
            logger.error("{}[IMPORT] 等待超时 taskId={}".format(log_prefix, task_id))
            return {"status": "TIMEOUT", "errors": [{"reason": "轮询超时"}]}

        status, body = http_request(
            url="{}{}/{}".format(host, PROSPECT_IMPORT_TASK_BASE, task_id),
            method="GET",
            headers={"Authorization": "Bearer {}".format(token)},
            timeout=30,
        )
        if status != 200 or body.get("code") != 0:
            log_business_error(logger, body, prefix="{}查询进度失败".format(log_prefix))
            return {
                "status": "FAILED",
                "errors": [{"reason": "查询进度 HTTP {}".format(status), "body": body}],
            }

        progress = body.get("data") or {}
        signature = "|".join([
            str(progress.get("status") or ""),
            str(progress.get("processed") or ""),
            str(progress.get("total") or ""),
            str(progress.get("okCount") or ""),
            str(progress.get("errorCount") or ""),
            str(progress.get("errorMsg") or ""),
        ])
        if signature != last_signature:
            log_import_progress(logger, task_id, progress, log_prefix=log_prefix)
            last_signature = signature

        cur_status = str(progress.get("status") or "").upper()
        if cur_status in IMPORT_TERMINAL_STATUSES:
            return progress
        time.sleep(poll_interval)


def upload_import_file(
    host: str,
    token: str,
    upload_file: Path,
    import_type: str,
    mode: str,
    logger: DualLogger,
    max_wait_seconds: int = 3600,
    poll_interval: int = 2,
    log_prefix: str = "",
) -> Dict[str, Any]:
    """上传文件创建导入任务并轮询至完成。

    POST /api/prospect/import/tasks/upload
    优先 curl -F，避免 Python 3.6 urllib multipart 与 Spring 不兼容。
    """
    if not upload_file.is_file():
        logger.error("{}待上传文件不存在：{}".format(log_prefix, upload_file))
        return {"status": "FAILED", "errors": [{"reason": "文件不存在"}]}

    file_size = upload_file.stat().st_size
    logger.info(
        "{}[IMPORT] 上传 file={} size={}B type={} mode={}".format(
            log_prefix, upload_file.name, file_size, import_type, mode
        )
    )

    submit_timeout = max(120, min(600, file_size // 10000 + 120))
    upload_url = "{}{}/upload".format(host, PROSPECT_IMPORT_TASK_BASE)
    fields = {"type": import_type, "mode": mode}

    status, body = http_upload_multipart(
        url=upload_url,
        token=token,
        fields=fields,
        file_path=upload_file,
        timeout=submit_timeout,
    )
    if status == 0 and body.get("code") == -1:
        logger.warn("{}curl 不可用，回退 urllib multipart".format(log_prefix))
        status, body = http_request(
            url=upload_url,
            method="POST",
            headers={"Authorization": "Bearer {}".format(token)},
            multipart=dict(fields, file=upload_file),
            timeout=submit_timeout,
        )

    if status != 200 or body.get("code") != 0:
        log_business_error(logger, body, prefix="{}上传失败".format(log_prefix))
        msg = str(body.get("message") or "")
        if body.get("code") == 403 and "意向通讯录功能已下线" in msg:
            logger.error("连接到了旧 backend（8080），请改 config.json prospect.host 为 http://localhost")
        return {"status": "FAILED", "errors": [{"reason": "上传 HTTP {}".format(status), "body": body}]}

    task_id = (body.get("data") or {}).get("taskId")
    if not task_id:
        logger.error("{}响应无 taskId：{}".format(log_prefix, json.dumps(body, ensure_ascii=False)))
        return {"status": "FAILED", "errors": [{"reason": "响应缺 taskId"}]}

    logger.info("{}[IMPORT] 已创建 taskId={}".format(log_prefix, task_id))
    progress = poll_import_task(
        host=host,
        token=token,
        task_id=task_id,
        max_wait_seconds=max_wait_seconds,
        poll_interval=poll_interval,
        logger=logger,
        log_prefix=log_prefix,
    )
    progress.setdefault("_taskId", task_id)
    return progress


def write_temp_json(records: List[Dict[str, Any]], indent: int = 2) -> Path:
    """写入临时 JSON 文件，供分批上传使用。"""
    fd, temp_path = tempfile.mkstemp(prefix="prospect_import_", suffix=".json")
    os.close(fd)
    path = Path(temp_path)
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=indent) + "\n",
        encoding="utf-8",
    )
    return path
