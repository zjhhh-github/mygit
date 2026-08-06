#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""意向学员通讯录上传到 Prospect 独立系统（STUDENTS_IMPORT）。

【脚本功能说明】
登录 Prospect 独立系统，将意向学员 JSON（如 xlsx_to_prospect_json.py / export_prospect_students.py 产物）
通过 STUDENTS_IMPORT 异步导入任务上传，并轮询直至成功或失败。
支持「推荐人」嵌套格式（意向通讯录.json）；上传前会自动转换为后端识别的「来源」格式。

接口：
    POST /api/prospect/import/tasks/upload   文件上传创建任务
    GET  /api/prospect/import/tasks/{id}    轮询进度

配置：upload_prospect.config.json + 根目录 config.json（prospect 段）

CLI：
    python upload_prospect.py
    python upload_prospect.py --file 意向通讯录.json --mode INCREMENTAL
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
    login_prospect_session,
    normalize_student_mode,
    parse_result_summary,
    resolve_prospect_server,
    summary_count,
    upload_import_file,
    write_temp_json,
)
from prospect_csv_backup import (  # noqa: E402
    STUDENT_SOURCES_KEY_BACKEND,
    STUDENT_SOURCES_KEY_CONTACT_BOOK,
    normalize_students_for_backend,
)

IMPORT_TYPE = "STUDENTS_IMPORT"
DEFAULT_CONFIG_PATH = _SCRIPT_DIR / "upload_prospect.config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "服务端": {"host": "", "account": "", "password": ""},
    "上传": {
        "source_file": "意向通讯录.json",
        "type": "STUDENTS_IMPORT",
        "mode": "INCREMENTAL",
        "dry_run": False,
        "batch_size": 0,
        "abort_on_batch_failure": False,
        "max_wait_seconds": 3600,
        "poll_interval_seconds": 2,
    },
    "日志": {
        "log_file": "logs/upload_prospect.log",
        "save_skipped": True,
        "skipped_filename": "logs/upload_prospect_skipped_{timestamp}.json",
    },
}


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return dict(DEFAULT_CONFIG)
    return deep_merge(DEFAULT_CONFIG, json.loads(config_path.read_text(encoding="utf-8")))


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
        "errors": errors,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已保存失败明细：{}".format(path))


def run_pipeline(
    config_path=None,
    source_file_override=None,
    mode_override=None,
    dry_run_override=None,
) -> int:
    ensure_utf8_stdio()
    config = load_config(config_path)
    upload_cfg = config["上传"]
    log_cfg = config["日志"]
    logger = DualLogger(_resolve(log_cfg.get("log_file", "logs/upload_prospect.log")))

    source_file = source_file_override or _resolve(upload_cfg["source_file"])
    if not source_file.exists():
        logger.error("待上传文件不存在：{}".format(source_file))
        return 2

    records: List[Dict[str, Any]] = []
    recommender_count = 0
    suffix = source_file.suffix.lower()
    if suffix == ".json":
        loaded = json.loads(source_file.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            logger.error("JSON 顶层必须是数组")
            return 2
        # 意向通讯录.json 使用「推荐人」嵌套字段，后端只识别「来源」，上传前统一转换
        recommender_count = sum(
            1 for row in loaded
            if isinstance(row, dict) and STUDENT_SOURCES_KEY_CONTACT_BOOK in row
        )
        source_count = sum(
            1 for row in loaded
            if isinstance(row, dict) and STUDENT_SOURCES_KEY_BACKEND in row
        )
        records = normalize_students_for_backend(loaded)
        if recommender_count:
            logger.info(
                "已识别 {} 条「推荐人」格式记录，上传前转换为后端「来源」格式".format(
                    recommender_count,
                ),
            )
        elif source_count:
            logger.info("JSON 已为后端「来源」格式，共 {} 条".format(source_count))

    try:
        mode = normalize_student_mode(mode_override or upload_cfg.get("mode"))
    except ValueError as ex:
        logger.error(str(ex))
        return 2

    import_type = upload_cfg.get("type") or IMPORT_TYPE
    dry_run = dry_run_override if dry_run_override is not None else bool(upload_cfg.get("dry_run", False))
    batch_size = int(upload_cfg.get("batch_size", 0) or 0)
    abort_on_failure = bool(upload_cfg.get("abort_on_batch_failure", False))
    max_wait = int(upload_cfg.get("max_wait_seconds", 3600))
    poll_interval = int(upload_cfg.get("poll_interval_seconds", 2))

    upload_jobs: List[tuple] = []
    temp_files: List[Path] = []
    if suffix == ".json" and batch_size > 0 and len(records) > batch_size:
        logger.warn("batch_size>0 会拆多个任务，后端同类型互斥，建议 batch_size=0")
        for i in range(0, len(records), batch_size):
            temp_path = write_temp_json(records[i:i + batch_size])
            temp_files.append(temp_path)
            upload_jobs.append((temp_path, len(records[i:i + batch_size])))
    elif suffix == ".json" and recommender_count:
        # 原文件是「推荐人」格式，必须写临时「来源」文件再上传，避免后端读不到推荐关系
        temp_path = write_temp_json(records)
        temp_files.append(temp_path)
        upload_jobs.append((temp_path, len(records)))
    else:
        upload_jobs.append((source_file, len(records) if records else -1))

    server = resolve_prospect_server(config.get("服务端"), logger=logger)
    logger.info("=" * 60)
    logger.info("[IMPORT] 意向学员上传 type={} mode={}".format(import_type, mode))
    logger.info("host={} file={} 条数={}".format(
        server["host"], source_file, len(records) if records else "后端解析"))
    logger.info("=" * 60)

    if dry_run:
        logger.info("dry-run：前 3 条示例")
        if records:
            logger.info(json.dumps(records[:3], ensure_ascii=False))
        return 0

    session = login_prospect_session(
        server["host"], server["account"], server["password"], logger)
    token = session["token"]

    totals = {"ok": 0, "error": 0}
    aggregated_errors: List[Dict[str, Any]] = []
    batch_results = []
    abort_flag = False

    try:
        for idx, (upload_path, batch_count) in enumerate(upload_jobs):
            if abort_flag:
                continue
            prefix = "[批次 {}/{}] ".format(idx + 1, len(upload_jobs))
            progress = upload_import_file(
                host=server["host"],
                token=token,
                upload_file=upload_path,
                import_type=import_type,
                mode=mode,
                logger=logger,
                max_wait_seconds=max_wait,
                poll_interval=poll_interval,
                log_prefix=prefix,
            )
            status_text = str(progress.get("status") or "")
            ok_count = int(progress.get("okCount") or 0)
            err_count = int(progress.get("errorCount") or progress.get("failed") or 0)
            totals["ok"] += ok_count
            totals["error"] += err_count

            summary = parse_result_summary(progress)
            for err in progress.get("errors") or progress.get("failures") or []:
                item = err if isinstance(err, dict) else {"raw": err}
                item.setdefault("_batch_index", idx + 1)
                aggregated_errors.append(item)

            batch_results.append({
                "batch_index": idx + 1,
                "size": batch_count,
                "task_id": progress.get("_taskId"),
                "status": status_text,
                "okCount": ok_count,
                "errorCount": err_count,
                "resultSummary": summary,
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

    logger.info("[IMPORT] 完成 ok={} error={}".format(totals["ok"], totals["error"]))
    if log_cfg.get("save_skipped", True) and aggregated_errors:
        save_skipped({"errors": aggregated_errors}, log_cfg.get(
            "skipped_filename", "logs/upload_prospect_skipped_{timestamp}.json"),
            source_file, logger)

    if any(str(r["status"]).upper() not in {"SUCCESS", "COMPLETED"} for r in batch_results):
        return 1
    logger.info("=== 意向学员上传成功 ===")
    return 0


def _parse_args():
    p = argparse.ArgumentParser(description="意向学员 → Prospect STUDENTS_IMPORT")
    p.add_argument("--config", default=None)
    p.add_argument("--file", default=None)
    p.add_argument("--mode", default=None, choices=["FULL", "INCREMENTAL", "APPEND"])
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
