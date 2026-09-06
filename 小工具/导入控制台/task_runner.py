# -*- coding: utf-8 -*-
"""
全局任务队列 / 单 worker 调度器
================================

设计目的：
    导入控制台原本每个 Tab 都自己一个 busy 锁，"碰到已在跑就直接跳过"。
    本模块提供一个"全局排队 + 单 worker"模型：
        - 任何 Tab 想"排队执行"时，把任务丢进队列
        - 一个常驻 worker 线程按 FIFO 顺序执行
        - 同一时刻只跑 1 个任务，不会让多种任务同时打远端 / 抢本地资源

UI 透明：
    GUI 调用 submit() 后立刻返回（非阻塞），用户能继续点别的；
    任务执行过程中通过传入的 logger 回写日志；
    Tab 卡片状态/状态栏由 UI 侧基于 status_callback 回调刷新。

线程安全：
    - submit / cancel / list 全部加同一把锁
    - worker 通过 queue.Queue 拿任务（block=True），不会忙等
    - 任务函数本身的异常被 worker 捕获并写日志，绝不让线程挂掉
"""

from __future__ import annotations

import threading
import time
import queue
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Dict, Any


# ─────────────────────────────── 数据类 ───────────────────────────────
@dataclass
class Task:
    """队列里的单个任务。

    Attributes:
        id        : 任务唯一 ID（uuid hex 前 8 位，便于日志识别）
        name      : 任务显示名（GUI 顶部状态栏会展示），如「内部备注导入」
        callable_ : 真正的执行体，约定签名 (log) -> bool
        log       : 任务执行时使用的 logger（一般直接传 INC.logger）
        on_done   : 任务跑完之后的回调，签名 (ok: bool, exc: Optional[Exception]) -> None
                    用于让 UI 刷新"最近一次状态/时间"，可为 None
        submitted_at : 入队时间戳，便于日志统计排队时长
        started_at   : 实际开始时间，worker 会写入
        finished_at  : 实际结束时间，worker 会写入
    """
    id: str
    name: str
    callable_: Callable[[Any], Any]
    log: Any
    on_done: Optional[Callable[[bool, Optional[BaseException]], None]] = None
    submitted_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0


# ─────────────────────────────── 调度器 ───────────────────────────────
class TaskRunner:
    """全局单 worker 调度器，整个控制台进程只起一个实例。

    使用方式：
        runner = TaskRunner()
        runner.start()

        runner.submit(name="内部备注导入", callable_=lambda log: pipe(...), log=INC.logger)

        # GUI 刷新时
        snapshot = runner.snapshot()
        # snapshot["current"]  正在执行的任务（dict 或 None）
        # snapshot["pending"]  等待中的任务列表
    """

    def __init__(self) -> None:
        self._q: "queue.Queue[Task]" = queue.Queue()
        self._lock = threading.Lock()
        self._current: Optional[Task] = None
        self._pending: List[Task] = []  # 与 _q 同步的"可窥探"快照，仅做 UI 展示用
        self._stop_flag = threading.Event()
        self._worker: Optional[threading.Thread] = None

        # 可选状态回调：每次队列/当前任务变化时触发；UI 注册一个就够了
        # 回调签名 () -> None，回调内部应通过 root.after 切回 UI 线程
        self._status_callbacks: List[Callable[[], None]] = []

    # ── 生命周期 ──────────────────────────────────────────────────
    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop_flag.clear()
        self._worker = threading.Thread(
            target=self._run_loop, name="task-runner", daemon=True
        )
        self._worker.start()

    def stop(self, drain: bool = False, timeout: float = 5.0) -> None:
        """退出前调用；drain=True 等当前任务自然结束。"""
        self._stop_flag.set()
        # 塞一个哨兵唤醒 worker
        self._q.put(_SENTINEL)
        if self._worker is not None and drain:
            self._worker.join(timeout=timeout)

    # ── 公共接口 ──────────────────────────────────────────────────
    def submit(
        self,
        name: str,
        callable_: Callable[[Any], Any],
        log: Any,
        on_done: Optional[Callable[[bool, Optional[BaseException]], None]] = None,
    ) -> str:
        """把任务塞进队列；立即返回任务 ID（非阻塞）。"""
        task = Task(
            id=uuid.uuid4().hex[:8],
            name=name,
            callable_=callable_,
            log=log,
            on_done=on_done,
        )
        with self._lock:
            self._pending.append(task)
        self._q.put(task)
        try:
            log.info(
                f"[队列] 已加入：{task.name}（id={task.id}），"
                f"当前排队 {self.pending_count()} 个"
            )
        except Exception:
            pass
        self._fire_status()
        return task.id

    def cancel(self, task_id: str) -> bool:
        """取消尚未开始执行的任务。已开始的无法取消（避免强杀线程）。"""
        cancelled = False
        # 重建 pending 列表，跳过待取消项
        with self._lock:
            new_pending: List[Task] = []
            for t in self._pending:
                if t.id == task_id:
                    cancelled = True
                    continue
                new_pending.append(t)
            self._pending = new_pending

        if cancelled:
            # 同步把队列里对应任务"打个标记"——简单做法：重建队列
            # 因为 queue.Queue 不能按值删除，这里用一次性重建
            tmp: List[Task] = []
            try:
                while True:
                    tmp.append(self._q.get_nowait())
            except queue.Empty:
                pass
            for t in tmp:
                if t.id != task_id and t is not _SENTINEL:
                    self._q.put(t)
            self._fire_status()
        return cancelled

    def snapshot(self) -> Dict[str, Any]:
        """给 UI 拍一张当前状态快照，供顶部状态栏 / 卡片刷新使用。"""
        with self._lock:
            cur = None
            if self._current is not None:
                cur = {
                    "id": self._current.id,
                    "name": self._current.name,
                    "started_at": self._current.started_at,
                }
            pending = [
                {"id": t.id, "name": t.name, "submitted_at": t.submitted_at}
                for t in self._pending
            ]
        return {"current": cur, "pending": pending}

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def is_busy(self) -> bool:
        with self._lock:
            return self._current is not None or len(self._pending) > 0

    # ── UI 回调注册 ───────────────────────────────────────────────
    def add_status_listener(self, cb: Callable[[], None]) -> None:
        with self._lock:
            self._status_callbacks.append(cb)

    def _fire_status(self) -> None:
        # 不持锁触发，避免回调里再 snapshot 死锁
        with self._lock:
            cbs = list(self._status_callbacks)
        for cb in cbs:
            try:
                cb()
            except Exception:
                pass

    # ── worker 主循环 ──────────────────────────────────────────────
    def _run_loop(self) -> None:
        while not self._stop_flag.is_set():
            task = self._q.get()
            if task is _SENTINEL:
                break

            # 队列内被 cancel 的任务会被 cancel() 重建队列时滤掉，
            # 但仍可能因竞态走到这里——再 double-check 一次。
            with self._lock:
                if task not in self._pending:
                    # 已被取消
                    continue
                self._pending.remove(task)
                self._current = task
                task.started_at = time.time()

            self._fire_status()

            ok = False
            exc: Optional[BaseException] = None
            try:
                try:
                    task.log.info(
                        f"[队列] 开始执行：{task.name}（id={task.id}）"
                    )
                except Exception:
                    pass
                # 约定：callable_ 接受一个 log 参数；用户给的也允许是 ()->bool
                # 简化：所有任务统一调用方式为 callable_(log)
                rc = task.callable_(task.log)
                ok = bool(rc) if rc is not None else True
            except BaseException as e:
                exc = e
                try:
                    task.log.error(
                        f"[队列] 任务异常：{task.name}（id={task.id}）→ {e}",
                        exc_info=True,
                    )
                except Exception:
                    pass

            with self._lock:
                task.finished_at = time.time()
                self._current = None

            self._fire_status()

            if task.on_done is not None:
                try:
                    task.on_done(ok, exc)
                except Exception:
                    pass


# 队列哨兵：用于唤醒阻塞在 q.get() 上的 worker 触发退出
_SENTINEL = Task(
    id="__sentinel__", name="__sentinel__", callable_=lambda log: None, log=None,
)
