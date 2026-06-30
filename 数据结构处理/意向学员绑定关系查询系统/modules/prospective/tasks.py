# -*- coding: utf-8 -*-
"""
意向查询系统 —— 后台定时任务（Phase 2d 抽出）
=================================================

把原本散落在 ``db_viewer.DatabaseViewer`` 中的「定时自动刷新」相关字段与方法
（``auto_refresh_timer_id`` / ``auto_refresh_interval`` /
``_start_auto_refresh`` / ``_stop_auto_refresh`` / ``_auto_refresh_callback``）
封装为一个独立的、可复用的调度器：

    AutoRefreshScheduler

设计要点：
    1. 只依赖 Tk 的 ``after`` / ``after_cancel``，与具体业务解耦。
    2. 一次性触发模型（one-shot）：定时器到点时调用回调一次，
       是否继续轮询由调用方负责（``load_data()`` 成功后重新调用 ``start()``）。
       这与原 ``_start_auto_refresh / _auto_refresh_callback`` 行为完全一致，
       避免「上一次刷新失败 / 卡住时还在不停叠加新定时器」的隐患。
    3. 重复 ``start()`` 会先取消上一次的待执行任务，确保只有一个待触发任务。
    4. ``stop()`` 在 Tk 已销毁等场景下会静默吞掉异常，便于 ``__del__`` 兜底调用。
"""

import tkinter as tk
from typing import Callable, Optional


class AutoRefreshScheduler(object):
    """
    定时自动刷新调度器（一次性 after，需要调用方在每次成功后再次 start）。
    """

    # 默认 5 分钟（毫秒）
    DEFAULT_INTERVAL_MS = 300000

    def __init__(self, parent, callback, interval_ms=DEFAULT_INTERVAL_MS):
        # type: (tk.Misc, Callable[[], None], int) -> None
        """
        Args:
            parent      : 用于 ``after`` / ``after_cancel`` 的 Tk widget（一般是 root）
            callback    : 到点要执行的无参回调（一般是 viewer.load_data）
            interval_ms : 间隔毫秒，默认 300000（5 分钟）
        """
        self._parent = parent
        self._callback = callback
        self._interval_ms = interval_ms
        self._timer_id = None  # type: Optional[str]

    @property
    def interval_ms(self):
        """当前间隔（毫秒），便于外部读取展示。"""
        return self._interval_ms

    def is_running(self):
        # type: () -> bool
        """是否有待触发的定时任务。"""
        return self._timer_id is not None

    def start(self):
        """
        启动（或重启）定时器。

        若已有待触发任务，会先取消再排一次新的，确保最多只有一个待触发任务。
        """
        # 先取消上一次（避免重复排队）
        self.stop()

        # 排一次新的 after
        self._timer_id = self._parent.after(self._interval_ms, self._on_fire)
        print("[\u5b9a\u65f6\u5237\u65b0] \u5df2\u542f\u52a8\uff0c\u5c06\u5728 {} \u5206\u949f\u540e\u81ea\u52a8\u5237\u65b0".format(
            self._interval_ms // 60000
        ))

    def stop(self):
        """
        停止定时器（无任务时无副作用）。

        在 Tk 已销毁等异常场景下静默吞掉异常，便于 __del__ 安全调用。
        """
        if self._timer_id is None:
            return

        try:
            self._parent.after_cancel(self._timer_id)
            print("[\u5b9a\u65f6\u5237\u65b0] \u5df2\u505c\u6b62")
        except (tk.TclError, ValueError, Exception):
            # 静默忽略：可能 Tk 已销毁，或 timer_id 已失效
            pass
        finally:
            self._timer_id = None

    def _on_fire(self):
        """
        定时器到点：清掉 id 后回调业务函数。

        不在内部自动 re-arm —— 由业务回调（如 load_data 成功路径）显式 start()，
        与原始实现完全一致。
        """
        self._timer_id = None
        print("[\u5b9a\u65f6\u5237\u65b0] \u5f00\u59cb\u81ea\u52a8\u5237\u65b0\u6570\u636e...")
        self._callback()
