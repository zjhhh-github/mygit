# -*- coding: utf-8 -*-
"""
意向查询系统 —— UI 通用控件（Phase 2c 抽出）
=================================================

把原本散落在 ``db_viewer.DatabaseViewer`` 中的 Toast 与 Loading 相关 UI 逻辑
封装为两个独立、可复用的组件：

    ToastManager     —— 居中浮层提示（成功 ✓ / 警告 ⚠）
    LoadingOverlay   —— 半透明遮罩 + 中央滑动进度条

设计目标：
    1. UI 控件本身负责 Tk 资源的创建、显示、隐藏、定时取消，
       业务代码只关心「我要提示什么 / 我要不要遮罩」。
    2. 不依赖 DatabaseViewer 自身，仅依赖一个 ``parent`` widget（一般是 root），
       便于将来嵌入到其他 Tab / 容器中复用。
    3. 接口保持与原方法语义一致，方便 db_viewer.py 中保留同名 thin shim：
           - ToastManager.show_info(content, duration_ms=1500)   ↔ _show_toast
           - ToastManager.show_warning(content, duration_ms=5000) ↔ _show_warning_toast
           - ToastManager.hide()                                  ↔ _hide_toast
           - LoadingOverlay.show()                                ↔ _show_loading
           - LoadingOverlay.hide()                                ↔ _hide_loading

注意：
    - ToastManager 的 ``label`` / ``var`` 由调用方在 _create_widgets 中创建好
      后再注入，沿用原有视觉风格与布局位置。
    - LoadingOverlay 自带 frame + canvas + bar，构造时即创建（保持隐藏），
      调用方不需要再额外实现 _create_loading_widget。
"""

import tkinter as tk
from typing import Optional


class ToastManager(object):
    """
    Toast 浮层管理器。

    成功提示（绿色 ✓ 已复制：xxx）默认 1.5s 自动消失；
    警告提示（红色 ⚠ xxx）默认 5s 自动消失。
    多次连续触发会复用同一个 Label，自动取消上一次的隐藏定时器，避免抖动。
    """

    # 单条 Toast 显示内容上限（超出会截断 + "..."）
    _MAX_CONTENT_LEN = 50

    def __init__(self, parent, label, var):
        # type: (tk.Misc, tk.Label, tk.StringVar) -> None
        """
        Args:
            parent: 用于调度 ``after`` 的 widget（一般是 root）
            label : 已经创建好的 Toast 标签（隐藏状态、绿色背景默认）
            var   : 与 label 绑定的 StringVar
        """
        self._parent = parent
        self._label = label
        self._var = var
        self._after_id = None  # type: Optional[str]

    def show_info(self, content, duration_ms=1500):
        # type: (str, int) -> None
        """显示成功 Toast（绿色背景，✓ 图标 + "已复制：" 前缀）。"""
        self._cancel_pending()

        display = content if len(content) <= self._MAX_CONTENT_LEN \
            else content[:self._MAX_CONTENT_LEN - 3] + "..."

        self._var.set("\u2713 \u5df2\u590d\u5236\uff1a{}".format(display))
        self._label.config(bg="#2ecc71")
        self._label.place(relx=0.5, rely=0.15, anchor="n")

        self._after_id = self._parent.after(duration_ms, self.hide)

    def show_warning(self, content, duration_ms=5000):
        # type: (str, int) -> None
        """显示警告 Toast（红色背景，⚠ 图标）。"""
        self._cancel_pending()

        self._var.set("\u26a0 {}".format(content))
        self._label.config(bg="#e74c3c")
        self._label.place(relx=0.5, rely=0.15, anchor="n")

        self._after_id = self._parent.after(duration_ms, self.hide)

    def hide(self):
        """隐藏 Toast 标签并清空定时器。"""
        try:
            self._label.place_forget()
        except tk.TclError:
            # 主窗口已销毁时忽略
            pass
        self._after_id = None

    def _cancel_pending(self):
        """取消上一次的自动隐藏定时器（如有）。"""
        if self._after_id is not None:
            try:
                self._parent.after_cancel(self._after_id)
            except (tk.TclError, ValueError):
                pass
            self._after_id = None


class LoadingOverlay(object):
    """
    Loading 遮罩 + 进度条。

    构造时即创建好所有 Tk 控件（保持隐藏状态），
    调用 ``show()`` 时铺满 parent，并启动左右滑动动画；
    调用 ``hide()`` 时停止动画并隐藏。
    """

    # 进度条尺寸 / 速度参数（保持与原实现一致）
    _BAR_WIDTH = 100
    _TRACK_WIDTH = 400
    _TRACK_HEIGHT = 6
    _STEP = 20            # 每次移动的像素
    _INTERVAL_MS = 15     # 动画刷新间隔
    _BG_COLOR = "#f5f6fa"
    _TRACK_COLOR = "#FEF5E7"
    _BAR_COLOR = "#f39c12"

    def __init__(self, parent):
        # type: (tk.Misc) -> None
        self._parent = parent

        self._position = 0
        self._direction = 1  # 1=右移, -1=左移
        self._anim_id = None  # type: Optional[str]

        # 半透明遮罩 frame（背景色与主窗口一致）
        self._frame = tk.Frame(
            parent,
            bg=self._BG_COLOR,
            bd=0,
            highlightthickness=0,
        )

        # 中央容器
        container = tk.Frame(
            self._frame,
            bg=self._BG_COLOR,
            bd=0,
            highlightthickness=0,
        )
        container.place(relx=0.5, rely=0.5, anchor="center")

        # 提示文字
        tk.Label(
            container,
            text="\u6b63\u5728\u52a0\u8f7d\u6570\u636e...",
            font=("\u5fae\u8f6f\u96c5\u9ed1", 13),
            fg="#2c3e50",
            bg=self._BG_COLOR,
        ).pack(pady=(0, 20))

        # 进度条 Canvas
        self._canvas = tk.Canvas(
            container,
            width=self._TRACK_WIDTH,
            height=self._TRACK_HEIGHT,
            bg=self._BG_COLOR,
            bd=0,
            highlightthickness=0,
        )
        self._canvas.pack()

        # 进度条背景（浅色）
        self._canvas.create_rectangle(
            0, 0, self._TRACK_WIDTH, self._TRACK_HEIGHT,
            fill=self._TRACK_COLOR,
            outline="",
            tags="bg",
        )

        # 进度条主体（橙色，可移动）
        self._bar_id = self._canvas.create_rectangle(
            0, 0, self._BAR_WIDTH, self._TRACK_HEIGHT,
            fill=self._BAR_COLOR,
            outline="",
            tags="bar",
        )

    def show(self):
        """显示遮罩并启动动画。"""
        self._frame.place(x=0, y=0, relwidth=1, relheight=1)
        # 强制刷新，确保 Canvas 立即可见
        try:
            self._parent.update_idletasks()
        except tk.TclError:
            pass
        self._animate()

    def hide(self):
        """停止动画并隐藏遮罩。"""
        if self._anim_id is not None:
            try:
                self._parent.after_cancel(self._anim_id)
            except (tk.TclError, ValueError):
                pass
            self._anim_id = None

        try:
            self._frame.place_forget()
        except tk.TclError:
            pass

        # 重置位置，下次 show() 从最左端开始
        self._position = 0
        self._direction = 1

    def _animate(self):
        """进度条左右滑动动画（递归 after 实现）。"""
        max_pos = self._TRACK_WIDTH - self._BAR_WIDTH

        self._position += self._direction * self._STEP
        if self._position >= max_pos:
            self._position = max_pos
            self._direction = -1
        elif self._position <= 0:
            self._position = 0
            self._direction = 1

        try:
            self._canvas.coords(
                self._bar_id,
                self._position, 0,
                self._position + self._BAR_WIDTH, self._TRACK_HEIGHT,
            )
            self._anim_id = self._parent.after(self._INTERVAL_MS, self._animate)
        except tk.TclError:
            # 父容器已销毁
            self._anim_id = None
