# -*- coding: utf-8 -*-
r"""
头像查询工具箱（可视化整合版）：把「导出用户名md5映射」「相似图片查找」
两个功能合并进同一个窗口，打包成一个 exe 就能用。

【包含哪两个功能，分别对应哪个标签页】
    1. 「导出 MD5 映射」标签页：
        对应 导出用户名md5映射.py 的 执行导出() 逻辑，可在界面上选择
        一个或多个微信联系人数据库文件，选择 CSV 输出位置，点「开始导出」即可。
    2. 「相似图片查找」标签页：
        对应 find_similar_images.py 的 find_similar_images() 逻辑，可在界面上
        选择一张参考图片、选择待搜索的图片文件夹，设置阈值和返回数量，
        结果会显示在下方表格里；若加载了 MD5 映射 CSV，还会尝试根据文件名
        反查联系人备注/昵称。

【依赖】
    - GUI：Python 内置 tkinter（无需 pip install）
    - 相似图片查找：pillow、numpy（打包前需 pip install）

【用法】
    cd D:\git\mygit\小工具\头像查询
    python 头像查询工具箱_GUI.py
"""

from __future__ import annotations

import csv
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Callable, Optional

# 同目录下的业务脚本必须放在一起，才能互相 import
sys.path.insert(0, str(Path(__file__).resolve().parent))
import find_similar_images as 相似图片模块  # noqa: E402
import 导出用户名md5映射 as 映射导出模块  # noqa: E402


def _程序目录() -> Path:
    """脚本直接运行时用源码目录；PyInstaller 打包后用 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _默认映射csv路径() -> str:
    return str(_程序目录() / "username_md5_mapping.csv")


class 队列输出流:
    """把 print 输出重定向到队列，由主线程轮询后写入日志文本框。"""

    def __init__(self, 输出队列: "queue.Queue[str]"):
        self._队列 = 输出队列

    def write(self, 内容: str) -> int:
        if 内容:
            self._队列.put(内容)
        return len(内容)

    def flush(self) -> None:
        pass


class 导出Md5映射标签页:
    """「导出 MD5 映射」标签页：选择数据库 → 选择输出 CSV → 开始导出。"""

    def __init__(self, 容器: tk.Widget):
        self.容器 = 容器
        self._运行中 = False
        self._日志队列: "queue.Queue[str]" = queue.Queue()
        self._数据库路径列表: list[str] = list(映射导出模块.DB_PATHS)
        self._输出路径变量 = tk.StringVar(value=_默认映射csv路径())

        self._构建界面()
        self.容器.after(100, self._轮询队列)

    def _构建界面(self) -> None:
        说明 = tk.Label(
            self.容器,
            text=(
                "从微信联系人 SQLite 数据库导出 username → md5 映射表。\n"
                "微信本地头像缓存文件名就是 md5(username)，导出后可用 Excel 搜索联系人。"
            ),
            justify="left",
            anchor="w",
        )
        说明.pack(fill="x", padx=12, pady=(12, 6))

        数据库区 = tk.LabelFrame(self.容器, text="联系人数据库（可选择一个或多个 .db 文件）")
        数据库区.pack(fill="x", padx=12, pady=6)

        按钮行 = tk.Frame(数据库区)
        按钮行.pack(fill="x", padx=8, pady=4)
        tk.Button(按钮行, text="添加数据库…", command=self._添加数据库).pack(side="left")
        tk.Button(按钮行, text="清空列表", command=self._清空数据库列表).pack(side="left", padx=(8, 0))

        列表框容器 = tk.Frame(数据库区)
        列表框容器.pack(fill="x", padx=8, pady=(0, 8))
        滚动条 = tk.Scrollbar(列表框容器, orient="vertical")
        self._数据库列表框 = tk.Listbox(列表框容器, height=4, yscrollcommand=滚动条.set)
        滚动条.config(command=self._数据库列表框.yview)
        滚动条.pack(side="right", fill="y")
        self._数据库列表框.pack(side="left", fill="x", expand=True)
        self._刷新数据库列表框()

        输出区 = tk.LabelFrame(self.容器, text="CSV 输出路径")
        输出区.pack(fill="x", padx=12, pady=6)
        输出行 = tk.Frame(输出区)
        输出行.pack(fill="x", padx=8, pady=8)
        tk.Entry(输出行, textvariable=self._输出路径变量).pack(side="left", fill="x", expand=True)
        tk.Button(输出行, text="选择…", command=self._选择输出路径).pack(side="left", padx=(8, 0))

        操作行 = tk.Frame(self.容器)
        操作行.pack(fill="x", padx=12, pady=6)
        self._开始按钮 = tk.Button(操作行, text="开始导出", command=self._开始导出, width=12)
        self._开始按钮.pack(side="left")
        tk.Button(操作行, text="清空日志", command=self._清空日志).pack(side="left", padx=(8, 0))

        日志区 = tk.LabelFrame(self.容器, text="运行日志")
        日志区.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self._日志文本框 = scrolledtext.ScrolledText(日志区, height=10, state="disabled")
        self._日志文本框.pack(fill="both", expand=True, padx=8, pady=8)

    def _刷新数据库列表框(self) -> None:
        self._数据库列表框.delete(0, tk.END)
        for 路径 in self._数据库路径列表:
            self._数据库列表框.insert(tk.END, 路径)

    def _添加数据库(self) -> None:
        选中路径 = filedialog.askopenfilenames(
            title="选择微信联系人数据库",
            filetypes=[("SQLite 数据库", "*.db"), ("所有文件", "*.*")],
        )
        if not 选中路径:
            return
        for 路径 in 选中路径:
            if 路径 not in self._数据库路径列表:
                self._数据库路径列表.append(路径)
        self._刷新数据库列表框()

    def _清空数据库列表(self) -> None:
        self._数据库路径列表.clear()
        self._刷新数据库列表框()

    def _选择输出路径(self) -> None:
        选中路径 = filedialog.asksaveasfilename(
            title="选择 CSV 输出位置",
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            initialfile="username_md5_mapping.csv",
        )
        if 选中路径:
            self._输出路径变量.set(选中路径)

    def _清空日志(self) -> None:
        self._日志文本框.config(state="normal")
        self._日志文本框.delete("1.0", tk.END)
        self._日志文本框.config(state="disabled")

    def _追加日志(self, 内容: str) -> None:
        self._日志文本框.config(state="normal")
        self._日志文本框.insert(tk.END, 内容)
        self._日志文本框.see(tk.END)
        self._日志文本框.config(state="disabled")

    def _轮询队列(self) -> None:
        try:
            while True:
                self._追加日志(self._日志队列.get_nowait())
        except queue.Empty:
            pass
        self.容器.after(100, self._轮询队列)

    def _开始导出(self) -> None:
        if self._运行中:
            return
        if not self._数据库路径列表:
            messagebox.showwarning("提示", "请至少添加一个联系人数据库文件。")
            return

        输出路径 = self._输出路径变量.get().strip()
        if not 输出路径:
            messagebox.showwarning("提示", "请指定 CSV 输出路径。")
            return

        self._运行中 = True
        self._开始按钮.config(state="disabled", text="导出中…")
        数据库副本 = list(self._数据库路径列表)

        def 后台任务() -> None:
            旧stdout = sys.stdout
            sys.stdout = 队列输出流(self._日志队列)
            try:
                映射导出模块.执行导出(db_paths=数据库副本, output_path=输出路径)
                self._日志队列.put("\n导出完成。\n")
            except Exception as 错误:
                self._日志队列.put(f"\n导出失败: {错误}\n")
            finally:
                sys.stdout = 旧stdout
                self.容器.after(0, self._导出结束)

        threading.Thread(target=后台任务, daemon=True).start()

    def _导出结束(self) -> None:
        self._运行中 = False
        self._开始按钮.config(state="normal", text="开始导出")


class 相似图片查找标签页:
    """「相似图片查找」标签页：选择参考图、搜索文件夹、展示相似结果。"""

    def __init__(self, 容器: tk.Widget):
        self.容器 = 容器
        self._运行中 = False
        self._日志队列: "queue.Queue[str]" = queue.Queue()
        self._进度队列: "queue.Queue[tuple]" = queue.Queue()
        self._结果队列: "queue.Queue[list]" = queue.Queue()

        self._参考图变量 = tk.StringVar(value="")
        self._搜索文件夹变量 = tk.StringVar(value="")
        self._映射csv变量 = tk.StringVar(value=_默认映射csv路径())
        self._阈值变量 = tk.StringVar(value="0.3")
        self._返回数量变量 = tk.StringVar(value="10")

        self._md5映射字典: dict[str, dict[str, str]] = {}
        self._构建界面()
        self.容器.after(100, self._轮询队列)

    def _构建界面(self) -> None:
        说明 = tk.Label(
            self.容器,
            text=(
                "选择一张参考头像，在指定文件夹中查找与之相似的图片。\n"
                "相似度 0~1，越接近 1 越相似；可选加载 MD5 映射 CSV 反查联系人。"
            ),
            justify="left",
            anchor="w",
        )
        说明.pack(fill="x", padx=12, pady=(12, 6))

        参数区 = tk.LabelFrame(self.容器, text="搜索参数")
        参数区.pack(fill="x", padx=12, pady=6)

        self._添加路径行(参数区, "参考图片：", self._参考图变量, self._选择参考图, 是文件夹=False)
        self._添加路径行(参数区, "搜索文件夹：", self._搜索文件夹变量, self._选择搜索文件夹, 是文件夹=True)
        self._添加路径行(参数区, "MD5 映射 CSV：", self._映射csv变量, self._选择映射csv, 是文件夹=False, 可选=True)

        数值行 = tk.Frame(参数区)
        数值行.pack(fill="x", padx=8, pady=4)
        tk.Label(数值行, text="相似度阈值：", width=14, anchor="w").pack(side="left")
        tk.Entry(数值行, textvariable=self._阈值变量, width=8).pack(side="left")
        tk.Label(数值行, text="（0~1，默认 0.3）", fg="#666666").pack(side="left", padx=(6, 24))
        tk.Label(数值行, text="返回数量：", anchor="w").pack(side="left")
        tk.Entry(数值行, textvariable=self._返回数量变量, width=8).pack(side="left")
        tk.Label(数值行, text="（默认 10）", fg="#666666").pack(side="left", padx=(6, 0))

        操作行 = tk.Frame(self.容器)
        操作行.pack(fill="x", padx=12, pady=6)
        self._开始按钮 = tk.Button(操作行, text="开始查找", command=self._开始查找, width=12)
        self._开始按钮.pack(side="left")
        tk.Button(操作行, text="清空结果", command=self._清空结果).pack(side="left", padx=(8, 0))
        self._进度标签 = tk.Label(操作行, text="", fg="#666666")
        self._进度标签.pack(side="left", padx=(12, 0))

        结果区 = tk.LabelFrame(self.容器, text="相似图片结果（双击可复制完整路径）")
        结果区.pack(fill="both", expand=True, padx=12, pady=(6, 6))

        列名 = ("相似度", "文件名", "备注/昵称", "完整路径")
        self._结果表格 = ttk.Treeview(结果区, columns=列名, show="headings", height=8)
        self._结果表格.heading("相似度", text="相似度")
        self._结果表格.heading("文件名", text="文件名")
        self._结果表格.heading("备注/昵称", text="备注/昵称")
        self._结果表格.heading("完整路径", text="完整路径")
        self._结果表格.column("相似度", width=70, anchor="center")
        self._结果表格.column("文件名", width=220, anchor="w")
        self._结果表格.column("备注/昵称", width=180, anchor="w")
        self._结果表格.column("完整路径", width=420, anchor="w")
        表格滚动条 = ttk.Scrollbar(结果区, orient="vertical", command=self._结果表格.yview)
        self._结果表格.configure(yscrollcommand=表格滚动条.set)
        self._结果表格.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        表格滚动条.pack(side="right", fill="y", pady=8, padx=(0, 8))
        self._结果表格.bind("<Double-1>", self._双击复制路径)

        日志区 = tk.LabelFrame(self.容器, text="运行日志")
        日志区.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._日志文本框 = scrolledtext.ScrolledText(日志区, height=6, state="disabled")
        self._日志文本框.pack(fill="both", expand=True, padx=8, pady=8)

    def _添加路径行(
        self,
        父容器: tk.Widget,
        标签文字: str,
        变量: tk.StringVar,
        选择命令: Callable[[], None],
        是文件夹: bool,
        可选: bool = False,
    ) -> None:
        行 = tk.Frame(父容器)
        行.pack(fill="x", padx=8, pady=4)
        后缀 = "（可选）" if 可选 else ""
        tk.Label(行, text=f"{标签文字}{后缀}", width=14, anchor="w").pack(side="left")
        tk.Entry(行, textvariable=变量).pack(side="left", fill="x", expand=True)
        tk.Button(行, text="浏览…", command=选择命令, width=8).pack(side="left", padx=(8, 0))

    def _选择参考图(self) -> None:
        选中路径 = filedialog.askopenfilename(
            title="选择参考图片",
            filetypes=[
                ("图片文件", "*.jpg;*.jpeg;*.png;*.bmp;*.tif;*.tiff;*.gif"),
                ("所有文件", "*.*"),
            ],
        )
        if 选中路径:
            self._参考图变量.set(选中路径)

    def _选择搜索文件夹(self) -> None:
        选中路径 = filedialog.askdirectory(title="选择待搜索的图片文件夹")
        if 选中路径:
            self._搜索文件夹变量.set(选中路径)

    def _选择映射csv(self) -> None:
        选中路径 = filedialog.askopenfilename(
            title="选择 MD5 映射 CSV（可选）",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if 选中路径:
            self._映射csv变量.set(选中路径)

    def _加载md5映射(self) -> None:
        """读取 CSV，建立 md5 -> 联系人信息 字典，用于结果反查。"""
        self._md5映射字典.clear()
        csv路径 = self._映射csv变量.get().strip()
        if not csv路径 or not os.path.isfile(csv路径):
            return

        with open(csv路径, "r", encoding="utf-8-sig", newline="") as 文件:
            读取器 = csv.DictReader(文件)
            for 行 in 读取器:
                md5值 = (行.get("md5") or "").strip().lower()
                if not md5值:
                    continue
                备注 = (行.get("remark") or "").strip()
                昵称 = (行.get("nick_name") or "").strip()
                微信号 = (行.get("alias") or "").strip()
                显示名 = 备注 or 昵称 or 微信号 or (行.get("username") or "")
                self._md5映射字典[md5值] = {
                    "显示名": 显示名,
                    "username": (行.get("username") or "").strip(),
                }

    def _反查联系人(self, 图片路径: str) -> str:
        文件名 = os.path.splitext(os.path.basename(图片路径))[0].lower()
        映射 = self._md5映射字典.get(文件名)
        if not 映射:
            return ""
        return 映射.get("显示名", "")

    def _清空结果(self) -> None:
        for 条目 in self._结果表格.get_children():
            self._结果表格.delete(条目)
        self._进度标签.config(text="")
        self._清空日志()

    def _清空日志(self) -> None:
        self._日志文本框.config(state="normal")
        self._日志文本框.delete("1.0", tk.END)
        self._日志文本框.config(state="disabled")

    def _追加日志(self, 内容: str) -> None:
        self._日志文本框.config(state="normal")
        self._日志文本框.insert(tk.END, 内容)
        self._日志文本框.see(tk.END)
        self._日志文本框.config(state="disabled")

    def _双击复制路径(self, _事件: tk.Event) -> None:
        选中项 = self._结果表格.selection()
        if not 选中项:
            return
        值列表 = self._结果表格.item(选中项[0], "values")
        if len(值列表) >= 4:
            完整路径 = 值列表[3]
            self.容器.clipboard_clear()
            self.容器.clipboard_append(完整路径)
            self._进度标签.config(text=f"已复制: {完整路径}")

    def _轮询队列(self) -> None:
        try:
            while True:
                self._追加日志(self._日志队列.get_nowait())
        except queue.Empty:
            pass

        try:
            while True:
                当前, 总数, 文件路径, 相似度, *其余 = self._进度队列.get_nowait()
                错误信息 = 其余[0] if 其余 else None
                if 错误信息:
                    self._进度标签.config(text=f"处理 {当前}/{总数}（有错误）")
                    self._日志队列.put(错误信息 + "\n")
                else:
                    分数文字 = f"{相似度:.4f}" if 相似度 is not None else "-"
                    self._进度标签.config(text=f"已处理 {当前}/{总数}，当前相似度 {分数文字}")
        except queue.Empty:
            pass

        try:
            while True:
                结果列表 = self._结果队列.get_nowait()
                self._展示结果(结果列表)
        except queue.Empty:
            pass

        self.容器.after(100, self._轮询队列)

    def _展示结果(self, 结果列表: list[tuple[str, float]]) -> None:
        for 条目 in self._结果表格.get_children():
            self._结果表格.delete(条目)

        if not 结果列表:
            self._追加日志("\n未找到高于阈值的相似图片。\n")
            self._进度标签.config(text="查找完成：无匹配结果")
            return

        for 图片路径, 相似度 in 结果列表:
            文件名 = os.path.basename(图片路径)
            联系人 = self._反查联系人(图片路径)
            self._结果表格.insert(
                "",
                tk.END,
                values=(f"{相似度:.4f}", 文件名, 联系人, 图片路径),
            )

        self._追加日志(f"\n共找到 {len(结果列表)} 张相似图片。\n")
        self._进度标签.config(text=f"查找完成：共 {len(结果列表)} 条结果")

    def _开始查找(self) -> None:
        if self._运行中:
            return

        参考图 = self._参考图变量.get().strip()
        搜索文件夹 = self._搜索文件夹变量.get().strip()
        if not 参考图:
            messagebox.showwarning("提示", "请选择参考图片。")
            return
        if not os.path.isfile(参考图):
            messagebox.showerror("错误", f"参考图片不存在:\n{参考图}")
            return
        if not 搜索文件夹:
            messagebox.showwarning("提示", "请选择待搜索的图片文件夹。")
            return
        if not os.path.isdir(搜索文件夹):
            messagebox.showerror("错误", f"搜索文件夹不存在:\n{搜索文件夹}")
            return

        try:
            阈值 = float(self._阈值变量.get().strip())
            if not 0 <= 阈值 <= 1:
                raise ValueError
        except ValueError:
            messagebox.showwarning("提示", "相似度阈值必须是 0 到 1 之间的数字。")
            return

        try:
            返回数量文字 = self._返回数量变量.get().strip()
            top_n = int(返回数量文字) if 返回数量文字 else 10
            if top_n <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("提示", "返回数量必须是大于 0 的整数。")
            return

        self._运行中 = True
        self._开始按钮.config(state="disabled", text="查找中…")
        self._清空结果()
        self._加载md5映射()
        self._追加日志(f"参考图片: {参考图}\n搜索文件夹: {搜索文件夹}\n阈值: {阈值}，返回前 {top_n} 条\n\n")

        def 进度回调(当前序号: int, 总数量: int, 当前文件: str, 相似度: Optional[float], 错误信息: Optional[str] = None) -> None:
            self._进度队列.put((当前序号, 总数量, 当前文件, 相似度, 错误信息))

        def 后台任务() -> None:
            try:
                结果 = 相似图片模块.find_similar_images(
                    参考图,
                    搜索文件夹,
                    threshold=阈值,
                    top_n=top_n,
                    progress_callback=进度回调,
                )
                self._结果队列.put(结果)
            except Exception as 错误:
                self._日志队列.put(f"\n查找失败: {错误}\n")
            finally:
                self.容器.after(0, self._查找结束)

        threading.Thread(target=后台任务, daemon=True).start()

    def _查找结束(self) -> None:
        self._运行中 = False
        self._开始按钮.config(state="normal", text="开始查找")


class 头像查询工具箱应用:
    """主窗口：两个标签页整合导出映射与相似图片查找。"""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("头像查询工具箱")
        self.root.geometry("980x720")
        self.root.minsize(860, 620)

        顶部说明 = tk.Label(
            self.root,
            text="头像查询工具箱：导出微信 username→md5 映射，或在文件夹中查找相似头像。",
            anchor="w",
        )
        顶部说明.pack(fill="x", padx=12, pady=(10, 4))

        笔记本 = ttk.Notebook(self.root)
        笔记本.pack(fill="both", expand=True, padx=12, pady=(4, 12))

        导出页 = tk.Frame(笔记本)
        查找页 = tk.Frame(笔记本)
        笔记本.add(导出页, text="导出 MD5 映射")
        笔记本.add(查找页, text="相似图片查找")

        导出Md5映射标签页(导出页)
        相似图片查找标签页(查找页)

    def 运行(self) -> None:
        self.root.mainloop()


def main() -> None:
    头像查询工具箱应用().运行()


if __name__ == "__main__":
    main()
