#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部通讯录上传到 Prospect 独立系统（INTERNAL_IMPORT）。

【脚本功能说明】
登录 Prospect 独立系统，将 export_internal_contacts.py 导出的内部通讯录 JSON
通过 INTERNAL_IMPORT 异步导入任务上传，并轮询直至成功或失败。

接口：
    POST /api/prospect/import/tasks/upload   文件上传创建任务
    GET  /api/prospect/import/tasks/{id}    轮询进度

配置：upload_internal.config.json + 根目录 config.json（prospect 段）
环境变量：PROSPECT_HOST / PROSPECT_ACCOUNT / PROSPECT_PASSWORD

CLI：
    python upload_internal.py
    python upload_internal.py --file 内部通讯录.json --dry-run
    python upload_internal.py --mode CONTACT_OVERWRITE
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_PARENT_DIR = _SCRIPT_DIR.parent
if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))

from common import DualLogger, deep_merge, ensure_utf8_stdio, resolve_path  # noqa: E402
from prospect_client import (  # noqa: E402
    IMPORT_TERMINAL_STATUSES,
    login_prospect_session,
    normalize_contact_mode,
    parse_result_summary,
    resolve_prospect_server,
    summary_count,
    upload_import_file,
    write_temp_json,
)

IMPORT_TYPE = "INTERNAL_IMPORT"
DEFAULT_CONFIG_PATH = _SCRIPT_DIR / "upload_internal.config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "服务端": {"host": "", "account": "", "password": ""},
    "上传": {
        "source_file": "内部通讯录.json",
        "mode": "CONTACT_INCREMENTAL",
        "dry_run": False,
        "batch_size": 0,
        "abort_on_batch_failure": False,
        "max_wait_seconds": 3600,
        "poll_interval_seconds": 2,
    },
    "日志": {
        "log_file": "logs/upload_internal.log",
        "save_skipped": True,
        "skipped_filename": "logs/upload_internal_skipped_{timestamp}.json",
    },
}


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return dict(DEFAULT_CONFIG)
    user_cfg = json.loads(config_path.read_text(encoding="utf-8"))
    return deep_merge(DEFAULT_CONFIG, user_cfg)


def _resolve(value: str) -> Path:
    return resolve_path(value, _SCRIPT_DIR)


def save_skipped(progress, skipped_template, source_file, logger):
    errors = progress.get("errors") or progress.get("failures") or []
    if not errors:
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = _resolve(skipped_template.format(timestamp=ts))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": str(source_file),
        "task_status": progress.get("status"),
        "count": len(errors),
        "errors": errors,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已保存失败明细：{}（{} 条）".format(path, len(errors)))


def run_pipeline(
    config_path=None,
    source_file_override=None,
    mode_override=None,
    dry_run_override=None,
) -> int:
    ensure_utf8_stdio()
    config = load_config(config_path)
    server = resolve_prospect_server(config.get("服务端"), logger=None)
    upload_cfg = config["上传"]
    log_cfg = config["日志"]
    logger = DualLogger(_resolve(log_cfg.get("log_file", "logs/upload_internal.log")))

    source_file = source_file_override or _resolve(upload_cfg["source_file"])
    if not source_file.exists():
        logger.error("待上传文件不存在：{}".format(source_file))
        return 2

    records: List[Dict[str, Any]] = []
    suffix = source_file.suffix.lower()
    if suffix == ".json":
        loaded = json.loads(source_file.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            logger.error("JSON 顶层必须是数组")
            return 2
        records = loaded

    try:
        mode = normalize_contact_mode(mode_override or upload_cfg.get("mode"))
    except ValueError as ex:
        logger.error(str(ex))
        return 2

    dry_run = dry_run_override if dry_run_override is not None else bool(upload_cfg.get("dry_run", False))
    batch_size = int(upload_cfg.get("batch_size", 0) or 0)
    abort_on_failure = bool(upload_cfg.get("abort_on_batch_failure", False))
    max_wait = int(upload_cfg.get("max_wait_seconds", 3600))
    poll_interval = int(upload_cfg.get("poll_interval_seconds", 2))

    upload_jobs: List[tuple] = []
    temp_files: List[Path] = []
    if suffix == ".json" and batch_size > 0 and len(records) > batch_size:
        for i in range(0, len(records), batch_size):
            temp_path = write_temp_json(records[i:i + batch_size])
            temp_files.append(temp_path)
            upload_jobs.append((temp_path, len(records[i:i + batch_size]), True))
    else:
        upload_jobs.append((source_file, len(records) if records else -1, False))

    server = resolve_prospect_server(config.get("服务端"), logger=logger)
    logger.info("=" * 60)
    logger.info("[IMPORT] 内部通讯录上传 type={} mode={}".format(IMPORT_TYPE, mode))
    logger.info("host={} account={}".format(server["host"], server["account"]))
    logger.info("file={} 条数={} 批次={}".format(
        source_file, len(records) if records else "后端解析", len(upload_jobs)))
    logger.info("=" * 60)

    if dry_run:
        logger.info("dry-run：跳过登录与上传")
        if records:
            logger.info("前 3 条：" + json.dumps(records[:3], ensure_ascii=False))
        return 0

    session = login_prospect_session(
        server["host"], server["account"], server["password"], logger)
    token = session["token"]

    totals = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    aggregated_errors: List[Dict[str, Any]] = []
    batch_results = []
    abort_flag = False
    progress: Dict[str, Any] = {}

    try:
        for idx, (upload_path, batch_count, is_temp) in enumerate(upload_jobs):
            if abort_flag:
                continue
            prefix = "[批次 {}/{}] ".format(idx + 1, len(upload_jobs))
            progress = upload_import_file(
                host=server["host"],
                token=token,
                upload_file=upload_path,
                import_type=IMPORT_TYPE,
                mode=mode,
                logger=logger,
                max_wait_seconds=max_wait,
                poll_interval=poll_interval,
                log_prefix=prefix,
            )
            status_text = str(progress.get("status") or "")
            summary = parse_result_summary(progress)
            ins = summary_count(summary, "insertedCount", "inserted")
            upd = summary_count(summary, "updatedCount", "updated")
            skp = summary_count(summary, "skippedCount", "skipped")
            fail = int(progress.get("errorCount") or progress.get("failed") or 0)
            totals["inserted"] += ins
            totals["updated"] += upd
            totals["skipped"] += skp
            totals["failed"] += fail

            for err in progress.get("errors") or progress.get("failures") or []:
                item = err if isinstance(err, dict) else {"raw": err}
                item.setdefault("_batch_index", idx + 1)
                aggregated_errors.append(item)

            batch_results.append({
                "batch_index": idx + 1,
                "size": batch_count,
                "task_id": progress.get("_taskId"),
                "status": status_text,
                "inserted": ins, "updated": upd, "skipped": skp, "failed": fail,
            })
            if status_text.upper() not in {"SUCCESS", "COMPLETED"} and abort_on_failure:
                abort_flag = True
    finally:
        for p in temp_files:
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass

    logger.info("[IMPORT] 完成 inserted={} updated={} skipped={} failed={}".format(
        totals["inserted"], totals["updated"], totals["skipped"], totals["failed"]))

    if log_cfg.get("save_skipped", True) and aggregated_errors:
        save_skipped({
            "status": "AGGREGATED", "errors": aggregated_errors, "batches": batch_results,
        }, log_cfg.get("skipped_filename", "logs/upload_internal_skipped_{timestamp}.json"),
            source_file, logger)

    failed_status = any(
        str(r["status"]).upper() not in {"SUCCESS", "COMPLETED"} for r in batch_results
    )
    if failed_status:
        return 1
    logger.info("=== 内部通讯录上传成功 ===")
    return 0


def _parse_args():
    p = argparse.ArgumentParser(description="内部通讯录 → Prospect INTERNAL_IMPORT")
    p.add_argument("--config", default=None)
    p.add_argument("--file", default=None)
    p.add_argument("--mode", default=None)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    try:
        args = _parse_args()
        sys.exit(run_pipeline(
            config_path=Path(args.config) if args.config else None,
            source_file_override=Path(args.file) if args.file else None,
            mode_override=args.mode,
            dry_run_override=True if args.dry_run else None,
        ))
    except KeyboardInterrupt:
        print("\n[CANCELLED]")
        sys.exit(130)
