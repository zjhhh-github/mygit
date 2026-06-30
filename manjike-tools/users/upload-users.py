#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""manjike 用户批量上传工具（外部独立脚本）。

本脚本用于运维 / 数据同步场景：把一份本地的 users 文件（.txt / .json / .xlsx）
批量上传到 manjike 后端，由后端的「全局用户管理 - 异步导入」接口完成
创建 / 更新 / 失败明细，等同于在管理员后台「批量导入」按钮点一次。

特点：
- 单文件，仅依赖 Python 标准库（urllib + json + email）。
- 无需 pip install，可直接拷贝到任何能访问后端的机器跑。
- 配置可放在同名 .config.json，也可全部用 CLI 参数覆盖。
- 默认幂等：用户已存在按 user_code/user_name 匹配做更新，不重复创建。
- 失败明细写入 ./upload-users.log，方便事后回溯。

用法示例：
    # 1) 第一次跑，先 dry-run 预览（不真正写库）
    python upload-users.py --config upload-users.config.json --dry-run

    # 2) 全部用 CLI 参数（不需要配置文件）
    python upload-users.py \
        --host http://api.example.com \
        --account admin --password 123456 \
        --file users-sample.txt

    # 3) 用配置文件，但把文件路径覆盖一下
    python upload-users.py --config upload-users.config.json \
        --file ./tmp/another-users.xlsx

CLI 参数完整列表请运行：
    python upload-users.py --help
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# 把父目录（manjike-tools/）加入 sys.path，以便 import common
# ============================================================
_SCRIPT_DIR = Path(__file__).resolve().parent
_PARENT_DIR = _SCRIPT_DIR.parent
if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))

# 从公共模块导入：HTTP 工具、登录、路径解析、UTF-8 初始化、公共配置
from common import (  # noqa: E402
    DualLogger,
    ensure_utf8_stdio,
    http_request,
    load_shared_config,
    login,
    normalize_host,
)


# ============================================================
# 日志配置（本脚本保留 logging 风格，用于向后兼容已有的调用方式）
# ============================================================

def setup_logger(log_file: Path) -> logging.Logger:
    """初始化 logger：INFO 级别，控制台 + 文件 双写。

    每次运行追加日志到同一文件，方便横向对比多次执行结果。
    """
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


# ============================================================
# 业务步骤：预览 / 异步导入 / 进度 / 结果（users 专用接口）
# ============================================================

def call_preview(host: str, token: str, file_path: Path, create_missing: bool,
                 logger: logging.Logger) -> Dict[str, Any]:
    """调用预览接口（不写库），返回预览数据。"""
    logger.info("预览导入：file=%s, createMissing=%s", file_path, create_missing)
    status, body = http_request(
        url=f"{host}/api/users/batch/import-preview",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        multipart={"file": file_path, "createMissing": str(create_missing).lower()},
        timeout=300,
    )
    if status != 200 or body.get("code") != 0:
        logger.error("预览失败：HTTP %s / body=%s", status, json.dumps(body, ensure_ascii=False))
        sys.exit(1)
    data = body.get("data") or {}
    logger.info("预览结果：total=%s", data.get("total"))
    logger.info("预览详情 JSON：%s", json.dumps(data, ensure_ascii=False))
    return data


def submit_import_async(host: str, token: str, file_path: Path, create_missing: bool,
                        allow_clear: bool, logger: logging.Logger) -> str:
    """提交异步导入任务，返回 taskId。"""
    logger.info("提交异步导入：file=%s", file_path)
    status, body = http_request(
        url=f"{host}/api/users/batch/import-async",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        multipart={
            "file": file_path,
            "createMissing": str(create_missing).lower(),
            "allowClear": str(allow_clear).lower(),
        },
        timeout=300,
    )
    if status != 200 or body.get("code") != 0:
        logger.error("提交异步任务失败：HTTP %s / body=%s", status, json.dumps(body, ensure_ascii=False))
        sys.exit(1)
    task_id = (body.get("data") or {}).get("taskId")
    if not task_id:
        logger.error("响应中没有 taskId：%s", json.dumps(body, ensure_ascii=False))
        sys.exit(1)
    logger.info("taskId = %s", task_id)
    return task_id


def poll_progress(host: str, token: str, task_id: str, max_wait_seconds: int,
                  poll_interval: int, logger: logging.Logger) -> str:
    """轮询任务进度直到任务进入终态，返回最终状态字符串。

    注意：users 模块的进度接口路径与 prospect 模块不同，因此保留在本脚本中。
    接口路径：/api/users/batch/import-tasks/{taskId}
    """
    started = time.time()
    last_processed = -1
    last_status = ""
    while True:
        if time.time() - started > max_wait_seconds:
            logger.error("等待超时：%ss 内任务仍未完成", max_wait_seconds)
            sys.exit(1)
        status, body = http_request(
            url=f"{host}/api/users/batch/import-tasks/{task_id}",
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if status != 200 or body.get("code") != 0:
            logger.error("查询进度失败：HTTP %s / body=%s", status, json.dumps(body, ensure_ascii=False))
            sys.exit(1)
        progress = body.get("data") or {}
        cur_status = progress.get("status") or ""
        processed = progress.get("processed") or 0
        total = progress.get("total") or 0

        # 仅在状态或处理数量变化时打印一次，避免日志被 1Hz 心跳刷屏
        if cur_status != last_status or processed != last_processed:
            logger.info("进度：status=%s processed=%s/%s", cur_status, processed, total)
            last_status = cur_status
            last_processed = processed

        if cur_status in {"SUCCESS", "FAILED", "CANCELLED", "COMPLETED"}:
            return cur_status
        time.sleep(poll_interval)


def fetch_result(host: str, token: str, task_id: str, logger: logging.Logger) -> Dict[str, Any]:
    """拉取任务最终结果，含 success / failed / errors 明细。"""
    status, body = http_request(
        url=f"{host}/api/users/batch/import-tasks/{task_id}/result",
        method="GET",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    if status != 200 or body.get("code") != 0:
        logger.error("取结果失败：HTTP %s / body=%s", status, json.dumps(body, ensure_ascii=False))
        sys.exit(1)
    return body.get("data") or {}


def save_skipped_records(source_file: Path, errors: List[Dict[str, Any]],
                         output_dir: Path, logger: logging.Logger) -> None:
    """把后端返回的跳过 / 失败明细单独落到独立文件，便于人工补全后重跑。

    会生成最多两个文件，都放在 output_dir（与日志同目录）下：

    1) upload-users-skipped.json
       结构化错误明细（后端 errors 数组原样落盘），字段 rowNum / code / wechat /
       reason 等，带时间戳，方便程序化处理或排查。

    2) <源文件名>.skipped.json （仅当源文件是 .json 时生成）
       从原始飞书 JSON 中按 rowNum 把对应记录提取出来。
       人工把"密码"等字段补完后，可直接把它当成 --file 重跑一次，
       完成"补缺式"二次同步，原文件不动。

    任何 IO / 解析异常都会被吞掉并记录 warning，不影响主流程退出码。
    """
    if not errors:
        return
    timestamp = time.strftime("%Y%m%d-%H%M%S")

    # === 文件 1：错误明细（始终写） ===
    errors_path = output_dir / "upload-users-skipped.json"
    payload: Dict[str, Any] = {
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": str(source_file),
        "count": len(errors),
        "errors": errors,
    }
    try:
        errors_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("已保存跳过/失败明细：%s（共 %s 条）", errors_path, len(errors))
    except OSError as ex:
        logger.warning("保存 %s 失败：%s", errors_path, ex)

    # === 文件 2：原始记录（仅在源文件是 JSON 时） ===
    if source_file.suffix.lower() != ".json":
        return
    try:
        source_data = json.loads(source_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        logger.warning("无法读取源 JSON 文件以提取原始记录：%s", ex)
        return
    if not isinstance(source_data, list):
        logger.warning("源 JSON 不是数组，跳过原始记录提取（实际类型：%s）",
                       type(source_data).__name__)
        return

    skipped_rows = []
    for err in errors:
        # 后端 rowNum 从 1 开始；忽略缺失或非整数的项
        row_num = err.get("rowNum")
        if not isinstance(row_num, int) or row_num < 1 or row_num > len(source_data):
            continue
        original = source_data[row_num - 1]
        if isinstance(original, dict):
            # 在原始记录上附加 _skip_reason 字段，方便人工排查；不破坏原数据结构
            enriched = dict(original)
            enriched["_skip_reason"] = err.get("reason") or err.get("message") or ""
            enriched["_row_num"] = row_num
            skipped_rows.append(enriched)
        else:
            skipped_rows.append({
                "_row_num": row_num,
                "_skip_reason": err.get("reason") or "",
                "_raw": original,
            })

    if not skipped_rows:
        return

    # 同名 .skipped.json：覆盖最新一份，便于 CI / 自动化下游消费
    latest_path = output_dir / f"{source_file.stem}.skipped.json"
    # 同名带时间戳：保留历史，便于多次跑后对比
    history_path = output_dir / f"{source_file.stem}.skipped.{timestamp}.json"
    try:
        content = json.dumps(skipped_rows, ensure_ascii=False, indent=2)
        latest_path.write_text(content, encoding="utf-8")
        history_path.write_text(content, encoding="utf-8")
        logger.info("已保存跳过的原始记录：%s（共 %s 条）", latest_path, len(skipped_rows))
        logger.info("已保存历史快照：%s", history_path)
        logger.info("人工补全后可直接 --file %s 二次重跑", latest_path)
    except OSError as ex:
        logger.warning("保存原始跳过记录失败：%s", ex)


# ============================================================
# 配置加载：CLI 参数 > 配置文件 > 默认值
# ============================================================

def load_config(path: Optional[Path]) -> Dict[str, Any]:
    """读取 .config.json 配置；不存在则返回空字典。"""
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as ex:
        raise SystemExit(f"配置文件解析失败：{path}：{ex}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="manjike 用户批量上传工具（外部独立脚本）",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--config", type=str, default=None,
                        help="JSON 配置文件路径（可选）。同时给出 CLI 参数和配置文件时，CLI 优先。")
    parser.add_argument("--host", type=str, default=None,
                        help="后端 base url，例如 http://api.example.com")
    parser.add_argument("--account", type=str, default=None,
                        help="登录账号（user_code 或 user_name 任一）")
    parser.add_argument("--password", type=str, default=None, help="登录密码")
    parser.add_argument("--file", type=str, default=None,
                        help="待导入的 users 文件（支持 .txt / .json / .xlsx）")
    parser.add_argument("--no-create-missing", action="store_true",
                        help="不自动创建不存在的用户（默认会自动创建）")
    parser.add_argument("--allow-clear", action="store_true",
                        help="允许导入数据为空时清空已有字段（默认 false）")
    parser.add_argument("--dry-run", action="store_true", help="只跑预览，不真正写库")
    parser.add_argument("--max-wait", type=int, default=None,
                        help="异步任务最长等待秒数（默认 300）")
    parser.add_argument("--poll-interval", type=int, default=None,
                        help="进度轮询间隔秒数（默认 1）")
    parser.add_argument("--log-file", type=str, default=None,
                        help="日志文件路径（默认 ./upload-users.log）")
    return parser.parse_args()


def merge(config: Dict[str, Any], args: argparse.Namespace, key: str, default: Any) -> Any:
    """合并优先级：CLI > 配置文件 > 默认值。"""
    cli_val = getattr(args, key.replace("-", "_"), None)
    if cli_val not in (None, False):
        return cli_val
    if key in config and config[key] not in (None, ""):
        return config[key]
    return default


# ============================================================
# 主流程
# ============================================================

def main() -> None:
    args = parse_args()
    # 默认配置查找顺序：
    #   1) --config 显式指定的路径
    #   2) 与脚本同目录的 upload-users.config.json（双击运行时就靠这个）
    # 都找不到就退化为空字典，所有参数从 CLI / 默认值取。
    config_path: Optional[Path] = None
    if args.config:
        config_path = Path(args.config)
    else:
        default_config = _SCRIPT_DIR / "upload-users.config.json"
        if default_config.exists():
            config_path = default_config
    config = load_config(config_path)

    # host 优先级：CLI > 脚本配置文件 > shared.config.json > 默认值
    shared = load_shared_config()
    host = normalize_host(merge(config, args, "host", shared.get("host", "http://localhost:8080")))
    account = merge(config, args, "account", None)
    password = merge(config, args, "password", None)
    file_str = merge(config, args, "file", None)
    no_create_missing = bool(args.no_create_missing or config.get("no_create_missing"))
    allow_clear = bool(args.allow_clear or config.get("allow_clear"))
    dry_run = bool(args.dry_run or config.get("dry_run"))
    max_wait = int(merge(config, args, "max-wait", 300))
    poll_interval = int(merge(config, args, "poll-interval", 1))
    log_file_str = merge(config, args, "log-file",
                         str(_SCRIPT_DIR / "upload-users.log"))

    if not (account and password and file_str):
        print("[ERROR] account / password / file 必填，可通过 --config 或 CLI 参数提供。")
        sys.exit(2)

    def _resolve(p: str) -> Path:
        """相对路径锚定到脚本目录，避免因 cwd 不同而找不到文件。"""
        q = Path(p)
        return q if q.is_absolute() else (_SCRIPT_DIR / q).resolve()

    file_path = _resolve(file_str)
    if not file_path.exists():
        print(f"[ERROR] 待导入文件不存在：{file_path}")
        print(f"        提示：相对路径基于脚本目录「{_SCRIPT_DIR}」解析，请确认文件名拼写或改用绝对路径。")
        sys.exit(2)

    log_file = _resolve(log_file_str)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    ensure_utf8_stdio()
    logger = setup_logger(log_file)

    logger.info("=" * 60)
    logger.info("manjike 用户批量上传开始")
    logger.info("host=%s, account=%s, file=%s, dry-run=%s",
                host, account, file_path, dry_run)
    logger.info("=" * 60)

    # login 来自 common，与其他脚本共享同一实现
    _dual_logger = DualLogger(log_file)
    token = login(host, account, password, _dual_logger)

    call_preview(host, token, file_path, not no_create_missing, logger)
    if dry_run:
        logger.info("已完成 dry-run，跳过实际写入。")
        return

    task_id = submit_import_async(host, token, file_path, not no_create_missing,
                                  allow_clear, logger)
    final_status = poll_progress(host, token, task_id, max_wait, poll_interval, logger)
    logger.info("任务终态：%s", final_status)

    result = fetch_result(host, token, task_id, logger)
    success = result.get("success", 0)
    failed = result.get("failed", 0)
    total = result.get("total", success + failed)
    errors = result.get("errors") or []

    logger.info("最终结果：total=%s success=%s failed=%s", total, success, failed)
    if errors:
        logger.info("失败明细（前 50 条）：")
        for err in errors[:50]:
            logger.info("  - %s", json.dumps(err, ensure_ascii=False))
        if len(errors) > 50:
            logger.info("  ...（共 %s 条，剩余请到日志文件查看完整 JSON 输出）", len(errors))
        logger.info("失败明细完整 JSON：%s", json.dumps(errors, ensure_ascii=False))
        # 同时把跳过 / 失败明细落成独立文件，便于人工补全后重跑
        save_skipped_records(file_path, errors, log_file.parent, logger)

    # 判定退出码：
    #   - 任务终态非 SUCCESS/COMPLETED → 真正失败（exit 1）
    #   - failed=0 → 完全成功（exit 0）
    #   - failed>0 但所有 errors 都是"业务预期跳过"原因（密码为空 / 编号重复等）
    #     → 视为成功（exit 0）+ 提示"有 N 条被跳过"
    #   - 其它情况 → 真正失败（exit 1）
    expected_skip_reasons = {
        "密码为空", "文件内编号重复", "编号不存在",
        "角色不存在", "编号格式错误",
    }
    real_failures = [
        err for err in errors
        if (err.get("reason") or err.get("message") or "").strip() not in expected_skip_reasons
    ]

    if final_status not in {"SUCCESS", "COMPLETED"}:
        logger.warning("=== 导入失败，任务终态=%s，详见 %s ===", final_status, log_file)
        sys.exit(1)
    if failed == 0:
        logger.info("=== 导入成功 ===")
        sys.exit(0)
    if not real_failures:
        # 全部失败都是已知的"业务跳过"，例如飞书源数据"密码"列空 → 后端按设计跳过
        logger.info("=== 导入成功（%s 条业务原因被跳过，详见 %s）===",
                    len(errors), log_file)
        sys.exit(0)
    logger.warning("=== 导入存在 %s 条真正失败，详见 %s ===",
                   len(real_failures), log_file)
    sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CANCELLED] 用户中断。")
        sys.exit(130)
    except Exception as ex:
        import traceback
        traceback.print_exc()
        print(f"\n[ERROR] 未处理异常：{ex}")
        sys.exit(1)
