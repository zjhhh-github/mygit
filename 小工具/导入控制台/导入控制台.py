# -*- coding: utf-8 -*-
"""
导入控制台 —— 集成两条流水线的统一 GUI：
  1) 增量导入（来自 增量导入.py，Excel → Supabase incremental-import）
  2) 内部备注导入（来自 export_contact.py，contact.db → internal_notes upsert）

依赖：requests（已被 增量导入.py 依赖），无新增。
运行：python 导入控制台.py
"""

import json
import logging
import math
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import requests

# 全局任务队列 + 自定义插件支持（拓展功能 Tab 用）
# 这两个模块都不依赖控制台内部对象，可以单独测试 / 复用
from task_runner import TaskRunner
import custom_plugins as cp_mod


# ─────────────────────────── 全局任务队列 ───────────────────────────
# 进程内单例：所有"想排队执行"的入口共用这一条队列，单 worker 顺序消费
# Tab2 / Tab4 / Tab5 / 扩展功能 Tab 都可以通过 GLOBAL_RUNNER.submit 入队
GLOBAL_RUNNER = TaskRunner()


# ─────────────────────────── 扩展功能配置 ───────────────────────────
# 与 exe / 控制台脚本同目录，方便用户手工编辑或 GUI 内增删
CUSTOM_PLUGINS_CONFIG_BASE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
CUSTOM_PLUGINS_CONFIG_PATH = CUSTOM_PLUGINS_CONFIG_BASE / "custom_plugins.json"


# ─────────────────────────── contact.db 来源查找 ───────────────────────────
_CONTACT_NET_BASE = r"X:\chatlog_backup"
# 默认前缀与当前运行前缀分离：
# - _CONTACT_FOLDER_PREFIX_DEFAULT：内置兜底值，不会被运行时修改
# - _CONTACT_FOLDER_PREFIX：当前生效值，可由 GUI 配置修改并持久化
_CONTACT_FOLDER_PREFIX_DEFAULT = "wxid_42272spv9uq522_6ded"
_CONTACT_FOLDER_PREFIX = _CONTACT_FOLDER_PREFIX_DEFAULT
_CONTACT_LOCAL_DST = str(Path.home() / "Desktop" / "contact.db")


def _split_multi_values(raw: str) -> list[str]:
    """
    将用户输入的“多值文本”解析为列表，支持 ;、,、中文符号和换行分隔。
    返回值会做 strip、去空、并按输入顺序去重。
    """
    text = str(raw or "").strip()
    if not text:
        return []
    parts = re.split(r"[;；,\n，]+", text)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in parts:
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _get_file_stat_info(file_path: str) -> dict:
    """读取文件基础信息（大小、修改时间、创建时间），用于稳定性判断与复制后校验。"""
    st = os.stat(file_path)
    return {
        "size": st.st_size,
        "mtime": st.st_mtime,
        "ctime": st.st_ctime,
    }


def _wait_file_stable(file_path: str, interval_sec: float = 2.0, max_retries: int = 5) -> dict:
    """
    等待源文件稳定：
    - 连续两次检查 size + mtime 一致，认为文件写入完成
    - 超过重试次数仍不稳定则抛异常，避免拷到半写入文件
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"源文件不存在：{file_path}")

    retries = max(1, int(max_retries))
    wait_s = max(0.1, float(interval_sec))
    for _ in range(retries):
        first = _get_file_stat_info(file_path)
        time.sleep(wait_s)
        second = _get_file_stat_info(file_path)
        if first["size"] == second["size"] and first["mtime"] == second["mtime"]:
            return second
    raise RuntimeError(f"源文件长时间不稳定，可能仍在写入：{file_path}")


def _copy_file_with_verify(src_file: str, dst_file: str) -> None:
    """
    按“稳定等待 → 复制 → 校验”执行单文件拷贝。
    该逻辑与用户提供脚本保持一致：重点保障正在写入时不误拷、拷后大小一致。
    """
    if not os.path.isfile(src_file):
        raise FileNotFoundError(f"源文件不存在：{src_file}")

    dst_parent = os.path.dirname(dst_file) or "."
    if not os.path.isdir(dst_parent):
        os.makedirs(dst_parent, exist_ok=True)

    src_info = _wait_file_stable(src_file)
    shutil.copyfile(src_file, dst_file)

    if not os.path.exists(dst_file):
        raise RuntimeError(f"拷贝失败，目标文件不存在：{dst_file}")
    dst_info = _get_file_stat_info(dst_file)
    if src_info["size"] != dst_info["size"]:
        raise RuntimeError(
            f"拷贝后文件大小不一致：源文件={src_info['size']}字节，目标文件={dst_info['size']}字节"
        )


def _find_contact_db_source(net_base: str = _CONTACT_NET_BASE,
                            prefix: str = "") -> str:
    """
    在 net_base 目录下查找以 prefix（支持多个）开头、日期时间后缀最大的文件夹，
    返回其中 db_storage/contact/contact.db 的完整路径；未找到时返回空串。

    支持两种文件夹命名格式：
        旧格式：prefix_YYYYMMDD                （如 wxid_xxx_6ded_20260417）
        新格式：prefix_YYYYMMDD_HHMM            （如 wxid_xxx_6ded_20260417_1915）
    """
    try:
        # prefix 允许外部显式传入；为空时回退到当前运行前缀。
        # 支持输入多个前缀（; / , / 换行分隔），任一命中即纳入候选。
        effective_raw = (prefix or _CONTACT_FOLDER_PREFIX).strip()
        effective_prefixes = _split_multi_values(effective_raw)
        if not effective_prefixes:
            effective_prefixes = [_CONTACT_FOLDER_PREFIX_DEFAULT]

        if not os.path.isdir(net_base):
            return ""
        valid: list = []
        for folder in os.listdir(net_base):
            if not any(folder.startswith(one_prefix) for one_prefix in effective_prefixes):
                continue
            parts = folder.split("_")
            last = parts[-1]
            second_last = parts[-2] if len(parts) >= 2 else ""
            date_str = time_str = ""
            if (len(parts) >= 3
                    and second_last.isdigit() and len(second_last) == 8
                    and last.isdigit() and 3 <= len(last) <= 6):
                date_str, time_str = second_last, last
            elif last.isdigit() and len(last) == 8:
                date_str, time_str = last, "0000"
            else:
                continue
            try:
                month = int(date_str[4:6])
                day = int(date_str[6:8])
                if not (1 <= month <= 12 and 1 <= day <= 31):
                    continue
                sort_key = int(date_str) * 1_000_000 + int(time_str)
            except ValueError:
                continue
            valid.append((folder, sort_key))
        if not valid:
            return ""
        valid.sort(key=lambda x: x[1], reverse=True)
        db_path = os.path.join(net_base, valid[0][0],
                               "db_storage", "contact", "contact.db")
        return db_path if os.path.isfile(db_path) else ""
    except Exception:
        return ""


# ─────────────────────────── 动态加载 增量导入.py ───────────────────────────
def _load_incremental_module():
    # 被 PyInstaller 打包后，优先用 exe 所在目录（便于外置、用户可直接编辑配置）；
    # 未打包时使用源文件所在目录。两种情况都找不到才报错。
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    candidates.append(Path(__file__).resolve().parent)

    target = None
    for base in candidates:
        p = base / "增量导入.py"
        if p.exists():
            target = p
            break
    if target is None:
        raise FileNotFoundError(
            "未找到 增量导入.py，请将其与 导入控制台.exe / 导入控制台.py 放在同一目录下。"
            f" 已搜索：{[str(x) for x in candidates]}"
        )
    spec = spec_from_file_location("incremental_import_mod", str(target))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块：{target}")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


INC = _load_incremental_module()


# ─────────────────────────── 飞书全量同步流水线 ───────────────────────────
if getattr(sys, "frozen", False):
    # 打包后优先读取 exe 同目录的外置同步脚本，方便用户直接替换脚本和 .env。
    FEISHU_SYNC_DEFAULT_SCRIPT = str(Path(sys.executable).resolve().parent / "意向学员关系导入到飞书多维表格.py")
else:
    FEISHU_SYNC_DEFAULT_SCRIPT = str(Path(__file__).resolve().parent / "意向学员关系导入到飞书多维表格.py")
FEISHU_SYNC_CONFIG_BASE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
FEISHU_SYNC_CONFIG_PATH = FEISHU_SYNC_CONFIG_BASE / "feishu_sync_console_config.json"
FEISHU_SYNC_DEFAULT_CONFIG = {
    "SCRIPT_PATH": FEISHU_SYNC_DEFAULT_SCRIPT,
    "DRY_RUN": True,
    "INTERVAL_MIN": 60,
    # 首次触发时刻（HH:MM，24h）。空 = 启动后等一个 INTERVAL_MIN 才跑第一次（旧行为）
    "START_TIME": "",
}


def load_feishu_sync_config() -> dict:
    """读取飞书同步页面配置；读取失败时使用默认值，避免影响控制台启动。"""
    cfg = dict(FEISHU_SYNC_DEFAULT_CONFIG)
    try:
        if FEISHU_SYNC_CONFIG_PATH.exists():
            saved = json.loads(FEISHU_SYNC_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update(saved)
        # 兼容历史配置：旧版默认脚本是「同步意向学员到飞书.py」。
        # 若当前仍指向旧脚本名，则自动迁移到新版目标脚本，避免继续走旧链路。
        old_name = "同步意向学员到飞书.py"
        script_path = str(cfg.get("SCRIPT_PATH", "") or "").strip()
        if script_path and Path(script_path).name == old_name:
            new_path = str((Path(script_path).resolve().parent / "意向学员关系导入到飞书多维表格.py"))
            cfg["SCRIPT_PATH"] = new_path
            try:
                INC.logger.info(f"[飞书同步] 已自动迁移脚本路径：{new_path}")
            except Exception:
                pass
    except Exception as e:
        INC.logger.warning(f"[飞书同步] 读取配置失败，使用默认配置：{e}")
    return cfg


def save_feishu_sync_config(cfg: dict, log) -> None:
    """保存飞书同步页面配置，方便下次打开控制台继续使用。"""
    try:
        FEISHU_SYNC_CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info(f"[飞书同步] 配置已保存：{FEISHU_SYNC_CONFIG_PATH}")
    except Exception as e:
        log.warning(f"[飞书同步] 保存配置失败：{e}")


def load_feishu_sync_module(script_path: str):
    """动态加载同步脚本，复用脚本中已有的导出、清空、导入函数。"""
    target = Path(script_path).expanduser()
    if not target.exists():
        raise FileNotFoundError(f"未找到飞书同步脚本：{target}")
    spec = spec_from_file_location("feishu_sync_mod", str(target))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载飞书同步脚本：{target}")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_feishu_env_from_config(script_path: str, log) -> dict:
    """
    从可用配置文件中补齐飞书环境变量（仅填充缺失值，不覆盖已存在值）。

    兼容场景：
    - 用户通过导入控制台直接执行脚本，但未在当前进程手动设置环境变量
    - 飞书凭证已写在 sync_to_feishu.config.json 中
    """
    script_dir = Path(script_path).expanduser().resolve().parent
    candidates: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    # 1) 脚本同目录（最直观）
    _add(script_dir / "sync_to_feishu.config.json")
    # 2) 向上查找 manjike-tools/prospect（兼容当前项目目录结构）
    for base in [script_dir, *script_dir.parents]:
        _add(base / "manjike-tools" / "prospect" / "sync_to_feishu.config.json")

    cfg: dict = {}
    used_path: Path | None = None
    for path in candidates:
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg = raw
                used_path = path
                break
        except Exception as exc:
            log.warning(f"[飞书同步] 读取配置失败（{path}）：{exc}")

    if not cfg:
        return {}

    feishu_cfg = cfg.get("飞书") if isinstance(cfg.get("飞书"), dict) else {}
    # 同时兼容“嵌套在 飞书 节点”和“平铺字段”两种写法
    values = {
        "FEISHU_APP_TOKEN": str(feishu_cfg.get("app_token") or cfg.get("app_token") or "").strip(),
        "FEISHU_TABLE_ID": str(feishu_cfg.get("table_id") or cfg.get("table_id") or "").strip(),
        "FEISHU_APP_ID": str(feishu_cfg.get("app_id") or cfg.get("app_id") or "").strip(),
        "FEISHU_APP_SECRET": str(feishu_cfg.get("app_secret") or cfg.get("app_secret") or "").strip(),
    }

    applied: dict[str, str] = {}
    for key, value in values.items():
        if value and not str(os.environ.get(key, "")).strip():
            os.environ[key] = value
            applied[key] = value

    if used_path:
        if applied:
            # 安全考虑：日志只打印键名，不打印密钥值
            log.info(
                f"[飞书同步] 已从配置自动注入环境变量：{', '.join(applied.keys())}（来源：{used_path}）"
            )
        else:
            log.info(f"[飞书同步] 检测到飞书配置文件（{used_path}），当前环境变量已存在，跳过注入。")
    return applied


def run_feishu_sync_pipeline(script_path: str, dry_run: bool, log) -> bool:
    """
    在控制台中运行飞书同步脚本。

    这里不调用同步脚本的 main()，而是调用其封装函数：
    login/get_valid_token -> export_students -> sync_to_feishu。
    这样可以避免 GUI 中出现终端 input() 阻塞，同时保留原脚本的限流重试和批处理逻辑。
    """
    mod = load_feishu_sync_module(script_path)
    # 先尝试从配置文件补齐环境变量，避免“通过导入控制台触发时变量丢失”。
    applied_env = _load_feishu_env_from_config(script_path, log)
    # 某些脚本在 import 时就读取了 APP_ID/APP_SECRET 到模块全局变量，
    # 这里把自动注入结果同步回模块，确保本次运行可立即生效。
    if "FEISHU_APP_ID" in applied_env and hasattr(mod, "FEISHU_APP_ID"):
        setattr(mod, "FEISHU_APP_ID", applied_env["FEISHU_APP_ID"])
    if "FEISHU_APP_SECRET" in applied_env and hasattr(mod, "FEISHU_APP_SECRET"):
        setattr(mod, "FEISHU_APP_SECRET", applied_env["FEISHU_APP_SECRET"])
    mod.DRY_RUN = bool(dry_run)
    mod.log = lambda msg: log.info(f"[飞书同步] {msg}")

    log.info(f"[飞书同步] 开始执行，DRY_RUN={bool(dry_run)}")

    # 兼容两类外置脚本：
    # 1) 旧版：提供 export_students/sync_to_feishu（控制台内直接调用）
    # 2) 新版：仅提供命令行入口（例如“转发脚本”），此时回退到子进程执行
    if hasattr(mod, "export_students") and hasattr(mod, "sync_to_feishu"):
        students = mod.export_students()
        summary = mod.sync_to_feishu(students)

        log.info("[飞书同步] ════════ 执行汇总 ════════")
        log.info(f"[飞书同步] 已清空记录数        ：{summary['deleted']}")
        log.info(f"[飞书同步] 导出接口学员总数    ：{summary['exported']}")
        log.info(f"[飞书同步] 展平后待导入记录数  ：{summary['prepared']}")
        log.info(f"[飞书同步] 已导入记录数        ：{summary['inserted']}")
        log.info(f"[飞书同步] 导入失败记录数      ：{summary['failed']}")
        return summary["failed"] == 0

    log.warning(
        "[飞书同步] 当前脚本未提供 export_students/sync_to_feishu，改为子进程执行脚本。"
    )
    cmd = [sys.executable, str(Path(script_path).expanduser())]
    # 约定式传参：如果脚本支持 --dry-run 就会生效；不支持也不会影响主流程。
    if bool(dry_run):
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"子进程执行失败，exit_code={proc.returncode}")
    log.info("[飞书同步] 子进程执行完成。")
    return True


# ─────────────────────────── Tab1（增量导入）控制台层配置 ───────────────────────────
# 仅存放控制台层独有的设置（如"首次触发时间"），不污染 增量导入.py 的 INC.CONFIG
TAB1_CONSOLE_CONFIG_BASE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
TAB1_CONSOLE_CONFIG_PATH = TAB1_CONSOLE_CONFIG_BASE / "tab1_console_config.json"

TAB1_DEFAULT_CONSOLE_CONFIG = {
    # 首次触发时刻（HH:MM，24h）。空 = 启动后等一个 INTERVAL_MIN 才跑第一次（旧行为）
    "START_TIME": "",
    # contact.db 动态查找目录名前缀（支持在 GUI 修改并持久化）。
    "CONTACT_FOLDER_PREFIX": _CONTACT_FOLDER_PREFIX_DEFAULT,
}


def load_tab1_console_config() -> dict:
    cfg = dict(TAB1_DEFAULT_CONSOLE_CONFIG)
    try:
        if TAB1_CONSOLE_CONFIG_PATH.exists():
            saved = json.loads(TAB1_CONSOLE_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update(saved)
    except Exception as e:
        try:
            INC.logger.warning(f"[Tab1] 读取首次触发时间配置失败：{e}")
        except Exception:
            pass
    return cfg


def save_tab1_console_config(cfg: dict, log) -> None:
    try:
        TAB1_CONSOLE_CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info(f"[Tab1] 控制台层配置已保存：{TAB1_CONSOLE_CONFIG_PATH}")
    except Exception as e:
        log.warning(f"[Tab1] 保存控制台层配置失败：{e}")


# ─────────────────────────── 通用：HH:MM 首次触发时间工具 ───────────────────────────
def parse_hhmm(raw: str):
    """
    解析 HH:MM（24h）。
    - 空串 → None（表示"未启用首次触发时间"）
    - 合法 → (h, m)
    - 非法 → 抛 ValueError，调用方自行 try/except 给用户提示
    """
    s = (raw or "").strip()
    if not s:
        return None
    if ":" not in s:
        raise ValueError("缺少冒号")
    hh, mm = s.split(":", 1)
    h = int(hh.strip())
    m = int(mm.strip())
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("时刻越界（小时 0~23 / 分钟 0~59）")
    return h, m


def compute_first_delay_sec(hm, interval_min: int) -> int:
    """
    "首次触发"等待秒数：
      - hm 为空 → 等一个 INTERVAL_MIN（旧行为）
      - hm 已过 → 等到次日同时刻
      - hm 未到 → 等到今天该时刻
    """
    if hm is None:
        return max(1, interval_min * 60)
    h, m = hm
    now = datetime.now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        from datetime import timedelta as _td
        target = target + _td(days=1)
    return max(1, int((target - now).total_seconds()))


# ─────────────────────────── 用户上传（飞书 → 秒哒 import-users）流水线 ───────────────────────────
# 复用本目录下的 上传用户结构.py：动态加载，调用其核心函数（不调用 main，避免 argparse）
if getattr(sys, "frozen", False):
    USER_UPLOAD_DEFAULT_SCRIPT = str(
        Path(sys.executable).resolve().parent / "上传用户结构.py"
    )
else:
    USER_UPLOAD_DEFAULT_SCRIPT = str(
        Path(__file__).resolve().parent / "上传用户结构.py"
    )

USER_UPLOAD_CONFIG_BASE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
USER_UPLOAD_CONFIG_PATH = USER_UPLOAD_CONFIG_BASE / "user_upload_console_config.json"

# 模式可选值：
#   "full"        飞书拉取 → 写本地 JSON → 上传秒哒（默认）
#   "json_only"   仅生成本地 JSON，不上传
#   "upload_only" 跳过飞书拉取，直接读本地 JSON 上传
USER_UPLOAD_DEFAULT_CONFIG = {
    "SCRIPT_PATH":  USER_UPLOAD_DEFAULT_SCRIPT,
    "VIEW_ID":      "vew7GtEotv",
    "MODE":         "full",
    "DRY_RUN":      False,
    "INTERVAL_MIN": 60,
    # 首次触发时刻（HH:MM，24 小时制）。留空 = 启动后等一个 INTERVAL_MIN 才跑第一次（旧行为）。
    "START_TIME":   "",
}


def load_user_upload_config() -> dict:
    """读取「用户上传」页面配置；读取失败时使用默认值，避免影响控制台启动。"""
    cfg = dict(USER_UPLOAD_DEFAULT_CONFIG)
    try:
        if USER_UPLOAD_CONFIG_PATH.exists():
            saved = json.loads(USER_UPLOAD_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update(saved)
    except Exception as e:
        INC.logger.warning(f"[用户上传] 读取配置失败，使用默认配置：{e}")
    return cfg


def save_user_upload_config(cfg: dict, log) -> None:
    """保存「用户上传」页面配置，方便下次打开控制台继续使用。"""
    try:
        USER_UPLOAD_CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info(f"[用户上传] 配置已保存：{USER_UPLOAD_CONFIG_PATH}")
    except Exception as e:
        log.warning(f"[用户上传] 保存配置失败：{e}")


def load_user_upload_module(script_path: str):
    """动态加载 上传用户结构.py，复用其内部的飞书拉取 + 秒哒上传函数。"""
    target = Path(script_path).expanduser()
    if not target.exists():
        raise FileNotFoundError(f"未找到「上传用户结构」脚本：{target}")
    spec = spec_from_file_location("user_upload_mod", str(target))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载脚本：{target}")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_user_upload_pipeline(
    script_path: str,
    mode: str,
    dry_run: bool,
    view_id: str,
    log,
) -> bool:
    """
    控制台调用入口：
        mode="full"        : 飞书拉取 → 写 JSON → 上传秒哒（dry_run 仅影响上传环节）
        mode="json_only"   : 飞书拉取 → 仅写 JSON
        mode="upload_only" : 跳过拉取，直接读本地 JSON → 上传秒哒

    成功定义：
        json_only   → 写 JSON 成功
        full / upload_only → 上传后 errors 数组为空 视为成功
    """
    mod = load_user_upload_module(script_path)

    # 允许从 GUI 临时覆盖视图 ID（脚本默认是 vew7GtEotv）
    if view_id:
        try:
            setattr(mod, "VIEW_ID", view_id)
        except Exception:
            pass

    log.info(f"[用户上传] 开始执行：mode={mode} dry_run={dry_run} view_id={view_id!r}")

    # ── 1. 取数据：飞书拉取 OR 本地 JSON ────────────────────
    if mode == "upload_only":
        json_path = Path(getattr(mod, "OUTPUT_JSON_PATH"))
        log.info(f"[用户上传] 读取本地 JSON：{json_path}")
        users = mod._读取本地_json(json_path)
    else:
        log.info("[用户上传] 飞书：获取 tenant_access_token")
        feishu_token = mod.获取_tenant_access_token()
        log.info("[用户上传] 飞书：分页拉取记录")
        records = mod.拉取所有记录(feishu_token)
        log.info(f"[用户上传] 飞书：共 {len(records)} 条记录，开始字段抽取与去重")
        users = mod.转换为上传结构(records)
        out_path = Path(getattr(mod, "OUTPUT_JSON_PATH"))
        mod.写入_json(users, out_path)
        log.info(f"[用户上传] 已写入本地 JSON：{out_path}（{len(users)} 条）")

    # ── 2. 仅生成 JSON 模式 → 直接结束 ───────────────────────
    if mode == "json_only":
        log.info(f"[用户上传] 完成：mode=json_only / 用户数 {len(users)}")
        return True

    # ── 3. 上传秒哒 ─────────────────────────────────────────
    log.info("[用户上传] 秒哒：登录获取 access_token")
    miaoda_token = mod.秒哒登录()
    log.info(f"[用户上传] 秒哒：开始上传 {len(users)} 条")
    summary = mod.秒哒上传用户(users, miaoda_token, dry_run=bool(dry_run))

    log.info("[用户上传] ════════ 上传汇总 ════════")
    log.info(f"[用户上传] 本地待上传    : {len(users)}")
    log.info(f"[用户上传] 接口 total    : {summary['total']}")
    log.info(f"[用户上传] 接口 created  : {summary['created']}")
    log.info(f"[用户上传] 接口 skipped  : {summary['skipped']}")
    log.info(f"[用户上传] 接口 errors   : {len(summary['errors'])}")
    log.info(f"[用户上传] 请求批次      : {summary['batches']}")

    if summary["errors"]:
        log.warning("[用户上传] 失败明细（前 20 条）：")
        for err in summary["errors"][:20]:
            log.warning(f"[用户上传]   - {err}")

    return not summary["errors"]


# ─────────────────────────── 内部备注导入流水线（外置脚本版）───────────────────────────
# 全部实现已经搬到外置脚本「内部备注导入.py」，控制台只负责：
#   1) 动态加载该脚本（修改脚本不需要重新打包 exe）
#   2) 把 SUPABASE_URL / ANON_KEY / get_token 等"控制台运行时依赖"注入到脚本
#   3) 在 UI Tab 里允许用户改"脚本路径 / DB 路径 / 导出 JSON 路径 / 上传模式"
#
# 注意：NOTE_CONFIG 仍然保留为运行时配置载体（UI 默认值 + 持久化结果），
#       但里面已经不再放任何"实现细节"，只放运行参数和外置脚本路径。

# 外置脚本默认路径：
# - 源码运行：和 导入控制台.py 同目录的 内部备注导入.py
# - 打包后  ：和 exe 同目录的 内部备注导入.py（方便客户直接替换脚本）
if getattr(sys, "frozen", False):
    NOTE_IMPORT_DEFAULT_SCRIPT = str(
        Path(sys.executable).resolve().parent / "内部备注导入.py"
    )
else:
    NOTE_IMPORT_DEFAULT_SCRIPT = str(
        Path(__file__).resolve().parent / "内部备注导入.py"
    )

# 控制台层持久化配置（脚本路径 / DB / JSON / UPLOAD_MODE）写在这个文件里，
# 下次打开控制台自动恢复上次保存的值。
NOTE_IMPORT_CONFIG_BASE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
NOTE_IMPORT_CONFIG_PATH = NOTE_IMPORT_CONFIG_BASE / "note_import_console_config.json"

# 运行参数 + 外置脚本路径（同时充当 UI 默认值与"持久化字段"）
NOTE_CONFIG = {
    "SCRIPT_PATH": NOTE_IMPORT_DEFAULT_SCRIPT,
    "DB_PATH": r"C:\Users\LENOVO\Desktop\contact_内部专用.db;C:\Users\LENOVO\Desktop\contact_内部专用2.db",
    "OUT_JSON": r"C:\Users\LENOVO\Desktop\contact_result.json",
    "UPLOAD_MODE": "postgrest",  # "postgrest" 或 "edge"
    "BATCH_SIZE": 100,
    "BATCH_INTERVAL": 0.3,
    "REQUEST_TIMEOUT": 120,
    "TABLE_NAME": "internal_notes",
    "CONFLICT_COLUMN": "wechat_id",
    "IMPORT_FUNCTION": "import-internal-notes",
}


def load_note_import_config() -> None:
    """启动时把上次保存的持久化配置合并进 NOTE_CONFIG。

    读取失败时使用默认值，绝不影响控制台启动。
    """
    try:
        if NOTE_IMPORT_CONFIG_PATH.exists():
            saved = json.loads(NOTE_IMPORT_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                for k, v in saved.items():
                    if k in NOTE_CONFIG and v not in (None, ""):
                        NOTE_CONFIG[k] = v
    except Exception as e:
        # 此时 INC.logger 还没绑好控制台，先 print 一行，启动后日志框也会看到 stderr
        print(f"[内部备注] 读取持久化配置失败，使用默认值：{e}")


def save_note_import_config(log) -> None:
    """把 NOTE_CONFIG 里的关键字段写回持久化文件，下次启动自动恢复。"""
    try:
        payload = {
            "SCRIPT_PATH": NOTE_CONFIG.get("SCRIPT_PATH", ""),
            "DB_PATH": NOTE_CONFIG.get("DB_PATH", ""),
            "OUT_JSON": NOTE_CONFIG.get("OUT_JSON", ""),
            "UPLOAD_MODE": NOTE_CONFIG.get("UPLOAD_MODE", "postgrest"),
        }
        NOTE_IMPORT_CONFIG_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info(f"[内部备注] 配置已保存：{NOTE_IMPORT_CONFIG_PATH}")
    except Exception as e:
        log.warning(f"[内部备注] 保存配置失败：{e}")


def load_note_import_module(script_path: str):
    """动态加载外置「内部备注导入.py」脚本，复用其 run_pipeline()。"""
    target = Path(script_path).expanduser()
    if not target.exists():
        raise FileNotFoundError(f"未找到内部备注导入脚本：{target}")
    spec = spec_from_file_location("note_import_mod", str(target))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载内部备注导入脚本：{target}")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_note_pipeline_external(do_upload: bool, log) -> bool:
    """通过外置脚本运行内部备注导入。

    所有控制台层的"运行时依赖"（Supabase URL / ANON_KEY / get_token）
    都以关键字参数方式注入到外置脚本 run_pipeline()，外置脚本本身完全
    不依赖控制台代码，因此可以独立调试 / 修改 / 替换。

    返回 True / False 与原有 run_note_pipeline 兼容，外部调用点无需改动逻辑。
    """
    script_path = NOTE_CONFIG.get("SCRIPT_PATH") or NOTE_IMPORT_DEFAULT_SCRIPT
    try:
        mod = load_note_import_module(script_path)
    except Exception as e:
        log.error(f"[内部备注] 加载外置脚本失败：{e}")
        return False

    return mod.run_pipeline(
        db_path=NOTE_CONFIG["DB_PATH"],
        out_json=NOTE_CONFIG["OUT_JSON"],
        upload=do_upload,
        upload_mode=NOTE_CONFIG.get("UPLOAD_MODE", "postgrest"),
        supabase_url=INC.CONFIG.get("SUPABASE_URL"),
        anon_key=INC.CONFIG.get("ANON_KEY"),
        get_token=INC.get_token,
        batch_size=int(NOTE_CONFIG.get("BATCH_SIZE", 100)),
        batch_interval=float(NOTE_CONFIG.get("BATCH_INTERVAL", 0.3)),
        request_timeout=int(NOTE_CONFIG.get("REQUEST_TIMEOUT", 120)),
        table_name=NOTE_CONFIG.get("TABLE_NAME", "internal_notes"),
        conflict_column=NOTE_CONFIG.get("CONFLICT_COLUMN", "wechat_id"),
        import_function=NOTE_CONFIG.get("IMPORT_FUNCTION", "import-internal-notes"),
        log=log,
    )


# 启动时立即合并持久化配置（必须在 NOTE_CONFIG 定义之后、UI 构建之前完成）
load_note_import_config()


# ─────────────────────────── 定时拷贝任务管理器 ───────────────────────────

# 外置定时拷贝脚本默认路径：
# - 打包后：与 exe 同目录，便于直接替换脚本
# - 源码运行：与导入控制台.py 同目录
if getattr(sys, "frozen", False):
    COPY_TASK_DEFAULT_SCRIPT = str(Path(sys.executable).resolve().parent / "定时拷贝任务.py")
else:
    COPY_TASK_DEFAULT_SCRIPT = str(Path(__file__).resolve().parent / "定时拷贝任务.py")

# 任务持久化 JSON 文件，与脚本放同一目录
_COPY_TASKS_JSON = str(Path(__file__).resolve().parent / "copy_tasks.json")


def load_copy_task_module(script_path: str):
    """动态加载外置「定时拷贝任务.py」脚本。"""
    target = Path(script_path).expanduser()
    if not target.exists():
        raise FileNotFoundError(f"未找到定时拷贝脚本：{target}")
    spec = spec_from_file_location("copy_task_mod", str(target))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载定时拷贝脚本：{target}")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_copy_task_manager_class(script_path: str):
    """
    从外置定时拷贝脚本中读取 CopyTaskManager 类。
    这样控制台无需改动即可替换定时拷贝实现。
    """
    mod = load_copy_task_module(script_path)
    mgr_cls = getattr(mod, "CopyTaskManager", None)
    if mgr_cls is None:
        raise AttributeError(f"{script_path} 中未找到 CopyTaskManager")
    return mgr_cls


class CopyTaskManager:
    """
    管理一组"定时文件拷贝"任务。
    每条任务描述"每隔 N 分钟把来源路径拷贝到目标路径"。
    任务列表持久化到 copy_tasks.json，后台线程每 30 秒检查一次是否有任务到期。
    """

    # 任务字段含义：
    #   id           : 唯一 UUID 字符串
    #   name         : 任务名称（用户自定义）
    #   src          : 来源文件的绝对路径（动态模式下为网络根目录）
    #   dst          : 目标文件或目录的绝对路径（支持多个，分号/逗号/换行分隔）
    #   contact_prefixes : 动态查找模式下的目录前缀（可选，支持多个分隔）
    #   interval_min : 拷贝间隔（分钟，整数 >= 1）
    #   enabled      : 是否启用（bool）
    #   contact_prefixes : 动态查找模式的目录前缀（可多个，分号分隔）
    #   last_run_ts  : 上次实际执行时间戳（float，0 表示未运行过）
    #   last_result  : 上次执行结果描述（str，空字符串表示未运行过）

    _CHECK_INTERVAL_SEC = 30  # 后台线程检查间隔

    def __init__(self, json_path: str = _COPY_TASKS_JSON,
                 log: logging.Logger | None = None) -> None:
        self._json_path = json_path
        # 若未传 logger，使用根 logger
        self._log = log or logging.getLogger(__name__)
        self._tasks: list[dict] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        # 加载已有任务
        self._load()

    # ── 持久化 ──────────────────────────────────────────────

    def _load(self) -> None:
        """从 JSON 文件加载任务列表；文件不存在则初始化为空列表。"""
        try:
            if os.path.isfile(self._json_path):
                with open(self._json_path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._tasks = data
                    self._log.info(f"[定时拷贝] 已加载 {len(self._tasks)} 条任务")
                    return
        except Exception as e:
            self._log.warning(f"[定时拷贝] 加载任务失败，将重置：{e}")
        self._tasks = []

    def _save(self) -> None:
        """将任务列表写入 JSON 文件。调用方需在持有 _lock 的情况下调用。"""
        try:
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(self._tasks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log.warning(f"[定时拷贝] 保存任务失败：{e}")

    # ── CRUD ────────────────────────────────────────────────

    def get_tasks(self) -> list[dict]:
        """返回任务列表的浅拷贝（线程安全）。"""
        with self._lock:
            return list(self._tasks)

    def add_task(self, name: str, src: str, dst: str,
                 interval_min: int, enabled: bool = True,
                 find_contact_db: bool = False,
                 start_time: str = "",
                 contact_prefixes: str = "") -> dict:
        """
        新增一条任务，返回新建的任务字典。
        find_contact_db=True 时，src 为网络根目录，执行时自动定位最新的 contact.db 文件。
        start_time 为可选 HH:MM（24h）；非空时表示"未运行过的任务首次触发的时刻"，
        触发过一次后回退到 interval_min 间隔轮询。
        """
        import uuid
        task = {
            "id": str(uuid.uuid4()),
            "name": name.strip(),
            "src": src.strip(),
            "dst": dst.strip(),
            "interval_min": max(1, int(interval_min)),
            "enabled": bool(enabled),
            "find_contact_db": bool(find_contact_db),
            "start_time": (start_time or "").strip(),
            "contact_prefixes": (contact_prefixes or "").strip(),
            "last_run_ts": 0.0,
            "last_result": "",
        }
        with self._lock:
            self._tasks.append(task)
            self._save()
        self._log.info(f"[定时拷贝] 新增任务：{task['name']}")
        return task

    def remove_task(self, task_id: str) -> bool:
        """按 ID 删除任务，返回是否找到并删除。"""
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if t["id"] != task_id]
            if len(self._tasks) < before:
                self._save()
                return True
        return False

    def update_task(self, task_id: str, **kwargs) -> bool:
        """
        更新指定任务的字段。
        可更新字段：name / src / dst / interval_min / enabled / find_contact_db / start_time / contact_prefixes。
        """
        allowed = {
            "name", "src", "dst", "interval_min", "enabled",
            "find_contact_db", "start_time", "contact_prefixes",
        }
        with self._lock:
            for task in self._tasks:
                if task["id"] == task_id:
                    for k, v in kwargs.items():
                        if k in allowed:
                            task[k] = v
                    self._save()
                    return True
        return False

    def set_enabled(self, task_id: str, enabled: bool) -> bool:
        """启用或禁用指定任务。"""
        return self.update_task(task_id, enabled=enabled)

    def run_task_now(self, task_id: str) -> bool:
        """立即在后台线程执行指定任务（不影响下次定时触发时间）。"""
        with self._lock:
            task = next((t for t in self._tasks if t["id"] == task_id), None)
        if task is None:
            return False
        threading.Thread(target=self._execute_task, args=(task,), daemon=True).start()
        return True

    # ── 执行 ────────────────────────────────────────────────

    def _execute_task(self, task: dict) -> None:
        """
        执行单条拷贝任务：
        - find_contact_db=True 时：src 为网络根目录，调用 _find_contact_db_source() 自动定位最新文件
        - 固定路径模式下，src 必须是文件路径
        - dst 始终按“目标文件路径”处理（可多个）
        执行完成后更新 last_run_ts 和 last_result，并持久化。
        """
        name = task.get("name", "")
        src  = task.get("src", "")
        dst  = task.get("dst", "")
        result = ""
        self._log.info(f"[定时拷贝] 开始执行：{name}  {src} → {dst}")
        try:
            # contact.db 动态查找模式：src 为网络根目录，自动找最新备份文件
            if task.get("find_contact_db"):
                # 优先使用任务级前缀；未填写时回退到全局前缀，保持老任务兼容。
                task_prefixes = (task.get("contact_prefixes", "") or "").strip()
                effective_prefixes = task_prefixes or _CONTACT_FOLDER_PREFIX
                actual_src = _find_contact_db_source(src, prefix=effective_prefixes)
                if not actual_src:
                    raise FileNotFoundError(
                        f"未在 {src} 下找到有效的 contact.db（请检查网络根目录是否可访问，当前前缀：{effective_prefixes}）"
                    )
                src = actual_src  # 替换为实际文件路径
                self._log.info(f"[定时拷贝] 动态定位 contact.db：{src}")

            if not os.path.exists(src):
                raise FileNotFoundError(f"来源不存在：{src}")
            if not os.path.isfile(src):
                raise ValueError(f"来源必须是文件路径，当前不是文件：{src}")

            # 支持“多个目标路径”：在 dst 输入框中使用 ; / , / 换行分隔
            dst_list = _split_multi_values(dst)
            if not dst_list:
                raise ValueError("目标路径不能为空")
            if len(dst_list) > 1:
                self._log.info(f"[定时拷贝] 检测到 {len(dst_list)} 个目标路径，开始逐个复制")

            failed_targets: list[str] = []
            # 拷贝文件到多个目标文件路径（使用稳定性检测 + 拷贝后校验）
            for one_dst in dst_list:
                try:
                    _copy_file_with_verify(src, one_dst)
                except Exception as exc:
                    failed_targets.append(f"{one_dst} -> {exc}")

            if failed_targets:
                brief = " | ".join(failed_targets[:3])
                if len(failed_targets) > 3:
                    brief = f"{brief} | ... 其余 {len(failed_targets) - 3} 个"
                raise RuntimeError(f"部分目标复制失败：{brief}")

            result = f"成功 {datetime.now().strftime('%H:%M:%S')}"
            self._log.info(f"[定时拷贝] 完成：{name}")
        except Exception as e:
            result = f"失败 {datetime.now().strftime('%H:%M:%S')} {e}"
            self._log.warning(f"[定时拷贝] 任务失败：{name}：{e}")

        # 写回 last_run_ts / last_result
        with self._lock:
            for t in self._tasks:
                if t["id"] == task["id"]:
                    t["last_run_ts"] = time.time()
                    t["last_result"] = result
                    break
            self._save()

    # ── 后台调度 ─────────────────────────────────────────────

    @staticmethod
    def _compute_next_start_ts(hhmm_str: str, now_ts: float) -> float | None:
        """根据任务 start_time（HH:MM）返回下一次该时刻的 epoch；HH:MM 非法则返回 None。"""
        try:
            hm = parse_hhmm(hhmm_str)
        except Exception:
            return None
        if hm is None:
            return None
        h, m = hm
        now_dt = datetime.fromtimestamp(now_ts)
        target = now_dt.replace(hour=h, minute=m, second=0, microsecond=0)
        if target.timestamp() <= now_ts:
            from datetime import timedelta as _td
            target = target + _td(days=1)
        return target.timestamp()

    def _scheduler_loop(self) -> None:
        """
        后台线程主循环：每 _CHECK_INTERVAL_SEC 秒扫描一次任务列表，触发到期任务。
        判定规则：
          - 任务 enabled 且未运行过（last_run_ts==0）且 start_time 非空 → 等到该时刻才触发
          - 否则按 (now - last_run_ts) >= interval_min*60 判定
        """
        while not self._stop_event.wait(self._CHECK_INTERVAL_SEC):
            now = time.time()
            with self._lock:
                due: list[dict] = []
                for t in self._tasks:
                    if not t.get("enabled"):
                        continue
                    last_ts = float(t.get("last_run_ts", 0) or 0)
                    interval_sec = max(1, int(t.get("interval_min", 1) or 1)) * 60
                    start_time_raw = (t.get("start_time") or "").strip()
                    if last_ts == 0 and start_time_raw:
                        first_ts = self._compute_next_start_ts(start_time_raw, now)
                        if first_ts is None or now < first_ts:
                            continue
                    elif (now - last_ts) < interval_sec:
                        continue
                    due.append(dict(t))
            for task in due:
                threading.Thread(
                    target=self._execute_task, args=(task,), daemon=True
                ).start()

    def start(self) -> None:
        """启动后台调度线程（幂等：已启动时不重复启动）。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._scheduler_loop, daemon=True, name="CopyTaskScheduler"
        )
        self._thread.start()
        self._log.info("[定时拷贝] 调度器已启动")

    def stop(self) -> None:
        """停止后台调度线程。"""
        self._stop_event.set()
        self._log.info("[定时拷贝] 调度器已停止")


# ─────────────────────────── GUI ───────────────────────────
def launch_gui() -> None:
    INC._enable_windows_dpi_awareness()

    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
    from tkinter import font as tkfont

    log_queue: "queue.Queue[str]" = queue.Queue()

    class QueueHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                log_queue.put_nowait(self.format(record))
            except Exception:
                pass

    gui_handler = QueueHandler()
    gui_handler.setLevel(logging.INFO)
    gui_handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    INC.logger.addHandler(gui_handler)

    scheduler = INC.Scheduler(interval_sec=15 * 60)

    feishu_sync_cfg = load_feishu_sync_config()
    tab1_console_cfg = load_tab1_console_config()
    # 启动时恢复上次保存的 contact.db 前缀配置。
    # 使用 global 是为了让后台定时拷贝线程与手动同步共用同一份前缀。
    global _CONTACT_FOLDER_PREFIX
    _CONTACT_FOLDER_PREFIX = (
        str(tab1_console_cfg.get("CONTACT_FOLDER_PREFIX", "") or "").strip()
        or _CONTACT_FOLDER_PREFIX_DEFAULT
    )
    # 定时拷贝任务管理器（优先使用外置脚本，加载失败时回退内置实现）
    try:
        copy_mgr_cls = load_copy_task_manager_class(COPY_TASK_DEFAULT_SCRIPT)
        INC.logger.info(f"[定时拷贝] 已加载外置脚本：{COPY_TASK_DEFAULT_SCRIPT}")
        try:
            # 新版外置脚本构造参数
            copy_mgr = copy_mgr_cls(
                json_path=_COPY_TASKS_JSON,
                logger=INC.logger,
                default_contact_prefix=_CONTACT_FOLDER_PREFIX,
            )
        except TypeError:
            # 兼容旧版本参数签名（log）
            copy_mgr = copy_mgr_cls(
                json_path=_COPY_TASKS_JSON,
                log=INC.logger,
            )
    except Exception as e:
        INC.logger.warning(f"[定时拷贝] 加载外置脚本失败，回退内置实现：{e}")
        copy_mgr = CopyTaskManager(log=INC.logger)
    copy_mgr.start()

    # 启动全局任务队列 + 加载自定义插件配置
    # 队列 worker 是单线程，所有"勾选了加入全局队列"的任务按 FIFO 串行执行
    GLOBAL_RUNNER.start()
    custom_plugins_cfg = cp_mod.load_config(CUSTOM_PLUGINS_CONFIG_PATH)

    root = tk.Tk()
    root.title(
        "导入控制台（增量导入 + 内部备注 + 定时拷贝 + 飞书同步 + 用户上传 + 扩展功能）"
    )
    root.geometry("1400x800")
    root.minsize(900, 640)
    # 启动后尝试最大化（Windows 用 'zoomed'，其他平台静默回退）
    try:
        root.state("zoomed")
    except Exception:
        try:
            root.attributes("-zoomed", True)
        except Exception:
            pass

    # 整体使用一个垂直方向的 PanedWindow 容纳「Notebook（功能区）」+「日志区」
    # 用户可以拖动两者中间的分隔条来调整高度
    # row=0：全局任务队列状态栏；row=1：主区域（Notebook + 日志）
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)

    # ── 顶部全局任务队列状态栏 ───────────────────────────────────────
    # 当任意一个 Tab 勾选"加入全局队列"提交了任务，这里会实时显示当前任务和等待数
    queue_status_var = tk.StringVar(value="全局队列：空闲")
    queue_status_frame = ttk.Frame(root, padding=(12, 6))
    queue_status_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
    queue_status_frame.columnconfigure(0, weight=1)
    ttk.Label(
        queue_status_frame,
        textvariable=queue_status_var,
        foreground="#1f4f8c",
    ).grid(row=0, column=0, sticky="w")

    def _refresh_queue_status() -> None:
        """刷新顶部队列状态文本（线程安全：由 root.after 在 UI 线程触发）。"""
        snap = GLOBAL_RUNNER.snapshot()
        cur = snap.get("current")
        pending = snap.get("pending") or []
        if cur is None and not pending:
            queue_status_var.set("全局队列：空闲")
        elif cur is not None:
            wait_n = len(pending)
            tail = f"，等待 {wait_n} 个" if wait_n else ""
            queue_status_var.set(f"全局队列：正在执行「{cur['name']}」{tail}")
        else:
            queue_status_var.set(f"全局队列：等待 {len(pending)} 个")

    # TaskRunner 状态变化时回调 → 切回 UI 线程刷新文本
    GLOBAL_RUNNER.add_status_listener(
        lambda: root.after(0, _refresh_queue_status)
    )
    # 兜底：每 1 秒主动刷一次，避免任何回调丢失
    def _periodic_queue_status() -> None:
        _refresh_queue_status()
        root.after(1000, _periodic_queue_status)

    main_paned = ttk.PanedWindow(root, orient=tk.VERTICAL)
    main_paned.grid(row=1, column=0, sticky="nsew", padx=12, pady=(6, 12))

    try:
        dpi = root.winfo_fpixels("1i")
        root.tk.call("tk", "scaling", max(1.25, dpi / 72.0))
    except Exception:
        pass

    ui_family = "Microsoft YaHei UI"
    mono_family = "Consolas"
    ui_size = 11
    for named in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(named).configure(family=ui_family, size=ui_size)
        except Exception:
            pass

    style = ttk.Style(root)
    style.configure("TLabel", font=(ui_family, ui_size))
    style.configure("TButton", font=(ui_family, ui_size), padding=(10, 6))
    style.configure("TLabelframe.Label", font=(ui_family, ui_size, "bold"))
    style.configure("TNotebook.Tab", font=(ui_family, ui_size, "bold"), padding=(16, 6))
    # 让 Treeview 行高更高，避免任务列表内容显示拥挤
    # rowheight 单位是像素；行内文字高度约 20px，留 12-14px 上下间距即可舒适
    style.configure("Treeview", font=(ui_family, ui_size), rowheight=40)
    style.configure("Treeview.Heading", font=(ui_family, ui_size, "bold"), padding=(4, 4))

    # ── 顶部 Notebook（放进 PanedWindow 上半部分，weight=3 确保默认占大部分） ──
    nb = ttk.Notebook(main_paned)
    main_paned.add(nb, weight=3)

    # ── 工具：把一个 Tab 包装成"垂直可滚动 Frame" ──
    # 当窗口高度不足时自动出现滚动条，避免按钮、列表被遮挡
    # 用法：tab_outer = ttk.Frame(nb); nb.add(tab_outer); tab = _make_scrollable(tab_outer)
    #      之后所有控件都放到 tab 里
    def _make_scrollable(outer: tk.Widget) -> ttk.Frame:
        """返回一个 ttk.Frame，放到 outer 中，并自动随内容出现垂直滚动条。"""
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        inner = ttk.Frame(canvas, padding=(8, 8))
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        # 内容尺寸变化时更新 canvas 滚动区域
        def _on_inner_configure(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_inner_configure)

        # 画布宽度变化时同步 inner 宽度，避免横向出现空白
        def _on_canvas_configure(e):
            canvas.itemconfigure(inner_id, width=e.width)
        canvas.bind("<Configure>", _on_canvas_configure)

        # 鼠标滚轮支持（鼠标进入时绑定，离开时解绑，避免影响其他 widget）
        def _on_mousewheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        return inner

    # ─── Tab 1：增量导入 ───
    # 外层占位 Frame 加进 Notebook，内层用 _make_scrollable 包裹，控件全部放 tab1
    tab1_outer = ttk.Frame(nb)
    nb.add(tab1_outer, text="增量导入")
    tab1 = _make_scrollable(tab1_outer)

    status_var = tk.StringVar(value="状态：空闲")
    next_var = tk.StringVar(value="下次：未启动")
    last_var = tk.StringVar(value="上次：—")
    stats_var = tk.StringVar(value="运行 0 次 / 跳过 0 次")

    row1 = ttk.Frame(tab1)
    row1.pack(fill=tk.X)
    ttk.Label(row1, textvariable=status_var, font=(ui_family, ui_size + 1, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 16))
    ttk.Label(row1, textvariable=next_var).grid(row=0, column=1, sticky="w", padx=(0, 16))
    ttk.Label(row1, textvariable=last_var).grid(row=0, column=2, sticky="w", padx=(0, 16))
    ttk.Label(row1, textvariable=stats_var).grid(row=0, column=3, sticky="w")

    row2 = ttk.Frame(tab1)
    row2.pack(fill=tk.X, pady=(8, 0))
    ttk.Label(row2, text="触发间隔（分钟）：").grid(row=0, column=0, sticky="w")
    interval_var = tk.StringVar(value="15")
    tk.Spinbox(row2, from_=1, to=1440, textvariable=interval_var, width=6, font=(ui_family, ui_size)).grid(row=0, column=1, padx=(6, 6))
    btn_apply_interval = ttk.Button(row2, text="应用间隔")
    btn_apply_interval.grid(row=0, column=2, padx=(0, 12))

    # 首次触发时间（HH:MM）；留空 = 旧行为，启动后等一个触发间隔
    row2b = ttk.Frame(tab1)
    row2b.pack(fill=tk.X, pady=(4, 0))
    ttk.Label(row2b, text="首次触发时间：").grid(row=0, column=0, sticky="w")
    start_time_var = tk.StringVar(value=str(tab1_console_cfg.get("START_TIME", "") or ""))
    ttk.Entry(row2b, textvariable=start_time_var, width=8, font=(ui_family, ui_size)).grid(
        row=0, column=1, padx=(6, 6)
    )
    ttk.Label(
        row2b,
        text="（HH:MM，留空=按间隔；填了则等到该时刻才发出第一次执行；过点推迟到次日）",
        foreground="#7f8c8d",
    ).grid(row=0, column=2, sticky="w")

    row3 = ttk.Frame(tab1)
    row3.pack(fill=tk.X, pady=(6, 0))
    row3.columnconfigure(1, weight=1)
    ttk.Label(row3, text="输出目录（CSV/JSON）：").grid(row=0, column=0, sticky="w")
    output_dir_var = tk.StringVar(value=INC.CONFIG.get("OUTPUT_DIR", ""))
    ttk.Entry(row3, textvariable=output_dir_var, font=(ui_family, ui_size)).grid(row=0, column=1, sticky="we", padx=(6, 6))
    btn_browse_out = ttk.Button(row3, text="浏览...")
    btn_browse_out.grid(row=0, column=2, padx=(0, 6))
    btn_apply_out = ttk.Button(row3, text="应用并保存")
    btn_apply_out.grid(row=0, column=3)

    row_skip = ttk.Frame(tab1)
    row_skip.pack(fill=tk.X, pady=(8, 0))
    skip_var = tk.BooleanVar(value=bool(INC.CONFIG.get("SKIP_ENROLLED_OR_BOUND", True)))

    def _on_toggle_skip() -> None:
        INC.CONFIG["SKIP_ENROLLED_OR_BOUND"] = bool(skip_var.get())
        INC.logger.info(
            f"[配置] 跳过已报名/有绑定学员：{'开启' if skip_var.get() else '关闭'}"
        )

    ttk.Checkbutton(
        row_skip,
        text="跳过意向学员查询系统中 已报名 或 有绑定 的学员",
        variable=skip_var,
        command=_on_toggle_skip,
    ).pack(side=tk.LEFT)

    row4 = ttk.Frame(tab1)
    row4.pack(fill=tk.X, pady=(10, 0))
    btn_start = ttk.Button(row4, text="启动定时")
    btn_stop = ttk.Button(row4, text="停止定时")
    btn_run_now = ttk.Button(row4, text="立即运行一次")
    btn_start.grid(row=0, column=0, padx=4)
    btn_stop.grid(row=0, column=1, padx=4)
    btn_run_now.grid(row=0, column=2, padx=4)

    # ─── contact.db 同步区（可配置来源根目录与目标路径） ───
    contact_frame = ttk.LabelFrame(tab1, text="contact.db 同步", padding=(8, 6))
    contact_frame.pack(fill=tk.X, pady=(10, 0))
    contact_frame.columnconfigure(1, weight=1)

    # 来源根目录
    ttk.Label(contact_frame, text="来源根目录：").grid(row=0, column=0, sticky="w", pady=(0, 4))
    contact_net_base_var = tk.StringVar(value=_CONTACT_NET_BASE)
    ttk.Entry(contact_frame, textvariable=contact_net_base_var,
              font=(ui_family, ui_size)).grid(row=0, column=1, sticky="we", padx=(6, 4), pady=(0, 4))
    def _browse_contact_net_base() -> None:
        from tkinter import filedialog
        d = filedialog.askdirectory(title="选择 contact.db 来源根目录",
                                   initialdir=contact_net_base_var.get().strip() or "")
        if d:
            contact_net_base_var.set(d)
    ttk.Button(contact_frame, text="浏览...",
               command=_browse_contact_net_base).grid(row=0, column=2, pady=(0, 4))

    # 目录前缀（可配置）：
    # 动态查找 contact.db 时，仅匹配以该前缀开头的备份目录。
    ttk.Label(contact_frame, text="目录前缀（多个用 ;）：").grid(row=1, column=0, sticky="w", pady=(0, 4))
    contact_prefix_var = tk.StringVar(value=_CONTACT_FOLDER_PREFIX)
    ttk.Entry(contact_frame, textvariable=contact_prefix_var,
              font=(ui_family, ui_size)).grid(row=1, column=1, sticky="we", padx=(6, 4), pady=(0, 4))

    def _save_contact_prefix_cfg(prefix_value: str) -> None:
        """保存并应用 contact.db 动态查找前缀。"""
        global _CONTACT_FOLDER_PREFIX
        prefix_list = _split_multi_values(prefix_value)
        normalized = ";".join(prefix_list) if prefix_list else _CONTACT_FOLDER_PREFIX_DEFAULT
        _CONTACT_FOLDER_PREFIX = normalized
        # 若当前使用的是外置定时拷贝管理器，同步其默认前缀，确保无需重启即可生效。
        if hasattr(copy_mgr, "_default_contact_prefix"):
            try:
                copy_mgr._default_contact_prefix = normalized
            except Exception:
                pass
        contact_prefix_var.set(normalized)
        save_tab1_console_config(
            {
                "START_TIME": (start_time_var.get() or "").strip(),
                "CONTACT_FOLDER_PREFIX": normalized,
            },
            INC.logger,
        )
        INC.logger.info(f"[contact.db 同步] 已保存目录前缀：{normalized}")

    ttk.Button(
        contact_frame,
        text="保存前缀",
        command=lambda: _save_contact_prefix_cfg(contact_prefix_var.get()),
    ).grid(row=1, column=2, pady=(0, 4))

    # 目标路径（支持多个）
    ttk.Label(contact_frame, text="目标路径（多个用 ;）：").grid(row=2, column=0, sticky="w", pady=(0, 4))
    contact_dst_var = tk.StringVar(value=_CONTACT_LOCAL_DST)
    ttk.Entry(contact_frame, textvariable=contact_dst_var,
              font=(ui_family, ui_size)).grid(row=2, column=1, sticky="we", padx=(6, 4), pady=(0, 4))
    def _browse_contact_dst() -> None:
        from tkinter import filedialog
        f = filedialog.asksaveasfilename(
            title="选择 contact.db 保存位置",
            initialfile=os.path.basename(contact_dst_var.get().strip() or "contact.db"),
            defaultextension=".db",
            filetypes=[("SQLite DB", "*.db"), ("所有文件", "*.*")],
        )
        if f:
            contact_dst_var.set(f)
    ttk.Button(contact_frame, text="浏览...",
               command=_browse_contact_dst).grid(row=2, column=2, pady=(0, 4))

    # 状态 + 操作按钮
    contact_status_row = ttk.Frame(contact_frame)
    contact_status_row.grid(row=3, column=0, columnspan=3, sticky="ew")
    contact_status_row.columnconfigure(1, weight=1)
    ttk.Label(contact_status_row, text="状态：").grid(row=0, column=0, sticky="w")
    contact_src_label_var = tk.StringVar(value="（点击按钮检测来源）")
    ttk.Label(contact_status_row, textvariable=contact_src_label_var,
              font=(ui_family, ui_size - 1), foreground="#7f8c8d",
              ).grid(row=0, column=1, sticky="we", padx=(4, 12))
    btn_sync_contact = ttk.Button(contact_status_row, text="立即同步")
    btn_sync_contact.grid(row=0, column=2)

    # ─── Tab 2：内部备注导入 ───
    tab2_outer = ttk.Frame(nb)
    nb.add(tab2_outer, text="内部备注导入")
    tab2 = _make_scrollable(tab2_outer)
    tab2.columnconfigure(1, weight=1)

    # 新增：外置脚本路径（修改脚本不需要重新打包 exe）
    ttk.Label(tab2, text="外置脚本路径：").grid(row=0, column=0, sticky="w", pady=4)
    note_script_var = tk.StringVar(
        value=NOTE_CONFIG.get("SCRIPT_PATH", NOTE_IMPORT_DEFAULT_SCRIPT)
    )
    ttk.Entry(tab2, textvariable=note_script_var, font=(ui_family, ui_size)).grid(
        row=0, column=1, sticky="we", padx=6
    )
    btn_browse_note_script = ttk.Button(tab2, text="浏览...")
    btn_browse_note_script.grid(row=0, column=2, padx=4)

    ttk.Label(tab2, text="contact.db 路径：").grid(row=1, column=0, sticky="w", pady=4)
    db_var = tk.StringVar(value=NOTE_CONFIG["DB_PATH"])
    ttk.Entry(tab2, textvariable=db_var, font=(ui_family, ui_size)).grid(row=1, column=1, sticky="we", padx=6)
    btn_browse_db = ttk.Button(tab2, text="浏览...")
    btn_browse_db.grid(row=1, column=2, padx=4)

    ttk.Label(tab2, text="导出 JSON 路径：").grid(row=2, column=0, sticky="w", pady=4)
    json_var = tk.StringVar(value=NOTE_CONFIG["OUT_JSON"])
    ttk.Entry(tab2, textvariable=json_var, font=(ui_family, ui_size)).grid(row=2, column=1, sticky="we", padx=6)
    btn_browse_json = ttk.Button(tab2, text="浏览...")
    btn_browse_json.grid(row=2, column=2, padx=4)

    ttk.Label(tab2, text="上传模式：").grid(row=3, column=0, sticky="w", pady=4)
    mode_var = tk.StringVar(value=NOTE_CONFIG["UPLOAD_MODE"])
    mode_frame = ttk.Frame(tab2)
    mode_frame.grid(row=3, column=1, sticky="w", padx=6)
    ttk.Radiobutton(mode_frame, text="增量同步（推荐，按 internal_note 去重）", variable=mode_var, value="postgrest").pack(side=tk.LEFT, padx=(0, 14))
    ttk.Radiobutton(mode_frame, text="Edge Function（import-internal-notes）", variable=mode_var, value="edge").pack(side=tk.LEFT)

    ttk.Label(tab2, text="自动触发：").grid(row=4, column=0, sticky="w", pady=(8, 4))
    auto_frame = ttk.Frame(tab2)
    auto_frame.grid(row=4, column=1, sticky="w", padx=6, pady=(8, 4))
    auto_var = tk.BooleanVar(value=True)
    delay_var = tk.StringVar(value="60")
    note_next_var = tk.StringVar(value="自动触发：未启用")

    cb_auto_note = ttk.Checkbutton(
        auto_frame,
        text="增量导入完成后延迟",
        variable=auto_var,
    )
    cb_auto_note.pack(side=tk.LEFT)
    tk.Spinbox(
        auto_frame, from_=5, to=3600, textvariable=delay_var, width=6,
        font=(ui_family, ui_size),
    ).pack(side=tk.LEFT, padx=(4, 4))
    ttk.Label(auto_frame, text="秒自动运行内部备注").pack(side=tk.LEFT)
    ttk.Label(auto_frame, textvariable=note_next_var, foreground="#1f7a1f").pack(side=tk.LEFT, padx=(14, 0))

    # 提示用户：脚本路径所指文件就是真正执行内部备注导入的脚本
    note_script_hint = ttk.Label(
        tab2,
        text=(
            "说明：实际执行逻辑全部位于「外置脚本路径」所指的 Python 文件中。"
            "需要调整 SQL 筛选规则、批次大小、上传逻辑时，直接编辑该脚本即可生效，"
            "不需要重新打包 exe。脚本路径与上方其它字段会保存到 "
            f"{NOTE_IMPORT_CONFIG_PATH.name}，下次启动自动恢复。"
        ),
        foreground="#666666",
        wraplength=720,
        justify="left",
    )
    note_script_hint.grid(row=5, column=0, columnspan=3, sticky="we", pady=(8, 4))

    row_btn2 = ttk.Frame(tab2)
    row_btn2.grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 0))
    btn_note_run = ttk.Button(row_btn2, text="立即运行（导出并上传）")
    btn_note_run.pack(side=tk.LEFT, padx=(0, 8))
    btn_note_export_only = ttk.Button(row_btn2, text="仅导出 JSON（不上传）")
    btn_note_export_only.pack(side=tk.LEFT)
    btn_note_save_cfg = ttk.Button(row_btn2, text="保存配置")
    btn_note_save_cfg.pack(side=tk.LEFT, padx=(8, 0))

    # 「加入全局队列」复选框：
    # - 未勾选（默认）：与历史一致，若已有内部备注任务在跑则跳过本次（skip）
    # - 已勾选：丢到全局任务队列，等前面任务跑完后顺序执行；不再 skip
    note_use_queue_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        row_btn2,
        text="加入全局队列（前面任务跑完后再执行，不丢失）",
        variable=note_use_queue_var,
    ).pack(side=tk.LEFT, padx=(16, 0))

    # ─── Tab 3：定时拷贝 ───
    # 不再用 _make_scrollable 包装：Treeview 已自带垂直滚动条，外层再加 Canvas
    # 会导致 Treeview 被压扁，行高异常（看不全任务）。直接用原生 Frame + grid 布局。
    tab_copy = ttk.Frame(nb, padding=(8, 8))
    nb.add(tab_copy, text="定时拷贝")
    tab_copy.columnconfigure(0, weight=1)
    tab_copy.rowconfigure(1, weight=1)  # Treeview 行随 Tab 高度拉伸

    # ── 顶部按钮栏 ──
    copy_btn_row = ttk.Frame(tab_copy)
    copy_btn_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))

    btn_copy_add    = ttk.Button(copy_btn_row, text="新增任务")
    btn_copy_edit   = ttk.Button(copy_btn_row, text="编辑任务")
    btn_copy_del    = ttk.Button(copy_btn_row, text="删除任务")
    btn_copy_toggle = ttk.Button(copy_btn_row, text="启用/禁用")
    btn_copy_run    = ttk.Button(copy_btn_row, text="立即运行")
    btn_copy_refresh= ttk.Button(copy_btn_row, text="刷新列表")
    for btn in (btn_copy_add, btn_copy_edit, btn_copy_del,
                btn_copy_toggle, btn_copy_run, btn_copy_refresh):
        btn.pack(side=tk.LEFT, padx=(0, 6))

    # 右侧：调度器状态提示
    copy_sched_var = tk.StringVar(value="调度器：运行中（每 30 秒检查一次）")
    ttk.Label(copy_btn_row, textvariable=copy_sched_var,
              foreground="#1f7a1f").pack(side=tk.RIGHT)

    # ── 任务列表 Treeview ──
    copy_cols = ("name", "src", "dst", "interval", "start_time", "enabled", "last_run", "last_result")
    copy_col_conf = {
        # (列标题, 初始宽度, 是否随窗口拉伸)
        "name":        ("任务名",       180, True),
        "src":         ("来源路径",     350, True),
        "dst":         ("目标路径",     350, True),
        "interval":    ("间隔(分钟)",   100, True),
        "start_time":  ("首次触发(HH:MM)", 120, True),
        "enabled":     ("状态",         100, True),
        "last_run":    ("上次运行",     180, True),
        "last_result": ("上次结果",     180, True),
    }

    copy_tree_frame = ttk.Frame(tab_copy)
    copy_tree_frame.grid(row=1, column=0, sticky="nsew")
    copy_tree_frame.columnconfigure(0, weight=1)
    copy_tree_frame.rowconfigure(0, weight=1)

    copy_tree = ttk.Treeview(
        copy_tree_frame, columns=copy_cols, show="headings", selectmode="browse",
        height=10  # 至少显示 10 行，避免任务列表被压缩到只剩半行
    )
    for col in copy_cols:
        label, width, stretch = copy_col_conf[col]
        copy_tree.heading(col, text=label)
        copy_tree.column(col, width=width, stretch=stretch, anchor="w")
    copy_tree.grid(row=0, column=0, sticky="nsew")

    copy_vsb = ttk.Scrollbar(copy_tree_frame, orient="vertical",   command=copy_tree.yview)
    copy_hsb = ttk.Scrollbar(copy_tree_frame, orient="horizontal", command=copy_tree.xview)
    copy_tree.configure(yscrollcommand=copy_vsb.set, xscrollcommand=copy_hsb.set)
    copy_vsb.grid(row=0, column=1, sticky="ns")
    copy_hsb.grid(row=1, column=0, sticky="ew")

    # ── Treeview 刷新函数 ──
    def _refresh_copy_tree() -> None:
        """从 CopyTaskManager 重新读取任务，刷新 Treeview 显示。"""
        # 记住当前选中的任务 ID，刷新后尝试恢复选中
        selected_id = None
        sel = copy_tree.selection()
        if sel:
            selected_id = copy_tree.item(sel[0], "values")[0] if sel else None
            # 实际 iid 即为 task id
            selected_id = sel[0]

        copy_tree.delete(*copy_tree.get_children())
        for task in copy_mgr.get_tasks():
            ts = task.get("last_run_ts", 0)
            last_run_str = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S") if ts > 0 else "—"
            # find_contact_db 任务在来源路径列加 [动态] 前缀，便于区分
            src_display = ("[动态] " if task.get("find_contact_db") else "") + task.get("src", "")
            copy_tree.insert(
                "", tk.END, iid=task["id"],
                values=(
                    task.get("name", ""),
                    src_display,
                    task.get("dst", ""),
                    task.get("interval_min", 1),
                    task.get("start_time", "") or "—",
                    "启用" if task.get("enabled") else "禁用",
                    last_run_str,
                    task.get("last_result", ""),
                ),
            )
        # 恢复选中
        if selected_id and copy_tree.exists(selected_id):
            copy_tree.selection_set(selected_id)

    def _get_selected_task_id() -> str | None:
        """获取当前 Treeview 选中行的任务 ID（即 iid），无选中时返回 None。"""
        sel = copy_tree.selection()
        return sel[0] if sel else None

    # ── 新增 / 编辑对话框 ──
    def _open_task_dialog(task_id: str | None = None) -> None:
        """
        弹出任务编辑对话框。
        task_id 为 None 时表示新增，否则表示编辑已有任务。
        """
        # 如果是编辑，先获取现有任务数据
        existing: dict = {}
        if task_id:
            tasks = copy_mgr.get_tasks()
            found = next((t for t in tasks if t["id"] == task_id), None)
            if found is None:
                messagebox.showerror("错误", "未找到该任务，请刷新列表后重试。")
                return
            existing = found

        dialog = tk.Toplevel(root)
        dialog.title("编辑任务" if task_id else "新增定时拷贝任务")
        dialog.resizable(False, False)
        dialog.grab_set()  # 模态
        dialog.columnconfigure(1, weight=1)

        # ── 字段：任务名 ──
        ttk.Label(dialog, text="任务名：").grid(row=0, column=0, sticky="w", padx=(12, 4), pady=(12, 4))
        dlg_name_var = tk.StringVar(value=existing.get("name", ""))
        ttk.Entry(dialog, textvariable=dlg_name_var, width=42).grid(
            row=0, column=1, columnspan=2, sticky="we", padx=(0, 12), pady=(12, 4))

        # ── 字段：来源类型（固定路径 / contact.db 动态查找） ──
        ttk.Label(dialog, text="来源类型：").grid(row=1, column=0, sticky="w", padx=(12, 4), pady=(4, 2))
        # IntVar：0=固定路径，1=contact.db动态查找（BooleanVar + Radiobutton 在部分 tkinter 版本有兼容问题）
        _find_init = 1 if existing.get("find_contact_db") else 0
        dlg_find_contact_var = tk.IntVar(value=_find_init)
        type_frame = ttk.Frame(dialog)
        type_frame.grid(row=1, column=1, columnspan=2, sticky="w", padx=(0, 12), pady=(4, 2))
        rb_fixed = ttk.Radiobutton(type_frame, text="固定路径（文件）",
                                   variable=dlg_find_contact_var, value=0)
        rb_fixed.pack(side=tk.LEFT, padx=(0, 20))
        rb_contact = ttk.Radiobutton(type_frame, text="contact.db 动态查找（自动定位网络根目录中最新备份）",
                                     variable=dlg_find_contact_var, value=1)
        rb_contact.pack(side=tk.LEFT)

        # ── 字段：来源路径（标签随来源类型变化） ──
        dlg_src_label_var = tk.StringVar(value="网络根目录：" if _find_init else "来源路径：")
        lbl_src = ttk.Label(dialog, textvariable=dlg_src_label_var)
        lbl_src.grid(row=2, column=0, sticky="w", padx=(12, 4), pady=4)
        dlg_src_var = tk.StringVar(value=existing.get("src", ""))
        entry_src = ttk.Entry(dialog, textvariable=dlg_src_var, width=42)
        entry_src.grid(row=2, column=1, sticky="we", padx=(0, 4), pady=4)
        # 提示行：始终 grid，靠 StringVar 内容控制文字（空字符串不占视觉空间）
        dlg_src_hint_var = tk.StringVar(
            value="（填写 chatlog_backup 根目录，程序将自动找最新 contact.db）" if _find_init else ""
        )
        lbl_src_hint = ttk.Label(dialog, textvariable=dlg_src_hint_var,
                                 foreground="#888", font=(ui_family, ui_size - 1))
        lbl_src_hint.grid(row=3, column=1, columnspan=2, sticky="w", padx=(0, 12), pady=(0, 4))

        def _update_src_label(*_) -> None:
            """切换来源类型时更新标签文字和提示。"""
            if dlg_find_contact_var.get():
                dlg_src_label_var.set("网络根目录：")
                dlg_src_hint_var.set("（填写 chatlog_backup 根目录，程序将自动找最新 contact.db）")
            else:
                dlg_src_label_var.set("来源路径：")
                dlg_src_hint_var.set("")

        dlg_find_contact_var.trace_add("write", _update_src_label)

        def _browse_src() -> None:
            if dlg_find_contact_var.get():
                # contact.db 动态查找：仅选择根目录
                chosen = filedialog.askdirectory(title="选择 chatlog_backup 网络根目录",
                                                  initialdir=dlg_src_var.get().strip() or "")
            else:
                # 固定路径：仅允许选择来源文件
                chosen = filedialog.askopenfilename(
                    title="选择来源文件",
                    initialfile=os.path.basename(dlg_src_var.get()) if os.path.isfile(dlg_src_var.get()) else "",
                )
            if chosen:
                dlg_src_var.set(chosen)
        ttk.Button(dialog, text="浏览...", command=_browse_src).grid(
            row=2, column=2, padx=(0, 12), pady=4)

        # ── 字段：动态前缀（仅动态查找模式生效，支持多个） ──
        ttk.Label(dialog, text="动态前缀（多个用 ;）：").grid(row=4, column=0, sticky="w", padx=(12, 4), pady=4)
        dlg_prefixes_var = tk.StringVar(
            value=(existing.get("contact_prefixes", "") or _CONTACT_FOLDER_PREFIX)
        )
        ttk.Entry(dialog, textvariable=dlg_prefixes_var, width=42).grid(
            row=4, column=1, sticky="we", padx=(0, 4), pady=4
        )
        ttk.Label(
            dialog,
            text="（示例：wxid_a_6ded;wxid_b_6ded，仅动态查找模式使用）",
            foreground="#888",
            font=(ui_family, ui_size - 1),
        ).grid(row=4, column=2, sticky="w", padx=(0, 12), pady=4)

        # ── 字段：目标路径（支持多个） ──
        ttk.Label(dialog, text="目标路径（多个用 ;）：").grid(row=5, column=0, sticky="w", padx=(12, 4), pady=4)
        dlg_dst_var = tk.StringVar(value=existing.get("dst", ""))
        ttk.Entry(dialog, textvariable=dlg_dst_var, width=42).grid(
            row=5, column=1, sticky="we", padx=(0, 4), pady=4)
        def _browse_dst() -> None:
            chosen = filedialog.asksaveasfilename(
                title="选择目标文件路径",
                initialfile=os.path.basename(dlg_dst_var.get()) if dlg_dst_var.get() else "",
            )
            if not chosen:
                chosen = filedialog.askdirectory(title="选择目标目录")
            if chosen:
                dlg_dst_var.set(chosen)
        ttk.Button(dialog, text="浏览...", command=_browse_dst).grid(
            row=5, column=2, padx=(0, 12), pady=4)

        # ── 字段：间隔（分钟） ──
        ttk.Label(dialog, text="间隔（分钟）：").grid(row=6, column=0, sticky="w", padx=(12, 4), pady=4)
        dlg_interval_var = tk.StringVar(value=str(existing.get("interval_min", 60)))
        tk.Spinbox(dialog, from_=1, to=10080, textvariable=dlg_interval_var, width=8).grid(
            row=6, column=1, sticky="w", padx=(0, 4), pady=4)

        # ── 字段：首次触发时间（HH:MM，可选） ──
        ttk.Label(dialog, text="首次触发时间：").grid(row=7, column=0, sticky="w", padx=(12, 4), pady=4)
        dlg_start_time_var = tk.StringVar(value=str(existing.get("start_time", "") or ""))
        ttk.Entry(dialog, textvariable=dlg_start_time_var, width=8).grid(
            row=7, column=1, sticky="w", padx=(0, 4), pady=4)
        ttk.Label(
            dialog,
            text="（HH:MM，留空=按间隔；填了则首次到该时刻才执行；过点推迟到次日）",
            foreground="#888",
            font=(ui_family, ui_size - 1),
        ).grid(row=7, column=2, sticky="w", padx=(0, 12), pady=4)

        # ── 字段：是否启用 ──
        ttk.Label(dialog, text="启用状态：").grid(row=8, column=0, sticky="w", padx=(12, 4), pady=4)
        dlg_enabled_var = tk.BooleanVar(value=existing.get("enabled", True))
        ttk.Checkbutton(dialog, text="启用此任务", variable=dlg_enabled_var).grid(
            row=8, column=1, sticky="w", padx=(0, 4), pady=4)

        # ── 底部按钮 ──
        def _on_confirm() -> None:
            name_v           = dlg_name_var.get().strip()
            src_v            = dlg_src_var.get().strip()
            dst_v            = dlg_dst_var.get().strip()
            prefixes_v_raw   = dlg_prefixes_var.get().strip()
            enabled_v        = dlg_enabled_var.get()
            find_contact_v   = bool(dlg_find_contact_var.get())
            try:
                interval_v = max(1, int(dlg_interval_var.get().strip()))
            except ValueError:
                messagebox.showerror("输入错误", "间隔分钟数必须是正整数。", parent=dialog)
                return
            start_v_raw = dlg_start_time_var.get().strip()
            try:
                parse_hhmm(start_v_raw)  # 仅校验格式；空串视为合法
            except Exception as e:
                messagebox.showerror(
                    "输入错误",
                    f"首次触发时间需为 HH:MM 24 小时制（例如 14:00），或留空。\n{e}",
                    parent=dialog,
                )
                return
            if not name_v:
                messagebox.showerror("输入错误", "任务名不能为空。", parent=dialog)
                return
            if not src_v:
                label = "网络根目录" if find_contact_v else "来源路径"
                messagebox.showerror("输入错误", f"{label}不能为空。", parent=dialog)
                return
            if not dst_v:
                messagebox.showerror("输入错误", "目标路径不能为空。", parent=dialog)
                return
            dst_list = _split_multi_values(dst_v)
            if not dst_list:
                messagebox.showerror("输入错误", "目标路径不能为空。", parent=dialog)
                return
            prefixes_list = _split_multi_values(prefixes_v_raw)
            if find_contact_v and not prefixes_list:
                messagebox.showerror("输入错误", "动态查找模式下，动态前缀不能为空。", parent=dialog)
                return
            if find_contact_v and len(prefixes_list) > 1 and len(dst_list) > 1 and len(prefixes_list) != len(dst_list):
                messagebox.showerror(
                    "输入错误",
                    "多个前缀与多个目标路径数量不一致。\n请保持一一对应，或只保留一个前缀/一个目标。",
                    parent=dialog,
                )
                return
            dst_v = ";".join(dst_list)
            prefixes_v = ";".join(prefixes_list)

            if task_id:
                # 编辑
                copy_mgr.update_task(
                    task_id,
                    name=name_v, src=src_v, dst=dst_v,
                    interval_min=interval_v, enabled=enabled_v,
                    find_contact_db=find_contact_v,
                    start_time=start_v_raw,
                    contact_prefixes=prefixes_v,
                )
                INC.logger.info(f"[定时拷贝] 任务已更新：{name_v}")
            else:
                # 新增
                copy_mgr.add_task(
                    name_v, src_v, dst_v, interval_v, enabled_v, find_contact_v,
                    start_time=start_v_raw,
                    contact_prefixes=prefixes_v,
                )

            dialog.destroy()
            _refresh_copy_tree()

        btn_row = ttk.Frame(dialog)
        btn_row.grid(row=9, column=0, columnspan=3, pady=(8, 12))
        ttk.Button(btn_row, text="确定", command=_on_confirm).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="取消", command=dialog.destroy).pack(side=tk.LEFT)

        # 居中显示对话框
        dialog.update_idletasks()
        x = root.winfo_x() + (root.winfo_width()  - dialog.winfo_reqwidth())  // 2
        y = root.winfo_y() + (root.winfo_height() - dialog.winfo_reqheight()) // 2
        dialog.geometry(f"+{x}+{y}")

    # ── 按钮事件绑定 ──
    def _on_copy_add() -> None:
        _open_task_dialog(task_id=None)

    def _on_copy_edit() -> None:
        tid = _get_selected_task_id()
        if not tid:
            messagebox.showinfo("提示", "请先在列表中选择一条任务。")
            return
        _open_task_dialog(task_id=tid)

    def _on_copy_del() -> None:
        tid = _get_selected_task_id()
        if not tid:
            messagebox.showinfo("提示", "请先在列表中选择一条任务。")
            return
        tasks = copy_mgr.get_tasks()
        task_name = next((t["name"] for t in tasks if t["id"] == tid), tid)
        if messagebox.askyesno("确认删除", f"确定要删除任务「{task_name}」吗？"):
            copy_mgr.remove_task(tid)
            _refresh_copy_tree()

    def _on_copy_toggle() -> None:
        tid = _get_selected_task_id()
        if not tid:
            messagebox.showinfo("提示", "请先在列表中选择一条任务。")
            return
        tasks = copy_mgr.get_tasks()
        task = next((t for t in tasks if t["id"] == tid), None)
        if task is None:
            return
        new_state = not task["enabled"]
        copy_mgr.set_enabled(tid, new_state)
        INC.logger.info(f"[定时拷贝] 任务「{task['name']}」已{'启用' if new_state else '禁用'}")
        _refresh_copy_tree()

    def _on_copy_run_now() -> None:
        tid = _get_selected_task_id()
        if not tid:
            messagebox.showinfo("提示", "请先在列表中选择一条任务。")
            return
        tasks = copy_mgr.get_tasks()
        task_name = next((t["name"] for t in tasks if t["id"] == tid), tid)
        copy_mgr.run_task_now(tid)
        INC.logger.info(f"[定时拷贝] 手动触发任务：{task_name}")
        # 延迟 1.5 秒刷新一次，等执行结果回写
        root.after(1500, _refresh_copy_tree)

    btn_copy_add.configure(command=_on_copy_add)
    btn_copy_edit.configure(command=_on_copy_edit)
    btn_copy_del.configure(command=_on_copy_del)
    btn_copy_toggle.configure(command=_on_copy_toggle)
    btn_copy_run.configure(command=_on_copy_run_now)
    btn_copy_refresh.configure(command=_refresh_copy_tree)

    # ── 任务详情查看对话框（双击行触发） ──
    def _show_task_detail(task_id: str) -> None:
        """以只读弹窗展示任务全部字段，便于查看被列宽截断的长路径与结果。"""
        tasks = copy_mgr.get_tasks()
        task = next((t for t in tasks if t["id"] == task_id), None)
        if task is None:
            return

        win = tk.Toplevel(root)
        win.title(f"任务详情 - {task.get('name', '')}")
        win.geometry("760x460")
        win.transient(root)

        ts = task.get("last_run_ts", 0)
        last_run_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts > 0 else "—"
        src_label = "网络根目录（动态查找）" if task.get("find_contact_db") else "来源路径"

        rows = [
            ("任务 ID",   task.get("id", "")),
            ("任务名",    task.get("name", "")),
            ("来源类型",  "contact.db 动态查找" if task.get("find_contact_db") else "固定路径"),
            (src_label,   task.get("src", "")),
            ("动态前缀",  task.get("contact_prefixes", "") or _CONTACT_FOLDER_PREFIX),
            ("目标路径",  task.get("dst", "")),
            ("间隔(分钟)", str(task.get("interval_min", ""))),
            ("启用状态",  "启用" if task.get("enabled") else "禁用"),
            ("上次运行",  last_run_str),
            ("上次结果",  task.get("last_result", "")),
        ]

        frame = ttk.Frame(win, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        for i, (k, v) in enumerate(rows):
            ttk.Label(frame, text=k + "：", font=(ui_family, ui_size, "bold")).grid(
                row=i, column=0, sticky="nw", pady=(0, 6))
            # 用 Entry 只读：方便用户复制长路径
            entry = ttk.Entry(frame, font=(mono_family, ui_size))
            entry.insert(0, v)
            entry.configure(state="readonly")
            entry.grid(row=i, column=1, sticky="we", pady=(0, 6), padx=(4, 0))

        ttk.Button(frame, text="关闭", command=win.destroy).grid(
            row=len(rows), column=0, columnspan=2, pady=(12, 0))

    def _on_copy_view_detail() -> None:
        tid = _get_selected_task_id()
        if tid:
            _show_task_detail(tid)

    # ── 单元格 Tooltip（鼠标悬停在任意行上显示完整路径与字段） ──
    _tooltip_state = {"win": None, "row": None}

    def _hide_tooltip() -> None:
        w = _tooltip_state.get("win")
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass
        _tooltip_state["win"] = None
        _tooltip_state["row"] = None

    def _show_tooltip_for_row(task_id: str, x: int, y: int) -> None:
        """在 (x, y) 屏幕坐标处显示该任务的完整信息浮窗。"""
        tasks = copy_mgr.get_tasks()
        task = next((t for t in tasks if t["id"] == task_id), None)
        if task is None:
            return

        ts = task.get("last_run_ts", 0)
        last_run_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts > 0 else "—"
        src_prefix = "[动态] " if task.get("find_contact_db") else ""
        text = (
            f"任务名：{task.get('name', '')}\n"
            f"来源：{src_prefix}{task.get('src', '')}\n"
            f"目标：{task.get('dst', '')}\n"
            f"间隔：{task.get('interval_min', '')} 分钟    "
            f"状态：{'启用' if task.get('enabled') else '禁用'}\n"
            f"上次运行：{last_run_str}\n"
            f"上次结果：{task.get('last_result', '')}"
        )

        _hide_tooltip()
        tip = tk.Toplevel(root)
        tip.wm_overrideredirect(True)  # 无边框
        tip.attributes("-topmost", True)
        tip.geometry(f"+{x + 16}+{y + 16}")
        lbl = tk.Label(
            tip, text=text, justify="left",
            background="#FFFFE0", relief="solid", borderwidth=1,
            font=(ui_family, ui_size - 1), padx=8, pady=6,
        )
        lbl.pack()
        _tooltip_state["win"] = tip

    def _on_tree_motion(event: tk.Event) -> None:
        """鼠标移动时：识别所在行，进入新行则显示该行 tooltip。"""
        row_id = copy_tree.identify_row(event.y)
        if not row_id:
            _hide_tooltip()
            return
        if row_id == _tooltip_state.get("row"):
            return  # 还在同一行，不重复弹窗
        _tooltip_state["row"] = row_id
        _show_tooltip_for_row(row_id, event.x_root, event.y_root)

    copy_tree.bind("<Motion>", _on_tree_motion)
    copy_tree.bind("<Leave>", lambda _e: _hide_tooltip())

    # 双击 Treeview 行打开「任务详情」（只读查看，便于复制完整路径）
    copy_tree.bind("<Double-1>", lambda e: _on_copy_view_detail())

    # 初始加载任务列表
    _refresh_copy_tree()

    # ─── Tab 4：飞书全量同步 ───
    tab_feishu_outer = ttk.Frame(nb)
    nb.add(tab_feishu_outer, text="飞书全量同步")
    tab_feishu = _make_scrollable(tab_feishu_outer)
    tab_feishu.columnconfigure(1, weight=1)

    feishu_status_var = tk.StringVar(value="状态：空闲")
    feishu_next_var = tk.StringVar(value="下次：未启动")
    feishu_last_var = tk.StringVar(value="上次：—")
    feishu_stats_var = tk.StringVar(value="运行 0 次 / 成功 0 次 / 失败 0 次")

    feishu_status_row = ttk.Frame(tab_feishu)
    feishu_status_row.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
    ttk.Label(
        feishu_status_row,
        textvariable=feishu_status_var,
        font=(ui_family, ui_size + 1, "bold"),
    ).grid(row=0, column=0, sticky="w", padx=(0, 16))
    ttk.Label(feishu_status_row, textvariable=feishu_next_var).grid(
        row=0, column=1, sticky="w", padx=(0, 16)
    )
    ttk.Label(feishu_status_row, textvariable=feishu_last_var).grid(
        row=0, column=2, sticky="w", padx=(0, 16)
    )
    ttk.Label(feishu_status_row, textvariable=feishu_stats_var).grid(
        row=0, column=3, sticky="w"
    )

    ttk.Label(tab_feishu, text="同步脚本路径：").grid(row=1, column=0, sticky="w", pady=4)
    feishu_script_var = tk.StringVar(
        value=feishu_sync_cfg.get("SCRIPT_PATH", FEISHU_SYNC_DEFAULT_SCRIPT)
    )
    ttk.Entry(tab_feishu, textvariable=feishu_script_var, font=(ui_family, ui_size)).grid(
        row=1, column=1, sticky="we", padx=6, pady=4
    )
    btn_feishu_browse = ttk.Button(tab_feishu, text="浏览...")
    btn_feishu_browse.grid(row=1, column=2, padx=4, pady=4)

    ttk.Label(tab_feishu, text="定时间隔（分钟）：").grid(row=2, column=0, sticky="w", pady=4)
    feishu_interval_var = tk.StringVar(
        value=str(feishu_sync_cfg.get("INTERVAL_MIN", 60))
    )
    feishu_interval_frame = ttk.Frame(tab_feishu)
    feishu_interval_frame.grid(row=2, column=1, columnspan=2, sticky="w", padx=6, pady=4)
    tk.Spinbox(
        feishu_interval_frame,
        from_=1,
        to=1440,
        textvariable=feishu_interval_var,
        width=6,
        font=(ui_family, ui_size),
    ).pack(side=tk.LEFT)
    ttk.Label(feishu_interval_frame, text="分钟检查并执行一次").pack(side=tk.LEFT, padx=(6, 0))

    # 首次触发时刻（HH:MM）；留空=启动后等一个间隔；填了则等到该时刻才执行第一次
    ttk.Label(tab_feishu, text="首次触发时间：").grid(row=3, column=0, sticky="w", pady=4)
    feishu_start_var = tk.StringVar(value=str(feishu_sync_cfg.get("START_TIME", "") or ""))
    feishu_start_frame = ttk.Frame(tab_feishu)
    feishu_start_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=6, pady=4)
    ttk.Entry(
        feishu_start_frame, textvariable=feishu_start_var,
        width=8, font=(ui_family, ui_size),
    ).pack(side=tk.LEFT)
    ttk.Label(
        feishu_start_frame,
        text="（HH:MM，留空=按间隔；如 14:00 = 等到今天14点；过点则推迟到次日）",
        foreground="#7f8c8d",
    ).pack(side=tk.LEFT, padx=(6, 0))

    ttk.Label(tab_feishu, text="执行模式：").grid(row=4, column=0, sticky="w", pady=4)
    feishu_dry_run_var = tk.BooleanVar(
        value=bool(feishu_sync_cfg.get("DRY_RUN", True))
    )
    ttk.Checkbutton(
        tab_feishu,
        text="Dry Run（只统计，不清空、不导入，默认建议开启）",
        variable=feishu_dry_run_var,
    ).grid(row=4, column=1, columnspan=2, sticky="w", padx=6, pady=4)

    feishu_desc = ttk.Label(
        tab_feishu,
        text=(
            "说明：真实执行时只清空飞书表内所有记录，然后重新导入全量数据；"
            "不会删除数据表、字段或视图。"
        ),
        foreground="#7f8c8d",
        wraplength=1000,
        justify="left",
    )
    feishu_desc.grid(row=5, column=0, columnspan=3, sticky="we", pady=(8, 4))

    feishu_btn_row = ttk.Frame(tab_feishu)
    feishu_btn_row.grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 0))
    btn_feishu_save = ttk.Button(feishu_btn_row, text="保存配置")
    btn_feishu_run = ttk.Button(feishu_btn_row, text="立即执行一次")
    btn_feishu_start = ttk.Button(feishu_btn_row, text="启动定时")
    btn_feishu_stop = ttk.Button(feishu_btn_row, text="停止定时")
    btn_feishu_open_mapping = ttk.Button(feishu_btn_row, text="打开字段映射配置")
    for btn in (btn_feishu_save, btn_feishu_run, btn_feishu_start, btn_feishu_stop, btn_feishu_open_mapping):
        btn.pack(side=tk.LEFT, padx=(0, 8))

    # 加入全局队列复选框（同 Tab2 注释）
    feishu_use_queue_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        feishu_btn_row,
        text="加入全局队列",
        variable=feishu_use_queue_var,
    ).pack(side=tk.LEFT, padx=(16, 0))

    # ── 字段映射说明（紧贴按钮行下方） ──
    feishu_mapping_hint = ttk.Label(
        tab_feishu,
        text=(
            "字段映射说明：飞书同步脚本同目录下的 field_mapping.json 控制"
            "「哪些字段从导出数据写入飞书表 / 写入哪个飞书字段名」。"
            "首次执行同步后会自动生成默认配置；点击右侧「打开字段映射配置」"
            "可直接打开该 JSON 文件编辑。修改后下次同步立即生效。"
        ),
        foreground="#7f8c8d",
        wraplength=1000,
        justify="left",
    )
    feishu_mapping_hint.grid(row=7, column=0, columnspan=3, sticky="we", pady=(8, 0))

    def do_feishu_open_mapping() -> None:
        """打开飞书字段映射 JSON 文件；不存在时根据脚本里的默认值创建一份。"""
        # 解析路径：与脚本同级目录下的 field_mapping.json
        script_v = (feishu_script_var.get() or "").strip()
        if not script_v:
            INC.logger.warning("[飞书同步] 请先在上方「同步脚本路径」中填入脚本位置")
            return
        script_path = Path(script_v)
        if not script_path.exists():
            INC.logger.warning(f"[飞书同步] 脚本不存在：{script_path}")
            return
        mapping_path = script_path.parent / "field_mapping.json"

        # 文件不存在时：动态加载脚本拿默认值后写出，避免先跑一次同步才能看到模板
        if not mapping_path.exists():
            try:
                _mod = load_feishu_sync_module(script_v)
                # 兼容不同版本脚本：
                # - 新版可能导出 DEFAULT_FIELD_MAPPING
                # - 老版本通常只保留 FIELD_MAP
                default_mapping = getattr(_mod, "DEFAULT_FIELD_MAPPING", None)
                if not isinstance(default_mapping, dict):
                    default_mapping = getattr(_mod, "FIELD_MAP", None)
                if not isinstance(default_mapping, dict):
                    raise RuntimeError("脚本未导出 DEFAULT_FIELD_MAPPING/FIELD_MAP（请确认脚本版本）")
                mapping_path.write_text(
                    json.dumps(default_mapping, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                INC.logger.info(f"[飞书同步] 已生成默认字段映射模板：{mapping_path}")
            except Exception as exc:
                INC.logger.error(f"[飞书同步] 生成字段映射模板失败：{exc}")
                return

        # 用系统默认编辑器打开（Windows 上等价于双击该文件）
        try:
            os.startfile(str(mapping_path))  # type: ignore[attr-defined]
            INC.logger.info(f"[飞书同步] 已打开字段映射文件：{mapping_path}")
        except Exception as exc:
            INC.logger.error(f"[飞书同步] 打开字段映射文件失败：{exc}")

    # ─── Tab 5：用户上传（飞书 → 秒哒 import-users） ───
    user_upload_cfg = load_user_upload_config()

    tab_uu_outer = ttk.Frame(nb)
    nb.add(tab_uu_outer, text="用户上传")
    tab_uu = _make_scrollable(tab_uu_outer)
    tab_uu.columnconfigure(1, weight=1)

    uu_status_var = tk.StringVar(value="状态：空闲")
    uu_next_var   = tk.StringVar(value="下次：未启动")
    uu_last_var   = tk.StringVar(value="上次:—")
    uu_stats_var  = tk.StringVar(value="运行 0 次 / 成功 0 次 / 失败 0 次")

    uu_status_row = ttk.Frame(tab_uu)
    uu_status_row.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
    ttk.Label(
        uu_status_row,
        textvariable=uu_status_var,
        font=(ui_family, ui_size + 1, "bold"),
    ).grid(row=0, column=0, sticky="w", padx=(0, 16))
    ttk.Label(uu_status_row, textvariable=uu_next_var).grid(row=0, column=1, sticky="w", padx=(0, 16))
    ttk.Label(uu_status_row, textvariable=uu_last_var).grid(row=0, column=2, sticky="w", padx=(0, 16))
    ttk.Label(uu_status_row, textvariable=uu_stats_var).grid(row=0, column=3, sticky="w")

    # 同步脚本路径
    ttk.Label(tab_uu, text="同步脚本路径：").grid(row=1, column=0, sticky="w", pady=4)
    uu_script_var = tk.StringVar(
        value=user_upload_cfg.get("SCRIPT_PATH", USER_UPLOAD_DEFAULT_SCRIPT)
    )
    ttk.Entry(tab_uu, textvariable=uu_script_var, font=(ui_family, ui_size)).grid(
        row=1, column=1, sticky="we", padx=6, pady=4
    )
    btn_uu_browse = ttk.Button(tab_uu, text="浏览...")
    btn_uu_browse.grid(row=1, column=2, padx=4, pady=4)

    # 飞书视图 ID
    ttk.Label(tab_uu, text="飞书视图 ID：").grid(row=2, column=0, sticky="w", pady=4)
    uu_view_var = tk.StringVar(value=user_upload_cfg.get("VIEW_ID", "vew7GtEotv"))
    ttk.Entry(tab_uu, textvariable=uu_view_var, font=(ui_family, ui_size)).grid(
        row=2, column=1, sticky="we", padx=6, pady=4
    )
    ttk.Label(
        tab_uu,
        text="（留空=读整张表；默认 vew7GtEotv）",
        foreground="#7f8c8d",
    ).grid(row=2, column=2, sticky="w", padx=4, pady=4)

    # 模式
    ttk.Label(tab_uu, text="执行模式：").grid(row=3, column=0, sticky="w", pady=4)
    uu_mode_var = tk.StringVar(value=user_upload_cfg.get("MODE", "full"))
    uu_mode_frame = ttk.Frame(tab_uu)
    uu_mode_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=6, pady=4)
    for _val, _label in [
        ("full",        "拉取飞书 + 写 JSON + 上传秒哒（默认）"),
        ("json_only",   "仅生成本地 JSON，不上传"),
        ("upload_only", "跳过飞书拉取，仅上传现有 JSON"),
    ]:
        ttk.Radiobutton(
            uu_mode_frame, text=_label, variable=uu_mode_var, value=_val
        ).pack(side=tk.LEFT, padx=(0, 12))

    # Dry run
    ttk.Label(tab_uu, text="上传环节：").grid(row=4, column=0, sticky="w", pady=4)
    uu_dry_var = tk.BooleanVar(value=bool(user_upload_cfg.get("DRY_RUN", False)))
    ttk.Checkbutton(
        tab_uu,
        text="Dry Run（仅打印批次，不真发请求；建议首次启用确认无误后再关闭）",
        variable=uu_dry_var,
    ).grid(row=4, column=1, columnspan=2, sticky="w", padx=6, pady=4)

    # 间隔
    ttk.Label(tab_uu, text="定时间隔（分钟）：").grid(row=5, column=0, sticky="w", pady=4)
    uu_interval_var = tk.StringVar(value=str(user_upload_cfg.get("INTERVAL_MIN", 60)))
    uu_interval_frame = ttk.Frame(tab_uu)
    uu_interval_frame.grid(row=5, column=1, sticky="w", padx=6, pady=4)
    tk.Spinbox(
        uu_interval_frame,
        from_=1,
        to=1440,
        textvariable=uu_interval_var,
        width=6,
        font=(ui_family, ui_size),
    ).pack(side=tk.LEFT)
    ttk.Label(uu_interval_frame, text="分钟检查并执行一次").pack(side=tk.LEFT, padx=(6, 0))

    # 首次触发时刻（HH:MM）
    ttk.Label(tab_uu, text="首次触发时间：").grid(row=6, column=0, sticky="w", pady=4)
    uu_start_var = tk.StringVar(value=str(user_upload_cfg.get("START_TIME", "") or ""))
    uu_start_frame = ttk.Frame(tab_uu)
    uu_start_frame.grid(row=6, column=1, sticky="w", padx=6, pady=4)
    ttk.Entry(
        uu_start_frame,
        textvariable=uu_start_var,
        width=8,
        font=(ui_family, ui_size),
    ).pack(side=tk.LEFT)
    ttk.Label(
        uu_start_frame,
        text="（HH:MM，留空=启动后立即按间隔；如 14:00 = 等到今天14点首次触发，过点则推迟到次日）",
        foreground="#7f8c8d",
    ).pack(side=tk.LEFT, padx=(6, 0))

    uu_desc = ttk.Label(
        tab_uu,
        text=(
            "说明：从飞书多维表格读取「编号 / 密码 / 合伙宝妈微信号」，"
            "生成本地 上传用户结构.json，并按 200 条/批 调用秒哒 import-users 接口。"
            "已存在账号会被秒哒计入 skipped，不会覆盖原有数据。"
        ),
        foreground="#7f8c8d",
        wraplength=1000,
        justify="left",
    )
    uu_desc.grid(row=7, column=0, columnspan=3, sticky="we", pady=(8, 4))

    uu_btn_row = ttk.Frame(tab_uu)
    uu_btn_row.grid(row=8, column=0, columnspan=3, sticky="w", pady=(12, 0))
    btn_uu_save  = ttk.Button(uu_btn_row, text="保存配置")
    btn_uu_run   = ttk.Button(uu_btn_row, text="立即执行一次")
    btn_uu_start = ttk.Button(uu_btn_row, text="启动定时")
    btn_uu_stop  = ttk.Button(uu_btn_row, text="停止定时")
    for _btn in (btn_uu_save, btn_uu_run, btn_uu_start, btn_uu_stop):
        _btn.pack(side=tk.LEFT, padx=(0, 8))

    # 加入全局队列复选框（同 Tab2 注释）
    uu_use_queue_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        uu_btn_row,
        text="加入全局队列",
        variable=uu_use_queue_var,
    ).pack(side=tk.LEFT, padx=(16, 0))

    # ─── Tab 6：扩展功能（自定义插件）─────────────────────────────
    # 让用户不重新打包 exe 就能加新功能：
    #   - 每个插件 = 一份 custom_plugins.json 配置 + 一个外置 Python 脚本
    #   - 脚本只要暴露一个 run(log, **params) -> bool 即可
    #   - 「立即执行」默认进全局队列，不会与其它正在执行的任务冲突
    # 详细规则见 custom_plugins.py 顶部注释
    tab_ext_outer = ttk.Frame(nb)
    nb.add(tab_ext_outer, text="扩展功能")
    tab_ext = _make_scrollable(tab_ext_outer)
    tab_ext.columnconfigure(0, weight=1)

    # 顶部说明 + 操作按钮
    ext_top = ttk.Frame(tab_ext)
    ext_top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
    ttk.Label(
        ext_top,
        text="扩展功能：可加任意外置 Python 脚本作为插件，立即执行默认进全局队列。",
        foreground="#444444",
    ).pack(side=tk.LEFT)

    ext_btn_row = ttk.Frame(tab_ext)
    ext_btn_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
    btn_ext_add     = ttk.Button(ext_btn_row, text="+ 新增功能")
    btn_ext_refresh = ttk.Button(ext_btn_row, text="刷新配置")
    btn_ext_open    = ttk.Button(ext_btn_row, text="打开配置文件")
    for _b in (btn_ext_add, btn_ext_refresh, btn_ext_open):
        _b.pack(side=tk.LEFT, padx=(0, 8))

    # 插件卡片容器（所有卡片都 grid 在 ext_cards 里，刷新时整体清空重建）
    ext_cards = ttk.Frame(tab_ext)
    ext_cards.grid(row=2, column=0, sticky="nsew")
    ext_cards.columnconfigure(0, weight=1)

    # 每个插件运行状态（运行中 / 上次成功 / 上次失败 + 时间戳）
    # 由「立即执行」入队 + 任务完成回调维护，UI 刷新时按需读取
    ext_state: dict = {}  # plugin_id -> {"running": bool, "last_ok": bool|None, "last_ts": float}
    ext_card_widgets: dict = {}  # plugin_id -> {"status_var": tk.StringVar}

    def _ensure_ext_state(pid: str) -> dict:
        return ext_state.setdefault(
            pid, {"running": False, "last_ok": None, "last_ts": 0.0}
        )

    def _format_ext_status(pid: str) -> str:
        st = _ensure_ext_state(pid)
        if st["running"]:
            return "状态：执行中"
        if st["last_ts"] <= 0:
            return "状态：空闲"
        ts = datetime.fromtimestamp(st["last_ts"]).strftime("%m-%d %H:%M:%S")
        result = "成功" if st["last_ok"] else "失败"
        return f"状态：空闲   上次：{result} ({ts})"

    def _refresh_ext_status_labels() -> None:
        for pid, w in list(ext_card_widgets.items()):
            try:
                w["status_var"].set(_format_ext_status(pid))
            except Exception:
                pass

    def _run_plugin_now(plugin: dict) -> None:
        """点「立即执行」：always 走全局队列，永远不会"已在跑就 skip"丢失触发。"""
        pid = plugin.get("id", "")
        name = plugin.get("name") or "未命名功能"
        callable_ = cp_mod.make_callable(plugin)

        st = _ensure_ext_state(pid)
        st["running"] = True
        _refresh_ext_status_labels()

        def _on_done(ok: bool, exc) -> None:
            st["running"] = False
            st["last_ok"] = bool(ok)
            st["last_ts"] = time.time()
            # 切回 UI 线程刷新文本
            root.after(0, _refresh_ext_status_labels)

        GLOBAL_RUNNER.submit(
            name=f"扩展功能: {name}",
            callable_=callable_,
            log=INC.logger,
            on_done=_on_done,
        )

    def _open_plugin_editor(plugin: dict | None) -> None:
        """新增 / 编辑插件的弹窗。plugin=None 表示新增。
        参数 params 用一段 JSON 文本编辑（最灵活，又不需要复杂 UI）。
        """
        is_new = plugin is None
        if is_new:
            plugin = {
                "id": cp_mod.Plugin.new_id(),
                "name": "",
                "script": "",
                "entry": "run",
                "params": {},
                "enabled": True,
                "auto_trigger": {"enabled": False, "interval_min": 60, "start_time": ""},
            }

        dlg = tk.Toplevel(root)
        dlg.title("新增扩展功能" if is_new else "编辑扩展功能")
        dlg.transient(root)
        dlg.grab_set()
        dlg.geometry("680x460")

        frm = ttk.Frame(dlg, padding=(12, 12))
        frm.pack(fill=tk.BOTH, expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="功能名称：").grid(row=0, column=0, sticky="w", pady=4)
        name_var = tk.StringVar(value=plugin.get("name", ""))
        ttk.Entry(frm, textvariable=name_var).grid(row=0, column=1, columnspan=2, sticky="we", padx=6)

        ttk.Label(frm, text="脚本路径：").grid(row=1, column=0, sticky="w", pady=4)
        script_var = tk.StringVar(value=plugin.get("script", ""))
        ttk.Entry(frm, textvariable=script_var).grid(row=1, column=1, sticky="we", padx=6)

        def _browse_script() -> None:
            chosen = filedialog.askopenfilename(
                title="选择插件 Python 脚本",
                initialfile=script_var.get().strip() or "",
                filetypes=[("Python 文件", "*.py"), ("所有文件", "*.*")],
            )
            if chosen:
                script_var.set(chosen)

        ttk.Button(frm, text="浏览...", command=_browse_script).grid(row=1, column=2, padx=4)

        ttk.Label(frm, text="入口函数：").grid(row=2, column=0, sticky="w", pady=4)
        entry_var = tk.StringVar(value=plugin.get("entry", "run"))
        ttk.Entry(frm, textvariable=entry_var).grid(row=2, column=1, columnspan=2, sticky="we", padx=6)

        ttk.Label(frm, text="参数 (JSON):").grid(row=3, column=0, sticky="nw", pady=4)
        params_text = tk.Text(frm, height=10, font=(mono_family, ui_size))
        params_text.grid(row=3, column=1, columnspan=2, sticky="nsew", padx=6, pady=(4, 4))
        frm.rowconfigure(3, weight=1)
        try:
            params_text.insert("1.0", json.dumps(plugin.get("params") or {}, ensure_ascii=False, indent=2))
        except Exception:
            params_text.insert("1.0", "{}")

        enabled_var = tk.BooleanVar(value=bool(plugin.get("enabled", True)))
        ttk.Checkbutton(frm, text="启用（取消勾选后在卡片里隐藏「立即执行」按钮）",
                        variable=enabled_var).grid(row=4, column=1, columnspan=2, sticky="w", padx=6, pady=(4, 6))

        hint = ttk.Label(
            frm,
            text=(
                "提示：脚本须暴露函数 def run(log, **params): ...。\n"
                "参数 JSON 里的键值对会作为关键字参数传给脚本入口。\n"
                "保存后立刻生效；修改脚本本身不用动配置，立即执行就会重新加载。"
            ),
            foreground="#666666",
            wraplength=620,
            justify="left",
        )
        hint.grid(row=5, column=0, columnspan=3, sticky="we", pady=(4, 4))

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=6, column=0, columnspan=3, sticky="e", pady=(8, 0))

        def _do_save() -> None:
            name_v = name_var.get().strip()
            script_v = script_var.get().strip()
            entry_v = entry_var.get().strip() or "run"
            params_raw = params_text.get("1.0", "end").strip() or "{}"
            if not name_v:
                messagebox.showwarning("名称必填", "请填写功能名称。")
                return
            if not script_v:
                messagebox.showwarning("脚本必填", "请填写或浏览选择脚本路径。")
                return
            try:
                params_v = json.loads(params_raw)
                if not isinstance(params_v, dict):
                    raise ValueError("参数必须是 JSON 对象 {...}")
            except Exception as e:
                messagebox.showwarning("参数 JSON 无效", f"请修正参数 JSON：\n{e}")
                return

            plugin["name"] = name_v
            plugin["script"] = script_v
            plugin["entry"] = entry_v
            plugin["params"] = params_v
            plugin["enabled"] = bool(enabled_var.get())

            if is_new:
                custom_plugins_cfg.setdefault("plugins", []).append(plugin)
            cp_mod.save_config(CUSTOM_PLUGINS_CONFIG_PATH, custom_plugins_cfg)
            INC.logger.info(f"[扩展功能] 已保存：{name_v}")
            dlg.destroy()
            _rebuild_ext_cards()

        ttk.Button(btn_row, text="取消", command=dlg.destroy).pack(side=tk.RIGHT)
        ttk.Button(btn_row, text="保存", command=_do_save).pack(side=tk.RIGHT, padx=(0, 8))

    def _delete_plugin(plugin: dict) -> None:
        if not messagebox.askyesno("确认删除", f"删除「{plugin.get('name', '')}」吗？"):
            return
        plugins = custom_plugins_cfg.get("plugins") or []
        custom_plugins_cfg["plugins"] = [p for p in plugins if p.get("id") != plugin.get("id")]
        cp_mod.save_config(CUSTOM_PLUGINS_CONFIG_PATH, custom_plugins_cfg)
        INC.logger.info(f"[扩展功能] 已删除：{plugin.get('name', '')}")
        _rebuild_ext_cards()

    def _build_card(parent: ttk.Frame, plugin: dict, row: int) -> None:
        pid = plugin.get("id", "")
        name = plugin.get("name", "未命名")
        script = plugin.get("script", "")
        enabled = bool(plugin.get("enabled", True))

        card = ttk.LabelFrame(parent, text=name, padding=(10, 8))
        card.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        card.columnconfigure(0, weight=1)

        info = ttk.Frame(card)
        info.grid(row=0, column=0, sticky="ew")
        info.columnconfigure(0, weight=1)

        status_var = tk.StringVar(value=_format_ext_status(pid))
        ext_card_widgets[pid] = {"status_var": status_var}

        ttk.Label(info, text=f"脚本：{script}").grid(row=0, column=0, sticky="w")
        ttk.Label(info, textvariable=status_var, foreground="#1f4f8c").grid(
            row=0, column=1, sticky="e", padx=(12, 0)
        )

        params = plugin.get("params") or {}
        if params:
            ttk.Label(
                info,
                text=f"参数：{json.dumps(params, ensure_ascii=False)}",
                foreground="#666666",
                wraplength=900,
                justify="left",
            ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        btns = ttk.Frame(card)
        btns.grid(row=1, column=0, sticky="w", pady=(8, 0))
        if enabled:
            ttk.Button(btns, text="立即执行（入队）",
                       command=lambda p=plugin: _run_plugin_now(p)).pack(side=tk.LEFT, padx=(0, 6))
        else:
            ttk.Label(btns, text="（已禁用）", foreground="#999999").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btns, text="编辑",
                   command=lambda p=plugin: _open_plugin_editor(p)).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btns, text="删除",
                   command=lambda p=plugin: _delete_plugin(p)).pack(side=tk.LEFT, padx=(0, 6))

    def _rebuild_ext_cards() -> None:
        """从 custom_plugins_cfg 重建所有卡片；插件配置变化后调用。"""
        # 清空旧卡片
        for child in list(ext_cards.winfo_children()):
            child.destroy()
        ext_card_widgets.clear()

        plugins = custom_plugins_cfg.get("plugins") or []
        if not plugins:
            ttk.Label(
                ext_cards,
                text="还没有任何扩展功能。点击「+ 新增功能」开始，或在 custom_plugins.json 里手工添加。",
                foreground="#999999",
            ).grid(row=0, column=0, sticky="w", pady=(8, 0))
            return

        for i, plugin in enumerate(plugins):
            _build_card(ext_cards, plugin, i)

    def do_ext_refresh() -> None:
        """从磁盘重新加载配置文件，刷新卡片。"""
        nonlocal_cfg = cp_mod.load_config(CUSTOM_PLUGINS_CONFIG_PATH)
        custom_plugins_cfg.clear()
        custom_plugins_cfg.update(nonlocal_cfg)
        _rebuild_ext_cards()
        INC.logger.info(f"[扩展功能] 已刷新配置，当前 {len(nonlocal_cfg.get('plugins') or [])} 个插件")

    def do_ext_open_config() -> None:
        """用系统默认程序打开配置文件，便于手工编辑。"""
        try:
            os.startfile(str(CUSTOM_PLUGINS_CONFIG_PATH))
        except Exception as e:
            messagebox.showwarning("打开失败", f"无法打开 {CUSTOM_PLUGINS_CONFIG_PATH}\n{e}")

    btn_ext_add.configure(command=lambda: _open_plugin_editor(None))
    btn_ext_refresh.configure(command=do_ext_refresh)
    btn_ext_open.configure(command=do_ext_open_config)

    # 启动时先构建一次卡片
    _rebuild_ext_cards()

    # ─── 查询模块预留插槽（待重写） ───
    # 旧实现（基于 spec_from_file_location 动态加载 db_viewer.py 并嵌入 DatabaseViewer）
    # 已于本次重构中整体移除。新版查询面板将以原生 ttk.Frame 子模块形式重新接入，
    # 届时在此处插入 nb.add(...) 即可，不再依赖外部文件路径配置。

    # ─── 底部共享日志区（放入 PanedWindow 下半部分，weight=1，初始占小份额） ───
    bottom = ttk.Frame(main_paned)
    main_paned.add(bottom, weight=1)

    log_head = ttk.Frame(bottom)
    log_head.pack(fill=tk.X)
    ttk.Label(log_head, text="运行日志", font=(ui_family, ui_size, "bold")).pack(side=tk.LEFT)
    btn_clear = ttk.Button(log_head, text="清空日志")
    btn_clear.pack(side=tk.RIGHT)

    log_text = scrolledtext.ScrolledText(
        bottom, wrap=tk.NONE, state="disabled", font=(mono_family, ui_size),
        height=8  # 固定 8 行高，避免默认 24 行吃掉 Notebook 空间
    )
    log_text.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

    def append_log(line: str) -> None:
        log_text.configure(state="normal")
        log_text.insert(tk.END, line + "\n")
        log_text.see(tk.END)
        log_text.configure(state="disabled")

    def clear_log() -> None:
        log_text.configure(state="normal")
        log_text.delete("1.0", tk.END)
        log_text.configure(state="disabled")

    # Tab1 首次触发延后状态：在 START_TIME 设置生效后，scheduler 还未真正 start
    # 之前的"等待"阶段由 root.after 维护，这里记录 after_id 以便 stop 时取消 + 状态展示
    tab1_pending = {"after_id": None, "deadline_ts": 0.0}

    def _tab1_cancel_pending() -> None:
        aid = tab1_pending.get("after_id")
        if aid is not None:
            try:
                root.after_cancel(aid)
            except Exception:
                pass
        tab1_pending["after_id"] = None
        tab1_pending["deadline_ts"] = 0.0

    def refresh_status() -> None:
        st = scheduler.stats()
        if st["running"]:
            status_var.set("状态：运行中")
        elif st["scheduled"]:
            status_var.set("状态：等待中")
        elif tab1_pending["after_id"] is not None:
            status_var.set("状态：等待首次触发")
        else:
            status_var.set("状态：空闲")

        if tab1_pending["after_id"] is not None:
            # 优先展示首次触发倒计时
            remain = max(0, int(tab1_pending["deadline_ts"] - time.time()))
            if remain >= 3600:
                ts = datetime.fromtimestamp(tab1_pending["deadline_ts"]).strftime("%m-%d %H:%M")
                hh = remain // 3600
                next_var.set(f"下次：{ts}（{hh} 小时后）")
            else:
                mm, ss = divmod(remain, 60)
                next_var.set(f"下次：{mm:02d}:{ss:02d} 后")
        elif st["scheduled"]:
            sec = st["next_run_in"]
            mm, ss = divmod(max(0, sec), 60)
            next_var.set(f"下次：{mm:02d}:{ss:02d} 后")
        else:
            next_var.set("下次：未启动")

        if st["last_run_ts"] > 0:
            ts = datetime.fromtimestamp(st["last_run_ts"]).strftime("%H:%M:%S")
            tag = "成功" if st["last_result"] else "异常"
            last_var.set(f"上次：{ts} ({tag})")

        stats_var.set(f"运行 {st['run_count']} 次 / 跳过 {st['skip_count']} 次")

    def pump_logs() -> None:
        try:
            while True:
                append_log(log_queue.get_nowait())
        except queue.Empty:
            pass
        refresh_status()
        root.after(500, pump_logs)

    # ─── Tab1 事件 ───
    def _get_tab1_start_hm(show_warning: bool = True):
        try:
            return True, parse_hhmm(start_time_var.get())
        except Exception as e:
            if show_warning:
                messagebox.showwarning(
                    "首次触发时间无效",
                    f"请使用 HH:MM 24 小时制（例如 14:00），或留空。\n{e}",
                )
            return False, None

    def _save_tab1_start_time() -> None:
        prefix_list = _split_multi_values(contact_prefix_var.get())
        normalized_prefix = ";".join(prefix_list) if prefix_list else _CONTACT_FOLDER_PREFIX_DEFAULT
        save_tab1_console_config(
            {
                "START_TIME": (start_time_var.get() or "").strip(),
                "CONTACT_FOLDER_PREFIX": normalized_prefix,
            },
            INC.logger,
        )

    def _tab1_first_fire() -> None:
        """到达 START_TIME 时由 root.after 回调进入：触发一次 + 启动调度器循环"""
        tab1_pending["after_id"] = None
        tab1_pending["deadline_ts"] = 0.0
        INC.logger.info("[增量导入] 首次触发时间到，开始执行并启动定时循环。")
        try:
            scheduler.trigger_once()
        finally:
            scheduler.start()
        refresh_status()

    def do_start() -> None:
        # 已经有 pending 或正在 scheduled，避免重复启动
        if tab1_pending["after_id"] is not None or scheduler.is_scheduled():
            INC.logger.info("[增量导入] 定时已在等待中或运行中，忽略重复启动。")
            return
        ok_t, hm = _get_tab1_start_hm(show_warning=True)
        if not ok_t:
            return
        _save_tab1_start_time()

        if hm is None:
            # 旧行为：直接启动 INC.Scheduler，第一次触发在 interval 后
            scheduler.start()
            refresh_status()
            return

        # 新行为：等到 HH:MM 才发出第一次执行；之后由 scheduler 按 interval 继续
        try:
            interval_min = max(1, int((interval_var.get() or "15").strip()))
        except ValueError:
            interval_min = 15
        delay_sec = compute_first_delay_sec(hm, interval_min)
        tab1_pending["deadline_ts"] = time.time() + delay_sec
        tab1_pending["after_id"] = root.after(delay_sec * 1000, _tab1_first_fire)
        ts = datetime.fromtimestamp(tab1_pending["deadline_ts"]).strftime("%Y-%m-%d %H:%M:%S")
        INC.logger.info(
            f"[增量导入] 首次触发时刻：{ts}（{delay_sec // 60} 分钟后）；之后按间隔循环。"
        )
        refresh_status()

    def do_stop() -> None:
        _tab1_cancel_pending()
        scheduler.stop()
        refresh_status()

    def do_run_now() -> None:
        if not scheduler.trigger_once():
            messagebox.showinfo("提示", "当前已有增量导入任务在执行，请稍后再试。")
        refresh_status()

    def do_apply_interval() -> None:
        raw = interval_var.get().strip()
        try:
            minutes = int(raw)
            if minutes < 1 or minutes > 1440:
                raise ValueError
        except ValueError:
            messagebox.showwarning("间隔无效", "请输入 1 ~ 1440 分钟的整数。")
            return
        scheduler.set_interval(minutes * 60)
        refresh_status()

    def do_browse_out() -> None:
        chosen = filedialog.askdirectory(
            title="选择输出目录", initialdir=output_dir_var.get().strip() or ""
        )
        if chosen:
            output_dir_var.set(chosen)

    def do_apply_out() -> None:
        raw = output_dir_var.get().strip()
        default_dir = str(Path(INC.CONFIG["EXCEL_DIR"]) / "LOG")
        target = raw if raw else default_dir
        try:
            Path(target).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showwarning("路径无效", f"无法创建或访问目录：\n{target}\n\n{e}")
            return
        INC.CONFIG["OUTPUT_DIR"] = target
        output_dir_var.set(target)
        try:
            INC.save_local_config()
            INC.logger.info(f"输出目录已更新并保存：{target}")
        except Exception as e:
            INC.logger.warning(f"保存本地配置失败：{e}")

    btn_start.configure(command=do_start)
    btn_stop.configure(command=do_stop)
    btn_run_now.configure(command=do_run_now)
    btn_apply_interval.configure(command=do_apply_interval)
    btn_browse_out.configure(command=do_browse_out)
    btn_apply_out.configure(command=do_apply_out)
    btn_clear.configure(command=clear_log)

    # ─── contact.db 同步事件 ───
    _contact_sync_lock = threading.Lock()

    def do_sync_contact() -> None:
        if not _contact_sync_lock.acquire(blocking=False):
            INC.logger.info("[contact.db 同步] 正在同步中，请稍候…")
            return

        net_base = contact_net_base_var.get().strip() or _CONTACT_NET_BASE
        raw_dst = contact_dst_var.get().strip() or _CONTACT_LOCAL_DST
        dst_list = _split_multi_values(raw_dst)
        if not dst_list:
            contact_src_label_var.set("未找到（目标路径为空）")
            INC.logger.warning("[contact.db 同步] 目标路径为空，请填写至少一个目标路径。")
            _contact_sync_lock.release()
            return

        prefix_values = _split_multi_values(contact_prefix_var.get())
        prefix = ";".join(prefix_values) if prefix_values else _CONTACT_FOLDER_PREFIX_DEFAULT
        # 手动同步前先应用并持久化一次当前前缀，避免“改了前缀但后台还用旧值”。
        _save_contact_prefix_cfg(prefix)
        src = _find_contact_db_source(net_base=net_base, prefix=prefix)
        if not src:
            contact_src_label_var.set("未找到（路径不可访问或无匹配文件夹）")
            INC.logger.warning(
                f"[contact.db 同步] 未找到有效来源，请确认来源根目录可访问：{net_base}，目录前缀：{prefix}"
            )
            _contact_sync_lock.release()
            return

        folder_name = Path(src).parts[-4] if len(Path(src).parts) >= 4 else src
        dst_show = os.path.basename(dst_list[0]) if len(dst_list) == 1 else f"{len(dst_list)} 个目标"
        contact_src_label_var.set(f"{folder_name}  →  {dst_show}")

        def _copy() -> None:
            try:
                failed_targets: list[str] = []
                for one_dst in dst_list:
                    try:
                        _copy_file_with_verify(src, one_dst)
                    except Exception as exc:
                        failed_targets.append(f"{one_dst} -> {exc}")
                if failed_targets:
                    brief = " | ".join(failed_targets[:3])
                    if len(failed_targets) > 3:
                        brief = f"{brief} | ... 其余 {len(failed_targets) - 3} 个"
                    raise RuntimeError(f"部分目标复制失败：{brief}")
                INC.logger.info(
                    f"[contact.db 同步] 成功\n    源：{src}\n    目标：{'; '.join(dst_list)}"
                )
            except Exception as exc:
                INC.logger.error(f"[contact.db 同步] 失败：{exc}")
            finally:
                _contact_sync_lock.release()

        threading.Thread(target=_copy, daemon=True, name="contact-sync").start()

    btn_sync_contact.configure(command=do_sync_contact)

    # ─── Tab2 事件 ───
    note_busy = threading.Lock()

    def _sync_note_config(persist: bool = False) -> None:
        """把 UI 上的值回写到 NOTE_CONFIG。
        persist=True 时同时落盘到 note_import_console_config.json。
        """
        NOTE_CONFIG["SCRIPT_PATH"] = (
            note_script_var.get().strip() or NOTE_IMPORT_DEFAULT_SCRIPT
        )
        NOTE_CONFIG["DB_PATH"] = db_var.get().strip()
        NOTE_CONFIG["OUT_JSON"] = json_var.get().strip()
        NOTE_CONFIG["UPLOAD_MODE"] = (mode_var.get().strip().lower() or "postgrest")
        if persist:
            save_note_import_config(INC.logger)

    def _run_note_async(do_upload: bool, silent: bool = False) -> None:
        # 不论走哪条路径，都先把最新 UI 值落库一次，保证执行体看到的是最新参数
        _sync_note_config(persist=True)

        if bool(note_use_queue_var.get()):
            # ── 走全局队列：不抢 note_busy 锁，让 worker 串行执行 ──────
            GLOBAL_RUNNER.submit(
                name="内部备注导入",
                callable_=lambda log: run_note_pipeline_external(do_upload, log),
                log=INC.logger,
            )
            return

        # ── 默认行为：与原版一致，若已在跑则 skip ────────────────────
        if not note_busy.acquire(blocking=False):
            msg = "内部备注任务已在运行中，跳过本次触发。"
            if silent:
                INC.logger.info(f"[自动触发] {msg}")
            else:
                messagebox.showinfo("提示", msg)
            return

        def _job() -> None:
            try:
                run_note_pipeline_external(do_upload, INC.logger)
            finally:
                note_busy.release()

        threading.Thread(target=_job, daemon=True, name="note-pipeline").start()

    def do_browse_note_script() -> None:
        """选择外置「内部备注导入.py」脚本路径。"""
        chosen = filedialog.askopenfilename(
            title="选择 内部备注导入 Python 脚本",
            initialfile=note_script_var.get().strip() or NOTE_IMPORT_DEFAULT_SCRIPT,
            filetypes=[("Python 文件", "*.py"), ("所有文件", "*.*")],
        )
        if chosen:
            note_script_var.set(chosen)

    def do_note_save_cfg() -> None:
        """显式「保存配置」按钮：把当前 UI 上的值持久化到 JSON。"""
        _sync_note_config(persist=True)

    btn_browse_note_script.configure(command=do_browse_note_script)
    btn_note_save_cfg.configure(command=do_note_save_cfg)

    def do_note_run() -> None:
        _run_note_async(True)

    def do_note_export_only() -> None:
        _run_note_async(False)

    # ─── 自动触发：增量导入完成后延迟 N 秒运行内部备注 ───
    auto_state = {"after_id": None, "deadline_ts": 0.0}

    def _cancel_pending_note_auto() -> None:
        aid = auto_state.get("after_id")
        if aid is not None:
            try:
                root.after_cancel(aid)
            except Exception:
                pass
        auto_state["after_id"] = None
        auto_state["deadline_ts"] = 0.0

    def _fire_note_auto() -> None:
        auto_state["after_id"] = None
        auto_state["deadline_ts"] = 0.0
        if not auto_var.get():
            return
        INC.logger.info("[自动触发] 增量导入完成后延迟到达，开始运行内部备注导入。")
        _run_note_async(do_upload=True, silent=True)

    def _schedule_note_auto_from_ui() -> None:
        """在 UI 线程调度：取消旧 after，按最新延迟排一个新的。"""
        _cancel_pending_note_auto()
        if not auto_var.get():
            return
        raw = (delay_var.get() or "").strip()
        try:
            delay = int(raw)
            if delay < 1 or delay > 3600:
                raise ValueError
        except ValueError:
            INC.logger.warning(f"[自动触发] 延迟值 '{raw}' 非法，回退为 60 秒。")
            delay = 60
        auto_state["after_id"] = root.after(delay * 1000, _fire_note_auto)
        auto_state["deadline_ts"] = time.time() + delay
        INC.logger.info(f"[自动触发] 将在 {delay} 秒后运行内部备注导入。")

    def _on_inc_run_end() -> None:
        """由 Scheduler 在工作线程里调用；通过 after 切回 UI 线程再调度。"""
        try:
            root.after(0, _schedule_note_auto_from_ui)
        except Exception:
            pass

    scheduler.set_callbacks(on_end=_on_inc_run_end)

    def _on_auto_toggle() -> None:
        if not auto_var.get():
            _cancel_pending_note_auto()
            INC.logger.info("[自动触发] 已关闭；若有 pending 计划已取消。")
        else:
            INC.logger.info("[自动触发] 已开启；下次增量导入完成后会自动排程。")

    cb_auto_note.configure(command=_on_auto_toggle)

    def _refresh_note_next_label() -> None:
        if auto_state.get("after_id") is None:
            note_next_var.set("自动触发：空闲" if auto_var.get() else "自动触发：未启用")
            return
        remain = max(0, int(auto_state["deadline_ts"] - time.time()))
        mm, ss = divmod(remain, 60)
        note_next_var.set(f"自动触发：{mm:02d}:{ss:02d} 后运行")

    # 把倒计时刷新挂到 pump_logs 一起（通过 after 链）
    def _tick_note_label() -> None:
        _refresh_note_next_label()
        root.after(500, _tick_note_label)

    root.after(500, _tick_note_label)

    def do_browse_db() -> None:
        chosen = filedialog.askopenfilename(
            title="选择 contact.db",
            initialfile=db_var.get(),
            filetypes=[("SQLite DB", "*.db"), ("所有文件", "*.*")],
        )
        if chosen:
            db_var.set(chosen)

    def do_browse_json() -> None:
        chosen = filedialog.asksaveasfilename(
            title="保存 JSON 到",
            initialfile=os.path.basename(json_var.get() or "contact_result.json"),
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if chosen:
            json_var.set(chosen)

    btn_note_run.configure(command=do_note_run)
    btn_note_export_only.configure(command=do_note_export_only)
    btn_browse_db.configure(command=do_browse_db)
    btn_browse_json.configure(command=do_browse_json)

    # ─── Tab4 事件：飞书全量同步 ───
    feishu_busy = threading.Lock()
    feishu_state = {
        "enabled": False,
        "after_id": None,
        "deadline_ts": 0.0,
        "running": False,
        "run_count": 0,
        "success_count": 0,
        "fail_count": 0,
        "last_run_ts": 0.0,
        "last_result": None,
        # 首次是否已触发；启动定时时复位为 False，触发一次后置 True，
        # 之后回退到 INTERVAL_MIN 间隔轮询。
        "first_fire_done": False,
    }

    def _get_feishu_interval_minutes(show_warning: bool = True) -> int | None:
        raw = (feishu_interval_var.get() or "").strip()
        try:
            minutes = int(raw)
            if minutes < 1 or minutes > 1440:
                raise ValueError
            return minutes
        except ValueError:
            if show_warning:
                messagebox.showwarning("间隔无效", "请输入 1 ~ 1440 分钟的整数。")
            return None

    def _get_feishu_start_time(show_warning: bool = True):
        """校验 Tab4 首次触发时间。返回 (是否合法, (h,m) 或 None)。"""
        try:
            return True, parse_hhmm(feishu_start_var.get())
        except Exception as e:
            if show_warning:
                messagebox.showwarning(
                    "首次触发时间无效",
                    f"请使用 HH:MM 24 小时制（例如 14:00），或留空。\n{e}",
                )
            return False, None

    def _current_feishu_cfg() -> dict:
        minutes = _get_feishu_interval_minutes(show_warning=False) or 60
        return {
            "SCRIPT_PATH": feishu_script_var.get().strip() or FEISHU_SYNC_DEFAULT_SCRIPT,
            "DRY_RUN": bool(feishu_dry_run_var.get()),
            "INTERVAL_MIN": minutes,
            "START_TIME": (feishu_start_var.get() or "").strip(),
        }

    def _save_feishu_cfg_from_ui() -> bool:
        minutes = _get_feishu_interval_minutes(show_warning=True)
        if minutes is None:
            return False
        ok_t, _ = _get_feishu_start_time(show_warning=True)
        if not ok_t:
            return False
        cfg = _current_feishu_cfg()
        cfg["INTERVAL_MIN"] = minutes
        save_feishu_sync_config(cfg, INC.logger)
        return True

    def do_feishu_browse() -> None:
        chosen = filedialog.askopenfilename(
            title="选择 飞书同步 Python 脚本",
            initialfile=feishu_script_var.get().strip() or FEISHU_SYNC_DEFAULT_SCRIPT,
            filetypes=[("Python 文件", "*.py"), ("所有文件", "*.*")],
        )
        if chosen:
            feishu_script_var.set(chosen)

    def _cancel_feishu_timer() -> None:
        aid = feishu_state.get("after_id")
        if aid is not None:
            try:
                root.after_cancel(aid)
            except Exception:
                pass
        feishu_state["after_id"] = None
        feishu_state["deadline_ts"] = 0.0

    def _schedule_next_feishu_run() -> None:
        _cancel_feishu_timer()
        if not feishu_state["enabled"]:
            return
        minutes = _get_feishu_interval_minutes(show_warning=False) or 60

        if not feishu_state["first_fire_done"]:
            ok_t, hm = _get_feishu_start_time(show_warning=False)
            delay_sec = compute_first_delay_sec(hm if ok_t else None, minutes)
            feishu_state["deadline_ts"] = time.time() + delay_sec
            feishu_state["after_id"] = root.after(delay_sec * 1000, _fire_feishu_timer)
            if hm is not None:
                ts = datetime.fromtimestamp(feishu_state["deadline_ts"]).strftime("%Y-%m-%d %H:%M:%S")
                INC.logger.info(f"[飞书同步] 首次触发时刻：{ts}（{delay_sec // 60} 分钟后）")
            else:
                INC.logger.info(f"[飞书同步] 已安排下次定时执行：{minutes} 分钟后")
        else:
            delay_ms = minutes * 60 * 1000
            feishu_state["deadline_ts"] = time.time() + minutes * 60
            feishu_state["after_id"] = root.after(delay_ms, _fire_feishu_timer)
            INC.logger.info(f"[飞书同步] 已安排下次定时执行：{minutes} 分钟后")

    def _run_feishu_async(silent: bool = False) -> None:
        # 真实执行的二次确认放在入口处，不论 skip / 队列两种路径都要走
        if not _save_feishu_cfg_from_ui():
            return
        cfg = _current_feishu_cfg()
        if not cfg["DRY_RUN"] and not silent:
            ok_confirm = messagebox.askyesno(
                "确认执行飞书全量同步",
                "将清空当前飞书表内所有记录并重新导入，\n"
                "表结构、字段、视图不会删除。\n\n"
                "是否继续？",
            )
            if not ok_confirm:
                INC.logger.info("[飞书同步] 用户取消真实执行。")
                return
        if not cfg["DRY_RUN"] and silent:
            INC.logger.info(
                "[飞书同步] 定时真实执行开始：仅清空表内记录并重新导入，不删除表结构/字段/视图。"
            )

        # 把"执行体"封装一次，两种路径复用
        def _do_run(log) -> bool:
            ok = False
            try:
                ok = run_feishu_sync_pipeline(
                    script_path=cfg["SCRIPT_PATH"],
                    dry_run=cfg["DRY_RUN"],
                    log=log,
                )
                if ok:
                    feishu_state["success_count"] += 1
                else:
                    feishu_state["fail_count"] += 1
                feishu_state["last_result"] = ok
            except Exception as exc:
                feishu_state["fail_count"] += 1
                feishu_state["last_result"] = False
                log.error(f"[飞书同步] 执行失败：{exc}")
            finally:
                feishu_state["last_run_ts"] = time.time()
                feishu_state["running"] = False
            return ok

        if bool(feishu_use_queue_var.get()):
            # ── 走全局队列 ────────────────────────────────────────
            # 标记一下 running，让 UI 状态栏显示"定时中"而不是"空闲"
            feishu_state["running"] = True
            feishu_state["run_count"] += 1
            feishu_state["last_result"] = None
            GLOBAL_RUNNER.submit(
                name="飞书全量同步",
                callable_=_do_run,
                log=INC.logger,
            )
            return

        # ── 默认行为：与原版一致，busy 锁 + skip ─────────────────────
        if not feishu_busy.acquire(blocking=False):
            msg = "飞书同步任务已在运行中，跳过本次触发。"
            if silent:
                INC.logger.info(f"[飞书同步] {msg}")
            else:
                messagebox.showinfo("提示", msg)
            return

        feishu_state["running"] = True
        feishu_state["run_count"] += 1
        feishu_state["last_result"] = None

        def _job() -> None:
            try:
                _do_run(INC.logger)
            finally:
                feishu_busy.release()

        threading.Thread(target=_job, daemon=True, name="feishu-sync").start()

    def _fire_feishu_timer() -> None:
        feishu_state["after_id"] = None
        feishu_state["deadline_ts"] = 0.0
        if not feishu_state["enabled"]:
            return
        feishu_state["first_fire_done"] = True
        INC.logger.info("[飞书同步] 定时时间到，开始执行。")
        _run_feishu_async(silent=True)
        _schedule_next_feishu_run()

    def do_feishu_save() -> None:
        _save_feishu_cfg_from_ui()

    def do_feishu_run_now() -> None:
        _run_feishu_async(silent=False)

    def do_feishu_start() -> None:
        if not _save_feishu_cfg_from_ui():
            return
        feishu_state["enabled"] = True
        # 每次"启动定时"重新走一遍首次触发逻辑（用户可能临时改了 START_TIME）
        feishu_state["first_fire_done"] = False
        _schedule_next_feishu_run()
        INC.logger.info("[飞书同步] 定时任务已启动。")

    def do_feishu_stop() -> None:
        feishu_state["enabled"] = False
        _cancel_feishu_timer()
        INC.logger.info("[飞书同步] 定时任务已停止。")

    def _refresh_feishu_status() -> None:
        if feishu_state["running"]:
            feishu_status_var.set("状态：执行中")
        elif feishu_state["enabled"]:
            feishu_status_var.set("状态：定时中")
        else:
            feishu_status_var.set("状态：空闲")

        if feishu_state.get("after_id") is not None:
            remain = max(0, int(feishu_state["deadline_ts"] - time.time()))
            if remain >= 3600:
                ts = datetime.fromtimestamp(feishu_state["deadline_ts"]).strftime("%m-%d %H:%M")
                hh = remain // 3600
                feishu_next_var.set(f"下次：{ts}（{hh} 小时后）")
            else:
                mm, ss = divmod(remain, 60)
                feishu_next_var.set(f"下次：{mm:02d}:{ss:02d} 后")
        else:
            feishu_next_var.set("下次：未启动")

        if feishu_state["last_run_ts"] > 0:
            ts = datetime.fromtimestamp(feishu_state["last_run_ts"]).strftime("%H:%M:%S")
            result = "成功" if feishu_state["last_result"] else "失败"
            feishu_last_var.set(f"上次：{ts} ({result})")
        else:
            feishu_last_var.set("上次：—")

        feishu_stats_var.set(
            f"运行 {feishu_state['run_count']} 次 / "
            f"成功 {feishu_state['success_count']} 次 / "
            f"失败 {feishu_state['fail_count']} 次"
        )
        root.after(500, _refresh_feishu_status)

    btn_feishu_browse.configure(command=do_feishu_browse)
    btn_feishu_save.configure(command=do_feishu_save)
    btn_feishu_run.configure(command=do_feishu_run_now)
    btn_feishu_start.configure(command=do_feishu_start)
    btn_feishu_stop.configure(command=do_feishu_stop)
    btn_feishu_open_mapping.configure(command=do_feishu_open_mapping)
    root.after(500, _refresh_feishu_status)

    # ─── Tab5 事件：用户上传 ───
    uu_busy = threading.Lock()
    uu_state = {
        "enabled":         False,
        "after_id":        None,
        "deadline_ts":     0.0,
        "running":         False,
        "run_count":       0,
        "success_count":   0,
        "fail_count":      0,
        "last_run_ts":     0.0,
        "last_result":     None,
        # 是否已经完成"首次触发"。启动定时时复位为 False，
        # 触发一次后置 True，后续按 INTERVAL_MIN 间隔轮询。
        "first_fire_done": False,
    }

    def _get_uu_interval_minutes(show_warning: bool = True) -> int | None:
        raw = (uu_interval_var.get() or "").strip()
        try:
            minutes = int(raw)
            if minutes < 1 or minutes > 1440:
                raise ValueError
            return minutes
        except ValueError:
            if show_warning:
                messagebox.showwarning("间隔无效", "请输入 1 ~ 1440 分钟的整数。")
            return None

    def _parse_start_time(raw: str) -> tuple[int, int] | None:
        """
        解析 HH:MM（24h），返回 (hour, minute)；空串返回 None；非法格式抛 ValueError。
        允许 '9:5' / '09:05' / ' 14 : 00 ' 这类宽松写法。
        """
        s = (raw or "").strip()
        if not s:
            return None
        if ":" not in s:
            raise ValueError("缺少冒号")
        hh, mm = s.split(":", 1)
        h = int(hh.strip())
        m = int(mm.strip())
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError("时刻越界")
        return h, m

    def _get_uu_start_time(show_warning: bool = True) -> tuple[bool, tuple[int, int] | None]:
        """
        校验"首次触发时间"输入。返回 (是否合法, (h, m) 或 None)。
        空串视为合法、值=None。非法时弹警告并返回 (False, None)。
        """
        try:
            return True, _parse_start_time(uu_start_var.get())
        except Exception as e:
            if show_warning:
                messagebox.showwarning(
                    "首次触发时间无效",
                    f"请使用 HH:MM 24 小时制（例如 14:00），或留空。\n{e}",
                )
            return False, None

    def _compute_first_delay_sec(hm: tuple[int, int] | None, interval_min: int) -> int:
        """
        计算"首次触发"等待秒数：
          - hm 为空 → 等一个 INTERVAL_MIN（保持旧行为）
          - hm 已过 → 等到次日同时刻
          - hm 未到 → 等到今天该时刻
        """
        if hm is None:
            return interval_min * 60
        h, m = hm
        now = datetime.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            from datetime import timedelta as _td
            target = target + _td(days=1)
        return max(1, int((target - now).total_seconds()))

    def _current_uu_cfg() -> dict:
        minutes = _get_uu_interval_minutes(show_warning=False) or 60
        mode = (uu_mode_var.get() or "full").strip()
        if mode not in ("full", "json_only", "upload_only"):
            mode = "full"
        return {
            "SCRIPT_PATH":  uu_script_var.get().strip() or USER_UPLOAD_DEFAULT_SCRIPT,
            "VIEW_ID":      uu_view_var.get().strip(),
            "MODE":         mode,
            "DRY_RUN":      bool(uu_dry_var.get()),
            "INTERVAL_MIN": minutes,
            "START_TIME":   (uu_start_var.get() or "").strip(),
        }

    def _save_uu_cfg_from_ui() -> bool:
        minutes = _get_uu_interval_minutes(show_warning=True)
        if minutes is None:
            return False
        ok_t, _ = _get_uu_start_time(show_warning=True)
        if not ok_t:
            return False
        cfg = _current_uu_cfg()
        cfg["INTERVAL_MIN"] = minutes
        save_user_upload_config(cfg, INC.logger)
        return True

    def do_uu_browse() -> None:
        chosen = filedialog.askopenfilename(
            title="选择 上传用户结构 Python 脚本",
            initialfile=uu_script_var.get().strip() or USER_UPLOAD_DEFAULT_SCRIPT,
            filetypes=[("Python 文件", "*.py"), ("所有文件", "*.*")],
        )
        if chosen:
            uu_script_var.set(chosen)

    def _cancel_uu_timer() -> None:
        aid = uu_state.get("after_id")
        if aid is not None:
            try:
                root.after_cancel(aid)
            except Exception:
                pass
        uu_state["after_id"] = None
        uu_state["deadline_ts"] = 0.0

    def _schedule_next_uu_run() -> None:
        _cancel_uu_timer()
        if not uu_state["enabled"]:
            return
        minutes = _get_uu_interval_minutes(show_warning=False) or 60

        # 首次触发：如果用户填了 START_TIME 且尚未触发过 → 走绝对时刻；否则走间隔
        if not uu_state["first_fire_done"]:
            ok_t, hm = _get_uu_start_time(show_warning=False)
            delay_sec = _compute_first_delay_sec(hm if ok_t else None, minutes)
            uu_state["deadline_ts"] = time.time() + delay_sec
            uu_state["after_id"] = root.after(delay_sec * 1000, _fire_uu_timer)
            if hm is not None:
                ts = datetime.fromtimestamp(uu_state["deadline_ts"]).strftime("%Y-%m-%d %H:%M:%S")
                INC.logger.info(
                    f"[用户上传] 首次触发时刻：{ts}（{delay_sec // 60} 分钟后）"
                )
            else:
                INC.logger.info(
                    f"[用户上传] 已安排下次定时执行：{minutes} 分钟后"
                )
        else:
            delay_ms = minutes * 60 * 1000
            uu_state["deadline_ts"] = time.time() + minutes * 60
            uu_state["after_id"] = root.after(delay_ms, _fire_uu_timer)
            INC.logger.info(f"[用户上传] 已安排下次定时执行：{minutes} 分钟后")

    def _run_uu_async(silent: bool = False) -> None:
        if not _save_uu_cfg_from_ui():
            return

        cfg = _current_uu_cfg()

        # 真实上传 + 模式涉及上传 + 非 DRY_RUN + 非 silent → 二次确认
        will_actually_upload = (
            cfg["MODE"] in ("full", "upload_only") and not cfg["DRY_RUN"]
        )
        if will_actually_upload and not silent:
            ok_confirm = messagebox.askyesno(
                "确认执行用户上传",
                "将通过秒哒 import-users 接口写入用户数据。\n"
                "已存在账号会被接口跳过，不覆盖原有数据。\n\n"
                f"模式：{cfg['MODE']}\n"
                f"视图：{cfg['VIEW_ID'] or '(整张表)'}\n\n"
                "是否继续？",
            )
            if not ok_confirm:
                INC.logger.info("[用户上传] 用户取消执行。")
                return

        def _do_run(log) -> bool:
            ok = False
            try:
                ok = run_user_upload_pipeline(
                    script_path=cfg["SCRIPT_PATH"],
                    mode=cfg["MODE"],
                    dry_run=cfg["DRY_RUN"],
                    view_id=cfg["VIEW_ID"],
                    log=log,
                )
                if ok:
                    uu_state["success_count"] += 1
                else:
                    uu_state["fail_count"] += 1
                uu_state["last_result"] = ok
            except Exception as exc:
                uu_state["fail_count"] += 1
                uu_state["last_result"] = False
                log.error(f"[用户上传] 执行失败：{exc}")
            finally:
                uu_state["last_run_ts"] = time.time()
                uu_state["running"] = False
            return ok

        if bool(uu_use_queue_var.get()):
            uu_state["running"] = True
            uu_state["run_count"] += 1
            uu_state["last_result"] = None
            GLOBAL_RUNNER.submit(
                name="用户上传",
                callable_=_do_run,
                log=INC.logger,
            )
            return

        if not uu_busy.acquire(blocking=False):
            msg = "用户上传任务已在运行中，跳过本次触发。"
            if silent:
                INC.logger.info(f"[用户上传] {msg}")
            else:
                messagebox.showinfo("提示", msg)
            return

        uu_state["running"] = True
        uu_state["run_count"] += 1
        uu_state["last_result"] = None

        def _job() -> None:
            try:
                _do_run(INC.logger)
            finally:
                uu_busy.release()

        threading.Thread(target=_job, daemon=True, name="user-upload").start()

    def _fire_uu_timer() -> None:
        uu_state["after_id"] = None
        uu_state["deadline_ts"] = 0.0
        if not uu_state["enabled"]:
            return
        # 标记首次已触发，下次起按 INTERVAL_MIN 间隔轮询
        uu_state["first_fire_done"] = True
        INC.logger.info("[用户上传] 定时时间到，开始执行。")
        _run_uu_async(silent=True)
        _schedule_next_uu_run()

    def do_uu_save() -> None:
        _save_uu_cfg_from_ui()

    def do_uu_run_now() -> None:
        _run_uu_async(silent=False)

    def do_uu_start() -> None:
        if not _save_uu_cfg_from_ui():
            return
        uu_state["enabled"] = True
        # 每次"启动定时"都重新走一遍首次触发逻辑（用户可能临时改了 START_TIME）
        uu_state["first_fire_done"] = False
        _schedule_next_uu_run()
        INC.logger.info("[用户上传] 定时任务已启动。")

    def do_uu_stop() -> None:
        uu_state["enabled"] = False
        _cancel_uu_timer()
        INC.logger.info("[用户上传] 定时任务已停止。")

    def _refresh_uu_status() -> None:
        if uu_state["running"]:
            uu_status_var.set("状态：执行中")
        elif uu_state["enabled"]:
            uu_status_var.set("状态：定时中")
        else:
            uu_status_var.set("状态：空闲")

        if uu_state.get("after_id") is not None:
            remain = max(0, int(uu_state["deadline_ts"] - time.time()))
            if remain >= 3600:
                # 长延迟（如首次触发到次日 HH:MM）→ 显示绝对时刻 + 剩余小时
                ts = datetime.fromtimestamp(uu_state["deadline_ts"]).strftime("%m-%d %H:%M")
                hh = remain // 3600
                uu_next_var.set(f"下次：{ts}（{hh} 小时后）")
            else:
                mm, ss = divmod(remain, 60)
                uu_next_var.set(f"下次：{mm:02d}:{ss:02d} 后")
        else:
            uu_next_var.set("下次：未启动")

        if uu_state["last_run_ts"] > 0:
            ts = datetime.fromtimestamp(uu_state["last_run_ts"]).strftime("%H:%M:%S")
            result = "成功" if uu_state["last_result"] else "失败"
            uu_last_var.set(f"上次：{ts} ({result})")
        else:
            uu_last_var.set("上次：—")

        uu_stats_var.set(
            f"运行 {uu_state['run_count']} 次 / "
            f"成功 {uu_state['success_count']} 次 / "
            f"失败 {uu_state['fail_count']} 次"
        )
        root.after(500, _refresh_uu_status)

    btn_uu_browse.configure(command=do_uu_browse)
    btn_uu_save.configure(command=do_uu_save)
    btn_uu_run.configure(command=do_uu_run_now)
    btn_uu_start.configure(command=do_uu_start)
    btn_uu_stop.configure(command=do_uu_stop)
    root.after(500, _refresh_uu_status)

    # ─── 关闭处理 ───
    def on_close() -> None:
        _cancel_pending_note_auto()
        _cancel_feishu_timer()
        _cancel_uu_timer()
        _tab1_cancel_pending()
        scheduler.stop()
        copy_mgr.stop()
        _hide_tooltip()
        root.after(100, root.destroy)

    root.protocol("WM_DELETE_WINDOW", on_close)
    # 每 10 秒自动刷新「定时拷贝」任务列表，同步最新运行状态
    def _auto_refresh_copy_tree() -> None:
        _refresh_copy_tree()
        root.after(10_000, _auto_refresh_copy_tree)

    # 首次启动时，若任务列表为空，自动预置 contact.db 动态同步任务
    # 使用 Tab 0 里配置的来源根目录和目标路径，间隔 30 分钟
    def _seed_default_copy_tasks() -> None:
        if copy_mgr.get_tasks():
            return  # 已有任务，不重复预置
        try:
            src = contact_net_base_var.get().strip() or _CONTACT_NET_BASE
            dst = contact_dst_var.get().strip() or _CONTACT_LOCAL_DST
            copy_mgr.add_task(
                name="contact.db 同步",
                src=src,
                dst=dst,
                interval_min=30,
                enabled=True,
                find_contact_db=True,
            )
            INC.logger.info(
                f"[定时拷贝] 已自动预置 contact.db 同步任务（来源：{src}，目标：{dst}，间隔：30 分钟）"
            )
            _refresh_copy_tree()
        except Exception as _e:
            INC.logger.warning(f"[定时拷贝] 预置默认任务失败：{_e}")

    root.after(300, pump_logs)
    root.after(800, _seed_default_copy_tasks)   # UI 变量完全初始化后执行
    root.after(10_000, _auto_refresh_copy_tree)
    root.after(500, _periodic_queue_status)     # 顶部队列状态栏的兜底刷新
    INC.logger.info("导入控制台已就绪。")
    root.mainloop()


# ─────────────────────────── 入口 ───────────────────────────
def main() -> None:
    if "--no-gui" in sys.argv:
        if "--note" in sys.argv:
            # CLI 也走外置脚本，与 GUI 行为一致
            ok = run_note_pipeline_external(do_upload=True, log=INC.logger)
            sys.exit(0 if ok else 1)
        ok = INC.run_pipeline_once()
        sys.exit(0 if ok else 1)
    launch_gui()


if __name__ == "__main__":
    main()
