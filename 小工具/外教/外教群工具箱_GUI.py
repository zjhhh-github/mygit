# -*- coding: utf-8 -*-
r"""
外教群工具箱（可视化整合版）：把「飞书批量回写外教群」「全量核对更新外教群」
「数据库拷贝路径设置」三个功能合并进同一个窗口，打包成一个 exe 就能用。

【包含哪三个功能，分别对应哪个标签页】
    1. 「飞书批量回写」标签页：
        对应 飞书回写外教群.py 的 执行批量处理() 逻辑（拷贝数据库 → 拉取飞书记录 →
        逐条计算「是否有外教群」「外教群名」→ 非预览模式下强制写回飞书，
        并顺带给「开始材料」「充值课时」补默认值），
        可以在界面上直接填参数、点「开始运行」，运行日志实时显示在下方文本框里，
        不需要再打开命令行、不需要再记各种 --xxx 参数。
    2. 「全量核对更新」标签页：
        对应 飞书回写外教群.py 的 执行全量核对更新() 逻辑，只核对/更新
        「是否有外教群」「外教群名」两列：重新算一次结果，跟飞书表里现有值逐字比较，
        一样就跳过（不调用写入接口），不一样才更新，不会碰「开始材料」「充值课时」等
        其他字段。适合日常对这张飞书表做一次全量核对纠错。
    3. 「拷贝路径设置」标签页：
        对应 拷贝路径设置_GUI.py 的界面，用于修改上面两个功能「拷贝数据库」这一步的
        源文件路径（比如换到另一台电脑，源路径盘符不一样），保存后所有功能都会自动用上新路径。

【实现方式，方便以后维护】
    本文件本身不重复实现业务逻辑，只是：
        - import 飞书回写外教群.py 里已经抽出来的 执行批量处理() / 执行全量核对更新() 函数，
          在后台线程里调用它们，把 print 输出重定向到界面的日志文本框；
        - import 拷贝路径设置_GUI.py 里的 拷贝路径设置界面 类，
          以 standalone=False 的方式嵌入到第三个标签页里。
    所以三个文件必须放在同一个目录下，缺一个都会导入失败。

【依赖】仅使用 Python 内置 tkinter + threading + queue，无需 pip install。

【用法】
    python 外教群工具箱_GUI.py
"""

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Optional

# 三个文件必须放在同一目录下，才能这样互相 import
sys.path.insert(0, str(Path(__file__).resolve().parent))
import 拷贝路径设置_GUI as 拷贝路径设置模块  # noqa: E402
import 飞书回写外教群 as 回写工具  # noqa: E402


class 队列输出流:
    """
    把 write() 写进来的内容放进队列，不直接操作界面控件。
    因为子线程不允许直接更新 tkinter 控件，必须靠主线程轮询队列来更新，
    这个类只是给 print() 提供一个"看起来像文件"的写入目标（file-like 对象）。
    """

    def __init__(self, 输出队列: "queue.Queue[str]"):
        self._队列 = 输出队列

    def write(self, 内容: str) -> int:
        if 内容:
            self._队列.put(内容)
        return len(内容)

    def flush(self) -> None:
        pass


class 飞书批量回写标签页:
    """
    「飞书批量回写」标签页：填参数 → 点「开始运行」→ 后台线程跑
    飞书回写外教群.执行批量处理()，日志实时显示在下方文本框里。
    """

    def __init__(self, 容器: tk.Widget):
        self.容器 = 容器
        self._运行中 = False
        self._日志队列: "queue.Queue[str]" = queue.Queue()

        self._db变量 = tk.StringVar(value="")
        self._跳过拷贝变量 = tk.BooleanVar(value=False)
        self._limit变量 = tk.StringVar(value="0")
        self._预览变量 = tk.BooleanVar(value=True)  # 界面默认勾选预览，避免误触发真实写入

        self._构建界面()
        self.容器.after(100, self._轮询队列)

    # ---------------- 界面构建 ----------------

    def _构建界面(self) -> None:
        说明 = tk.Label(
            self.容器,
            text=(
                "按需填写下面的参数后点「开始运行」。默认勾选「预览模式」，只打印结果、不写入飞书；\n"
                "确认结果无误后，取消勾选「预览模式」再运行一次，才会真正写入飞书数据。"
            ),
            justify="left",
            anchor="w",
        )
        说明.pack(fill="x", padx=12, pady=(12, 6))

        参数区 = tk.LabelFrame(self.容器, text="运行参数")
        参数区.pack(fill="x", padx=12, pady=6)

        db行 = tk.Frame(参数区)
        db行.pack(fill="x", padx=8, pady=4)
        tk.Label(db行, text="自定义数据库路径：", width=16, anchor="w").pack(side="left")
        tk.Entry(db行, textvariable=self._db变量).pack(side="left", fill="x", expand=True)
        tk.Label(db行, text="（留空则使用默认路径，多个路径用 ; 分隔）", fg="#666666").pack(side="left", padx=(6, 0))

        limit行 = tk.Frame(参数区)
        limit行.pack(fill="x", padx=8, pady=4)
        tk.Label(limit行, text="只处理前 N 条：", width=16, anchor="w").pack(side="left")
        tk.Entry(limit行, textvariable=self._limit变量, width=10).pack(side="left")
        tk.Label(limit行, text="（0 表示处理全部记录，用于先小范围验证）", fg="#666666").pack(side="left", padx=(6, 0))

        勾选行 = tk.Frame(参数区)
        勾选行.pack(fill="x", padx=8, pady=4)
        tk.Checkbutton(勾选行, text="跳过自动拷贝数据库", variable=self._跳过拷贝变量).pack(side="left")
        tk.Checkbutton(
            勾选行,
            text="预览模式（只打印结果，不写入飞书）",
            variable=self._预览变量,
        ).pack(side="left", padx=(16, 0))

        按钮区 = tk.Frame(self.容器)
        按钮区.pack(fill="x", padx=12, pady=(6, 6))
        self._运行按钮 = tk.Button(按钮区, text="开始运行", width=12, command=self._开始运行)
        self._运行按钮.pack(side="left")
        tk.Button(按钮区, text="清空日志", width=10, command=self._清空日志).pack(side="left", padx=8)

        日志区 = tk.LabelFrame(self.容器, text="运行日志")
        日志区.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._日志文本框 = scrolledtext.ScrolledText(日志区, wrap="word", state="disabled")
        self._日志文本框.pack(fill="both", expand=True, padx=6, pady=6)

    # ---------------- 运行控制 ----------------

    def _开始运行(self) -> None:
        if self._运行中:
            return

        limit文本 = self._limit变量.get().strip()
        try:
            limit = int(limit文本) if limit文本 else 0
        except ValueError:
            messagebox.showwarning("提示", "「只处理前 N 条」必须是数字，比如 0、5、20。")
            return

        预览 = self._预览变量.get()
        if not 预览:
            确认 = messagebox.askyesno(
                "确认写入飞书",
                "当前【没有】勾选「预览模式」，运行后会真正调用飞书接口写入数据。\n\n确定要继续吗？",
            )
            if not 确认:
                return

        db参数: Optional[str] = self._db变量.get().strip() or None
        跳过拷贝 = self._跳过拷贝变量.get()

        self._运行中 = True
        self._运行按钮.configure(state="disabled", text="运行中...")
        self._追加日志(f"\n{'=' * 24} 开始运行 {'=' * 24}\n")

        线程 = threading.Thread(
            target=self._后台线程执行,
            args=(db参数, 跳过拷贝, limit, 预览),
            daemon=True,
        )
        线程.start()

    def _后台线程执行(
        self,
        db参数: Optional[str],
        skip_copy_db: bool,
        limit: int,
        preview: bool,
    ) -> None:
        """
        在后台线程里真正执行批量处理逻辑，把 print 输出重定向到本标签页的日志队列，
        避免界面在处理飞书大量记录时被卡死、也避免子线程直接操作 tkinter 控件。
        """
        旧_stdout, 旧_stderr = sys.stdout, sys.stderr
        输出流 = 队列输出流(self._日志队列)
        sys.stdout = 输出流
        sys.stderr = 输出流
        try:
            返回码 = 回写工具.执行批量处理(db参数, skip_copy_db, limit, preview)
            self._日志队列.put(f"\n[运行结束] 返回码={返回码}\n")
        except Exception as ex:  # noqa: BLE001 - 界面工具需要兜底展示任何异常，不能让线程静默崩溃
            self._日志队列.put(f"\n[出错] {ex}\n")
        finally:
            sys.stdout, sys.stderr = 旧_stdout, 旧_stderr
            self._日志队列.put("__RUN_FINISHED__")

    def _轮询队列(self) -> None:
        """主线程定时轮询队列，把子线程写入的日志追加到文本框（tkinter 控件只能在主线程操作）。"""
        try:
            while True:
                内容 = self._日志队列.get_nowait()
                if 内容 == "__RUN_FINISHED__":
                    self._运行结束()
                    continue
                self._追加日志(内容)
        except queue.Empty:
            pass
        self.容器.after(100, self._轮询队列)

    def _运行结束(self) -> None:
        self._运行中 = False
        self._运行按钮.configure(state="normal", text="开始运行")

    # ---------------- 日志显示 ----------------

    def _追加日志(self, 内容: str) -> None:
        self._日志文本框.configure(state="normal")
        self._日志文本框.insert("end", 内容)
        self._日志文本框.see("end")
        self._日志文本框.configure(state="disabled")

    def _清空日志(self) -> None:
        self._日志文本框.configure(state="normal")
        self._日志文本框.delete("1.0", "end")
        self._日志文本框.configure(state="disabled")


class 全量核对更新标签页:
    """
    「全量核对更新」标签页：填参数 → 点「开始运行」→ 后台线程跑
    飞书回写外教群.执行全量核对更新()，日志实时显示在下方文本框里。

    跟「飞书批量回写」标签页界面结构基本一样（同样有数据库路径 / 只处理前 N 条 /
    跳过拷贝 / 预览模式这几个参数），区别只在于背后调用的核心函数不同：
    这里只核对/更新「是否有外教群」「外教群名」，一致则跳过、不动其他字段。
    """

    def __init__(self, 容器: tk.Widget):
        self.容器 = 容器
        self._运行中 = False
        self._日志队列: "queue.Queue[str]" = queue.Queue()

        self._db变量 = tk.StringVar(value="")
        self._跳过拷贝变量 = tk.BooleanVar(value=False)
        self._limit变量 = tk.StringVar(value="0")
        self._预览变量 = tk.BooleanVar(value=True)  # 界面默认勾选预览，避免误触发真实写入
        self._视图地址变量 = tk.StringVar(value="")  # 留空则用脚本里写死的默认表格/视图

        self._构建界面()
        self.容器.after(100, self._轮询队列)

    # ---------------- 界面构建 ----------------

    def _构建界面(self) -> None:
        说明 = tk.Label(
            self.容器,
            text=(
                "只核对/更新「是否有外教群」「外教群名」两列：重新算一次结果，跟飞书表里现有值\n"
                "逐字比较，一样就跳过（不调用写入接口），不一样才更新；不会碰「开始材料」「充值课时」等其他字段。\n"
                "默认勾选「预览模式」，只打印哪些记录不一致、不会真正写入；确认无误后取消勾选再运行一次才会正式更新。"
            ),
            justify="left",
            anchor="w",
        )
        说明.pack(fill="x", padx=12, pady=(12, 6))

        参数区 = tk.LabelFrame(self.容器, text="运行参数")
        参数区.pack(fill="x", padx=12, pady=6)

        视图行 = tk.Frame(参数区)
        视图行.pack(fill="x", padx=8, pady=4)
        tk.Label(视图行, text="自定义飞书视图：", width=16, anchor="w").pack(side="left")
        tk.Entry(视图行, textvariable=self._视图地址变量).pack(side="left", fill="x", expand=True)
        tk.Label(视图行, text="（留空则核对默认表格，填浏览器地址栏复制的完整视图链接可核对别的表/视图）", fg="#666666").pack(
            side="left", padx=(6, 0)
        )

        db行 = tk.Frame(参数区)
        db行.pack(fill="x", padx=8, pady=4)
        tk.Label(db行, text="自定义数据库路径：", width=16, anchor="w").pack(side="left")
        tk.Entry(db行, textvariable=self._db变量).pack(side="left", fill="x", expand=True)
        tk.Label(db行, text="（留空则使用默认路径，多个路径用 ; 分隔）", fg="#666666").pack(side="left", padx=(6, 0))

        limit行 = tk.Frame(参数区)
        limit行.pack(fill="x", padx=8, pady=4)
        tk.Label(limit行, text="只处理前 N 条：", width=16, anchor="w").pack(side="left")
        tk.Entry(limit行, textvariable=self._limit变量, width=10).pack(side="left")
        tk.Label(limit行, text="（0 表示处理全部记录，用于先小范围验证）", fg="#666666").pack(side="left", padx=(6, 0))

        勾选行 = tk.Frame(参数区)
        勾选行.pack(fill="x", padx=8, pady=4)
        tk.Checkbutton(勾选行, text="跳过自动拷贝数据库", variable=self._跳过拷贝变量).pack(side="left")
        tk.Checkbutton(
            勾选行,
            text="预览模式（只打印哪些记录不一致，不写入飞书）",
            variable=self._预览变量,
        ).pack(side="left", padx=(16, 0))

        按钮区 = tk.Frame(self.容器)
        按钮区.pack(fill="x", padx=12, pady=(6, 6))
        self._运行按钮 = tk.Button(按钮区, text="开始运行", width=12, command=self._开始运行)
        self._运行按钮.pack(side="left")
        tk.Button(按钮区, text="清空日志", width=10, command=self._清空日志).pack(side="left", padx=8)

        日志区 = tk.LabelFrame(self.容器, text="运行日志")
        日志区.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._日志文本框 = scrolledtext.ScrolledText(日志区, wrap="word", state="disabled")
        self._日志文本框.pack(fill="both", expand=True, padx=6, pady=6)

    # ---------------- 运行控制 ----------------

    def _开始运行(self) -> None:
        if self._运行中:
            return

        limit文本 = self._limit变量.get().strip()
        try:
            limit = int(limit文本) if limit文本 else 0
        except ValueError:
            messagebox.showwarning("提示", "「只处理前 N 条」必须是数字，比如 0、5、20。")
            return

        预览 = self._预览变量.get()
        if not 预览:
            确认 = messagebox.askyesno(
                "确认写入飞书",
                "当前【没有】勾选「预览模式」，运行后会把核对到的不一致记录真正更新到飞书。\n\n确定要继续吗？",
            )
            if not 确认:
                return

        db参数: Optional[str] = self._db变量.get().strip() or None
        跳过拷贝 = self._跳过拷贝变量.get()
        视图地址: Optional[str] = self._视图地址变量.get().strip() or None

        self._运行中 = True
        self._运行按钮.configure(state="disabled", text="运行中...")
        self._追加日志(f"\n{'=' * 24} 开始运行 {'=' * 24}\n")

        线程 = threading.Thread(
            target=self._后台线程执行,
            args=(db参数, 跳过拷贝, limit, 预览, 视图地址),
            daemon=True,
        )
        线程.start()

    def _后台线程执行(
        self,
        db参数: Optional[str],
        skip_copy_db: bool,
        limit: int,
        preview: bool,
        view_url: Optional[str],
    ) -> None:
        """
        在后台线程里真正执行全量核对更新逻辑，把 print 输出重定向到本标签页的日志队列，
        避免界面在处理飞书大量记录时被卡死、也避免子线程直接操作 tkinter 控件。
        """
        旧_stdout, 旧_stderr = sys.stdout, sys.stderr
        输出流 = 队列输出流(self._日志队列)
        sys.stdout = 输出流
        sys.stderr = 输出流
        try:
            返回码 = 回写工具.执行全量核对更新(db参数, skip_copy_db, limit, preview, view_url)
            self._日志队列.put(f"\n[运行结束] 返回码={返回码}\n")
        except Exception as ex:  # noqa: BLE001 - 界面工具需要兜底展示任何异常，不能让线程静默崩溃
            self._日志队列.put(f"\n[出错] {ex}\n")
        finally:
            sys.stdout, sys.stderr = 旧_stdout, 旧_stderr
            self._日志队列.put("__RUN_FINISHED__")

    def _轮询队列(self) -> None:
        """主线程定时轮询队列，把子线程写入的日志追加到文本框（tkinter 控件只能在主线程操作）。"""
        try:
            while True:
                内容 = self._日志队列.get_nowait()
                if 内容 == "__RUN_FINISHED__":
                    self._运行结束()
                    continue
                self._追加日志(内容)
        except queue.Empty:
            pass
        self.容器.after(100, self._轮询队列)

    def _运行结束(self) -> None:
        self._运行中 = False
        self._运行按钮.configure(state="normal", text="开始运行")

    # ---------------- 日志显示 ----------------

    def _追加日志(self, 内容: str) -> None:
        self._日志文本框.configure(state="normal")
        self._日志文本框.insert("end", 内容)
        self._日志文本框.see("end")
        self._日志文本框.configure(state="disabled")

    def _清空日志(self) -> None:
        self._日志文本框.configure(state="normal")
        self._日志文本框.delete("1.0", "end")
        self._日志文本框.configure(state="disabled")


def _图标文件路径() -> Optional[str]:
    """
    定位图标文件 spec/外教建群_icon.ico 的实际路径，兼容两种运行方式：
      - 直接用 python 跑源码：图标文件路径是相对本文件的 spec/外教建群_icon.ico；
      - 打包成 exe 运行：PyInstaller 打包 exe 时只会把图标嵌入「文件本身」的图标资源
        （在资源管理器里看到的图标），并不会让正在运行的窗口自动使用这个图标——
        tkinter 窗口标题栏 / 任务栏显示的图标，必须由代码里显式调用
        root.iconbitmap() 设置，所以这里需要把图标文件也当作数据文件打包进 exe
        （见 spec/外教群工具箱_GUI.spec 里的 datas 配置），再用 sys._MEIPASS
        找到解压后的临时目录来定位它。
    找不到文件时返回 None，调用方应该跳过设置图标（不影响程序正常运行）。
    """
    if getattr(sys, "frozen", False):
        资源根目录 = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        候选路径 = 资源根目录 / "外教建群_icon.ico"
    else:
        候选路径 = Path(__file__).resolve().parent / "spec" / "外教建群_icon.ico"

    return str(候选路径) if 候选路径.exists() else None


class 外教群工具箱界面:
    """顶层窗口：一个 ttk.Notebook，三个标签页分别对应三个功能。"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("外教群工具箱")
        self.root.geometry("900x680")
        self.root.minsize(780, 560)

        图标路径 = _图标文件路径()
        if 图标路径:
            try:
                self.root.iconbitmap(default=图标路径)
            except Exception:
                # 极少数环境下 iconbitmap 可能失败（比如某些精简版 Windows），
                # 不应该因为图标设置失败导致整个窗口打不开，静默跳过即可。
                pass

        标签容器 = ttk.Notebook(self.root)
        标签容器.pack(fill="both", expand=True)

        回写标签页 = tk.Frame(标签容器)
        标签容器.add(回写标签页, text="飞书批量回写")
        飞书批量回写标签页(回写标签页)

        核对标签页 = tk.Frame(标签容器)
        标签容器.add(核对标签页, text="全量核对更新")
        全量核对更新标签页(核对标签页)

        拷贝标签页 = tk.Frame(标签容器)
        标签容器.add(拷贝标签页, text="拷贝路径设置")
        拷贝路径设置模块.拷贝路径设置界面(拷贝标签页, standalone=False)


def main() -> int:
    root = tk.Tk()
    外教群工具箱界面(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
