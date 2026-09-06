# -*- coding: utf-8 -*-
r"""
可视化工具：设置「查询外教群.py」「飞书回写外教群.py」共用的数据库拷贝任务源路径。

【为什么需要这个工具】
    这两个脚本运行前，都会先把最新的两个「内部专用」微信 contact.db
    从别的地方拷贝到本机 Desktop（见 查询外教群.py 的 拷贝内部专用数据库()）。
    源文件路径在不同电脑上可能不一样：
        - 在部署这些 chatlog 存储的电脑本机上，是本机路径（C: 盘 Documents 下）；
        - 换到其他电脑，源文件可能是挂载的网络盘（比如 Z: 盘）。
    这个工具就是用来可视化修改这两个源路径的，不用直接改代码。

【功能说明】
    - 预设方案：一键切换「内部专用（本机路径）」「其他设备（网络盘 Z:）」；
    - 每个任务的源路径、目标路径都可以手动编辑，也可以点「浏览...」选择文件；
    - 点「立即测试拷贝」可以马上按当前填写的路径试跑一次拷贝，看结果是否成功；
    - 点「保存并应用」后，配置写入 Windows 注册表（不产生额外配置文件）：
        查询外教群.py / 飞书回写外教群.py 下次运行时会自动读取并使用这里保存的路径；
        如果从没保存过，两个脚本仍然使用代码里写死的默认路径（内部专用·本机路径）。

【依赖】仅使用 Python 内置 tkinter，无需 pip install。

【用法】
    python 拷贝路径设置_GUI.py
"""

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Dict, List, Tuple

# 复用 查询外教群.py 里的注册表读写函数、拷贝执行函数，两边保持同一套逻辑，不重复实现。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import 查询外教群 as 外教群工具  # noqa: E402


# ============================================================
# 一、预设方案
# ============================================================

_目标路径1 = r"C:\Users\LENOVO\Desktop\contact_内部专用.db"
_目标路径2 = r"C:\Users\LENOVO\Desktop\contact_内部专用2.db"

预设方案顺序: List[str] = ["内部专用（本机路径）", "其他设备（网络盘 Z:）"]

预设方案: Dict[str, List[Tuple[str, str]]] = {
    "内部专用（本机路径）": [
        (
            r"C:\Users\LENOVO\Documents\chatlog\wxid_42272spv9uq522_6ded\db_storage\contact\contact.db",
            _目标路径1,
        ),
        (
            r"C:\Users\LENOVO\Documents\chatlog\wxid_7u0rihcbbpbz12_ec5a\db_storage\contact\contact.db",
            _目标路径2,
        ),
    ],
    "其他设备（网络盘 Z:）": [
        (
            r"Z:\Documents\chatlog\wxid_42272spv9uq522_6ded\db_storage\contact\contact.db",
            _目标路径1,
        ),
        (
            r"Z:\Documents\chatlog\wxid_7u0rihcbbpbz12_ec5a\db_storage\contact\contact.db",
            _目标路径2,
        ),
    ],
}

任务标题列表: List[str] = ["任务1（内部专用 contact.db）", "任务2（内部专用2 contact.db）"]


# ============================================================
# 二、主界面
# ============================================================

class 拷贝路径设置界面:
    """
    两个任务、每个任务「源路径 + 目标路径」的可视化编辑界面。

    支持两种用法：
      1. 独立运行（standalone=True，默认）：root 必须是 tk.Tk 顶层窗口，
         本类会设置窗口标题/大小，并显示「关闭」按钮。
      2. 嵌入到别的窗口（standalone=False）：root 传一个普通容器
         （比如 ttk.Notebook 里的一个 Frame），本类只在这个容器里铺内容，
         不会修改父窗口的标题/大小，也不显示「关闭」按钮（由父窗口统一管理）。
         供 外教群工具箱_GUI.py 把本界面作为一个标签页嵌入使用。
    """

    def __init__(self, root: tk.Widget, *, standalone: bool = True):
        self.root = root
        self.standalone = standalone
        if standalone:
            self.root.title("外教群工具 - 数据库拷贝路径设置")
            self.root.geometry("900x430")
            self.root.minsize(780, 400)

        # 界面状态：预设方案选中项 + 两个任务各自的源/目标路径输入框内容
        self._方案变量 = tk.StringVar(value="")
        self._源路径变量列表: List[tk.StringVar] = [tk.StringVar(), tk.StringVar()]
        self._目标路径变量列表: List[tk.StringVar] = [
            tk.StringVar(value=_目标路径1),
            tk.StringVar(value=_目标路径2),
        ]

        self._构建界面()
        self._加载已保存配置()

    # ---------------- 界面构建 ----------------

    def _构建界面(self) -> None:
        说明 = tk.Label(
            self.root,
            text=(
                "设置「内部专用」「内部专用2」两个微信 contact.db 的拷贝来源路径。\n"
                "保存后，查询外教群.py / 飞书回写外教群.py 下次运行时会自动使用这里保存的路径。"
            ),
            justify="left",
            anchor="w",
        )
        说明.pack(fill="x", padx=12, pady=(12, 6))

        方案区 = tk.LabelFrame(self.root, text="快速选择预设方案")
        方案区.pack(fill="x", padx=12, pady=6)
        for 方案名 in 预设方案顺序:
            tk.Radiobutton(
                方案区,
                text=方案名,
                variable=self._方案变量,
                value=方案名,
                command=lambda 名=方案名: self._应用预设(名),
            ).pack(side="left", padx=10, pady=6)

        任务区 = tk.LabelFrame(self.root, text="拷贝任务详情（可手动编辑）")
        任务区.pack(fill="both", expand=True, padx=12, pady=6)

        for 序号 in range(2):
            分组 = tk.LabelFrame(任务区, text=任务标题列表[序号])
            分组.pack(fill="x", padx=8, pady=6)
            self._构建一行路径(分组, "源文件路径：", self._源路径变量列表[序号], 是源文件=True)
            self._构建一行路径(分组, "目标文件路径：", self._目标路径变量列表[序号], 是源文件=False)

        按钮区 = tk.Frame(self.root)
        按钮区.pack(fill="x", padx=12, pady=(6, 12))
        tk.Button(按钮区, text="保存并应用", width=12, command=self._保存).pack(side="left")
        tk.Button(按钮区, text="立即测试拷贝", width=12, command=self._测试拷贝).pack(side="left", padx=8)
        tk.Button(
            按钮区,
            text="恢复默认（内部专用）",
            width=18,
            command=lambda: self._应用预设("内部专用（本机路径）"),
        ).pack(side="left")
        if self.standalone:
            tk.Button(按钮区, text="关闭", width=8, command=self.root.destroy).pack(side="right")

    def _构建一行路径(self, 父容器: tk.Widget, 标签文字: str, 变量: tk.StringVar, 是源文件: bool) -> None:
        行 = tk.Frame(父容器)
        行.pack(fill="x", padx=6, pady=3)
        tk.Label(行, text=标签文字, width=12, anchor="w").pack(side="left")
        输入框 = tk.Entry(行, textvariable=变量)
        输入框.pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Button(
            行,
            text="浏览...",
            width=8,
            command=lambda: self._选择文件(变量, 是源文件),
        ).pack(side="left")

    # ---------------- 交互逻辑 ----------------

    def _选择文件(self, 变量: tk.StringVar, 是源文件: bool) -> None:
        """打开文件选择框，让用户挑选源文件 / 目标文件的实际路径。"""
        初始路径 = 变量.get().strip()
        初始目录 = str(Path(初始路径).parent) if 初始路径 else None

        if 是源文件:
            选择结果 = filedialog.askopenfilename(
                title="选择源数据库文件（微信 contact.db）",
                initialdir=初始目录,
                filetypes=[("SQLite 数据库", "*.db"), ("全部文件", "*.*")],
            )
        else:
            选择结果 = filedialog.asksaveasfilename(
                title="选择目标数据库文件保存位置",
                initialdir=初始目录,
                defaultextension=".db",
                filetypes=[("SQLite 数据库", "*.db"), ("全部文件", "*.*")],
            )

        if 选择结果:
            变量.set(选择结果)

    def _应用预设(self, 方案名: str) -> None:
        """选中某个预设方案后，把对应的源/目标路径直接填入输入框（用户之后仍可手动改）。"""
        任务列表 = 预设方案.get(方案名)
        if not 任务列表:
            return
        self._方案变量.set(方案名)
        for 序号, (源, 目标) in enumerate(任务列表):
            self._源路径变量列表[序号].set(源)
            self._目标路径变量列表[序号].set(目标)

    def _当前任务列表(self) -> List[Tuple[str, str]]:
        """读取界面上当前填写的两个任务的（源路径, 目标路径）。"""
        任务列表: List[Tuple[str, str]] = []
        for 序号 in range(2):
            源 = self._源路径变量列表[序号].get().strip()
            目标 = self._目标路径变量列表[序号].get().strip()
            任务列表.append((源, 目标))
        return 任务列表

    def _校验非空(self, 任务列表: List[Tuple[str, str]]) -> bool:
        for 源, 目标 in 任务列表:
            if not 源 or not 目标:
                messagebox.showwarning("提示", "源路径和目标路径都不能为空，请检查后再继续。")
                return False
        return True

    def _保存(self) -> None:
        """把当前填写的路径写入注册表，供 查询外教群.py / 飞书回写外教群.py 下次运行时自动读取。"""
        任务列表 = self._当前任务列表()
        if not self._校验非空(任务列表):
            return
        外教群工具.写入自定义拷贝任务(任务列表)
        messagebox.showinfo(
            "保存成功",
            "已保存。查询外教群.py / 飞书回写外教群.py 下次运行时会自动使用这里保存的路径。",
        )

    def _测试拷贝(self) -> None:
        """按当前填写的路径，立即执行一次真实拷贝，方便验证路径是否正确、源文件能否访问。"""
        任务列表 = self._当前任务列表()
        if not self._校验非空(任务列表):
            return
        结果列表 = 外教群工具.拷贝内部专用数据库(任务列表)
        messagebox.showinfo("测试拷贝结果", "\n".join(结果列表))

    # ---------------- 配置加载 ----------------

    def _加载已保存配置(self) -> None:
        """打开界面时，优先回显上次保存过的自定义路径；没保存过则默认选中「内部专用」预设。"""
        已保存 = 外教群工具.读取自定义拷贝任务()
        if 已保存:
            for 序号, (源, 目标) in enumerate(已保存[:2]):
                self._源路径变量列表[序号].set(源)
                self._目标路径变量列表[序号].set(目标)
            for 方案名, 任务 in 预设方案.items():
                if 任务 == 已保存:
                    self._方案变量.set(方案名)
                    break
        else:
            self._应用预设("内部专用（本机路径）")


def main() -> int:
    root = tk.Tk()
    拷贝路径设置界面(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
