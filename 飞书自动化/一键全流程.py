# -*- coding: utf-8 -*-
"""
合伙宝妈个人码 一键全流程编排器
==============================================================

把以下 4 个脚本按业务顺序串成一条流水线，单脚本不改动：

    步骤 1：飞书保存合伙宝妈个人码2.py
            飞书"个人码2"附件 → 下载到 桌面\\保存的二维码\\
            同时生成台账：桌面\\读取后的内容.txt

    步骤 2：批量执行动作_二维码.py
            对 桌面\\保存的二维码\\ 内所有图片做 OpenCV
            (灰度 → 高斯降噪 → OTSU 二值化 → 缩放) 原地覆盖。

    步骤 3：生成个人码3.py
            按 PSD 模板 + 读取后的内容.txt 合成"个人码3"，
            输出到 桌面\\二维码输出\\

    步骤 4：个人码回写\\main.py
            把图片上传回飞书多维表格的"个人码3"附件字段。
            (默认输入目录见 config.py，本仓库当前为 桌面\\保存的二维码\\)

设计原则：
    - 每一步用 importlib 按绝对文件路径加载，避免中文模块名的 import 问题
    - 每一步独立 try/except，单步失败默认"继续后续步骤"，避免链路阻塞
    - --strict 模式下遇错即停
    - --only / --skip 支持灵活跳过
    - 启动即修复 Windows 控制台 UTF-8（与"个人码回写"一致）

用法（PowerShell，在脚本所在目录执行）：

    cd D:\\桌面文件\\新建文件夹\\飞书自动化

    # 跑全流程
    python .\\一键全流程.py

    # 只跑某几步（例如只跑 1 和 4）
    python .\\一键全流程.py --only 1,4

    # 跳过某几步（例如跳过 PSD 合成）
    python .\\一键全流程.py --skip 3

    # 严格模式：单步失败立即停止
    python .\\一键全流程.py --strict
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Callable


# ────────────────────────── 0. 控制台 UTF-8 修复 ──────────────────────────
class _NullWriter:
    """空写入器：PyInstaller --windowed 模式下 sys.stdout/stderr 可能是 None，
    给所有 print 一个安全去处，避免 NoneType 报错把脚本搞崩。"""

    def write(self, _s: str) -> int:
        return 0

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def _setup_console() -> None:
    """
    Windows 默认控制台是 GBK，遇到中文 / emoji 容易抛 UnicodeEncodeError。
    这里把 stdout / stderr 强制改成 UTF-8 + errors='replace'。

    PyInstaller --windowed 时 sys.stdout / sys.stderr 可能是 None：
    无脑替换为 _NullWriter，让所有 print 安全无害。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            # windowed 模式下 stdout 为 None：替换为静默写入器
            setattr(sys, stream_name, _NullWriter())
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if os.name == "nt":
        try:
            # 仅在有真实控制台时才尝试 chcp（避免 windowed 模式下弹出 cmd 窗口）
            if sys.stdout and not isinstance(sys.stdout, _NullWriter):
                os.system("chcp 65001 > nul")
        except Exception:
            pass


_setup_console()


# ────────────────────────── 0.5 TLS 证书路径自修复 ──────────────────────────
def _setup_tls_ca_bundle() -> None:
    """
    统一修复 requests 读取的 CA 证书路径。

    背景：
    - 旧机器打包产物常把 REQUESTS_CA_BUNDLE / SSL_CERT_FILE 指向旧绝对路径；
    - 新机器路径不存在时，请求会报：
      OSError: Could not find a suitable TLS CA certificate bundle ...

    策略：
    1) 若用户已配置且路径有效：保留，不改；
    2) 若路径无效：清理该环境变量；
    3) 自动探测当前运行环境下可用的 cacert.pem，并回填到 3 个常见环境变量。
    """
    tls_env_keys = ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE")

    # 先处理环境变量：有效就保留，无效就清理
    valid_env_path: str | None = None
    for key in tls_env_keys:
        value = os.environ.get(key, "").strip()
        if not value:
            continue
        if Path(value).exists():
            valid_env_path = value
            break
        # 仅清理"失效路径"，避免 requests 继续使用旧机器目录
        os.environ.pop(key, None)
        print(f"[TLS] 检测到失效 {key}，已清理：{value}")

    # 已有有效路径时不覆盖，尊重用户/系统自定义证书配置
    if valid_env_path:
        return

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend([
            exe_dir / "_internal" / "certifi" / "cacert.pem",
            exe_dir / "certifi" / "cacert.pem",
        ])
    else:
        here = Path(__file__).resolve().parent
        candidates.extend([
            here / "_internal" / "certifi" / "cacert.pem",
            here / "certifi" / "cacert.pem",
        ])

    # 最后兜底：使用当前 Python 环境的 certifi
    try:
        import certifi  # type: ignore

        certifi_path = Path(certifi.where())
        candidates.append(certifi_path)
    except Exception:
        pass

    chosen: Path | None = None
    for path in candidates:
        if path.exists():
            chosen = path
            break

    if chosen is None:
        print("[TLS] 未找到可用 cacert.pem，继续使用系统默认证书链。")
        return

    chosen_str = str(chosen)
    for key in tls_env_keys:
        os.environ[key] = chosen_str
    print(f"[TLS] 已设置 CA 证书路径：{chosen_str}")


_setup_tls_ca_bundle()


# ────────────────────────── 1. 路径与步骤定义 ──────────────────────────
# 编排器自身所在目录（飞书自动化\）
# 适配 PyInstaller --onedir：被打包后 __file__ 指向运行时临时 _MEI 目录，
# 必须改用 sys.executable 的父目录定位用户实际部署目录。
if getattr(sys, "frozen", False):
    HERE = Path(sys.executable).resolve().parent
else:
    HERE = Path(__file__).resolve().parent

# 4 个步骤脚本统一存放在子目录 个人码相关\ 下
STEP_DIR = HERE / "个人码相关"

# 每一步：(序号, 显示名, 脚本绝对路径, 调用入口函数名)
STEPS: list[tuple[int, str, Path, str]] = [
    (1, "下载个人码2 + 生成台账 txt",
     STEP_DIR / "飞书保存合伙宝妈个人码2.py", "main"),
    (2, "OpenCV 批量预处理二维码",
     STEP_DIR / "批量执行动作_二维码.py",     "main"),
    (3, "PSD 模板合成个人码3",
     STEP_DIR / "生成个人码3.py",           "main"),
    (4, "上传个人码3 回写飞书",
     STEP_DIR / "个人码回写" / "main.py",    "main"),
]


# ────────────────────────── 1.5 清理目标定义 ──────────────────────────
# 全流程结束后需要清理的中间产物（按你确认的口径）：
#   保留：X:\backup\合伙宝妈个人码（成品备份，由 生成个人码3.py 同步备份）
#   删除：以下 3 个中间产物
CLEANUP_TARGETS: list[Path] = [
    Path(r"C:\Users\LENOVO\Desktop\保存的二维码"),   # 步骤1下载的原始二维码（dir）
    Path(r"C:\Users\LENOVO\Desktop\读取后的内容.txt"),  # 步骤1台账（file）
    Path(r"C:\Users\LENOVO\Desktop\二维码输出"),     # 步骤3合成结果（dir，回写后即可删）
]
# 必须存在且非空才允许清理（防误删；备份不在 → 拒绝清理）
BACKUP_REQUIRED: Path = Path(r"X:\backup\合伙宝妈个人码")


# ────────────────────────── 2. 动态加载脚本 ──────────────────────────
def _load_module(step_no: int, script_path: Path):
    """
    用 importlib 按绝对路径加载模块。

    选择 importlib 而不是普通 import 的原因：
        - 4 个脚本文件名含中文（"飞书保存合伙宝妈个人码2.py" 等），
          普通 import 需要折腾 sys.path + 中文模块名，跨终端容易踩坑。
        - 用 spec_from_file_location 直接按路径加载最稳。

    "个人码回写\\main.py" 内部用 `import config / file_parser / feishu_api` 之类的
    相对兄弟模块，因此这里把它的父目录临时加到 sys.path 头部，加载完再恢复。
    """
    if not script_path.exists():
        raise FileNotFoundError(f"找不到脚本：{script_path}")

    parent = str(script_path.parent)
    sys_path_added = False
    if parent not in sys.path:
        sys.path.insert(0, parent)
        sys_path_added = True

    try:
        # 模块名只用于 sys.modules 缓存，不影响外部 import
        mod_name = f"_pipeline_step_{step_no}"
        spec = importlib.util.spec_from_file_location(mod_name, str(script_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"无法构造 spec：{script_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        # 只回收我们这次加的，避免污染 sys.path（"个人码回写"内部的兄弟模块
        # 已经在 exec_module 阶段完成 import，不需要保留）
        if sys_path_added:
            try:
                sys.path.remove(parent)
            except ValueError:
                pass


# ────────────────────────── 3. 单步执行 ──────────────────────────
def _run_step(
    step_no: int,
    title: str,
    script_path: Path,
    entry: str,
    *,
    strict: bool,
) -> tuple[bool, float, str]:
    """
    执行一步，返回 (是否成功, 耗时秒, 错误简述)。

    strict=True：失败立即抛异常，由上层中止全流程。
    strict=False：捕获异常并返回失败状态，让后续步骤继续跑。
    """
    print()
    print("█" * 70)
    print(f"█  步骤 {step_no} / {len(STEPS)}：{title}")
    print(f"█  脚本：{script_path}")
    print("█" * 70)

    started = time.time()
    try:
        mod = _load_module(step_no, script_path)
        func: Callable[[], None] | None = getattr(mod, entry, None)
        if func is None:
            raise AttributeError(f"脚本 {script_path.name} 没有 {entry}() 入口")
        func()
        elapsed = time.time() - started
        print(f"\n[步骤 {step_no}] ✅ 完成，耗时 {elapsed:.2f}s")
        return True, elapsed, ""
    except SystemExit as e:
        # 子脚本里 raise SystemExit(...) 应该被视为"主动退出"
        elapsed = time.time() - started
        msg = f"SystemExit: {e.code}"
        print(f"\n[步骤 {step_no}] ⚠️ 子脚本主动退出：{msg}")
        if strict:
            raise
        return False, elapsed, msg
    except Exception as e:
        elapsed = time.time() - started
        err_msg = f"{type(e).__name__}: {e}"
        print(f"\n[步骤 {step_no}] ❌ 失败：{err_msg}")
        traceback.print_exc()
        if strict:
            raise
        return False, elapsed, err_msg


# ────────────────────────── 3.5 清理中间产物 ──────────────────────────
def _is_backup_ready() -> tuple[bool, str]:
    """
    判断备份目录是否可信（必须存在 + 至少有 1 个文件）。
    备份不可信时**禁止清理**，保护中间产物。
    """
    if not BACKUP_REQUIRED.exists():
        return False, f"备份目录不存在：{BACKUP_REQUIRED}"
    if not BACKUP_REQUIRED.is_dir():
        return False, f"备份路径不是目录：{BACKUP_REQUIRED}"
    # 至少有 1 个文件才认为备份可信
    try:
        for _ in BACKUP_REQUIRED.iterdir():
            return True, ""
    except Exception as e:
        return False, f"备份目录访问失败：{type(e).__name__}: {e}"
    return False, f"备份目录为空：{BACKUP_REQUIRED}"


def _delete_path(target: Path) -> tuple[bool, str]:
    """删除单个文件或目录；不存在视为已删除"""
    import shutil

    try:
        if not target.exists():
            return True, "(原本就不存在)"
        if target.is_file() or target.is_symlink():
            target.unlink()
            return True, "已删除文件"
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=False)
            return True, "已删除目录"
        return False, f"未知文件类型：{target}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _cleanup(*, all_steps_ok: bool, force: bool) -> None:
    """
    清理中间产物。
    - all_steps_ok=False 且 force=False → 跳过清理（防止失败时误删）
    - 备份不可信  → 跳过清理
    - 否则按 CLEANUP_TARGETS 逐项删除
    """
    print()
    print("█" * 70)
    print("█  清理中间产物")
    print("█" * 70)

    # 安全闸 1：步骤失败 + 非强制 → 不清理
    if not all_steps_ok and not force:
        print("⏭️  前置步骤存在失败，已跳过清理（如需强制清理请加 --force-clean）")
        return

    # 安全闸 2：备份不可信 → 不清理
    backup_ok, backup_msg = _is_backup_ready()
    if not backup_ok:
        print(f"⏭️  备份不可信，已跳过清理：{backup_msg}")
        print(f"    （成品备份目录 = {BACKUP_REQUIRED}）")
        return
    print(f"✅ 备份可信：{BACKUP_REQUIRED}")

    # 真正执行清理
    for target in CLEANUP_TARGETS:
        ok, msg = _delete_path(target)
        flag = "✅" if ok else "❌"
        print(f"  {flag}  {target}  ->  {msg}")


# ────────────────────────── 4. 入口 ──────────────────────────
def _parse_step_list(raw: str | None) -> set[int]:
    """把 '1,3,4' 解析成 {1,3,4}；空值返回空集合"""
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise SystemExit(f"--only/--skip 参数非法：{part!r}（必须是数字）")
        out.add(int(part))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="合伙宝妈个人码一键全流程",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--only",
        help="只运行指定步骤，逗号分隔，例如 --only 1,4",
    )
    parser.add_argument(
        "--skip",
        help="跳过指定步骤，逗号分隔，例如 --skip 3",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：单步失败立即终止，默认是失败后继续后续步骤",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="跳过末尾的清理中间产物步骤",
    )
    parser.add_argument(
        "--force-clean",
        action="store_true",
        help="即使前置步骤失败也强制清理（仍要求备份目录可信）",
    )
    args = parser.parse_args()

    only_set = _parse_step_list(args.only)
    skip_set = _parse_step_list(args.skip)

    # ── 自检：脚本文件存在性 ───────────────────────────────
    print("=" * 70)
    print("【一键全流程】运行环境")
    print(f"  Python         : {sys.version.split()[0]}")
    print(f"  运行入口       : {sys.executable if getattr(sys, 'frozen', False) else __file__}")
    print(f"  解析后的根目录 : {HERE}")
    print("=" * 70)
    print("【一键全流程】路径自检")
    for no, title, path, _ in STEPS:
        flag = "✅" if path.exists() else "❌"
        print(f"  {flag}  步骤 {no}  {title:<30}  {path}")
    missing = [str(p) for _, _, p, _ in STEPS if not p.exists()]
    if missing:
        raise SystemExit(f"以下脚本不存在，无法继续：\n  - " + "\n  - ".join(missing))

    print("=" * 70)
    print(f"模式: {'严格' if args.strict else '容错（单步失败继续）'}")
    if only_set:
        print(f"仅运行步骤: {sorted(only_set)}")
    if skip_set:
        print(f"跳过步骤  : {sorted(skip_set)}")
    print("=" * 70)

    # ── 逐步执行并收集结果 ────────────────────────────────
    results: list[tuple[int, str, str, float, str]] = []  # (no, title, status, elapsed, err)
    overall_started = time.time()

    for no, title, path, entry in STEPS:
        if only_set and no not in only_set:
            print(f"\n[步骤 {no}] ⏭️  按 --only 跳过：{title}")
            results.append((no, title, "skipped", 0.0, ""))
            continue
        if no in skip_set:
            print(f"\n[步骤 {no}] ⏭️  按 --skip 跳过：{title}")
            results.append((no, title, "skipped", 0.0, ""))
            continue

        ok, elapsed, err = _run_step(no, title, path, entry, strict=args.strict)
        results.append((no, title, "ok" if ok else "failed", elapsed, err))

    # ── 总结 ────────────────────────────────────────────
    total_elapsed = time.time() - overall_started

    print()
    print("=" * 70)
    print("【一键全流程】执行总结")
    print("-" * 70)
    print(f"{'步骤':<6}{'状态':<8}{'耗时':<10}{'说明':<30}")
    print("-" * 70)
    icon = {"ok": "✅", "failed": "❌", "skipped": "⏭️"}
    for no, title, status, elapsed, err in results:
        suffix = f"  -> {err}" if err else ""
        print(
            f"{no:<6}{icon.get(status,'?')+' '+status:<8}"
            f"{elapsed:>6.2f}s   {title}{suffix}"
        )
    ok_cnt   = sum(1 for r in results if r[2] == "ok")
    fail_cnt = sum(1 for r in results if r[2] == "failed")
    skip_cnt = sum(1 for r in results if r[2] == "skipped")
    print("-" * 70)
    print(
        f"成功 {ok_cnt} / 失败 {fail_cnt} / 跳过 {skip_cnt}，"
        f"全程耗时 {total_elapsed:.2f}s"
    )
    print("=" * 70)

    # ── 清理中间产物 ───────────────────────────────────────
    if args.no_clean:
        print("\n⏭️  按 --no-clean 跳过清理")
    else:
        # 只有"实际跑了的步骤都成功"才认为可清理；纯 skipped 不影响判断
        executed_results = [r for r in results if r[2] != "skipped"]
        all_ok = bool(executed_results) and all(r[2] == "ok" for r in executed_results)
        _cleanup(all_steps_ok=all_ok, force=args.force_clean)

    # 失败时退出码 = 1，方便外层 CI / 计划任务感知
    if fail_cnt > 0:
        sys.exit(1)


# ────────────────────────── 5. 可视化界面（Tkinter） ──────────────────────────
def launch_gui() -> None:
    """
    启动 Tkinter 可视化界面。

    特性：
      - 4 个步骤复选框（默认全选）+ strict / no-clean / force-clean
      - 实时显示每步状态：⏳进行中 / ✅成功 / ⏭️跳过 / ❌失败
      - 工作线程跑业务，把 stdout/stderr/logger 实时泵到日志区
      - 可选定时：间隔分钟 + 首次触发时间(HH:MM)；单次防重入
      - 关闭窗口前自动取消挂起的 after 任务
    """
    import io
    import queue
    import threading
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox
    from datetime import datetime, timedelta

    # ── 子工具 ───────────────────────────────────────────────
    def _parse_hhmm(raw: str):
        s = (raw or "").strip()
        if not s:
            return None
        if ":" not in s:
            raise ValueError("缺少冒号")
        hh, mm = s.split(":", 1)
        h, m = int(hh.strip()), int(mm.strip())
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError("时刻越界（小时 0~23 / 分钟 0~59）")
        return h, m

    def _compute_first_delay_sec(hm, interval_min: int) -> int:
        if hm is None:
            return max(1, interval_min * 60)
        h, m = hm
        now = datetime.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        return max(1, int((target - now).total_seconds()))

    log_queue: "queue.Queue[str]" = queue.Queue()

    class _QueueWriter(io.TextIOBase):
        """把工作线程的 stdout/stderr 实时推到队列；保证 GUI 主线程消费。"""

        def __init__(self) -> None:
            self._buf = ""

        def write(self, s: str) -> int:
            if not s:
                return 0
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                log_queue.put(line)
            return len(s)

        def flush(self) -> None:
            if self._buf:
                log_queue.put(self._buf)
                self._buf = ""

    # ── 窗口 ─────────────────────────────────────────────────
    root = tk.Tk()
    root.title("一键全流程")
    try:
        root.tk.call("tk", "scaling", 1.25)
    except Exception:
        pass

    # 日志字体：优先用中文细体（Light 字重），让汉字更细；找不到再回落英文等宽体。
    # 等距属性 < 视觉细度（用户更在意中文不粗）。
    # tkfont.families() 在 Windows 同时返回中文名与英文名，全列以最大化命中率。
    import tkinter.font as tkfont
    _avail_families = set(tkfont.families())
    _log_family = next(
        (f for f in (
            "Microsoft YaHei UI Light",
            "Microsoft YaHei Light",
            "微软雅黑 Light",
            "等线 Light",
            "DengXian Light",
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "等线",
            "DengXian",
            "Cascadia Mono",
            "Consolas",
            "Courier New",
        ) if f in _avail_families),
        "TkDefaultFont",
    )
    log_font = (_log_family, 9, "normal")

    main_frame = ttk.Frame(root, padding=(12, 10))
    main_frame.pack(fill=tk.BOTH, expand=True)
    main_frame.columnconfigure(0, weight=1)
    main_frame.rowconfigure(99, weight=1)

    # 标题 + 路径
    ttk.Label(
        main_frame,
        text="合伙宝妈个人码 一键全流程（飞书 → 本地处理 → 回写飞书）",
        font=("Microsoft YaHei", 11, "bold"),
    ).grid(row=0, column=0, sticky="w")

    ttk.Label(
        main_frame,
        text=f"步骤目录：{STEP_DIR}",
        foreground="#7f8c8d",
    ).grid(row=1, column=0, sticky="w", pady=(2, 8))

    # 步骤选择
    step_box = ttk.LabelFrame(main_frame, text="步骤", padding=(8, 6))
    step_box.grid(row=2, column=0, sticky="we")
    step_box.columnconfigure(2, weight=1)

    step_vars: dict[int, tk.BooleanVar] = {}
    step_status_vars: dict[int, tk.StringVar] = {}
    for i, (no, title, path, _entry) in enumerate(STEPS):
        v = tk.BooleanVar(value=True)
        step_vars[no] = v
        ttk.Checkbutton(step_box, text=f"步骤 {no}：{title}", variable=v).grid(
            row=i, column=0, sticky="w", pady=2
        )
        sv = tk.StringVar(value="待执行")
        step_status_vars[no] = sv
        ttk.Label(step_box, textvariable=sv, width=12).grid(
            row=i, column=1, sticky="w", padx=(8, 0)
        )
        ttk.Label(step_box, text=str(path), foreground="#7f8c8d").grid(
            row=i, column=2, sticky="w", padx=(8, 0)
        )

    # 总进度（在工作线程跑流水线时实时推进）
    progress_box = ttk.LabelFrame(main_frame, text="总进度", padding=(8, 6))
    progress_box.grid(row=3, column=0, sticky="we", pady=(8, 0))
    progress_box.columnconfigure(0, weight=1)

    progress_var = tk.DoubleVar(value=0.0)
    progress_text_var = tk.StringVar(value="尚未开始")
    progress_step_var = tk.StringVar(value="当前：—")
    progress_elapsed_var = tk.StringVar(value="步骤耗时：0.0s   累计：0.0s")

    pb = ttk.Progressbar(
        progress_box,
        orient="horizontal",
        mode="determinate",
        maximum=100,
        variable=progress_var,
    )
    pb.grid(row=0, column=0, sticky="we", pady=(0, 4))
    ttk.Label(
        progress_box, textvariable=progress_text_var,
        font=("Microsoft YaHei", 10, "bold"),
    ).grid(row=1, column=0, sticky="w")
    ttk.Label(progress_box, textvariable=progress_step_var, foreground="#1f7a1f").grid(
        row=2, column=0, sticky="w"
    )
    ttk.Label(progress_box, textvariable=progress_elapsed_var, foreground="#7f8c8d").grid(
        row=3, column=0, sticky="w"
    )

    # 选项
    opt_box = ttk.LabelFrame(main_frame, text="选项", padding=(8, 6))
    opt_box.grid(row=4, column=0, sticky="we", pady=(8, 0))
    strict_var = tk.BooleanVar(value=False)
    no_clean_var = tk.BooleanVar(value=False)
    force_clean_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        opt_box, text="严格模式（单步失败立即终止）", variable=strict_var
    ).grid(row=0, column=0, sticky="w", padx=(0, 16))
    ttk.Checkbutton(
        opt_box, text="跳过末尾清理中间产物", variable=no_clean_var
    ).grid(row=0, column=1, sticky="w", padx=(0, 16))
    ttk.Checkbutton(
        opt_box, text="即使前置失败也强制清理", variable=force_clean_var
    ).grid(row=0, column=2, sticky="w")

    # 定时（可选）
    sched_box = ttk.LabelFrame(main_frame, text="定时（可选）", padding=(8, 6))
    sched_box.grid(row=5, column=0, sticky="we", pady=(8, 0))
    sched_box.columnconfigure(5, weight=1)

    ttk.Label(sched_box, text="间隔（分钟）:").grid(row=0, column=0, sticky="w")
    interval_var = tk.StringVar(value="1440")  # 默认每天一次
    tk.Spinbox(
        sched_box, from_=1, to=10080, textvariable=interval_var, width=6
    ).grid(row=0, column=1, padx=(6, 16))

    ttk.Label(sched_box, text="首次触发时间:").grid(row=0, column=2, sticky="w")
    start_time_var = tk.StringVar(value="")
    ttk.Entry(sched_box, textvariable=start_time_var, width=8).grid(
        row=0, column=3, padx=(6, 16)
    )
    ttk.Label(
        sched_box,
        text="（HH:MM，留空=按间隔；如 14:00 = 等到今天14点；过点推迟到次日）",
        foreground="#7f8c8d",
    ).grid(row=0, column=4, columnspan=2, sticky="w")

    # 状态行
    status_var = tk.StringVar(value="状态：空闲")
    next_var = tk.StringVar(value="下次：未启动")
    last_var = tk.StringVar(value="上次：—")
    summary_var = tk.StringVar(value="尚未运行")
    status_row = ttk.Frame(main_frame)
    status_row.grid(row=6, column=0, sticky="we", pady=(8, 0))
    ttk.Label(status_row, textvariable=status_var, font=("Microsoft YaHei", 10, "bold")).pack(
        side=tk.LEFT, padx=(0, 16)
    )
    ttk.Label(status_row, textvariable=next_var).pack(side=tk.LEFT, padx=(0, 16))
    ttk.Label(status_row, textvariable=last_var).pack(side=tk.LEFT, padx=(0, 16))
    ttk.Label(status_row, textvariable=summary_var).pack(side=tk.LEFT)

    # 按钮
    btn_row = ttk.Frame(main_frame)
    btn_row.grid(row=7, column=0, sticky="w", pady=(10, 0))
    btn_run = ttk.Button(btn_row, text="立即执行")
    btn_start_sched = ttk.Button(btn_row, text="启动定时")
    btn_stop_sched = ttk.Button(btn_row, text="停止定时")
    btn_clear = ttk.Button(btn_row, text="清空日志")
    for b in (btn_run, btn_start_sched, btn_stop_sched, btn_clear):
        b.pack(side=tk.LEFT, padx=(0, 8))

    # 日志区
    ttk.Label(main_frame, text="运行日志").grid(
        row=98, column=0, sticky="w", pady=(12, 2)
    )
    log_text = scrolledtext.ScrolledText(
        main_frame, wrap=tk.NONE, state="disabled",
        font=log_font, height=18,
    )
    log_text.grid(row=99, column=0, sticky="nsew")

    def append_log(line: str) -> None:
        log_text.configure(state="normal")
        log_text.insert(tk.END, line + "\n")
        log_text.see(tk.END)
        log_text.configure(state="disabled")

    def clear_log() -> None:
        log_text.configure(state="normal")
        log_text.delete("1.0", tk.END)
        log_text.configure(state="disabled")

    btn_clear.configure(command=clear_log)

    # ── 状态机 ───────────────────────────────────────────────
    busy_lock = threading.Lock()
    progress_state = {
        "pipeline_started_ts": 0.0,   # 本次流水线开始时间
        "step_started_ts":     0.0,   # 当前步骤开始时间
        "current_step_no":     0,     # 0 表示无步骤在运行
        "current_step_title":  "",
        "done_count":          0,
        "total_count":         0,
    }
    sched_state = {
        "enabled": False,
        "after_id": None,
        "deadline_ts": 0.0,
        "first_fire_done": False,
        "run_count": 0,
        "ok_count": 0,
        "fail_count": 0,
        "last_run_ts": 0.0,
        "last_result": None,
    }

    def _set_step_status(no: int, text: str) -> None:
        try:
            step_status_vars[no].set(text)
        except Exception:
            pass

    def _reset_step_status() -> None:
        for no in step_vars:
            _set_step_status(no, "待执行")

    def pump_logs() -> None:
        try:
            while True:
                append_log(log_queue.get_nowait())
        except queue.Empty:
            pass
        # 状态行刷新
        if busy_lock.locked():
            status_var.set("状态：执行中")
        elif sched_state["enabled"]:
            status_var.set("状态：定时中")
        else:
            status_var.set("状态：空闲")

        if sched_state["after_id"] is not None:
            remain = max(0, int(sched_state["deadline_ts"] - time.time()))
            if remain >= 3600:
                ts = datetime.fromtimestamp(sched_state["deadline_ts"]).strftime("%m-%d %H:%M")
                hh = remain // 3600
                next_var.set(f"下次：{ts}（{hh} 小时后）")
            else:
                mm, ss = divmod(remain, 60)
                next_var.set(f"下次：{mm:02d}:{ss:02d} 后")
        else:
            next_var.set("下次：未启动")

        if sched_state["last_run_ts"] > 0:
            ts = datetime.fromtimestamp(sched_state["last_run_ts"]).strftime("%H:%M:%S")
            tag = "成功" if sched_state["last_result"] else "异常"
            last_var.set(f"上次：{ts} ({tag})")

        summary_var.set(
            f"运行 {sched_state['run_count']} / "
            f"成功 {sched_state['ok_count']} / "
            f"失败 {sched_state['fail_count']}"
        )

        # 进度区耗时刷新（每帧基于 progress_state 计算，避免跨线程改 StringVar）
        if progress_state["pipeline_started_ts"] > 0 and busy_lock.locked():
            now = time.time()
            total_el = now - progress_state["pipeline_started_ts"]
            step_el = (
                now - progress_state["step_started_ts"]
                if progress_state["step_started_ts"] > 0
                else 0.0
            )
            progress_elapsed_var.set(
                f"步骤耗时：{step_el:.1f}s   累计：{total_el:.1f}s"
            )

        root.after(400, pump_logs)

    # ── 业务执行（工作线程） ────────────────────────────────
    def _run_pipeline_in_thread(silent: bool) -> None:
        """
        在后台线程跑流水线。silent=True 用于定时静默触发。

        关键点：把 sys.stdout / sys.stderr 替换成 _QueueWriter，让 4 个步骤
        内部所有 print 都同步到 GUI 日志区。结束时恢复。
        """
        only_set: set[int] = {no for no, v in step_vars.items() if v.get()}
        strict   = bool(strict_var.get())
        no_clean = bool(no_clean_var.get())
        force_cl = bool(force_clean_var.get())

        if not only_set:
            log_queue.put("⚠️  没有勾选任何步骤，已取消执行。")
            return

        old_out, old_err = sys.stdout, sys.stderr
        writer = _QueueWriter()
        sys.stdout = writer
        sys.stderr = writer

        sched_state["run_count"] += 1
        ok_overall = True
        results: list[tuple[int, str, str, float, str]] = []

        # 初始化进度区
        progress_state["pipeline_started_ts"] = time.time()
        progress_state["step_started_ts"] = 0.0
        progress_state["current_step_no"] = 0
        progress_state["current_step_title"] = ""
        progress_state["done_count"] = 0
        progress_state["total_count"] = len(only_set)
        progress_var.set(0.0)
        progress_text_var.set(f"进度 0 / {len(only_set)}（0%）")
        progress_step_var.set("当前：准备中…")
        progress_elapsed_var.set("步骤耗时：0.0s   累计：0.0s")

        def _advance_progress(done: int, total: int) -> None:
            pct = (done / total * 100) if total > 0 else 0.0
            progress_var.set(pct)
            progress_text_var.set(f"进度 {done} / {total}（{pct:.0f}%）")

        try:
            print("=" * 70)
            print(f"【一键全流程】开始执行 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
            print(f"  勾选步骤: {sorted(only_set)}")
            print(f"  严格={strict}  no-clean={no_clean}  force-clean={force_cl}")
            print("=" * 70)

            for no, title, path, entry in STEPS:
                if no not in only_set:
                    _set_step_status(no, "⏭️ 跳过")
                    print(f"\n[步骤 {no}] ⏭️  未勾选，跳过：{title}")
                    results.append((no, title, "skipped", 0.0, ""))
                    continue

                # 推进"当前步骤"显示
                progress_state["current_step_no"] = no
                progress_state["current_step_title"] = title
                progress_state["step_started_ts"] = time.time()
                progress_step_var.set(f"当前：步骤 {no} - {title}")

                _set_step_status(no, "⏳ 进行中")
                ok, elapsed, err = _run_step(no, title, path, entry, strict=strict)
                results.append((no, title, "ok" if ok else "failed", elapsed, err))
                if ok:
                    _set_step_status(no, "✅ 成功")
                else:
                    _set_step_status(no, "❌ 失败")
                    ok_overall = False

                # 不论成败，本步骤都"占用"了一个完成名额
                progress_state["done_count"] += 1
                _advance_progress(
                    progress_state["done_count"], progress_state["total_count"]
                )

                if not ok and strict:
                    print(f"[严格模式] 步骤 {no} 失败，终止后续步骤。")
                    # 尚未运行的步骤标记为"取消"
                    for nx, ttl, _, _ in STEPS:
                        if nx > no and nx in only_set:
                            _set_step_status(nx, "⏭️ 已取消")
                            results.append((nx, ttl, "skipped", 0.0, "前置失败终止"))
                    break

            # 总结表
            ok_cnt   = sum(1 for r in results if r[2] == "ok")
            fail_cnt = sum(1 for r in results if r[2] == "failed")
            skip_cnt = sum(1 for r in results if r[2] == "skipped")

            print()
            print("=" * 70)
            print("【一键全流程】执行总结")
            print("-" * 70)
            for no, title, status, elapsed, err in results:
                icon = {"ok": "✅", "failed": "❌", "skipped": "⏭️"}.get(status, "?")
                suffix = f"  -> {err}" if err else ""
                print(f"  步骤 {no}  {icon} {status:<8}  {elapsed:>6.2f}s   {title}{suffix}")
            print("-" * 70)
            print(f"成功 {ok_cnt} / 失败 {fail_cnt} / 跳过 {skip_cnt}")
            print("=" * 70)

            # 清理
            if no_clean:
                print("\n⏭️  按选项跳过末尾清理")
            else:
                executed = [r for r in results if r[2] != "skipped"]
                all_ok = bool(executed) and all(r[2] == "ok" for r in executed)
                _cleanup(all_steps_ok=all_ok, force=force_cl)

            sched_state["last_result"] = ok_overall and (fail_cnt == 0)
            if sched_state["last_result"]:
                sched_state["ok_count"] += 1
            else:
                sched_state["fail_count"] += 1
        except Exception as e:
            print(f"\n❌ 流水线异常：{type(e).__name__}: {e}")
            sched_state["last_result"] = False
            sched_state["fail_count"] += 1
        finally:
            sched_state["last_run_ts"] = time.time()
            sys.stdout, sys.stderr = old_out, old_err
            try:
                writer.flush()
            except Exception:
                pass
            # 定格进度区
            ok_cnt   = sum(1 for r in results if r[2] == "ok")
            fail_cnt = sum(1 for r in results if r[2] == "failed")
            skip_cnt = sum(1 for r in results if r[2] == "skipped")
            total = progress_state["total_count"] or 1
            pct = (progress_state["done_count"] / total * 100) if total else 0.0
            progress_var.set(pct)
            progress_text_var.set(
                f"已完成 {progress_state['done_count']} / {progress_state['total_count']}"
                f"（{pct:.0f}%）"
                f" — 成功 {ok_cnt} / 失败 {fail_cnt} / 跳过 {skip_cnt}"
            )
            progress_step_var.set("当前：—（流水线已结束）")
            total_el = time.time() - progress_state["pipeline_started_ts"]
            progress_elapsed_var.set(
                f"步骤耗时：—   累计：{total_el:.1f}s（最终）"
            )
            progress_state["pipeline_started_ts"] = 0.0
            progress_state["step_started_ts"] = 0.0
            progress_state["current_step_no"] = 0
            progress_state["current_step_title"] = ""
            busy_lock.release()

    def _start_pipeline(silent: bool = False) -> None:
        if not busy_lock.acquire(blocking=False):
            msg = "上一次执行尚未结束，已忽略本次触发。"
            if silent:
                log_queue.put(f"[定时] {msg}")
            else:
                messagebox.showinfo("提示", msg)
            return
        _reset_step_status()
        threading.Thread(
            target=_run_pipeline_in_thread, args=(silent,), daemon=True,
            name="pipeline-worker",
        ).start()

    # ── 定时调度 ─────────────────────────────────────────────
    def _cancel_timer() -> None:
        aid = sched_state.get("after_id")
        if aid is not None:
            try:
                root.after_cancel(aid)
            except Exception:
                pass
        sched_state["after_id"] = None
        sched_state["deadline_ts"] = 0.0

    def _schedule_next_run() -> None:
        _cancel_timer()
        if not sched_state["enabled"]:
            return
        try:
            interval_min = max(1, int((interval_var.get() or "1440").strip()))
        except ValueError:
            interval_min = 1440
        try:
            hm = _parse_hhmm(start_time_var.get())
        except Exception as e:
            messagebox.showwarning("首次触发时间无效", str(e))
            sched_state["enabled"] = False
            return

        if not sched_state["first_fire_done"]:
            delay = _compute_first_delay_sec(hm, interval_min)
            sched_state["deadline_ts"] = time.time() + delay
            sched_state["after_id"] = root.after(delay * 1000, _fire_timer)
            if hm is not None:
                ts = datetime.fromtimestamp(sched_state["deadline_ts"]).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                log_queue.put(f"[定时] 首次触发时刻：{ts}（{delay // 60} 分钟后）")
            else:
                log_queue.put(f"[定时] 已安排下次执行：{interval_min} 分钟后")
        else:
            delay = interval_min * 60
            sched_state["deadline_ts"] = time.time() + delay
            sched_state["after_id"] = root.after(delay * 1000, _fire_timer)
            log_queue.put(f"[定时] 已安排下次执行：{interval_min} 分钟后")

    def _fire_timer() -> None:
        sched_state["after_id"] = None
        sched_state["deadline_ts"] = 0.0
        if not sched_state["enabled"]:
            return
        sched_state["first_fire_done"] = True
        log_queue.put("[定时] 时间到，开始执行。")
        _start_pipeline(silent=True)
        _schedule_next_run()

    def do_run() -> None:
        _start_pipeline(silent=False)

    def do_start_sched() -> None:
        sched_state["enabled"] = True
        sched_state["first_fire_done"] = False
        _schedule_next_run()
        log_queue.put("[定时] 已启动。")

    def do_stop_sched() -> None:
        sched_state["enabled"] = False
        _cancel_timer()
        log_queue.put("[定时] 已停止。")

    btn_run.configure(command=do_run)
    btn_start_sched.configure(command=do_start_sched)
    btn_stop_sched.configure(command=do_stop_sched)

    # 关闭：取消挂起 timer 防泄漏
    def on_close() -> None:
        _cancel_timer()
        root.after(100, root.destroy)

    root.protocol("WM_DELETE_WINDOW", on_close)

    # 启动路径自检日志（让用户一目了然）
    log_queue.put("=" * 70)
    log_queue.put("【一键全流程】路径自检")
    for no, title, path, _ in STEPS:
        flag = "✅" if path.exists() else "❌"
        log_queue.put(f"  {flag}  步骤 {no}  {title:<30}  {path}")
    if not all(p.exists() for _, _, p, _ in STEPS):
        log_queue.put("⚠️  存在缺失脚本，部分步骤将失败。")
    log_queue.put("=" * 70)

    root.geometry("1000x700")
    root.after(200, pump_logs)
    root.mainloop()


if __name__ == "__main__":
    # 入口分发：
    #   1) 没有任何 CLI 参数              → 启动 GUI
    #   2) 显式 --cli                     → 走 CLI 默认全跑（去掉 --cli 后再 main）
    #   3) 任何其他 --xxx                 → 走原 CLI（保持向后兼容）
    if len(sys.argv) == 1:
        launch_gui()
    elif "--cli" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--cli"]
        main()
    else:
        main()
