# -*- coding: utf-8 -*-
"""
自动 Excel 备份模块
====================

职责：
    在数据刷新成功之后，把"意向专用通讯录"静默导出一份 Excel 到 NAS 目录，
    以便其他同事直接取用。

设计要点：
    1. 本模块只关心"何时导出 / 导出到哪里 / 失败如何提示去重"，
       具体的 Excel 生成逻辑仍然复用主程序里的 `_do_export_full`
       （因为手动导出按钮也在用同一份逻辑，避免重复实现）。
    2. 通过依赖注入方式，让模块与 db_viewer 解耦：
        - exporter   ：实际执行导出的可调用对象（即 _do_export_full）
        - data_provider：返回当前完整数据集的可调用对象（即 lambda: self.all_data）
        - tk_root    ：用于在 UI 线程上调度 toast 提示（可为 None）
        - toast_warn ：警告 toast 函数，可为 None；为 None 时只打印日志
    3. 任何异常都不会抛到调用方，确保不影响主刷新流程。

使用示例（在 db_viewer 的 __init__ 末尾）：

    from modules.auto_excel_backup import AutoExcelBackup

    self.auto_excel_backup = AutoExcelBackup(
        exporter=self._do_export_full,
        data_provider=lambda: self.all_data,
        tk_root=self.root,
        toast_warn=self._show_warning_toast,
    )

刷新成功的回调里，将原本的：

    self._auto_export_after_refresh()

替换为：

    self.auto_excel_backup.trigger()
"""

import os
from datetime import datetime
from typing import Any, Callable, List, Optional


# 默认的 NAS 自动导出目录
# 用途：每次数据刷新成功后，将"意向专用通讯录"静默导出到该目录，便于其他同事直接取用
# 注意：仅在该目录已存在且可访问时才导出，不会自动创建
DEFAULT_AUTO_EXPORT_DIR = r"X:\backup\ProspectiveContacts"


# 导出函数签名：exporter(timestamp, output_dir=None) -> (success, path_or_name, error)
ExporterFunc = Callable[..., Any]
# 数据提供函数签名：data_provider() -> List[dict]
DataProviderFunc = Callable[[], List[dict]]
# Toast 提示函数签名：toast_warn(text, duration_ms=...)
ToastFunc = Callable[..., Any]


class AutoExcelBackup:
    """
    自动 Excel 备份器

    Attributes:
        export_dir: 实际生效的导出目录（默认 DEFAULT_AUTO_EXPORT_DIR）
        last_ok:    上一次自动导出是否成功；用于失败提示去重，
                    避免每 5 分钟自动刷新连续失败时刷屏。
    """

    def __init__(
        self,
        exporter: ExporterFunc,
        data_provider: DataProviderFunc,
        tk_root: Any = None,
        toast_warn: Optional[ToastFunc] = None,
        export_dir: Optional[str] = None,
    ) -> None:
        """
        Args:
            exporter:      实际执行 Excel 导出的回调，签名同 _do_export_full
                            (timestamp: str, output_dir: Optional[str]) -> (bool, str, str)
            data_provider: 返回当前完整数据集（list[dict]）的回调
            tk_root:       Tk 根窗口（用于在 UI 线程调度 toast）；为 None 时不弹 toast
            toast_warn:    警告 toast 函数；为 None 时不弹 toast
            export_dir:    可选，覆盖默认导出目录
        """
        self._exporter = exporter
        self._data_provider = data_provider
        self._tk_root = tk_root
        self._toast_warn = toast_warn
        self.export_dir: str = export_dir or DEFAULT_AUTO_EXPORT_DIR

        # 上一次自动导出是否成功；初始为 True，表示"尚未失败过"
        # 仅在"上一次成功 → 本次失败"时才弹一次 toast，连续失败只打日志
        self.last_ok: bool = True

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------
    def trigger(self) -> None:
        """
        触发一次自动导出。

        典型调用时机：UI 已用最新数据刷新完成之后。
        本方法保证"任何异常都不抛到调用方"，因此可以放心 try/except 包一层即可。
        """
        try:
            self._do_trigger()
        except Exception as e:
            # 双保险：自动导出任何异常都不能影响刷新主流程
            print(f"[自动导出] 触发异常（已忽略）：{type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _do_trigger(self) -> None:
        """实际的导出流程：检查数据 → 检查目录 → 调用 exporter → 处理结果。"""
        # 1) 没有数据时直接跳过（不视为失败，不重置状态）
        data = self._data_provider() or []
        if len(data) == 0:
            print("[自动导出] 跳过：当前没有可导出的数据")
            return

        # 2) 检查 NAS 目录是否可访问（不存在则跳过，不自动创建）
        if not (os.path.exists(self.export_dir) and os.path.isdir(self.export_dir)):
            print(f"[自动导出] 自动导出目录不可访问，已跳过本次自动导出：{self.export_dir}")
            # 仅在状态翻转（上次成功 → 本次失败）时提示一次，避免刷屏
            if self.last_ok:
                self.last_ok = False
                self._safe_toast("自动导出目录不可访问，已跳过本次自动导出")
            return

        # 3) 执行导出（复用现有核心逻辑，只是指定输出目录）
        # 时间戳精确到毫秒，避免同一秒内重复触发自动导出导致同名覆盖
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        try:
            success, path_or_name, error = self._exporter(
                timestamp, output_dir=self.export_dir
            )
        except Exception as e:
            success, path_or_name, error = False, "", f"{type(e).__name__}: {e}"

        if success:
            print(f"[自动导出] 成功：{path_or_name}（共 {len(data)} 条）")
            self.last_ok = True
        else:
            print(f"[自动导出] 失败：{error}")
            if self.last_ok:
                self.last_ok = False
                self._safe_toast(f"自动导出失败：{error}")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def _safe_toast(self, text: str) -> None:
        """
        安全地弹一条警告 toast：
            - 若没有 tk_root 或 toast_warn，则只打日志
            - 通过 root.after 切回 UI 线程，避免线程安全问题
            - 任何异常都吞掉，绝不影响主流程
        """
        if self._tk_root is None or self._toast_warn is None:
            return
        try:
            self._tk_root.after(
                600,
                lambda t=text: self._toast_warn(t, duration_ms=5000),
            )
        except Exception:
            pass
