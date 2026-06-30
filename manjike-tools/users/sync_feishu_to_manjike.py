#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询系统上传用户：飞书多维表格 → manjike 后端 一键流水线。

整体功能名称：查询系统上传用户
脚本职责：把飞书多维表格里维护的用户数据，按顺序同步到 manjike 查询系统。

执行顺序：
    1) feishu-read-bitable.py  → 拉取飞书最新数据，写入 feishu-users.json
    2) upload-users.py         → 通过后端 API 批量导入 / 更新用户

设计原则：
    - 子脚本独立可用，本脚本只是流水线；任一步失败立即停止后续步骤。
    - 子脚本的配置文件（feishu-read-bitable.config.json / upload-users.config.json）
      不需要在这里复读，子脚本自身会按规则读取。
    - 所有 stdout / stderr 实时透传到当前控制台，看到的就是子脚本看到的。
    - 写一份合并日志到 sync-feishu-to-manjike.log，记录每个步骤的耗时与退出码。

可作为模块被 import：
    本文件特意采用下划线命名 sync_feishu_to_manjike.py，方便未来"统一定时调度器"
    直接 import 调用。后期把多个功能（如本脚本 + 其它同步脚本）打包成带定时器的
    可执行文件时，可这样使用：

        from sync_feishu_to_manjike import run_pipeline

        rc = run_pipeline(skip_fetch=False, skip_upload=False)
        # rc == 0 即视为成功

CLI 用法：
    python sync_feishu_to_manjike.py
    python sync_feishu_to_manjike.py --skip-fetch              # 跳过飞书拉取
    python sync_feishu_to_manjike.py --skip-upload             # 只拉取，不上传
    python sync_feishu_to_manjike.py --upload-args="--dry-run"
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_FILE = SCRIPT_DIR / "sync-feishu-to-manjike.log"

# 子脚本入口文件名集中常量化，未来重命名 / 拆分时只改这里
FEISHU_FETCH_SCRIPT = "feishu-read-bitable.py"
UPLOAD_USERS_SCRIPT = "upload-users.py"


# ============================================================
# 日志：屏幕 + 文件双写。注意 print 不能在被定时器宿主复用时干扰宿主输出，
# 所以把日志函数抽出来，未来需要替换为 logging 模块也只动这里。
# ============================================================

def _log_line(message: str) -> None:
    """同时把消息打到屏幕和日志文件。"""
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        # 日志写不进去就忽略，不能阻塞主流程
        pass


# ============================================================
# 核心：执行单个子脚本
# ============================================================

def _run_step(title: str, script_name: str, extra_args: Optional[Sequence[str]] = None) -> int:
    """执行一个子脚本，返回 exit code。

    使用与本脚本相同的 Python 解释器调用，保证虚拟环境一致；
    cwd 固定为脚本所在目录，确保子脚本里的相对路径（如 ./feishu-users.json）正确解析。
    """
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        _log_line(f"[ERROR] 找不到子脚本：{script_path}")
        return 127

    cmd = [sys.executable, str(script_path)]
    if extra_args:
        cmd.extend(extra_args)

    _log_line("=" * 60)
    _log_line(f"[STEP] {title}")
    _log_line(f"[STEP] 命令：{' '.join(cmd)}")
    _log_line("=" * 60)

    started = time.time()
    try:
        # stdout/stderr 不捕获，直接继承当前控制台 → 子脚本日志即时可见。
        # 未来若希望宿主进程吞掉子脚本输出，可改成 capture_output=True 再回传。
        proc = subprocess.run(cmd, cwd=str(SCRIPT_DIR), check=False)
    except OSError as ex:
        _log_line(f"[ERROR] 启动子脚本失败：{ex}")
        return 1
    elapsed = time.time() - started

    _log_line(f"[STEP] {title} 完成，耗时 {elapsed:.1f}s，exit_code={proc.returncode}")
    return proc.returncode


# ============================================================
# 公开 API：run_pipeline()
# 未来定时器宿主程序应直接调用本函数，而不是再用 subprocess 套一层。
# ============================================================

def run_pipeline(
    skip_fetch: bool = False,
    skip_upload: bool = False,
    fetch_args: Optional[Sequence[str]] = None,
    upload_args: Optional[Sequence[str]] = None,
) -> int:
    """执行完整的"飞书 → manjike"流水线。

    参数：
        skip_fetch:   True 时跳过飞书拉取（适合上次拉过、本次只想重传 JSON）
        skip_upload:  True 时跳过上传（只拉取生成 feishu-users.json）
        fetch_args:   传给 feishu-read-bitable.py 的额外 CLI 参数列表
        upload_args:  传给 upload-users.py 的额外 CLI 参数列表

    返回值：
        0       全部成功（含 dry-run 顺利完成）
        非 0     某一步失败，对应步骤返回的 exit code

    注意：
        本函数不会调用 sys.exit，方便宿主进程根据返回值决定是否 abort，
        或继续执行其它流水线（适合"统一定时调度器"批跑多任务的场景）。
    """
    overall_started = time.time()
    _log_line("#" * 60)
    _log_line("# 查询系统上传用户：开始")
    _log_line(f"# 工作目录：{SCRIPT_DIR}")
    _log_line(f"# Python：{sys.executable}")
    _log_line("#" * 60)

    # ============ STEP 1：从飞书拉取数据 ============
    if skip_fetch:
        _log_line("[INFO] skip_fetch=True，跳过飞书拉取")
    else:
        rc = _run_step("1/2 从飞书拉取最新数据", FEISHU_FETCH_SCRIPT, fetch_args)
        if rc != 0:
            _log_line(f"[ABORT] 飞书拉取失败（exit={rc}），不执行后续上传步骤。")
            return rc

    # ============ STEP 2：上传到 manjike 后端 ============
    if skip_upload:
        _log_line("[INFO] skip_upload=True，跳过上传步骤")
    else:
        rc = _run_step("2/2 上传到 manjike 后端", UPLOAD_USERS_SCRIPT, upload_args)
        if rc != 0:
            _log_line(f"[ABORT] 上传失败（exit={rc}）。")
            return rc

    overall_elapsed = time.time() - overall_started
    _log_line("#" * 60)
    _log_line(f"# 查询系统上传用户：完成，总耗时 {overall_elapsed:.1f}s")
    _log_line("#" * 60)
    return 0


# ============================================================
# CLI 入口：仅在直接运行时启用，作为模块 import 时不会触发
# ============================================================

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="查询系统上传用户：飞书 → manjike 一键流水线",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--skip-fetch", action="store_true",
                        help="跳过飞书拉取（适合上次拉过、本次只想重新上传）")
    parser.add_argument("--skip-upload", action="store_true",
                        help="跳过上传（只拉取飞书生成 JSON，不推到 manjike）")
    parser.add_argument("--fetch-args", type=str, default="",
                        help='传给 feishu-read-bitable.py 的额外参数，'
                             '用空格分隔，例如 --fetch-args="--page-size 200"')
    parser.add_argument("--upload-args", type=str, default="",
                        help='传给 upload-users.py 的额外参数，'
                             '例如 --upload-args="--dry-run"')
    return parser.parse_args()


def _split_extra(s: str) -> List[str]:
    """把 --fetch-args="--a 1 --b 2" 形式的字符串拆成 list。"""
    return [piece for piece in s.split() if piece]


def _cli_main() -> int:
    """CLI 入口逻辑，返回退出码（不直接调用 sys.exit，便于测试）。"""
    args = _parse_args()

    # Windows 控制台 GBK → UTF-8，中文不再乱码
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    return run_pipeline(
        skip_fetch=args.skip_fetch,
        skip_upload=args.skip_upload,
        fetch_args=_split_extra(args.fetch_args),
        upload_args=_split_extra(args.upload_args),
    )


if __name__ == "__main__":
    exit_code = 0
    try:
        exit_code = _cli_main()
    except KeyboardInterrupt:
        _log_line("[CANCELLED] 用户中断（Ctrl+C）")
        exit_code = 130
    except Exception as ex:
        import traceback
        traceback.print_exc()
        _log_line(f"[ERROR] 未处理异常：{ex}")
        exit_code = 1
    sys.exit(exit_code)
