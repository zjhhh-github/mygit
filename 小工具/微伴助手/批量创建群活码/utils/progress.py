# -*- coding: utf-8 -*-
"""
批量创建进度记录：记录已成功/失败项，支持中断后续跑。

记录文件（项目目录下）：
- batch_progress.json：结构化进度，程序续跑用
- 创建记录.log：人类可读日志，方便查看创建到了谁
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _now_text() -> str:
    """返回当前时间的可读字符串。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ProgressTracker:
    """管理批量创建进度，每条成功后立即落盘。"""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.progress_file = self.base_dir / "batch_progress.json"
        self.log_file = self.base_dir / "创建记录.log"
        self.data = self._load()

    def _default_data(self) -> dict:
        """初始化空的进度结构。"""
        return {
            "completed": [],
            "failed": [],
            "last_completed_index": 0,
            "last_completed_nick_name": "",
            "updated_at": "",
        }

    def _load(self) -> dict:
        """读取已有进度；不存在则返回默认结构。"""
        if not self.progress_file.exists():
            return self._default_data()

        try:
            raw = json.loads(self.progress_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._default_data()

        data = self._default_data()
        if isinstance(raw, dict):
            data.update(raw)
        return data

    def _save(self) -> None:
        """保存 JSON 进度文件。"""
        self.data["updated_at"] = _now_text()
        self.progress_file.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _append_log(self, line: str) -> None:
        """追加一行人类可读日志。"""
        with self.log_file.open("a", encoding="utf-8") as file:
            file.write(f"{_now_text()} {line}\n")

    @property
    def completed_names(self) -> set[str]:
        """已成功创建的 nick_name 集合。"""
        names: set[str] = set()
        for item in self.data.get("completed", []):
            if isinstance(item, dict) and item.get("nick_name"):
                names.add(str(item["nick_name"]))
        return names

    @property
    def completed_count(self) -> int:
        """已成功条数。"""
        return len(self.data.get("completed", []))

    @property
    def last_completed_nick_name(self) -> str:
        """最后一次成功创建的 nick_name。"""
        return str(self.data.get("last_completed_nick_name") or "")

    @property
    def last_completed_index(self) -> int:
        """最后一次成功创建的序号（从 1 开始）。"""
        return int(self.data.get("last_completed_index") or 0)

    def reset(self) -> None:
        """清空进度记录（不会删除 log 文件历史）。"""
        self.data = self._default_data()
        self._save()
        self._append_log("[系统] 已清空 batch_progress.json 进度记录")

    def print_summary(self, total_count: int) -> None:
        """启动时打印当前进度摘要。"""
        completed = self.completed_count
        pending = max(total_count - completed, 0)
        print(f"进度记录文件：{self.progress_file}")
        print(f"可读日志文件：{self.log_file}")
        print(f"已完成：{completed}/{total_count}，待处理：{pending}")
        if self.last_completed_nick_name:
            print(
                f"上次成功：第 {self.last_completed_index} 条 "
                f"「{self.last_completed_nick_name}」"
            )

    def build_pending_items(
        self,
        nick_names: list[str],
        *,
        start_index: int = 1,
    ) -> list[tuple[int, str]]:
        """根据进度记录生成待处理列表（跳过已成功项）。"""
        completed = self.completed_names
        pending: list[tuple[int, str]] = []

        for index, nick_name in enumerate(nick_names, 1):
            if index < start_index:
                continue
            if nick_name in completed:
                continue
            pending.append((index, nick_name))

        return pending

    def mark_completed(self, index: int, nick_name: str, total_count: int) -> None:
        """记录一条成功，并立即写入磁盘。"""
        record = {
            "index": index,
            "nick_name": nick_name,
            "completed_at": _now_text(),
        }
        self.data.setdefault("completed", []).append(record)
        self.data["last_completed_index"] = index
        self.data["last_completed_nick_name"] = nick_name

        # 若之前失败过，成功后从失败列表移除
        self.data["failed"] = [
            item
            for item in self.data.get("failed", [])
            if not (
                isinstance(item, dict)
                and item.get("nick_name") == nick_name
                and item.get("index") == index
            )
        ]

        self._save()
        self._append_log(f"[成功] [{index}/{total_count}] {nick_name}")

    def mark_failed(
        self,
        index: int,
        nick_name: str,
        error: str,
        total_count: int,
    ) -> None:
        """记录一条失败，并立即写入磁盘。"""
        record = {
            "index": index,
            "nick_name": nick_name,
            "error": error,
            "failed_at": _now_text(),
        }
        self.data.setdefault("failed", []).append(record)
        self._save()
        self._append_log(f"[失败] [{index}/{total_count}] {nick_name} -> {error}")
