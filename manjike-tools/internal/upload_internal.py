#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「内部通讯录」JSON 上传到 manjike 后端。

【脚本作用】
读取内部通讯录 JSON（通常为 Excel 导出或数据库导出脚本生成的数组），
通过售前异步导入接口写入 presale_internal_contact：

    POST  /api/prospect/import-tasks        提交任务（type=INTERNAL_IMPORT）
    GET   /api/prospect/import-tasks/{id}   查询进度

接口入参（application/json）：
    {
      "type":    "INTERNAL_IMPORT",
      "mode":    "INCREMENTAL",
      "payload": "[{...}, ...]"    # payload 为字符串化的 JSON 数组
    }

【与 upload_prospect.py 的差异】
  - 固定 type=INTERNAL_IMPORT；后端内部按 200 条/批写库，完成后全量同步意向学员。
  - 默认 batch_size=0（一次提交全部），避免多任务互斥。
  - 进度 resultSummary 含 inserted / updated / skipped。

【配置】同目录 upload_internal.config.json

【依赖】Python 3.7+，仅标准库。

CLI：
    python upload_internal.py
    python upload_internal.py --file 内部通讯录.json
    python upload_internal.py --dry-run
    python upload_internal.py --mode INCREMENTAL
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# 把父目录（manjike-tools/）加入 sys.path，以便 import common
# ============================================================
_SCRIPT_DIR = Path(__file__).resolve().parent
_PARENT_DIR = _SCRIPT_DIR.parent
if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))

# 从公共模块导入：HTTP 工具、登录、日志、配置合并、路径解析、公共配置
from common import (  # noqa: E402
    DualLogger,
    deep_merge,
    ensure_utf8_stdio,
    http_request,
    load_shared_config,
    login,
    normalize_host,
    resolve_path,
)

IMPORT_TYPE = "INTERNAL_IMPORT"

DEFAULT_CONFIG_PATH = _SCRIPT_DIR / "upload_internal.config.json"

# 内置默认值：配置文件缺字段时兜底，保证脚本可跑
DEFAULT_CONFIG: Dict[str, Any] = {
    "服务端": {
        "host": "http://localhost:8080",
        "account": "admin",
        "password": "admin123",
    },
    "上传": {
        "source_file": "内部通讯录.json",
        "mode": "INCREMENTAL",
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
    """加载配置文件，找不到时使用内置默认值。"""
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        user_cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as ex:
        raise SystemExit(f"[ERROR] 配置文件 JSON 解析失败：{config_path}：{ex}")
    return deep_merge(DEFAULT_CONFIG, user_cfg)


def _resolve(value: str) -> Path:
    """相对路径锚定到脚本目录的快捷函数。"""
    return resolve_path(value, _SCRIPT_DIR)


def parse_result_summary(progress: Dict[str, Any]) -> Dict[str, Any]:
    """解析进度里的 resultSummary（可能是 JSON 字符串）。"""
    raw = progress.get("resultSummary")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return {}


# ============================================================
# 业务步骤：提交任务 / 轮询进度（internal 专用接口路径）
# ============================================================

def poll_progress(
    host: str,
    token: str,
    task_id: str,
    max_wait_seconds: int,
    poll_interval: int,
    logger: DualLogger,
    log_prefix: str = "",
) -> Dict[str, Any]:
    """轮询 /api/prospect/import-tasks/{id} 直到终态，返回最终 progress。

    接口路径与 upload_prospect.py 相同，但 resultSummary 字段不同，
    因此轮询逻辑保留在本脚本中（打印 inserted/updated/skipped 字段）。
    """
    logger.info(f"{log_prefix}轮询进度：taskId={task_id}（最长 {max_wait_seconds}s）")
    started = time.time()
    last_signature = ""
    while True:
        if time.time() - started > max_wait_seconds:
            logger.error(f"{log_prefix}等待超时：{max_wait_seconds}s 内任务仍未完成")
            return {"status": "TIMEOUT", "errors": [{"reason": "本批次轮询超时"}]}
        status, body = http_request(
            url=f"{host}/api/prospect/import-tasks/{task_id}",
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if status != 200 or body.get("code") != 0:
            logger.error(
                f"{log_prefix}查询进度失败：HTTP {status} / body={json.dumps(body, ensure_ascii=False)}"
            )
            return {
                "status": "FAILED",
                "errors": [{"reason": f"查询进度 HTTP {status}", "body": body}],
            }
        progress = body.get("data") or {}
        cur_status = str(progress.get("status") or "")
        processed = progress.get("processed") or 0
        total = progress.get("total") or 0
        stage = progress.get("currentStage") or ""
        msg = progress.get("currentMessage") or ""
        summary = parse_result_summary(progress)
        signature = f"{cur_status}|{processed}|{total}|{stage}|{msg}"
        if signature != last_signature:
            extra = ""
            if summary:
                extra = (
                    f" inserted={summary.get('inserted')} updated={summary.get('updated')}"
                    f" skipped={summary.get('skipped')}"
                )
            logger.info(
                f"{log_prefix}进度：status={cur_status} processed={processed}/{total}"
                f" stage={stage}{extra} msg={msg}"
            )
            last_signature = signature
        if cur_status.upper() in {"SUCCESS", "FAILED", "CANCELLED", "COMPLETED"}:
            return progress
        time.sleep(poll_interval)


def upload_one_batch(
    host: str,
    token: str,
    mode: str,
    batch_records: List[Dict[str, Any]],
    max_wait_seconds: int,
    poll_interval: int,
    logger: DualLogger,
    log_prefix: str = "",
) -> Dict[str, Any]:
    """提交一批内部通讯录 → 轮询完成 → 返回最终 progress。"""
    payload_json = json.dumps(batch_records, ensure_ascii=False)
    logger.info(
        f"{log_prefix}提交：通讯录 {len(batch_records)} 条，payload={len(payload_json)}B，"
        f"type={IMPORT_TYPE} mode={mode}"
    )
    # 全量 JSON 可能较大，提交超时适当放宽
    submit_timeout = max(120, min(600, len(payload_json) // 10000 + 120))
    status, body = http_request(
        url=f"{host}/api/prospect/import-tasks",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        json_body={"type": IMPORT_TYPE, "mode": mode, "payload": payload_json},
        timeout=submit_timeout,
    )
    if status != 200 or body.get("code") != 0:
        logger.error(f"{log_prefix}提交失败：HTTP {status} / body={json.dumps(body, ensure_ascii=False)}")
        return {"status": "FAILED", "errors": [{"reason": f"提交 HTTP {status}", "body": body}]}
    task_id = (body.get("data") or {}).get("taskId")
    if not task_id:
        logger.error(f"{log_prefix}响应中没有 taskId：{json.dumps(body, ensure_ascii=False)}")
        return {"status": "FAILED", "errors": [{"reason": "响应缺 taskId", "body": body}]}
    logger.info(f"{log_prefix}taskId = {task_id}")

    progress = poll_progress(
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


def save_skipped(
    progress: Dict[str, Any],
    skipped_template: str,
    source_file: Path,
    logger: DualLogger,
) -> None:
    """把进度返回里的 errors / failures 数组保存为独立 JSON，便于人工排查。"""
    errors = progress.get("errors") or progress.get("failures") or []
    if not errors:
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    skipped_path = _resolve(skipped_template.format(timestamp=timestamp))
    skipped_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": str(source_file),
        "task_status": progress.get("status"),
        "count": len(errors),
        "errors": errors,
    }
    skipped_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"已保存跳过/失败明细：{skipped_path}（{len(errors)} 条）")


# ============================================================
# 主流程
# ============================================================

def run_pipeline(
    config_path: Optional[Path] = None,
    source_file_override: Optional[Path] = None,
    mode_override: Optional[str] = None,
    dry_run_override: Optional[bool] = None,
) -> int:
    """端到端：读配置 → 登录 → 分批提交 → 轮询 → 保存跳过明细。

    返回 0 = 成功；非 0 = 失败码。
    不调用 sys.exit，便于宿主程序根据返回值决定下一步。
    """
    ensure_utf8_stdio()
    config = load_config(config_path)
    server = config["服务端"]
    # host 优先级：脚本配置文件 > shared.config.json > 默认值
    shared = load_shared_config()
    server_host = normalize_host(server.get("host") or shared.get("host") or "http://localhost:8080")
    upload_cfg = config["上传"]
    log_cfg = config["日志"]

    log_path = _resolve(log_cfg.get("log_file", "logs/upload_internal.log"))
    logger = DualLogger(log_path)

    source_file = source_file_override or _resolve(upload_cfg["source_file"])
    if not source_file.exists():
        logger.error(f"待上传文件不存在：{source_file}")
        return 2
    try:
        records = json.loads(source_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as ex:
        logger.error(f"待上传文件不是合法 JSON：{source_file}：{ex}")
        return 2
    if not isinstance(records, list):
        logger.error(f"待上传 JSON 顶层不是数组（实际类型：{type(records).__name__}）")
        return 2

    mode = (mode_override or upload_cfg.get("mode") or "INCREMENTAL").upper()
    dry_run = dry_run_override if dry_run_override is not None else bool(upload_cfg.get("dry_run", False))
    batch_size = int(upload_cfg.get("batch_size", 0) or 0)
    abort_on_failure = bool(upload_cfg.get("abort_on_batch_failure", False))
    max_wait = int(upload_cfg.get("max_wait_seconds", 3600))
    poll_interval = int(upload_cfg.get("poll_interval_seconds", 2))

    if batch_size > 0 and len(records) > batch_size:
        batches = [records[i : i + batch_size] for i in range(0, len(records), batch_size)]
        logger.warn(
            "batch_size>0 会拆成多个 INTERNAL_IMPORT 任务；后端同类型任务互斥，将串行执行。"
            "全量导入建议 batch_size=0。"
        )
    else:
        batches = [records]

    logger.info("=" * 60)
    logger.info("查询系统：内部通讯录上传开始")
    logger.info(f"host={server_host}, account={server['account']}")
    logger.info(
        f"source_file={source_file}, 条数={len(records)}, "
        f"客户端批次={len(batches)}（batch_size={batch_size if batch_size > 0 else '一次提交'}）, "
        f"type={IMPORT_TYPE}, mode={mode}, dry_run={dry_run}"
    )
    logger.info("=" * 60)

    if dry_run:
        logger.info("dry-run 模式：跳过登录 / 提交 / 轮询")
        logger.info("前 3 条示例：" + json.dumps(records[:3], ensure_ascii=False))
        return 0

    token = login(server_host, server["account"], server["password"], logger)

    total_inserted = 0
    total_updated = 0
    total_skipped = 0
    total_failed = 0
    aggregated_errors: List[Dict[str, Any]] = []
    batch_results: List[Optional[Dict[str, Any]]] = [None] * len(batches)
    abort_flag = False
    overall_started = time.time()
    progress: Dict[str, Any] = {}

    for idx, batch in enumerate(batches):
        if abort_flag:
            logger.warn(f"[批次 {idx + 1}/{len(batches)}] abort_on_batch_failure=True，跳过")
            continue

        prefix = f"[批次 {idx + 1}/{len(batches)}] "
        progress = upload_one_batch(
            host=server_host,
            token=token,
            mode=mode,
            batch_records=batch,
            max_wait_seconds=max_wait,
            poll_interval=poll_interval,
            logger=logger,
            log_prefix=prefix,
        )

        status_text = str(progress.get("status") or "")
        summary = parse_result_summary(progress)
        inserted = int(summary.get("inserted") or 0)
        updated = int(summary.get("updated") or 0)
        skipped = int(summary.get("skipped") or 0)
        failed = int(progress.get("failed") or 0)
        total_inserted += inserted
        total_updated += updated
        total_skipped += skipped
        total_failed += failed

        for err in progress.get("errors") or progress.get("failures") or []:
            if isinstance(err, dict):
                err.setdefault("_batch_index", idx + 1)
                aggregated_errors.append(err)
            else:
                aggregated_errors.append({"_batch_index": idx + 1, "raw": err})

        batch_results[idx] = {
            "batch_index": idx + 1,
            "size": len(batch),
            "task_id": progress.get("_taskId"),
            "status": status_text,
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "resultSummary": summary,
        }
        logger.info(
            f"{prefix}完成 status={status_text} inserted={inserted} updated={updated} "
            f"skipped={skipped} failed={failed} taskId={progress.get('_taskId')}"
        )

        if status_text.upper() not in {"SUCCESS", "COMPLETED"} and abort_on_failure:
            abort_flag = True
            logger.error(f"{prefix}非成功终态，后续批次将跳过")

    overall_elapsed = time.time() - overall_started
    finished_results = [r for r in batch_results if r is not None]

    logger.info("=" * 60)
    logger.info(
        f"全部完成：耗时 {overall_elapsed:.1f}s，批次={len(finished_results)}/{len(batches)}，"
        f"累计 inserted={total_inserted} updated={total_updated} skipped={total_skipped} failed={total_failed}"
    )
    for r in finished_results:
        logger.info(
            f"  - 批次 {r['batch_index']}/{len(batches)}：size={r['size']} status={r['status']} "
            f"inserted={r['inserted']} updated={r['updated']} skipped={r['skipped']} taskId={r['task_id']}"
        )
    logger.info("=" * 60)

    if log_cfg.get("save_skipped", True) and aggregated_errors:
        save_skipped(
            progress={
                "status": "AGGREGATED",
                "inserted": total_inserted,
                "updated": total_updated,
                "skipped": total_skipped,
                "failed": total_failed,
                "total": len(records),
                "errors": aggregated_errors,
                "batches": finished_results,
            },
            skipped_template=log_cfg.get(
                "skipped_filename", "logs/upload_internal_skipped_{timestamp}.json"
            ),
            source_file=source_file,
            logger=logger,
        )

    has_failure_status = any(
        str(r["status"]).upper() not in {"SUCCESS", "COMPLETED"} for r in finished_results
    )
    if has_failure_status:
        logger.warn(f"=== 部分批次未成功，详见 {log_path} ===")
        return 1
    if str(progress.get("errorMsg") or "").strip():
        logger.warn(f"=== 任务带 errorMsg：{progress.get('errorMsg')} ===")
        return 1
    logger.info("=== 内部通讯录上传成功 ===")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="内部通讯录 JSON → manjike 后端（INTERNAL_IMPORT）",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--config", type=str, default=None, help="配置文件路径")
    parser.add_argument("--file", type=str, default=None, help="覆盖 source_file")
    parser.add_argument(
        "--mode",
        type=str,
        default=None,
        choices=["FULL", "INCREMENTAL", "APPEND"],
        help="覆盖 mode",
    )
    parser.add_argument("--dry-run", action="store_true", help="只校验 JSON，不调接口")
    return parser.parse_args()


def _cli_main() -> int:
    args = _parse_args()
    return run_pipeline(
        config_path=Path(args.config) if args.config else None,
        source_file_override=Path(args.file) if args.file else None,
        mode_override=args.mode,
        dry_run_override=True if args.dry_run else None,
    )


if __name__ == "__main__":
    exit_code = 0
    try:
        exit_code = _cli_main()
    except KeyboardInterrupt:
        print("\n[CANCELLED] 用户中断（Ctrl+C）")
        exit_code = 130
    except SystemExit as ex:
        exit_code = ex.code if isinstance(ex.code, int) else 1
    except Exception as ex:
        import traceback
        traceback.print_exc()
        print(f"\n[ERROR] 未处理异常：{ex}")
        exit_code = 1
    sys.exit(exit_code)
