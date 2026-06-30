# -*- coding: utf-8 -*-
"""
定时拷贝任务脚本（可独立运行）。

设计目标：
1) 支持固定文件路径拷贝（文件 -> 指定目标文件名）
2) 支持 contact.db 动态查找（网络根目录 + 前缀）
3) 支持多个目标路径（分号/逗号/换行分隔）
4) 文件拷贝采用“等待稳定 -> 复制 -> 大小校验”流程，避免拷贝半写入文件
5) 支持后台定时调度，也支持命令行单次执行
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
import re


# ─────────────────────────── 默认配置 ───────────────────────────

DEFAULT_CONTACT_PREFIX = "wxid_42272spv9uq522_6ded"
DEFAULT_COPY_TASKS_JSON = str(Path(__file__).resolve().parent / "copy_tasks.json")
CHECK_INTERVAL_SEC = 30


# ─────────────────────────── 工具函数 ───────────────────────────

def split_multi_values(raw: str) -> list[str]:
    """
    将用户输入的“多值文本”解析为列表，支持 ;、,、中文符号和换行分隔。
    返回值会做 strip、去空，并按输入顺序去重。
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


def parse_hhmm(raw: str):
    """
    解析 HH:MM（24h）。
    - 空串 -> None（表示“未启用首次触发时间”）
    - 合法 -> (h, m)
    - 非法 -> 抛 ValueError
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


def get_file_stat_info(file_path: str) -> dict:
    """读取文件基础信息（大小、修改时间、创建时间），用于稳定性判断与复制后校验。"""
    st = os.stat(file_path)
    return {
        "size": st.st_size,
        "mtime": st.st_mtime,
        "ctime": st.st_ctime,
    }


def wait_file_stable(file_path: str, interval_sec: float = 2.0, max_retries: int = 5) -> dict:
    """
    等待源文件稳定：
    - 连续两次检查 size + mtime 一致，认为文件写入完成
    - 超过重试次数仍不稳定则抛异常
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"源文件不存在：{file_path}")

    retries = max(1, int(max_retries))
    wait_s = max(0.1, float(interval_sec))
    for _ in range(retries):
        first = get_file_stat_info(file_path)
        time.sleep(wait_s)
        second = get_file_stat_info(file_path)
        if first["size"] == second["size"] and first["mtime"] == second["mtime"]:
            return second
    raise RuntimeError(f"源文件长时间不稳定，可能仍在写入：{file_path}")


def copy_file_with_verify(src_file: str, dst_file: str) -> None:
    """
    按“稳定等待 -> 复制 -> 校验”执行单文件拷贝。
    """
    if not os.path.isfile(src_file):
        raise FileNotFoundError(f"源文件不存在：{src_file}")

    dst_parent = os.path.dirname(dst_file) or "."
    if not os.path.isdir(dst_parent):
        os.makedirs(dst_parent, exist_ok=True)

    src_info = wait_file_stable(src_file)
    shutil.copyfile(src_file, dst_file)

    if not os.path.exists(dst_file):
        raise RuntimeError(f"拷贝失败，目标文件不存在：{dst_file}")
    dst_info = get_file_stat_info(dst_file)
    if src_info["size"] != dst_info["size"]:
        raise RuntimeError(
            f"拷贝后文件大小不一致：源文件={src_info['size']}字节，目标文件={dst_info['size']}字节"
        )


def find_contact_db_source(net_base: str, prefix: str) -> str:
    """
    在 net_base 目录下查找以 prefix（支持多个）开头、日期时间后缀最大的文件夹，
    返回其中 db_storage/contact/contact.db 的完整路径；未找到时返回空串。

    支持两种文件夹命名格式：
      - 旧格式：prefix_YYYYMMDD
      - 新格式：prefix_YYYYMMDD_HHMM
    """
    try:
        effective_prefixes = split_multi_values(prefix)
        if not effective_prefixes:
            effective_prefixes = [DEFAULT_CONTACT_PREFIX]

        if not os.path.isdir(net_base):
            return ""

        valid: list[tuple[str, int]] = []
        for folder in os.listdir(net_base):
            if not any(folder.startswith(one_prefix) for one_prefix in effective_prefixes):
                continue
            parts = folder.split("_")
            last = parts[-1]
            second_last = parts[-2] if len(parts) >= 2 else ""
            date_str = time_str = ""
            if (
                len(parts) >= 3
                and second_last.isdigit()
                and len(second_last) == 8
                and last.isdigit()
                and 3 <= len(last) <= 6
            ):
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
        db_path = os.path.join(net_base, valid[0][0], "db_storage", "contact", "contact.db")
        return db_path if os.path.isfile(db_path) else ""
    except Exception:
        return ""


# ─────────────────────────── 定时拷贝管理器 ───────────────────────────

class CopyTaskManager:
    """
    定时拷贝任务管理器。

    每条任务字段：
    - id
    - name
    - src
    - dst
    - interval_min
    - enabled
    - find_contact_db
    - contact_prefixes
    - start_time
    - last_run_ts
    - last_result
    """

    def __init__(
        self,
        json_path: str = DEFAULT_COPY_TASKS_JSON,
        logger: logging.Logger | None = None,
        default_contact_prefix: str = DEFAULT_CONTACT_PREFIX,
    ) -> None:
        self._json_path = json_path
        self._log = logger or logging.getLogger(__name__)
        self._default_contact_prefix = default_contact_prefix or DEFAULT_CONTACT_PREFIX
        self._tasks: list[dict] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._load()

    # ── 持久化 ─────────────────────
    def _load(self) -> None:
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
        try:
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(self._tasks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log.warning(f"[定时拷贝] 保存任务失败：{e}")

    def get_tasks(self) -> list[dict]:
        with self._lock:
            return list(self._tasks)

    def add_task(
        self,
        name: str,
        src: str,
        dst: str,
        interval_min: int,
        enabled: bool = True,
        find_contact_db: bool = False,
        start_time: str = "",
        contact_prefixes: str = "",
    ) -> dict:
        """
        新增一条任务，返回新建任务字典。
        该方法与导入控制台中的调用签名保持一致。
        """
        import uuid

        task = {
            "id": str(uuid.uuid4()),
            "name": (name or "").strip(),
            "src": (src or "").strip(),
            "dst": (dst or "").strip(),
            "interval_min": max(1, int(interval_min)),
            "enabled": bool(enabled),
            "find_contact_db": bool(find_contact_db),
            "contact_prefixes": (contact_prefixes or "").strip(),
            "start_time": (start_time or "").strip(),
            "last_run_ts": 0.0,
            "last_result": "",
        }
        with self._lock:
            self._tasks.append(task)
            self._save()
        self._log.info(f"[定时拷贝] 新增任务：{task['name']}")
        return task

    def remove_task(self, task_id: str) -> bool:
        """按任务 ID 删除任务。"""
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if t.get("id") != task_id]
            if len(self._tasks) < before:
                self._save()
                return True
        return False

    def update_task(self, task_id: str, **kwargs) -> bool:
        """
        更新任务字段。
        仅允许更新导入控制台会写入的字段，防止脏数据污染。
        """
        allowed = {
            "name",
            "src",
            "dst",
            "interval_min",
            "enabled",
            "find_contact_db",
            "contact_prefixes",
            "start_time",
        }
        with self._lock:
            for task in self._tasks:
                if task.get("id") != task_id:
                    continue
                for key, value in kwargs.items():
                    if key not in allowed:
                        continue
                    if key == "interval_min":
                        task[key] = max(1, int(value))
                    elif key in {"name", "src", "dst", "contact_prefixes", "start_time"}:
                        task[key] = (value or "").strip()
                    elif key in {"enabled", "find_contact_db"}:
                        task[key] = bool(value)
                    else:
                        task[key] = value
                self._save()
                return True
        return False

    def set_enabled(self, task_id: str, enabled: bool) -> bool:
        """启用/禁用任务。"""
        return self.update_task(task_id, enabled=enabled)

    def run_task_now(self, task_id: str) -> bool:
        """
        立即后台执行一次任务，不影响后续按间隔调度。
        """
        with self._lock:
            task = next((t for t in self._tasks if t.get("id") == task_id), None)
        if task is None:
            return False
        threading.Thread(target=self._execute_task, args=(dict(task),), daemon=True).start()
        return True

    # ── 执行核心 ───────────────────
    @staticmethod
    def _compute_next_start_ts(hhmm_str: str, now_ts: float) -> float | None:
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
            target = target + timedelta(days=1)
        return target.timestamp()

    def _execute_task(self, task: dict) -> None:
        name = task.get("name", "")
        src = task.get("src", "")
        dst = task.get("dst", "")
        contact_prefixes_raw = task.get("contact_prefixes", "")
        result = ""
        self._log.info(f"[定时拷贝] 开始执行：{name}  {src} → {dst}")
        try:
            dst_list = split_multi_values(dst)
            if not dst_list:
                raise ValueError("目标路径不能为空")

            if task.get("find_contact_db"):
                task_prefixes = (contact_prefixes_raw or "").strip()
                effective_prefixes = task_prefixes or self._default_contact_prefix
                actual_src = find_contact_db_source(src, prefix=effective_prefixes)
                if not actual_src:
                    raise FileNotFoundError(
                        f"未在 {src} 下找到有效的 contact.db（当前前缀：{effective_prefixes}）"
                    )
                src = actual_src
                self._log.info(f"[定时拷贝] 动态定位 contact.db：{src}")

            if not os.path.exists(src):
                raise FileNotFoundError(f"来源不存在：{src}")
            if not os.path.isfile(src):
                raise ValueError(
                    f"来源必须是文件路径，当前不是文件：{src}"
                )

            failed_targets: list[str] = []
            for one_dst in dst_list:
                try:
                    copy_file_with_verify(src, one_dst)
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

        with self._lock:
            for t in self._tasks:
                if t.get("id") == task.get("id"):
                    t["last_run_ts"] = time.time()
                    t["last_result"] = result
                    break
            self._save()

    def run_all_enabled_once(self) -> int:
        """执行一次所有启用任务，返回实际执行的任务数。"""
        with self._lock:
            due = [dict(t) for t in self._tasks if t.get("enabled")]
        for task in due:
            self._execute_task(task)
        return len(due)

    def _scheduler_loop(self) -> None:
        while not self._stop_event.wait(CHECK_INTERVAL_SEC):
            now = time.time()
            with self._lock:
                due: list[dict] = []
                for t in self._tasks:
                    if not t.get("enabled"):
                        continue
                    last_ts = float(t.get("last_run_ts", 0) or 0)
                    interval_sec = max(1, int(t.get("interval_min", 1) or 1)) * 60
                    if last_ts <= 0:
                        start_ts = self._compute_next_start_ts(str(t.get("start_time", "") or ""), now)
                        if start_ts is not None:
                            if now >= start_ts:
                                due.append(dict(t))
                            continue
                    if (now - last_ts) >= interval_sec:
                        due.append(dict(t))
            for task in due:
                self._execute_task(task)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="copy-task-scheduler")
        self._thread.start()
        self._log.info("[定时拷贝] 调度器已启动")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        self._log.info("[定时拷贝] 调度器已停止")


# ─────────────────────────── 命令行入口 ───────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="定时拷贝任务脚本")
    p.add_argument("--tasks-json", default=DEFAULT_COPY_TASKS_JSON, help="任务 JSON 路径")
    p.add_argument("--prefix", default=DEFAULT_CONTACT_PREFIX, help="默认动态前缀（任务未配置时使用）")
    p.add_argument("--run-once", action="store_true", help="只执行一次所有启用任务后退出")
    p.add_argument("--log-level", default="INFO", help="日志级别（DEBUG/INFO/WARNING/ERROR）")
    return p


def main() -> int:
    args = build_arg_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger("copy-task-script")

    mgr = CopyTaskManager(
        json_path=args.tasks_json,
        logger=log,
        default_contact_prefix=args.prefix,
    )

    if args.run_once:
        count = mgr.run_all_enabled_once()
        log.info(f"[定时拷贝] 单次执行完成，任务数：{count}")
        return 0

    mgr.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("[定时拷贝] 收到中断信号，准备退出")
    finally:
        mgr.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

