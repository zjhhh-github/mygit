#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""意向通讯录 · 用户管理批量上传（Prospect USERS_IMPORT）。

【脚本功能说明】
读取本地用户 JSON，批量导入到 Prospect 用户管理（USERS_IMPORT）或佣金主系统全局用户；
默认走 Prospect 独立系统，仅显式指定 --target commission 时才写入 8080 主系统。

默认上传到 **Prospect 独立系统** 的用户管理（`http://localhost`），
**不会** 操作佣金查询主系统（`http://localhost:8080`），除非你显式指定
`--target commission`。

接口：
    POST /api/prospect/import/tasks/upload   type=USERS_IMPORT, mode=USER_INCREMENTAL
    GET  /api/prospect/import/tasks/{id}    轮询进度

配置：upload-users.config.json + 根目录 config.json（prospect 段）
环境变量：PROSPECT_HOST / PROSPECT_ACCOUNT / PROSPECT_PASSWORD

用法：
    cd manjike-tools/users
    python upload-users.py --config upload-users.config.json --dry-run
    python upload-users.py --config upload-users.config.json

    # 极少数场景：仍要写入佣金主系统全局用户（需显式指定）
    python upload-users.py --target commission --config upload-users.config.json
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_PARENT_DIR = _SCRIPT_DIR.parent
if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))

from common import (  # noqa: E402
    DualLogger,
    ensure_utf8_stdio,
    http_request,
    is_effective_config_value,
    login,
    normalize_host,
    resolve_commission_config,
    resolve_path,
)
from prospect_client import (  # noqa: E402
    login_prospect_session,
    normalize_user_mode,
    parse_result_summary,
    resolve_prospect_server,
    summary_count,
    upload_import_file,
    write_temp_json,
)

IMPORT_TYPE = "USERS_IMPORT"
DEFAULT_MODE = "USER_INCREMENTAL"


def setup_logger(log_file: Path) -> logging.Logger:
    """控制台 + 文件双写日志（与历史 upload-users.log 兼容）。"""
    logger = logging.getLogger("upload-users")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)
    fh = logging.FileHandler(str(log_file), mode="a", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


def load_config(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as ex:
        raise SystemExit("配置文件解析失败：{}：{}".format(path, ex))


def merge(config: Dict[str, Any], args: argparse.Namespace, key: str, default: Any) -> Any:
    """CLI > 配置文件 > 默认值；占位符视为未填写。"""
    cli_val = getattr(args, key.replace("-", "_"), None)
    if cli_val not in (None, False):
        return cli_val
    if key in config and is_effective_config_value(config[key]):
        return config[key]
    return default


def _resolve_path(value: str) -> Path:
    return resolve_path(value, _SCRIPT_DIR)


def save_skipped(progress: Dict[str, Any], output_dir: Path, source_file: Path, logger: logging.Logger) -> None:
    """保存 Prospect 导入失败/跳过明细。"""
    errors = progress.get("errors") or progress.get("failures") or []
    if not errors:
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / "upload-users-skipped.{}.json".format(ts)
    path.write_text(json.dumps({
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": str(source_file),
        "task_status": progress.get("status"),
        "count": len(errors),
        "errors": errors,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已保存失败/跳过明细：%s（%s 条）", path, len(errors))


def run_prospect_pipeline(
    config: Dict[str, Any],
    args: argparse.Namespace,
    file_path: Path,
    log_file: Path,
) -> int:
    """默认流程：上传到 Prospect 用户管理（USERS_IMPORT）。"""
    server = resolve_prospect_server(config, logger=DualLogger(log_file))
    account = merge(config, args, "account", server.get("account"))
    password = merge(config, args, "password", server.get("password"))
    host = normalize_host(merge(config, args, "host", server.get("host") or "http://localhost"))

    if not (account and password):
        print("[ERROR] account / password 必填（config.json prospect 段或 CLI）。")
        return 2

    try:
        mode = normalize_user_mode(merge(config, args, "mode", DEFAULT_MODE))
    except ValueError as ex:
        print("[ERROR] {}".format(ex))
        return 2

    dry_run = bool(args.dry_run or config.get("dry_run"))
    max_wait = int(merge(config, args, "max-wait", 3600))
    poll_interval = int(merge(config, args, "poll-interval", 2))
    batch_size = int(config.get("batch_size") or 0)

    ensure_utf8_stdio()
    logger = setup_logger(log_file)
    dual = DualLogger(log_file)

    records: List[Dict[str, Any]] = []
    if file_path.suffix.lower() == ".json":
        loaded = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            records = loaded

    logger.info("=" * 60)
    logger.info("意向通讯录 · 用户管理上传（Prospect USERS_IMPORT）")
    logger.info("host=%s account=%s file=%s mode=%s dry-run=%s",
                host, account, file_path, mode, dry_run)
    logger.info("后台验证页：http://localhost/presale/admin/users")
    logger.info("=" * 60)

    if dry_run:
        if config.get("dry_run") and not args.dry_run:
            logger.info("配置文件 dry_run=true，仅本地校验，不上传。")
        logger.info("dry-run：跳过登录与上传")
        if records:
            logger.info("记录数=%s，前 3 条：%s", len(records),
                        json.dumps(records[:3], ensure_ascii=False))
        else:
            logger.info("非 JSON 或无法本地解析条数，正式运行将由 Prospect 后端解析文件。")
        return 0

    session = login_prospect_session(host, account, password, dual)
    token = session["token"]

    upload_jobs: List[tuple] = []
    temp_files: List[Path] = []
    if file_path.suffix.lower() == ".json" and batch_size > 0 and len(records) > batch_size:
        for i in range(0, len(records), batch_size):
            temp_path = write_temp_json(records[i:i + batch_size])
            temp_files.append(temp_path)
            upload_jobs.append((temp_path, len(records[i:i + batch_size])))
    else:
        upload_jobs.append((file_path, len(records) if records else -1))

    totals = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    aggregated_errors: List[Dict[str, Any]] = []
    batch_results: List[Dict[str, Any]] = []
    abort_flag = False

    try:
        for idx, (upload_path, batch_count) in enumerate(upload_jobs):
            if abort_flag:
                continue
            prefix = "[批次 {}/{}] ".format(idx + 1, len(upload_jobs))
            progress = upload_import_file(
                host=host,
                token=token,
                upload_file=upload_path,
                import_type=IMPORT_TYPE,
                mode=mode,
                logger=dual,
                max_wait_seconds=max_wait,
                poll_interval=poll_interval,
                log_prefix=prefix,
            )
            status_text = str(progress.get("status") or "").upper()
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
            logger.info("%s[IMPORT] 批次结果 inserted=%s updated=%s skipped=%s failed=%s",
                        prefix.strip(), ins, upd, skp, fail)
            if status_text not in {"SUCCESS", "COMPLETED"}:
                abort_flag = True
    finally:
        for p in temp_files:
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass

    logger.info("[IMPORT] 合计 inserted=%s updated=%s skipped=%s failed=%s",
                totals["inserted"], totals["updated"], totals["skipped"], totals["failed"])

    if aggregated_errors:
        save_skipped({"status": "AGGREGATED", "errors": aggregated_errors}, log_file.parent, file_path, logger)

    failed_status = any(r["status"] not in {"SUCCESS", "COMPLETED"} for r in batch_results)
    if failed_status:
        logger.warning("=== 导入失败，详见 %s ===", log_file)
        return 1
    logger.info("=== 导入成功（请在 Prospect 用户管理页核对）===")
    return 0


# ---------------------------------------------------------------------------
# 可选：佣金主系统全局用户导入（仅 --target commission 时启用）
# ---------------------------------------------------------------------------

def _commission_preview(host: str, token: str, file_path: Path, create_missing: bool,
                        logger: logging.Logger) -> None:
    logger.info("预览导入：file=%s, createMissing=%s", file_path, create_missing)
    status, body = http_request(
        url="{}/api/users/batch/import-preview".format(host),
        method="POST",
        headers={"Authorization": "Bearer {}".format(token)},
        multipart={"file": file_path, "createMissing": str(create_missing).lower()},
        timeout=300,
    )
    if status != 200 or body.get("code") != 0:
        logger.error("预览失败：HTTP %s / body=%s", status, json.dumps(body, ensure_ascii=False))
        sys.exit(1)
    data = body.get("data") or {}
    logger.info("预览结果：total=%s", data.get("total"))
    logger.info("预览详情 JSON：%s", json.dumps(data, ensure_ascii=False))


def _commission_import(host: str, token: str, file_path: Path, create_missing: bool,
                       allow_clear: bool, max_wait: int, poll_interval: int,
                       logger: logging.Logger) -> int:
    logger.info("提交异步导入：file=%s", file_path)
    status, body = http_request(
        url="{}/api/users/batch/import-async".format(host),
        method="POST",
        headers={"Authorization": "Bearer {}".format(token)},
        multipart={
            "file": file_path,
            "createMissing": str(create_missing).lower(),
            "allowClear": str(allow_clear).lower(),
        },
        timeout=300,
    )
    if status != 200 or body.get("code") != 0:
        logger.error("提交失败：HTTP %s / body=%s", status, json.dumps(body, ensure_ascii=False))
        return 1
    task_id = (body.get("data") or {}).get("taskId")
    if not task_id:
        logger.error("响应无 taskId")
        return 1
    logger.info("taskId = %s", task_id)

    started = time.time()
    final_status = ""
    while True:
        if time.time() - started > max_wait:
            logger.error("等待超时")
            return 1
        status, body = http_request(
            url="{}/api/users/batch/import-tasks/{}".format(host, task_id),
            method="GET",
            headers={"Authorization": "Bearer {}".format(token)},
            timeout=30,
        )
        if status != 200 or body.get("code") != 0:
            logger.error("查询进度失败")
            return 1
        progress = body.get("data") or {}
        final_status = progress.get("status") or ""
        logger.info("进度：status=%s processed=%s/%s",
                    final_status, progress.get("processed"), progress.get("total"))
        if final_status in {"SUCCESS", "FAILED", "CANCELLED", "COMPLETED"}:
            break
        time.sleep(poll_interval)

    status, body = http_request(
        url="{}/api/users/batch/import-tasks/{}/result".format(host, task_id),
        method="GET",
        headers={"Authorization": "Bearer {}".format(token)},
        timeout=60,
    )
    result = (body.get("data") or {}) if status == 200 and body.get("code") == 0 else {}
    logger.info("最终结果：total=%s success=%s failed=%s",
                result.get("total"), result.get("success"), result.get("failed"))
    if final_status not in {"SUCCESS", "COMPLETED"}:
        return 1
    return 0


def run_commission_pipeline(
    config: Dict[str, Any],
    args: argparse.Namespace,
    file_path: Path,
    log_file: Path,
) -> int:
    """显式指定 --target commission 时才写入主系统 8080。"""
    commission_cfg = resolve_commission_config(config)
    host = normalize_host(merge(config, args, "host", commission_cfg.get("host", "http://localhost:8080")))
    account = merge(config, args, "account", commission_cfg.get("account"))
    password = merge(config, args, "password", commission_cfg.get("password"))
    dry_run = bool(args.dry_run or config.get("dry_run"))
    no_create_missing = bool(args.no_create_missing or config.get("no_create_missing"))
    allow_clear = bool(args.allow_clear or config.get("allow_clear"))
    max_wait = int(merge(config, args, "max-wait", 600))
    poll_interval = int(merge(config, args, "poll-interval", 1))

    if not (account and password):
        print("[ERROR] commission 模式需要 account / password。")
        return 2

    ensure_utf8_stdio()
    logger = setup_logger(log_file)
    dual = DualLogger(log_file)

    logger.info("=" * 60)
    logger.warning("目标=佣金主系统全局用户（8080），非意向通讯录默认路径")
    logger.info("host=%s account=%s file=%s dry-run=%s", host, account, file_path, dry_run)
    logger.info("=" * 60)

    token = login(host, account, password, dual)
    _commission_preview(host, token, file_path, not no_create_missing, logger)
    if dry_run:
        logger.info("已完成 dry-run，跳过实际写入。")
        return 0
    return _commission_import(host, token, file_path, not no_create_missing,
                            allow_clear, max_wait, poll_interval, logger)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="意向通讯录用户管理批量上传（默认 Prospect USERS_IMPORT）",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--config", type=str, default=None, help="配置文件路径")
    parser.add_argument(
        "--target",
        choices=("prospect", "commission"),
        default=None,
        help="上传目标：prospect=意向通讯录用户管理（默认）；commission=主系统8080（需显式指定）",
    )
    parser.add_argument("--host", type=str, default=None, help="Prospect 或主系统 host")
    parser.add_argument("--account", type=str, default=None, help="登录账号")
    parser.add_argument("--password", type=str, default=None, help="登录密码")
    parser.add_argument("--file", type=str, default=None, help=".json / .xlsx / .csv 用户文件")
    parser.add_argument("--mode", type=str, default=None, help="Prospect 用户导入 mode，默认 USER_INCREMENTAL")
    parser.add_argument("--no-create-missing", action="store_true",
                        help="仅 commission 模式：禁止自动创建不存在用户")
    parser.add_argument("--allow-clear", action="store_true",
                        help="仅 commission 模式：允许空值清空字段")
    parser.add_argument("--dry-run", action="store_true", help="仅校验/预览，不上传")
    parser.add_argument("--max-wait", type=int, default=None, help="轮询最长秒数")
    parser.add_argument("--poll-interval", type=int, default=None, help="轮询间隔秒数")
    parser.add_argument("--log-file", type=str, default=None, help="日志文件路径")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path: Optional[Path] = None
    if args.config:
        config_path = Path(args.config)
    else:
        default_config = _SCRIPT_DIR / "upload-users.config.json"
        if default_config.exists():
            config_path = default_config
    config = load_config(config_path)

    target = merge(config, args, "target", "prospect")
    file_str = merge(config, args, "file", None)
    log_file_str = merge(config, args, "log-file", str(_SCRIPT_DIR / "upload-users.log"))

    if not file_str:
        print("[ERROR] file 必填（配置文件 file 或 --file）。")
        sys.exit(2)

    file_path = _resolve_path(file_str)
    if not file_path.exists():
        print("[ERROR] 待导入文件不存在：{}".format(file_path))
        sys.exit(2)

    log_file = _resolve_path(log_file_str)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    if target == "commission":
        code = run_commission_pipeline(config, args, file_path, log_file)
    else:
        code = run_prospect_pipeline(config, args, file_path, log_file)
    sys.exit(code)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CANCELLED] 用户中断。")
        sys.exit(130)
    except Exception as ex:
        import traceback
        traceback.print_exc()
        print("\n[ERROR] 未处理异常：{}".format(ex))
        sys.exit(1)
