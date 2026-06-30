#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「意向通讯录」JSON 上传到 manjike 后端。

【脚本作用】
读取一份按学员聚合的意向通讯录 JSON（默认是 xlsx_to_prospect_json.py 输出的
「意向通讯录.json」），通过后端的异步导入接口写入数据库：

    POST  /api/prospect/import-tasks        提交任务（type=STUDENTS_IMPORT）
    GET   /api/prospect/import-tasks/{id}   查询进度

接口入参（application/json）：
    {
      "type":    "STUDENTS_IMPORT",
      "mode":    "INCREMENTAL",      # FULL / INCREMENTAL / APPEND
      "payload": "[{...}, ...]"       # 注意 payload 是「字符串化的 JSON 数组」
    }

【权限】上传接口要求 ADMIN 角色，登录账号必须是超级管理员。

【配置驱动】
所有可调项放到同目录的 upload_prospect.config.json：
  - 服务端：host / account / password
  - 上传：source_file / type / mode / dry_run / max_wait_seconds / poll_interval_seconds
  - 日志：log_file / save_skipped / skipped_filename

【输出物】
  - <log_file>                                  屏幕日志 + 文件双写（默认 logs/upload_prospect.log）
  - <skipped_filename>（如有跳过/失败）         结构化跳过明细 JSON（带时间戳）

【依赖】Python 3.7+，仅标准库（urllib + json），无需 pip install。

【模块化】
本脚本可被直接 import：
    from upload_prospect import run_pipeline
    rc = run_pipeline()  # 0 = 成功

CLI 用法：
    python upload_prospect.py
    python upload_prospect.py --config some.config.json
    python upload_prospect.py --file 其它.json
    python upload_prospect.py --dry-run
    python upload_prospect.py --mode FULL          # 覆盖默认配置的 mode
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

# 从公共模块导入：HTTP 工具、登录、日志、配置合并、路径解析、Host 规范化、公共配置
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

_SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = _SCRIPT_DIR / "upload_prospect.config.json"

# 内置默认值：配置文件缺字段时兜底，保证脚本可跑
DEFAULT_CONFIG: Dict[str, Any] = {
    "服务端": {
        "host": "http://localhost:8080",
        "account": "admin",
        "password": "admin123",
    },
    "上传": {
        "source_file": "意向通讯录.json",
        "type": "STUDENTS_IMPORT",
        "mode": "INCREMENTAL",
        "dry_run": False,
        # batch_size > 0：按学员维度分批提交，每批独立异步任务；= 0 不分批
        "batch_size": 1000,
        # concurrent_batches：保留字段，后端同类型任务互斥，当前串行执行
        "concurrent_batches": 3,
        # 任一批失败时是否中止后续批次。默认 False
        "abort_on_batch_failure": False,
        "max_wait_seconds": 1800,
        "poll_interval_seconds": 2,
    },
    "日志": {
        "log_file": "logs/upload_prospect.log",
        "save_skipped": True,
        "skipped_filename": "logs/upload_prospect_skipped_{timestamp}.json",
    },
}


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """加载配置，找不到时使用内置默认值。"""
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


# ============================================================
# 业务步骤：提交任务 / 轮询进度（prospect 专用）
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

    注意：本接口没有独立的 /result 端点，最终结果（含 errors / failed）就在
    最后一次 progress 响应里。

    log_prefix：分批模式下用来标记当前是第几批，例如 "[批次 5/21] "。
    """
    logger.info(f"{log_prefix}轮询进度：taskId={task_id}（最长 {max_wait_seconds}s）")
    started = time.time()
    last_signature = ""
    while True:
        if time.time() - started > max_wait_seconds:
            logger.error(f"{log_prefix}等待超时：{max_wait_seconds}s 内任务仍未完成")
            # 返回超时对象，不直接 exit，让批次循环决定是否继续
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
        signature = f"{cur_status}|{processed}|{total}"
        if signature != last_signature:
            logger.info(f"{log_prefix}进度：status={cur_status} processed={processed}/{total}")
            last_signature = signature

        # 后端返回的 status 既有大写（SUCCESS）也有小写（success），统一按 upper() 比对
        if cur_status.upper() in {"SUCCESS", "FAILED", "CANCELLED", "COMPLETED"}:
            return progress
        time.sleep(poll_interval)


def upload_one_batch(
    host: str,
    token: str,
    type_: str,
    mode: str,
    batch_records: List[Dict[str, Any]],
    max_wait_seconds: int,
    poll_interval: int,
    logger: DualLogger,
    log_prefix: str = "",
) -> Dict[str, Any]:
    """提交一批意向通讯录 → 轮询完成 → 返回最终 progress。

    把"提交-轮询"封装成一个原子操作，便于主流程循环调用做分批上传。
    任一步异常都不直接 exit，转成 progress 形式返回给调用方决策。
    """
    payload_json = json.dumps(batch_records, ensure_ascii=False)
    logger.info(
        f"{log_prefix}提交：学员 {len(batch_records)} 个，payload={len(payload_json)}B"
    )
    status, body = http_request(
        url=f"{host}/api/prospect/import-tasks",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        json_body={"type": type_, "mode": mode, "payload": payload_json},
        timeout=120,
    )
    if status != 200 or body.get("code") != 0:
        logger.error(f"{log_prefix}提交失败：HTTP {status} / body={json.dumps(body, ensure_ascii=False)}")
        return {
            "status": "FAILED",
            "errors": [{"reason": f"提交 HTTP {status}", "body": body}],
        }
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


# ============================================================
# 跳过明细落盘
# ============================================================

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
    skipped_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"已保存跳过/失败明细：{skipped_path}（{len(errors)} 条）")


# ============================================================
# 公开 API：run_pipeline()
# ============================================================

def run_pipeline(
    config_path: Optional[Path] = None,
    source_file_override: Optional[Path] = None,
    mode_override: Optional[str] = None,
    dry_run_override: Optional[bool] = None,
) -> int:
    """端到端：读配置 → 登录 → 提交 → 轮询 → 保存跳过明细。

    返回 0 = 成功；非 0 = 失败码。
    本函数不调用 sys.exit，便于宿主程序根据返回值决定下一步。
    """
    config = load_config(config_path)
    server = dict(config["服务端"])
    # host 优先级：脚本配置文件 > shared.config.json > 默认值
    shared = load_shared_config()
    server_host = normalize_host(server.get("host") or shared.get("host") or "http://localhost:8080")
    upload_cfg = config["上传"]
    log_cfg = config["日志"]

    log_path = _resolve(log_cfg.get("log_file", "logs/upload_prospect.log"))
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
    type_ = upload_cfg.get("type") or "STUDENTS_IMPORT"
    dry_run = dry_run_override if dry_run_override is not None else bool(upload_cfg.get("dry_run", False))
    batch_size = int(upload_cfg.get("batch_size", 0) or 0)
    abort_on_failure = bool(upload_cfg.get("abort_on_batch_failure", False))
    max_wait = int(upload_cfg.get("max_wait_seconds", 1800))
    poll_interval = int(upload_cfg.get("poll_interval_seconds", 2))

    # 计算分批：batch_size <= 0 时退化为"一次性提交"（视作 1 批）
    if batch_size > 0 and len(records) > batch_size:
        batches = [records[i : i + batch_size] for i in range(0, len(records), batch_size)]
    else:
        batches = [records]

    logger.info("=" * 60)
    logger.info("查询系统：意向通讯录上传开始")
    logger.info(f"host={server_host}, account={server['account']}")
    logger.info(
        f"source_file={source_file}, 学员数={len(records)}, "
        f"批次={len(batches)}（batch_size={batch_size if batch_size > 0 else '不分批'}）, "
        f"type={type_}, mode={mode}, dry_run={dry_run}"
    )
    logger.info("=" * 60)

    if dry_run:
        # dry-run 不调任何写接口；做一次轻量校验看看 JSON 顶层结构
        logger.info("dry-run 模式：跳过登录 / 提交 / 轮询")
        sample = records[:3]
        logger.info("前 3 条示例：" + json.dumps(sample, ensure_ascii=False))
        return 0

    # 只登录一次，所有批次复用同一个 token
    token = login(server_host, server["account"], server["password"], logger)

    # 后端同类型任务同时只允许一个，串行执行：提交一批→等完成→再提交下一批
    total_processed = 0
    total_success = 0
    total_failed = 0
    aggregated_errors: List[Dict[str, Any]] = []
    batch_results: List[Optional[Dict[str, Any]]] = [None] * len(batches)
    abort_flag = False
    overall_started = time.time()

    for idx, batch in enumerate(batches):
        if abort_flag:
            logger.warn(f"[批次 {idx + 1}/{len(batches)}] abort_on_batch_failure=True，跳过")
            continue

        prefix = f"[批次 {idx + 1}/{len(batches)}] "
        progress = upload_one_batch(
            host=server_host,
            token=token,
            type_=type_,
            mode=mode,
            batch_records=batch,
            max_wait_seconds=max_wait,
            poll_interval=poll_interval,
            logger=logger,
            log_prefix=prefix,
        )

        status_text = str(progress.get("status") or "")
        success = int(progress.get("success") or 0)
        failed = int(progress.get("failed") or 0)
        processed = int(progress.get("processed") or progress.get("total") or len(batches[idx]))
        total_processed += processed
        total_success += success
        total_failed += failed

        for err in (progress.get("errors") or progress.get("failures") or []):
            if isinstance(err, dict):
                err.setdefault("_batch_index", idx + 1)
                aggregated_errors.append(err)
            else:
                aggregated_errors.append({"_batch_index": idx + 1, "raw": err})

        batch_results[idx] = {
            "batch_index": idx + 1,
            "size": len(batches[idx]),
            "task_id": progress.get("_taskId"),
            "status": status_text,
            "success": success,
            "failed": failed,
        }
        logger.info(
            f"{prefix}完成 status={status_text} success={success} failed={failed}；"
            f"累计 {total_success}/{len(records)}（成功），跨批 {total_failed} 条问题"
        )

        if status_text.upper() not in {"SUCCESS", "COMPLETED"} and abort_on_failure:
            abort_flag = True
            logger.error(f"{prefix}非成功终态 + abort_on_batch_failure=True，后续批次将跳过")

    overall_elapsed = time.time() - overall_started

    # 过滤掉被 abort_on_batch_failure 跳过、没有实际执行的批次（值为 None）
    finished_results: List[Dict[str, Any]] = [r for r in batch_results if r is not None]
    logger.info("=" * 60)
    logger.info(
        f"全部批次完成：耗时 {overall_elapsed:.1f}s，"
        f"批次={len(finished_results)}/{len(batches)}，"
        f"累计 success={total_success}, failed={total_failed}"
    )
    for r in finished_results:
        logger.info(
            f"  - 批次 {r['batch_index']}/{len(batches)}：size={r['size']} "
            f"status={r['status']} success={r['success']} failed={r['failed']} "
            f"taskId={r['task_id']}"
        )
    logger.info("=" * 60)

    # 跳过明细落盘
    if log_cfg.get("save_skipped", True) and aggregated_errors:
        consolidated_progress = {
            "status": "AGGREGATED",
            "success": total_success,
            "failed": total_failed,
            "total": len(records),
            "errors": aggregated_errors,
            "batches": finished_results,
        }
        save_skipped(
            progress=consolidated_progress,
            skipped_template=log_cfg.get(
                "skipped_filename", "logs/upload_prospect_skipped_{timestamp}.json"
            ),
            source_file=source_file,
            logger=logger,
        )

    # 退出码判定
    has_failure_status = any(
        str(r["status"]).upper() not in {"SUCCESS", "COMPLETED"} for r in finished_results
    )
    if has_failure_status:
        logger.warn(f"=== 部分批次未成功，详见 {log_path} ===")
        return 1
    if total_failed == 0:
        logger.info("=== 上传成功 ===")
        return 0
    logger.warn(f"=== 上传完成，但有 {total_failed} 条记录失败/跳过，详见 {log_path} ===")
    # 与 users 上传保持一致：业务跳过不算 fail。如想严格 fail，把下方改成 return 1。
    return 0


# ============================================================
# CLI 入口
# ============================================================

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="意向通讯录 JSON → manjike 后端（上传同步）",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--config", type=str, default=None,
                        help="配置文件路径，默认为同目录 upload_prospect.config.json")
    parser.add_argument("--file", type=str, default=None,
                        help="覆盖配置里的 source_file，指定其它意向通讯录 JSON")
    parser.add_argument("--mode", type=str, default=None,
                        choices=["FULL", "INCREMENTAL", "APPEND"],
                        help="覆盖配置里的 mode")
    parser.add_argument("--dry-run", action="store_true",
                        help="只做 JSON 校验，不真正登录或调接口")
    return parser.parse_args()


def _cli_main() -> int:
    ensure_utf8_stdio()
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
