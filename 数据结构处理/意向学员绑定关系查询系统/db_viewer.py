"""
意向学员查询系统 - 数据库查看器

功能说明:
    - 从多个数据源加载联系人数据（AutoAgreeAddFriendTask、售前通讯录.txt）
    - 支持数据去重、搜索过滤、导出Excel
    - 自动备份数据库文件
    - 提供友好的图形界面

性能优化:
    - 延迟导入 openpyxl，提升启动速度
    - 使用列索引常量，提升代码可维护性
    - 预加载数据映射表，减少数据库查询次数
    - 异步备份数据库，不阻塞主流程

数据结构:
    - 12列数据：
      意向学员昵称、意向学员微信ID、意向学员微信号、意向学员总微信号、意向学员添加时间、
      意向学员内部备注、意向学员是否删除、来源昵称、来源微信ID、来源微信号、
      来源总微信号、来源内部备注

作者: Cursor AI Assistant
版本: 2.1
最后更新: 2026-01-26
"""

# 启用DPI感知,解决Windows高DPI下界面模糊问题
import ctypes
try:
    # Windows 8.1及以上
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except:
    try:
        # Windows Vista/7
        ctypes.windll.user32.SetProcessDPIAware()
    except:
        pass

import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from typing import List, Tuple, Optional
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET
import threading
import os
import shutil
import time
import sys
import gc  # 垃圾回收，用于内存优化
# openpyxl 延迟导入（仅在导出时导入，提升启动速度）
# from openpyxl import Workbook
# from openpyxl.styles import Font, Alignment, PatternFill

# 自动 Excel 备份模块（刷新成功后静默导出到 NAS 目录）
# 该功能从本文件中拆出，独立维护，详见 modules/auto_excel_backup.py
from modules.auto_excel_backup import AutoExcelBackup


# ==================== 路径常量与列索引（从 modules.prospective.config 复用） ====================
# Phase 1 重构：原本定义在本文件顶部的常量与 get_resource_path 已迁移到
#   modules/prospective/config.py
# 这里通过 import 把它们重新引入到本模块命名空间，目的是：
#   1) 保持向下兼容：from db_viewer import PRIMARY_DB / COL_OBJ_TIME / ... 仍能用
#   2) 保留「外部覆写模块全局变量」的旧依赖注入方式，例如：
#         mod.CONTACT_NETWORK_BASE = "新的网络根目录"
#      因为 DatabaseViewer 内部读取的就是本模块下面这几个名字。
from modules.prospective.config import (
    get_resource_path,
    PRIMARY_DB,
    BACKUP_DB,
    CONTACT_NETWORK_BASE,
    CONTACT_LOCAL_BACKUP,
    NETWORK_BACKUP_BASE,
    COL_OBJ_NICK,
    COL_OBJ_WXID,
    COL_OBJ_NUMBER,
    COL_OBJ_TOTAL_NUMBER,
    COL_OBJ_TIME,
    COL_OBJ_INTERNAL_NOTE,
    COL_OBJ_IS_DELETE,
    COL_SOURCE_NICK,
    COL_SOURCE_WXID,
    COL_SOURCE_NUMBER,
    COL_SOURCE_TOTAL_NUMBER,
    COL_SOURCE_INTERNAL_NOTE,
)

# 自动导出目标目录（NAS 路径）已迁移到 modules/auto_excel_backup.py，
# 默认值见 modules.auto_excel_backup.DEFAULT_AUTO_EXPORT_DIR


# ==================== 数据清洗流水线（迁移至 modules.prospective.data_clean） ====================
# Phase 1 重构：原本定义在本文件内、约 540 行的「9 步清洗流水线 + 辅助函数」已整体
# 迁移到 modules/prospective/data_clean.py，行为完全等价。
# 这里 re-export 顶层 API（clean_newlines / run_clean_pipeline），
# 让原本 from db_viewer import clean_newlines 这种调用方继续可用。
from modules.prospective.data_clean import clean_newlines, run_clean_pipeline


# ==================== 数据库访问与备份（迁移至 modules.prospective.{db_access,backup}） ====================
# Phase 1 重构：原本定义在本文件内的备份函数、contact.db 路径解析、DatabaseReader
# 已整体迁移到 modules/prospective/db_access.py 与 modules/prospective/backup.py。
#
# 这里通过两类做法保持向下兼容：
#   1) 无依赖全局变量的对象 / 函数 → 直接 re-export
#        DatabaseReader、get_short_path、backup_database
#   2) 依赖本模块顶部常量的函数（PRIMARY_DB / BACKUP_DB / CONTACT_NETWORK_BASE /
#      CONTACT_LOCAL_BACKUP / NETWORK_BACKUP_BASE）→ 改为薄薄的兼容包装函数，
#      内部读取本模块当前生效的常量值（外部 mod.XXX = ... 仍能覆写），
#      再转发给新模块带显式参数版本。
from modules.prospective.db_access import (
    DatabaseReader,
    get_short_path,
    get_contact_db_path as _impl_get_contact_db_path,
    get_database_connection as _impl_get_database_connection,
)
from modules.prospective.backup import (
    backup_database,
    backup_to_network as _impl_backup_to_network,
)
from modules.prospective.utils import (
    convert_timestamp as _utils_convert_timestamp,
    convert_time_format as _utils_convert_time_format,
    validate_remark_format as _utils_validate_remark_format,
    parse_xml_content as _utils_parse_xml_content,
)
from modules.prospective.service import export_to_excel as _service_export_to_excel
from modules.prospective.ui_widgets import ToastManager, LoadingOverlay
from modules.prospective.tasks import AutoRefreshScheduler


def get_contact_db_path() -> str:
    """兼容包装：使用本模块当前生效的 CONTACT_NETWORK_BASE / CONTACT_LOCAL_BACKUP。

    保留这种"读模块全局"的实现，是为了让导入控制台可以通过
        mod.CONTACT_NETWORK_BASE = "..."
    临时覆写网络根目录而无需重新构造对象（Phase 3 会切换到显式 config 注入）。
    """
    return _impl_get_contact_db_path(CONTACT_NETWORK_BASE, CONTACT_LOCAL_BACKUP)


def get_database_connection():
    """兼容包装：使用本模块当前生效的 PRIMARY_DB / BACKUP_DB。"""
    return _impl_get_database_connection(PRIMARY_DB, BACKUP_DB)


def backup_to_network(source_path: str) -> bool:
    """兼容包装：使用本模块当前生效的 NETWORK_BACKUP_BASE。"""
    return _impl_backup_to_network(source_path, NETWORK_BACKUP_BASE)


# ==================== 图形界面模块 ====================

class DatabaseViewer:
    """数据库查看器GUI"""

    def __init__(self, root: tk.Tk):
        """
        初始化数据库查看器

        Args:
            root: tkinter主窗口
        """
        self.root = root
        self.root.title("意向学员查询系统")

        # 设置主窗口背景色（浅灰色，提升视觉层次）
        self.root.configure(bg="#f5f6fa")

        # 设置最小尺寸
        self.root.minsize(1440, 900)

        # 设置初始全屏(最大化窗口)
        self.root.state('zoomed')

        # 超微数据库当前连接的文件路径（仅用于状态栏展示）
        # 注：超微数据库连接对象的生命周期完全收敛在 _load_data_in_thread() 内部，
        #     使用局部 conn 变量并在 finally 中关闭，不再挂到实例字段上
        self.current_db_path: str = ""

        # contact.db 数据库连接（新增，用于后续数据交叉）
        self.contact_conn: Optional[sqlite3.Connection] = None
        self.contact_db_path: str = ""

        # 性能优化：数据缓存（避免重复加载）
        self._data_cache_timestamp: Optional[float] = None  # 数据缓存时间戳
        self._cache_validity_seconds: int = 300  # 缓存有效期：5分钟

        # 首次启动标记（用于判断是否显示主数据库不存在的提醒）
        self.is_first_load: bool = True
        self.need_show_db_warning: bool = False  # 是否需要显示数据库警告

        # Toast 提示相关（实现已迁移到 modules.prospective.ui_widgets.ToastManager）
        # toast_var / toast_label 由 _create_widgets 创建，之后注入到 toast_manager
        self.toast_var = tk.StringVar(value="")
        self.toast_label: Optional[tk.Label] = None
        self.toast_manager: Optional[ToastManager] = None

        # Loading 遮罩（实现已迁移到 modules.prospective.ui_widgets.LoadingOverlay）
        # 在 _create_widgets() 之后构造（见下方），构造时即创建 Tk 资源
        self.loading_overlay: Optional[LoadingOverlay] = None

        # 搜索功能相关
        self.all_data: List[dict] = []  # 存储完整数据集（用于搜索过滤）
        self.search_var = tk.StringVar(value="")  # 搜索框内容
        self.search_entry: Optional[tk.Entry] = None  # 搜索输入框引用
        self.search_count_label: Optional[tk.Label] = None  # 搜索结果计数标签
        self.empty_state_label: Optional[tk.Label] = None  # 空状态提示标签

        # 定时自动刷新调度器（实现已迁移到 modules.prospective.tasks.AutoRefreshScheduler）
        # 一次性触发：每次 load_data() 成功路径会重新 start()，与原行为完全一致
        # 默认间隔 5 分钟，可通过 AutoRefreshScheduler.DEFAULT_INTERVAL_MS 调整
        self.auto_refresh_scheduler = AutoRefreshScheduler(
            parent=self.root,
            callback=self.load_data,
            interval_ms=AutoRefreshScheduler.DEFAULT_INTERVAL_MS,
        )

        # 自动 Excel 备份器：负责刷新成功后静默导出到 NAS
        # 失败提示去重、目录可达性检查、异常隔离等逻辑均在该模块内处理
        # 这里通过依赖注入传入：导出回调（_do_export_full）、数据提供者、Tk 根、toast 函数
        self.auto_excel_backup = AutoExcelBackup(
            exporter=self._do_export_full,
            data_provider=lambda: self.all_data,
            tk_root=self.root,
            toast_warn=self._show_warning_toast,
        )

        # 上一轮异步备份失败的记录（线程安全，仅 append + 下一轮一次性读取/清空）
        # 用途：异步备份失败时无法立刻在本轮 toast 反馈，挂到这里下一轮刷新时合并提示
        self._pending_backup_errors: List[str] = []
        self._pending_backup_errors_lock = threading.Lock()

        # 底部状态栏"本次刷新链路摘要"标签（在 _create_widgets 里创建）
        # 用途：稳定展示本次刷新最终走通的链路，与 toast（事件提示）形成互补
        self.summary_label: Optional[tk.Label] = None

        # 底部状态栏"最近刷新时间"标签（在 _create_widgets 里创建）
        # 仅在 UI 真正成功更新时刷新，刷新失败时保留上一次成功的时间
        self.last_refresh_label: Optional[tk.Label] = None

        # 底部状态栏"内部库副本时间"标签（在 _create_widgets 里创建）
        # 含义：当前实际连接的本地 contact.db 文件的最后修改时间（mtime）
        # 用途：让用户判断"这份本地副本是不是最新的"，与"最近刷新时间"语义不同
        # 仅在 UI 成功更新时刷新；从未成功则显示"暂无"
        self.contact_mtime_label: Optional[tk.Label] = None

        # 配置样式
        self._configure_styles()

        # 创建界面
        self._create_widgets()

        # 创建 UI 控件管理器（依赖 _create_widgets 已创建好的 toast_label）
        # ToastManager 接管 _show_toast / _show_warning_toast / _hide_toast 行为
        self.toast_manager = ToastManager(self.root, self.toast_label, self.toast_var)
        # LoadingOverlay 自带 Tk 资源构造，初始隐藏；接管 _show_loading / _hide_loading
        self.loading_overlay = LoadingOverlay(self.root)

        # 首次加载数据
        self.load_data()

    def _center_window(self, width: int, height: int):
        """
        将窗口居中显示在屏幕中央

        Args:
            width: 窗口宽度
            height: 窗口高度
        """
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        x = (screen_width - width) // 2
        y = (screen_height - height) // 2

        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _configure_styles(self):
        """配置界面样式主题"""
        style = ttk.Style()
        style.theme_use("clam")  # 使用更现代的主题

        # 配置Treeview样式 - 优化清晰度和列边界
        style.configure(
            "Custom.Treeview",
            background="#ffffff",
            foreground="#2c3e50",  # 深色文字,提高对比度
            rowheight=44,  # 固定行高44px（跨分辨率统一，不随DPI缩放）
            fieldbackground="#ffffff",
            font=("微软雅黑", 11),  # 增大字体
            borderwidth=1,  # 添加边框，让列边界更清晰
            relief="solid"  # 实线边框
        )

        # 配置Treeview表头样式 - 蓝色背景（优化清晰度和列边界）
        style.configure(
            "Custom.Treeview.Heading",
            background="#3498db",  # 标准蓝色背景
            foreground="#ffffff",
            font=("微软雅黑", 13, "bold"),  # 增大表头字体到13pt，提升清晰度
            relief="raised",  # 凸起效果，让列边界更明显
            borderwidth=2,  # 增加边框宽度，突出列分隔
            padding=10  # 增加内边距，提升可读性
        )

        # 鼠标悬停效果
        style.map(
            "Custom.Treeview",
            background=[("selected", "#E3F2FD")],
            foreground=[("selected", "#2c3e50")]  # 选中时保持深色文字
        )

        # 表头悬停效果
        style.map(
            "Custom.Treeview.Heading",
            background=[("active", "#2980b9")]
        )

        # 配置按钮样式
        style.configure(
            "Custom.TButton",
            font=("微软雅黑", 11),  # 增大按钮字体
            padding=10,
            relief=tk.FLAT
        )

        style.map(
            "Custom.TButton",
            background=[("active", "#2980b9"), ("!active", "#3498db")],
            foreground=[("active", "#ffffff"), ("!active", "#ffffff")]
        )


    # ------------------------------------------------------------------
    # 以下 4 个 @staticmethod 已抽到 modules.prospective.utils
    # 这里保留同名薄包装，确保历史调用 DatabaseViewer.convert_timestamp(...) 仍然可用
    # ------------------------------------------------------------------

    @staticmethod
    def convert_timestamp(timestamp):
        """毫秒级时间戳 → 'YYYY-MM-DD HH:MM:SS'（转发到 utils.convert_timestamp）。"""
        return _utils_convert_timestamp(timestamp)

    @staticmethod
    def convert_time_format(time_str):
        """'2025年3月' → '2025-03-01 00:00:00'（转发到 utils.convert_time_format）。"""
        return _utils_convert_time_format(time_str)

    @staticmethod
    def validate_remark_format(remark):
        """校验 ¿¿¿NNNNNN-xxx 备注格式（转发到 utils.validate_remark_format）。"""
        return _utils_validate_remark_format(remark)

    @staticmethod
    def parse_xml_content(content):
        """从 sharecard XML 提取 (username, nickname)（转发到 utils.parse_xml_content）。"""
        return _utils_parse_xml_content(content)

    def _create_widgets(self):
        """创建界面组件"""

        # 顶部信息栏（白色卡片式设计）
        top_frame = tk.Frame(self.root, bg="#ffffff", padx=20, pady=15)
        top_frame.pack(fill=tk.X, padx=15, pady=(15, 10))

        # 左侧搜索区域
        search_frame = tk.Frame(top_frame, bg="#ffffff")
        search_frame.pack(side=tk.LEFT)

        # 搜索标签（去掉图标，使用中文冒号）
        search_label = tk.Label(
            search_frame,
            text="搜索：",
            font=("微软雅黑", 12, "bold"),
            bg="#ffffff",
            fg="#2c3e50"
        )
        search_label.pack(side=tk.LEFT, padx=(0, 10))

        # 搜索输入框容器（用于添加边框效果）
        entry_container = tk.Frame(
            search_frame,
            bg="#bdc3c7",  # 边框颜色
            bd=0,
            highlightthickness=1,
            highlightbackground="#bdc3c7",
            highlightcolor="#3498db"  # 聚焦时边框颜色
        )
        entry_container.pack(side=tk.LEFT, padx=(0, 12))

        # 搜索输入框（现代化样式，无占位文本）
        self.search_entry = tk.Entry(
            entry_container,
            textvariable=self.search_var,
            font=("微软雅黑", 12),
            width=35,  # 增加宽度
            relief=tk.FLAT,
            bd=0,
            fg="#1a1a1a",  # 更醒目的黑色
            bg="#ffffff",
            insertbackground="#3498db"  # 光标颜色
        )
        self.search_entry.pack(padx=2, pady=2, ipady=6)  # 增加内边距，提升高度

        # 边框高亮效果（聚焦/失焦）
        def on_focus_in(event):
            # 聚焦时边框高亮
            entry_container.config(highlightbackground="#3498db", highlightthickness=2)

        def on_focus_out(event):
            # 失焦时恢复边框
            entry_container.config(highlightbackground="#bdc3c7", highlightthickness=1)

        self.search_entry.bind("<FocusIn>", on_focus_in)
        self.search_entry.bind("<FocusOut>", on_focus_out)

        # 绑定实时搜索（边输入边搜索）
        self.search_var.trace_add("write", lambda *args: self._on_search_change())

        # 清空按钮（现代化样式，添加图标）
        clear_btn = tk.Button(
            search_frame,
            text="✕ 清空",
            command=self._on_clear_button_click,
            font=("微软雅黑", 11, "bold"),
            bg="#95a5a6",
            fg="#ffffff",
            activebackground="#7f8c8d",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=18,
            pady=10,  # 增加高度与搜索框对齐
            cursor="hand2",
            bd=0
        )
        clear_btn.pack(side=tk.LEFT, padx=(0, 12))

        # 搜索结果计数标签（在清空按钮右侧）
        self.search_count_label = tk.Label(
            search_frame,
            text="",
            font=("微软雅黑", 12, "bold"),
            fg="#2ecc71",  # 绿色，与底部状态一致
            bg="#ffffff"
        )
        self.search_count_label.pack(side=tk.LEFT)

        # 右侧按钮区域
        # 刷新按钮（右对齐，自定义样式）
        refresh_btn = tk.Button(
            top_frame,
            text="🔄 刷新数据",
            command=self.load_data,
            font=("微软雅黑", 11),
            bg="#3498db",
            fg="#ffffff",
            activebackground="#2980b9",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=20,
            pady=8,
            cursor="hand2",
            bd=0
        )
        refresh_btn.pack(side=tk.RIGHT)

        # 导出按钮
        export_btn = tk.Button(
            top_frame,
            text="📥 导出数据",
            command=self._export_data,
            font=("微软雅黑", 11),
            bg="#e67e22",
            fg="#ffffff",
            activebackground="#d35400",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=20,
            pady=8,
            cursor="hand2",
            bd=0
        )
        export_btn.pack(side=tk.RIGHT, padx=(0, 10))

        # 表格区域（白色背景容器）
        table_container = tk.Frame(self.root, bg="#ffffff", padx=5, pady=5)
        table_container.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 10))

        # 表格内部框架
        table_frame = tk.Frame(table_container, bg="#ffffff")
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 创建Treeview表格（列顺序：意向学员昵称、意向学员微信ID、意向学员微信号、意向学员总微信号、意向学员添加时间、意向学员内部备注、意向学员是否删除、来源昵称、来源微信ID、来源微信号、来源总微信号、来源内部备注）
        columns = ("nick", "wxid", "number", "total_number", "time", "obj_internal_note", "is_delete", "source_nickname", "source_username", "source_number", "source_total_number", "source_internal_note")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            style="Custom.Treeview"
        )

        # 设置列标题
        self.tree.heading("nick", text="意向学员(昵称)")
        self.tree.heading("wxid", text="意向学员(微信ID)")
        self.tree.heading("number", text="意向学员(微信号)")
        self.tree.heading("total_number", text="意向学员(总微信号)")
        self.tree.heading("time", text="意向学员(添加时间)")
        self.tree.heading("obj_internal_note", text="意向学员(内部备注)")
        self.tree.heading("is_delete", text="意向学员(是否删除)")
        self.tree.heading("source_nickname", text="来源(昵称)")
        self.tree.heading("source_username", text="来源(微信ID)")
        self.tree.heading("source_number", text="来源(微信号)")
        self.tree.heading("source_total_number", text="来源(总微信号)")
        self.tree.heading("source_internal_note", text="来源(内部备注)")

        # 设置列宽(适配1600宽度,用户可手动调整)
        self.tree.column("nick", width=140, minwidth=120, anchor=tk.W)
        self.tree.column("wxid", width=170, minwidth=150, anchor=tk.W)
        self.tree.column("number", width=130, minwidth=110, anchor=tk.W)
        self.tree.column("total_number", width=130, minwidth=110, anchor=tk.W)
        self.tree.column("time", width=140, minwidth=130, anchor=tk.W)
        self.tree.column("obj_internal_note", width=200, minwidth=150, anchor=tk.W)
        self.tree.column("is_delete", width=100, minwidth=80, anchor=tk.CENTER)
        self.tree.column("source_nickname", width=180, minwidth=140, anchor=tk.W)
        self.tree.column("source_username", width=180, minwidth=140, anchor=tk.W)
        self.tree.column("source_number", width=130, minwidth=110, anchor=tk.W)
        self.tree.column("source_total_number", width=130, minwidth=110, anchor=tk.W)
        self.tree.column("source_internal_note", width=200, minwidth=150, anchor=tk.W)

        # 添加交替行颜色（统一普通行样式，所有数据行只使用 oddrow / evenrow 两种斑马纹底色）
        # 已移除：warning_*、deleted_*、txt_file_*、new_data_* 等基于业务状态的特殊前景色样式，
        # 统一所有行的视觉表现，避免用颜色强调“已删除 / txt 来源”等业务含义。
        self.tree.tag_configure("oddrow", background="#F8F9FA")
        self.tree.tag_configure("evenrow", background="white")

        # 添加占位行样式（灰色、斜体，用于搜索无结果时的提示）
        self.tree.tag_configure("placeholder", background="#f5f6fa", foreground="#95a5a6", font=("微软雅黑", 12, "italic"))

        # 绑定单击事件
        self.tree.bind("<ButtonRelease-1>", self._on_cell_click)

        # 表格布局（已移除横向和纵向滚动条）
        self.tree.grid(row=0, column=0, sticky="nsew")

        # 配置表格区域权重,使其可伸缩
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        # 空状态提示标签（居中显示在表格上方，初始隐藏）
        self.empty_state_label = tk.Label(
            table_frame,
            text="🔍 未找到匹配的数据，请尝试其他关键词",
            font=("微软雅黑", 16, "bold"),
            fg="#95a5a6",
            bg="#ffffff"
        )
        # 使用 place 布局，居中显示（初始隐藏）
        # 注意：place 会覆盖在 grid 布局的表格上方
        self.empty_state_label.place(in_=table_frame, relx=0.5, rely=0.5, anchor="center")
        self.empty_state_label.place_forget()

        # 底部状态栏（白色卡片式设计）
        status_frame = tk.Frame(self.root, bg="#ffffff", padx=20, pady=15)
        status_frame.pack(fill=tk.X, padx=15, pady=(0, 15))

        # 状态栏内部垂直布局容器
        status_container = tk.Frame(status_frame, bg="#ffffff")
        status_container.pack(side=tk.LEFT, fill=tk.BOTH)

        # 第一行：加载状态
        self.status_label = tk.Label(
            status_container,
            text="就绪",
            font=("微软雅黑", 12, "bold"),
            fg="#2ecc71",
            bg="#ffffff",
            anchor=tk.W
        )
        self.status_label.pack(side=tk.TOP, anchor=tk.W)

        # 第二行：最近刷新时间（仅在 UI 成功更新时刷新；失败时保留上一次成功值）
        # 文案：未成功刷新过显示"暂无"
        self.last_refresh_label = tk.Label(
            status_container,
            text="最近刷新时间：暂无",
            font=("微软雅黑", 10),
            fg="#7f8c8d",  # 灰色，比加载状态弱、与路径行同色系
            bg="#ffffff",
            anchor=tk.W
        )
        self.last_refresh_label.pack(side=tk.TOP, anchor=tk.W, pady=(4, 0))

        # 第三行：本次刷新链路摘要（一句话总结本次实际走的链路）
        # 与 toast 区别：toast 提示"发生了什么降级"，摘要展示"最终结果是什么"
        self.summary_label = tk.Label(
            status_container,
            text="",
            font=("微软雅黑", 11),
            fg="#34495e",  # 默认深蓝灰，正常链路使用
            bg="#ffffff",
            anchor=tk.W
        )
        self.summary_label.pack(side=tk.TOP, anchor=tk.W, pady=(4, 0))

        # 第四行：内部库副本时间（当前本地 contact.db 文件的最后修改时间）
        # 与"最近刷新时间"区分：这里反映的是文件本身的新旧，而不是本次刷新动作的时间
        self.contact_mtime_label = tk.Label(
            status_container,
            text="内部库副本时间：暂无",
            font=("微软雅黑", 10),
            fg="#7f8c8d",  # 灰色，与最近刷新时间同色系，不抢眼
            bg="#ffffff",
            anchor=tk.W
        )
        self.contact_mtime_label.pack(side=tk.TOP, anchor=tk.W, pady=(2, 0))

        # 第五行：原有数据库路径（初始隐藏）
        self.main_db_label = tk.Label(
            status_container,
            text="",
            font=("微软雅黑", 10),
            fg="#7f8c8d",  # 灰色，与绿色加载状态区分
            bg="#ffffff",
            anchor=tk.W
        )
        self.main_db_label.pack(side=tk.TOP, anchor=tk.W, pady=(5, 0))

        # 第三行：contact.db 路径（初始隐藏）
        self.contact_db_label = tk.Label(
            status_container,
            text="",
            font=("微软雅黑", 10),
            fg="#7f8c8d",  # 灰色，与绿色加载状态区分
            bg="#ffffff",
            anchor=tk.W
        )
        self.contact_db_label.pack(side=tk.TOP, anchor=tk.W, pady=(2, 0))

        # Toast提示标签（初始隐藏，参考query_tool.py）
        self.toast_label = tk.Label(
            self.root,
            textvariable=self.toast_var,
            bg="#2ecc71",
            fg="#ffffff",
            font=("微软雅黑", 12, "bold"),
            padx=25,
            pady=12,
            relief=tk.FLAT,
            bd=0
        )

    def _on_cell_click(self, event):
        """
        处理单元格点击事件，复制内容到剪贴板

        简化逻辑：所有数据单元格都直接复制其显示值，
        仅保留必要的安全判断（避免点击表头/空白区域时报错）。

        Args:
            event: 点击事件对象
        """
        # 基础安全判断：必须点击在单元格区域，避免表头/空白报错
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return

        column = self.tree.identify_column(event.x)
        row_id = self.tree.identify_row(event.y)

        if not row_id or not column:
            return

        # 解析列索引，索引非法时直接返回，避免报错
        try:
            column_index = int(column.replace("#", "")) - 1
        except ValueError:
            return

        row_values = self.tree.item(row_id, "values")
        if column_index < 0 or column_index >= len(row_values):
            return

        # 直接取该单元格显示值进行复制
        cell_content = str(row_values[column_index])

        self.root.clipboard_clear()
        self.root.clipboard_append(cell_content)

        # 显示 Toast 提示
        self._show_toast(cell_content)

    def _export_data(self):
        """
        导出数据到Excel文件（直接导出，无需密码验证）

        功能:
            - 仅导出主表（完整数据）
        """
        self._do_export_all()

    def _do_export_all(self):
        """
        执行导出操作（仅完整导出表）

        功能:
            - 导出一个 Excel 文件（完整数据）
            - 统一处理成功/失败提示
        """
        # 检查是否有数据
        if not self.all_data or len(self.all_data) == 0:
            messagebox.showwarning("导出失败", "没有可导出的数据！")
            return

        # 生成时间戳（精确到毫秒，避免同一秒内重复导出导致同名覆盖）
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]

        # 执行导出
        success, filename, error = self._do_export_full(timestamp)

        if success:
            messagebox.showinfo(
                "导出成功",
                f"数据已成功导出！\n\n"
                f"文件名：{filename}\n"
                f"数据条数：{len(self.all_data)}\n\n"
                f"保存位置：当前目录"
            )
        else:
            messagebox.showerror(
                "导出失败",
                f"导出失败：{error}"
            )

    def _do_export_full(self, timestamp: str, output_dir: Optional[str] = None) -> Tuple[bool, str, str]:
        """
        导出当前数据为 Excel（薄包装）。

        实际实现已迁移至 modules.prospective.service.export_to_excel。
        本方法仅负责把 self.all_data + 入参转发给 service 层，
        以保持手动导出与自动导出（AutoExcelBackup.exporter=self._do_export_full）的统一出口。

        Args:
            timestamp: 时间戳字符串（格式：YYYYMMDD_HHMMSS）
            output_dir: 可选输出目录；None 时第二个返回值为文件名，否则为完整路径

        Returns:
            (是否成功, 文件名/完整路径, 错误信息) 三元组
        """
        return _service_export_to_excel(self.all_data, timestamp, output_dir)

    # ------------------------------------------------------------------
    # Toast 系列方法 —— 实现已迁移至 modules.prospective.ui_widgets.ToastManager
    # 这里保留 thin shim，确保 AutoExcelBackup(toast_warn=self._show_warning_toast) 等
    # 旧调用方式仍然可用；若管理器尚未就绪（极早期阶段）则降级为静默 no-op
    # ------------------------------------------------------------------
    def _show_toast(self, content: str, duration_ms: int = 1500):
        """成功 Toast（绿色 ✓）。转发到 toast_manager。"""
        if self.toast_manager is not None:
            self.toast_manager.show_info(content, duration_ms)

    def _show_warning_toast(self, content: str, duration_ms: int = 5000):
        """警告 Toast（红色 ⚠）。转发到 toast_manager。"""
        if self.toast_manager is not None:
            self.toast_manager.show_warning(content, duration_ms)

    def _hide_toast(self):
        """隐藏 Toast。转发到 toast_manager。"""
        if self.toast_manager is not None:
            self.toast_manager.hide()

    def _on_search_change(self):
        """
        搜索框内容变化时的处理（实时搜索）
        """
        # 获取搜索关键字
        keyword = self.search_var.get().strip()

        # 过滤并显示数据
        self._filter_and_display_data(keyword)

    def _on_clear_button_click(self):
        """
        清空按钮点击事件（清空搜索框并显示全量数据）
        """
        # 清空搜索框
        self.search_var.set("")

        # 直接显示全量数据（不重新加载数据库）
        self._filter_and_display_data("")

    def _filter_and_display_data(self, keyword: str = ""):
        """
        根据关键字过滤并显示数据

        Args:
            keyword: 搜索关键字（空字符串表示显示全部数据）
        """
        # 清空当前显示
        for item in self.tree.get_children():
            self.tree.delete(item)

        # 隐藏空状态提示（默认）
        if self.empty_state_label:
            self.empty_state_label.place_forget()

        # 如果没有数据，直接返回
        if not self.all_data:
            # 更新搜索结果计数（无数据）
            if self.search_count_label:
                self.search_count_label.config(text="")
            return

        # 过滤数据（性能优化：使用列索引常量和列表推导式）
        if keyword:
            # 转换为小写进行模糊匹配
            keyword_lower = keyword.lower()

            # 使用列表推导式提升性能
            filtered_data = [
                item for item in self.all_data
                if keyword_lower in str(item['values'][COL_OBJ_NICK]).lower()
                or keyword_lower in str(item['values'][COL_OBJ_TOTAL_NUMBER]).lower()
            ]
        else:
            # 无关键字，显示全部数据
            filtered_data = self.all_data

        # 更新搜索结果计数
        if self.search_count_label:
            if keyword:
                # 有搜索关键字时显示计数
                self.search_count_label.config(text=f"搜索到 {len(filtered_data)} 条数据")
            else:
                # 无搜索关键字时隐藏计数
                self.search_count_label.config(text="")

        # 显示过滤后的数据
        if filtered_data:
            for index, item in enumerate(filtered_data, start=1):
                # 统一普通行样式：仅按结果行序号决定斑马纹底色，
                # 不再根据业务标签（已删除 / txt 来源）做特殊视觉强调。
                new_tag = "oddrow" if index % 2 == 1 else "evenrow"
                self.tree.insert("", tk.END, values=item['values'], tags=(new_tag,))
        else:
            # 无匹配数据，显示居中的空状态提示
            if self.empty_state_label:
                self.empty_state_label.place(relx=0.5, rely=0.5, anchor="center")

    # ------------------------------------------------------------------
    # Loading 系列方法 —— 实现已迁移至 modules.prospective.ui_widgets.LoadingOverlay
    # 旧的 _create_loading_widget / _animate_loading 不再需要：
    #   - 资源在 LoadingOverlay 构造时自动创建
    #   - 动画在 show()/hide() 内部调度
    # 这里只保留 _show_loading / _hide_loading 作为 thin shim，
    # 历史调用方（如 load_data 中的 self._show_loading()）保持不变
    # ------------------------------------------------------------------
    def _show_loading(self):
        """显示 Loading 遮罩。转发到 loading_overlay。"""
        if self.loading_overlay is not None:
            self.loading_overlay.show()

    def _hide_loading(self):
        """隐藏 Loading 遮罩。转发到 loading_overlay。"""
        if self.loading_overlay is not None:
            self.loading_overlay.hide()

    def load_data(self):
        """加载数据库数据(入口方法 - 使用线程异步加载)"""

        # 重置搜索框
        self.search_var.set("")

        # 隐藏空状态提示
        if self.empty_state_label:
            self.empty_state_label.place_forget()

        # 清空搜索结果计数
        if self.search_count_label:
            self.search_count_label.config(text="")

        # 清空现有数据(在主线程中操作UI)
        for item in self.tree.get_children():
            self.tree.delete(item)

        # 显示Loading动画
        self._show_loading()

        # 更新状态
        self.status_label.config(text="正在连接数据库...", fg="#3498db")

        # 在子线程中执行数据加载
        thread = threading.Thread(target=self._load_data_in_thread)
        thread.daemon = True  # 守护线程,主程序退出时自动结束
        thread.start()

    def _load_data_in_thread(self):
        """在子线程中执行数据加载"""

        # ========== 性能日志：开始计时 ==========
        start_time = time.time()
        print(f"\n{'='*60}")
        print(f"[性能日志] 开始加载数据")
        print(f"{'='*60}")

        # ========== 本次加载的"降级 / 回退 / 失败"消息收集 ==========
        # 用途：本次刷新结束后，合并成一条 toast 提示用户（避免静默降级无感知）
        # 只记录"程序仍能继续运行、但用户不一定知道"的事件
        degradation_messages: List[str] = []

        # 用于状态栏标注主路径 / 备用路径
        is_using_backup_db: bool = False
        # 用于状态栏标注 contact.db 来源类型
        contact_db_source: str = ""  # 取值: "网络同步副本" / "本地旧副本" / "未连接"
        # 文案语义：
        #   "网络同步副本"：本轮成功从 NAS 同步到本地，当前连接的是这份刚同步下来的本地副本
        #   "本地旧副本"  ：网络不可用 或 网络可用但同步失败，仍使用桌面已有的旧副本
        #   "未连接"      ：本地副本不存在或连接失败，contact_conn 为 None
        # 用于状态栏标注售前通讯录.txt 本次是否真的参与了数据合并
        # 取值:
        #   "loaded"  - 已参与（文件存在且无异常，至少进入了读取流程）
        #   "missing" - 未参与（文件不存在）
        #   "failed"  - 读取失败（文件存在但解析异常）
        # 默认 "loaded"，下面遇到对应分支时再覆盖
        txt_status: str = "loaded"

        # ========== 新增：检测主数据库是否存在（仅首次启动时提示） ==========
        if self.is_first_load and not os.path.exists(PRIMARY_DB):
            # 主数据库不存在，标记需要显示警告（在数据加载完成后显示）
            self.need_show_db_warning = True

        # 标记已完成首次加载
        self.is_first_load = False

        # 获取数据库连接(在子线程中执行)
        step_start = time.time()
        conn, message = get_database_connection()
        print(f"[性能日志] 1. 获取数据库连接: {(time.time() - step_start)*1000:.2f}ms")

        if not conn:
            # 连接失败 - 调度主线程更新UI
            error_msg = message
            self.root.after(0, lambda: self._on_load_error("连接失败", f"无法连接到数据库:\n\n{error_msg}"))
            return

        # 注：不再把 conn 赋给 self.conn，超微数据库连接生命周期完全在本函数内
        #     所有查询使用局部 conn，finally 中由 conn.close() 统一释放

        # 提取数据库路径(从消息中)
        if ":" in message:
            self.current_db_path = message.split(":", 1)[1].strip()

        # 判断本次连接的是主路径还是备用路径
        # 注意：DatabaseReader 返回的 message 形如 "✓ 成功连接主路径: ..." / "✓ 成功连接备用路径: ..."
        if "备用路径" in message:
            is_using_backup_db = True
            degradation_messages.append("超微主库不可用，已回退到备用库")

        # ========== 新增：异步备份超微数据库（性能优化） ==========
        # 如果连接成功且使用的是主数据库，则异步备份
        if self.current_db_path:
            # 只有当前连接的是主数据库时才备份（使用模块级常量）
            if self.current_db_path == PRIMARY_DB:
                # 异步备份到本地，不阻塞数据加载
                # 包装一层，失败时把错误挂到 _pending_backup_errors，下次刷新合并 toast 提示
                def _async_local_backup():
                    if not backup_database(PRIMARY_DB, BACKUP_DB):
                        with self._pending_backup_errors_lock:
                            self._pending_backup_errors.append("超微库本地备份失败")

                backup_thread = threading.Thread(
                    target=_async_local_backup,
                    daemon=True
                )
                backup_thread.start()
                print(f"[性能日志] 2. 异步备份超微数据库到本地（已启动后台线程）")

                # 异步备份到网络路径（带时间标签）
                def _async_network_backup():
                    if not backup_to_network(PRIMARY_DB):
                        with self._pending_backup_errors_lock:
                            self._pending_backup_errors.append("超微库网络备份失败")

                network_backup_thread = threading.Thread(
                    target=_async_network_backup,
                    daemon=True
                )
                network_backup_thread.start()
                print(f"[性能日志] 3. 异步备份超微数据库到网络（已启动后台线程）")

        # 生成原有数据库精简路径（最后4级）
        main_db_short_path = ""
        if self.current_db_path:
            main_db_short_path = get_short_path(self.current_db_path, num_parts=4)

        # ========== 新增：优化 contact.db 连接逻辑（性能优化） ==========
        # 实时性保证：每次刷新都必须重新走一遍完整链路
        #   1) 先安全关闭并丢弃上一轮的 contact.db 连接和状态，避免：
        #      - 旧 sqlite 连接句柄泄漏
        #      - 旧句柄在 Windows 上锁住桌面 contact.db，影响新一轮 backup_database 覆盖
        #      - 本轮失败分支下错误沿用上一轮的 self.contact_db_path 残留状态
        #   2) 再重新执行：路径解析 / 网络判断 / 同步落盘 / sqlite 连接
        #   3) 后续 contact_info_map 在 _load_data_in_thread 内是局部变量，每轮天然不复用
        if getattr(self, 'contact_conn', None) is not None:
            try:
                self.contact_conn.close()
            except Exception as _close_err:
                # 关闭失败不影响主流程，仅记录日志
                print(f"[备份日志] 关闭上一轮 contact.db 连接失败（已忽略）: {_close_err}")
        self.contact_conn = None
        self.contact_db_path = ""

        step_start = time.time()
        try:
            # 获取 contact.db 网络路径
            network_db_path = get_contact_db_path()

            # 如果网络路径存在，先备份到桌面，然后访问桌面文件
            if network_db_path.startswith(CONTACT_NETWORK_BASE) and os.path.exists(network_db_path):
                backup_start = time.time()
                backup_success = backup_database(network_db_path, CONTACT_LOCAL_BACKUP)
                print(f"[性能日志] 4. 备份 contact.db 到桌面: {(time.time() - backup_start)*1000:.2f}ms")

                # 使用本地备份（性能优化：从本地访问更快）
                self.contact_db_path = CONTACT_LOCAL_BACKUP
                # 标识：网络同步副本（程序实际连接的是本地副本，但本轮内容刚从网络同步下来）
                contact_db_source = "网络同步副本"

                # 同步落盘失败：网络盘读到了，但拷贝桌面失败 → 仍可用旧桌面副本，但要提示
                if not backup_success:
                    degradation_messages.append("contact.db 同步到本地失败，仍使用本地旧副本")
            else:
                # 网络路径不存在，直接使用桌面路径
                self.contact_db_path = CONTACT_LOCAL_BACKUP
                contact_db_source = "本地旧副本"
                print(f"[性能日志] 4. 网络路径不存在，直接使用桌面 contact.db")
                # 静默降级：网络盘掉线/路径不存在 → 用户需要知道
                degradation_messages.append("内部数据库网络路径不可用，已使用本地旧副本")

            # 连接本地 contact.db
            if os.path.exists(self.contact_db_path):
                connect_start = time.time()
                self.contact_conn = sqlite3.connect(self.contact_db_path)
                # 验证连接可用性
                cursor = self.contact_conn.cursor()
                cursor.execute("SELECT 1")
                cursor.close()
                print(f"[性能日志] 5. 连接本地 contact.db: {(time.time() - connect_start)*1000:.2f}ms")
            else:
                self.contact_conn = None
                contact_db_source = "未连接"
                degradation_messages.append("内部数据库未连接，内部备注将为空")
                print(f"[性能日志] 5. 本地 contact.db 不存在")

        except Exception as e:
            # 连接失败：保证不沿用任何上一轮残留状态
            # 如果异常发生在 sqlite3.connect 之后，要把这个半成品连接也关掉
            if getattr(self, 'contact_conn', None) is not None:
                try:
                    self.contact_conn.close()
                except Exception:
                    pass
            self.contact_conn = None
            contact_db_source = "未连接"
            degradation_messages.append("内部数据库连接失败，内部备注将为空")
            print(f"[备份日志] contact.db 连接失败: {e}")
            pass

        # 更新状态 - 调度主线程更新UI
        self.root.after(0, lambda: self.status_label.config(text="正在读取数据...", fg="#3498db"))

        # 读取数据(在子线程中执行)
        try:

            cursor = conn.cursor()

            # ========== 预加载：构建微信ID到昵称、微信号和删除状态的映射表（性能优化） ==========
            step_start = time.time()
            wxid_info = {}
            try:
                # 性能优化：添加 WHERE 条件，过滤空 WxID
                cursor.execute("SELECT WxID, Nick, Number, Deleted FROM ContactConfigTable WHERE WxID IS NOT NULL")

                # 性能优化：使用字典推导式批量构建映射
                wxid_info = {
                    row[0]: {
                        'nick': row[1] or "",
                        'number': row[2] or "",
                        'deleted': row[3] or 0
                    }
                    for row in cursor.fetchall()
                }
                print(f"[性能日志] 6. 预加载 ContactConfigTable ({len(wxid_info)} 条): {(time.time() - step_start)*1000:.2f}ms")
            except sqlite3.OperationalError:
                # ContactConfigTable 表不存在,使用空映射
                print("警告: ContactConfigTable 表不存在,昵称、微信号和删除状态列将为空")
                wxid_info = {}
                degradation_messages.append("ContactConfigTable 表读取失败")

            # ========== 预加载：构建 contact.db 的 username 到完整信息的映射表（性能优化：流式处理） ==========
            step_start = time.time()
            contact_info_map = {}
            valid_count = 0
            invalid_count = 0
            if self.contact_conn:
                try:
                    contact_cursor = self.contact_conn.cursor()
                    # 性能优化：只查询需要的列，减少数据传输
                    contact_cursor.execute("SELECT username, remark, nick_name, alias FROM contact WHERE username IS NOT NULL")

                    # 性能优化：使用流式处理（fetchmany），减少内存峰值
                    batch_size = 1000
                    while True:
                        rows = contact_cursor.fetchmany(batch_size)
                        if not rows:
                            break

                        for row in rows:
                            username = row[0]
                            remark = row[1] if row[1] else ""
                            nick_name = row[2] if row[2] else ""
                            alias = row[3] if row[3] else ""

                            # 验证 remark 格式（¿¿¿ + 6个数字 + - + 其他内容）
                            if self.validate_remark_format(remark):
                                contact_info_map[username] = {
                                    'remark': remark,
                                    'nick_name': nick_name,
                                    'alias': alias
                                }
                                valid_count += 1
                            else:
                                contact_info_map[username] = {
                                    'remark': "",
                                    'nick_name': nick_name,
                                    'alias': alias
                                }
                                invalid_count += 1

                    contact_cursor.close()
                    print(f"[性能日志] 7. 预加载 contact 表 (总计: {len(contact_info_map)}, 有效: {valid_count}, 无效: {invalid_count}): {(time.time() - step_start)*1000:.2f}ms")
                except (sqlite3.OperationalError, Exception) as e:
                    # contact 表不存在或查询失败,使用空映射
                    contact_info_map = {}
                    print(f"[性能日志] 7. 预加载 contact 表失败: {e}")
                    degradation_messages.append("contact.db 的 contact 表读取失败")
                finally:
                    # 读取完成（无论成功或失败）立即关闭 contact.db 连接：
                    #   - 后续数据处理只消费内存中的 contact_info_map，不再访问 sqlite
                    #   - 避免文件句柄长期占用桌面 contact.db，影响下一轮 backup_database 覆盖
                    #   - 避免半开连接残留
                    # 注意：__del__ / 窗口关闭仍保留兜底关闭逻辑，作为双保险
                    try:
                        self.contact_conn.close()
                    except Exception as _close_err:
                        print(f"[备份日志] 关闭本轮 contact.db 连接失败（已忽略）: {_close_err}")
                    self.contact_conn = None

            # ========== 预加载：构建售前通讯录.txt的微信ID到添加时间的映射表（性能优化：流式读取） ==========
            step_start = time.time()
            txt_time_map = {}  # {微信ID: 添加时间}
            txt_file_path = get_resource_path("售前通讯录.txt")
            try:
                if os.path.exists(txt_file_path):
                    # 性能优化：使用生成器逐行读取，避免一次性加载整个文件
                    with open(txt_file_path, 'r', encoding='utf-8') as f:
                        # 跳过表头
                        next(f, None)

                        for line in f:
                            # 去除首尾空白字符
                            line = line.rstrip('\r\n')

                            # 跳过完全空白的行
                            if not line.strip():
                                continue

                            # 按制表符分割
                            parts = line.split('\t')

                            # 提取微信ID（第2列）和添加时间（第5列）
                            if len(parts) >= 5:
                                wxid = parts[1].strip()
                                time_str = parts[4].strip()

                                # 转换时间格式（处理"2025年3月"格式）
                                if wxid and time_str:
                                    converted_time = self.convert_time_format(time_str)
                                    if converted_time:
                                        txt_time_map[wxid] = converted_time

                    print(f"[性能日志] 8. 预加载售前通讯录.txt时间映射 ({len(txt_time_map)} 条): {(time.time() - step_start)*1000:.2f}ms")
                else:
                    print(f"[性能日志] 8. 售前通讯录.txt 不存在，跳过时间映射")
                    # 标记一次即可，下面"数据行追加"步骤检测到不存在时不重复 append
                    degradation_messages.append("售前通讯录.txt 不存在，未参与补充")
                    txt_status = "missing"
            except Exception as e:
                print(f"[性能日志] 8. 预加载售前通讯录.txt时间映射失败: {e}")
                txt_time_map = {}
                degradation_messages.append("售前通讯录.txt 时间映射读取失败")
                txt_status = "failed"

            # ========== 第一部分：读取 AutoAgreeAddFriendTask 表 ==========
            step_start = time.time()
            cursor.execute("SELECT wxid, time, content FROM AutoAgreeAddFriendTask")
            rows = cursor.fetchall()
            print(f"[性能日志] 9. 读取 AutoAgreeAddFriendTask ({len(rows)} 条): {(time.time() - step_start)*1000:.2f}ms")

            # 准备要插入的数据列表(在子线程中处理数据)
            data_to_insert = []

            # 第一步：预处理数据，构建每个wxid的最早记录字典
            step_start = time.time()
            wxid_earliest = {}  # {wxid: {'nickname': str, 'username': str, 'time': int}}

            for row in rows:
                wxid_value = row[0] if row[0] is not None else ""
                time_value = row[1] if row[1] is not None else 0
                content_value = row[2] if row[2] is not None else ""

                # 解析XML提取用户名和昵称
                username, nickname = self.parse_xml_content(content_value)

                # 如果该wxid还没有记录，或者当前时间更早，则更新
                if wxid_value:
                    if wxid_value not in wxid_earliest:
                        wxid_earliest[wxid_value] = {
                            'nickname': nickname,
                            'username': username,
                            'time': time_value
                        }
                    else:
                        # 比较时间，保留最早的记录
                        if time_value < wxid_earliest[wxid_value]['time']:
                            wxid_earliest[wxid_value] = {
                                'nickname': nickname,
                                'username': username,
                                'time': time_value
                            }

            print(f"[性能日志] 10. 预处理数据（构建最早记录）: {(time.time() - step_start)*1000:.2f}ms")

            # 第二步：插入数据到表格，并标记不一致的数据
            step_start = time.time()
            for index, row in enumerate(rows):
                # 提取原始数据
                wxid_value = row[0] if row[0] is not None else ""
                time_value = row[1] if row[1] is not None else 0
                content_value = row[2] if row[2] is not None else ""

                # 转换时间戳
                formatted_time = self.convert_timestamp(time_value)

                # 备用时间源：如果时间为空，从售前通讯录.txt映射表查询
                if not formatted_time and wxid_value and wxid_value in txt_time_map:
                    formatted_time = txt_time_map[wxid_value]

                # 解析XML提取用户名和昵称
                username, nickname = self.parse_xml_content(content_value)

                # 直接使用 XML 解析得到的来源昵称 / 来源微信ID 原值
                display_nickname = nickname if nickname else ""
                display_username = username if username else ""

                # 查询昵称、微信号和删除状态(从预加载的映射表中获取)
                # 如果wxid_value不在映射表中，视为已删除
                if wxid_value and wxid_value in wxid_info:
                    info = wxid_info[wxid_value]
                    nick = clean_newlines(info['nick'])  # 清理意向学员昵称中的换行符
                    number = info['number']
                    deleted_value = info.get('deleted', 0)
                else:
                    # WxID不存在于ContactConfigTable，视为已删除
                    nick = ""
                    number = ""
                    deleted_value = 1  # 标记为已删除

                # 计算"意向学员(总微信号)"：如果number非空则用number，否则用wxid_value
                total_number = number if number and number.strip() else wxid_value

                # 判断是否删除：如果deleted==1，显示"已删除"(红色)
                is_delete_text = "已删除" if deleted_value == 1 else ""

                # 查询"意向学员(内部备注)"：使用意向学员微信ID去contact.db查询
                obj_internal_note = ""
                if wxid_value:
                    contact_data = contact_info_map.get(wxid_value, None)
                    if contact_data and contact_data['remark']:
                        # remark已经在预加载时验证过格式，这里直接使用
                        obj_internal_note = contact_data['remark']

                # 三级数据优先级：来源相关列的数据填充
                # 来源微信ID 为空时，所有来源相关列保持空字符串，不注入任何提示文案
                if not username or username.strip() == "":
                    display_nickname = ""
                    source_number = ""
                    source_total_number = ""
                    internal_note = ""
                else:
                    # 第一优先级：ContactConfigTable
                    source_info = wxid_info.get(username, None)
                    if source_info:
                        # 使用ContactConfigTable数据
                        display_nickname = clean_newlines(source_info['nick'])  # 清理来源昵称中的换行符
                        source_number = source_info['number']
                        # 查询内部备注
                        contact_data = contact_info_map.get(username, None)
                        internal_note = contact_data['remark'] if contact_data else ""
                    else:
                        # 第二优先级：contact.db
                        contact_data = contact_info_map.get(username, None)
                        if contact_data:
                            # 使用contact.db数据
                            display_nickname = clean_newlines(contact_data['nick_name'])  # 清理来源昵称中的换行符
                            source_number = contact_data['alias']
                            internal_note = contact_data['remark']
                        else:
                            # 第三优先级：数据库数据填空串
                            display_nickname = ""
                            source_number = ""
                            internal_note = ""

                    # 计算"来源(总微信号)"：如果source_number非空则用source_number，否则用username
                    source_total_number = source_number if source_number and source_number.strip() else username

                # 统一普通行样式：所有数据按行序号分配普通斑马纹 tag
                tag = "evenrow" if index % 2 == 0 else "oddrow"

                # 添加到待插入列表（新列顺序：意向学员相关列在前，来源相关列在后）
                # 列顺序：意向学员昵称、意向学员微信ID、意向学员微信号、意向学员总微信号、意向学员添加时间、意向学员内部备注、意向学员是否删除、来源昵称、来源微信ID、来源微信号、来源总微信号、来源内部备注
                data_to_insert.append({
                    'values': (nick, wxid_value, number, total_number, formatted_time, obj_internal_note, is_delete_text, display_nickname, display_username, source_number, source_total_number, internal_note),
                    'tag': tag
                })

            print(f"[性能日志] 11. 处理 AutoAgreeAddFriendTask 数据: {(time.time() - step_start)*1000:.2f}ms")

            # 默认状态文案：若后续 txt 链未参与，则使用此文案
            # 注：ContactAuthMsgTable 链路已移除，数据来源仅保留 AutoAgreeAddFriendTask 与 售前通讯录.txt
            status_msg = f"✓ 加载完成，共 {len(rows)} 条记录（仅 AutoAgreeAddFriendTask）"

            cursor.close()

            # ========== 第二部分：读取售前通讯录.txt并追加 ==========
            step_start = time.time()
            txt_data_count = 0
            skipped_lines = []  # 记录跳过的行

            try:
                if os.path.exists(txt_file_path):
                    with open(txt_file_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()

                    print(f"[调试] 售前通讯录.txt 总行数: {len(lines)}，数据行数（不含表头）: {len(lines) - 1}")

                    # 跳过表头（第一行）
                    current_total = len(data_to_insert)

                    for line_idx, line in enumerate(lines[1:], start=1):
                        # 去除首尾空白字符（但保留制表符）
                        line = line.rstrip('\r\n')

                        # 跳过完全空白的行
                        if not line.strip():
                            skipped_lines.append((line_idx + 1, "空行"))
                            continue

                        # 按制表符分割
                        parts = line.split('\t')

                        # 增强容错：如果列数不足12列，用空字符串填充
                        while len(parts) < 12:
                            parts.append('')

                        # 提取各列数据（安全提取，防止索引越界）- 新列顺序（12列）
                        # 列顺序：意向学员昵称、意向学员微信ID、意向学员微信号、意向学员总微信号、意向学员添加时间、意向学员内部备注、意向学员是否删除、来源昵称、来源微信ID、来源微信号、来源总微信号、来源内部备注
                        nick_from_file = parts[0].strip() if len(parts) > 0 else ""
                        wxid_value = parts[1].strip() if len(parts) > 1 else ""
                        number_from_file = parts[2].strip() if len(parts) > 2 else ""
                        total_number_from_file = parts[3].strip() if len(parts) > 3 else ""
                        formatted_time = parts[4].strip() if len(parts) > 4 else ""
                        obj_internal_note_from_file = parts[5].strip() if len(parts) > 5 else ""
                        is_delete_symbol = parts[6].strip() if len(parts) > 6 else ""
                        source_nickname = parts[7].strip() if len(parts) > 7 else ""
                        source_username = parts[8].strip() if len(parts) > 8 else ""
                        source_number = parts[9].strip() if len(parts) > 9 else ""
                        source_total_number = parts[10].strip() if len(parts) > 10 else ""
                        source_internal_note = parts[11].strip() if len(parts) > 11 else ""

                        # 第 5 列添加时间：统一只采用 txt 第 5 列，经 convert_time_format 标准化
                        formatted_time = self.convert_time_format(formatted_time)

                        # 统一删除状态判断：用wxid_value匹配ContactConfigTable
                        if wxid_value and wxid_value in wxid_info:
                            # 匹配成功，昵称 / 微信号 / 删除状态使用数据库数据
                            info = wxid_info[wxid_value]
                            nick = clean_newlines(info['nick'])  # 使用数据库Nick，清理换行符
                            number = info['number']  # 使用数据库Number
                            deleted_value = info.get('deleted', 0)
                        else:
                            # 匹配失败，标记为已删除
                            nick = clean_newlines(nick_from_file)  # 使用文本文件Nick，清理换行符
                            number = number_from_file
                            deleted_value = 1  # 标记为已删除

                        # 计算"意向学员(总微信号)"：如果number非空则用number，否则用wxid_value
                        total_number = number if number and number.strip() else wxid_value

                        # 判断是否删除：如果deleted==1，显示"已删除"
                        is_delete_text = "已删除" if deleted_value == 1 else ""

                        # 查询"意向学员(内部备注)"：优先使用contact.db数据，否则使用文本文件数据
                        obj_internal_note = ""
                        if wxid_value:
                            contact_data = contact_info_map.get(wxid_value, None)
                            if contact_data and contact_data['remark']:
                                # 使用contact.db数据（已验证格式）
                                obj_internal_note = contact_data['remark']
                            else:
                                # 使用文本文件数据（需要验证格式）
                                if obj_internal_note_from_file and self.validate_remark_format(obj_internal_note_from_file):
                                    obj_internal_note = obj_internal_note_from_file

                        # 三级数据优先级：来源相关列的数据填充（售前通讯录.txt）
                        if not source_username or source_username.strip() == "":
                            # 来源微信ID为空，使用文本文件数据
                            final_source_nickname = clean_newlines(source_nickname)  # 清理来源昵称中的换行符
                            final_source_number = source_number
                            final_source_total_number = source_total_number
                            final_source_internal_note = source_internal_note
                        else:
                            # 第一优先级：ContactConfigTable
                            source_info = wxid_info.get(source_username, None)
                            if source_info:
                                # 使用ContactConfigTable数据
                                final_source_nickname = clean_newlines(source_info['nick'])  # 清理来源昵称中的换行符
                                final_source_number = source_info['number']
                                # 查询内部备注
                                contact_data = contact_info_map.get(source_username, None)
                                final_source_internal_note = contact_data['remark'] if contact_data else ""
                            else:
                                # 第二优先级：contact.db
                                contact_data = contact_info_map.get(source_username, None)
                                if contact_data:
                                    # 使用contact.db数据
                                    final_source_nickname = clean_newlines(contact_data['nick_name'])  # 清理来源昵称中的换行符
                                    final_source_number = contact_data['alias']
                                    final_source_internal_note = contact_data['remark']
                                else:
                                    # 第三优先级：使用文本文件原始数据
                                    final_source_nickname = clean_newlines(source_nickname)  # 清理来源昵称中的换行符
                                    final_source_number = source_number
                                    final_source_internal_note = source_internal_note

                            # 计算"来源(总微信号)"：如果final_source_number非空则用final_source_number，否则用source_username
                            final_source_total_number = final_source_number if final_source_number and final_source_number.strip() else source_username

                        # 统一普通行样式：txt 来源数据也按行序号分配普通斑马纹 tag，
                        # 不再使用 txt_file_* / deleted_* 等带特殊前景色的 tag。
                        tag = "evenrow" if (current_total + txt_data_count) % 2 == 0 else "oddrow"

                        # 添加到待插入列表（新列顺序：意向学员相关列在前，来源相关列在后）
                        # 列顺序：意向学员昵称、意向学员微信ID、意向学员微信号、意向学员总微信号、意向学员添加时间、意向学员内部备注、意向学员是否删除、来源昵称、来源微信ID、来源微信号、来源总微信号、来源内部备注
                        data_to_insert.append({
                            'values': (nick, wxid_value, number, total_number, formatted_time, obj_internal_note, is_delete_text, final_source_nickname, source_username, final_source_number, final_source_total_number, final_source_internal_note),
                            'tag': tag
                        })
                        txt_data_count += 1

                    # 输出跳过的行信息
                    if skipped_lines:
                        print(f"[调试] 跳过 {len(skipped_lines)} 行:")
                        for line_num, reason in skipped_lines[:10]:  # 只显示前10条
                            print(f"  - 第 {line_num} 行: {reason}")
                        if len(skipped_lines) > 10:
                            print(f"  - ... 还有 {len(skipped_lines) - 10} 行")

                    print(f"[性能日志] 14. 读取售前通讯录.txt ({txt_data_count} 条): {(time.time() - step_start)*1000:.2f}ms")

                    # 更新状态消息
                    total_count = len(rows) + txt_data_count
                    status_msg = f"✓ 加载完成，共 {total_count} 条记录（AutoAgreeAddFriendTask: {len(rows)}，售前通讯录: {txt_data_count}）"
                else:
                    print(f"[性能日志] 14. 售前通讯录.txt 不存在，跳过")
                    # 注意：上面"时间映射"分支已 append 过相同消息，这里不重复

            except Exception as e:
                # 文本文件读取失败，仅记录警告，不中断流程
                print(f"警告: 无法读取售前通讯录.txt: {e}")
                import traceback
                traceback.print_exc()
                degradation_messages.append("售前通讯录.txt 读取失败")
                txt_status = "failed"
                pass

            # 生成 contact.db 精简路径（最后4级）
            contact_db_short_path = ""
            if self.contact_db_path:
                contact_db_short_path = get_short_path(self.contact_db_path, num_parts=4)

            # 性能日志：总耗时
            total_time = time.time() - start_time
            print(f"\n[性能日志] 15. 数据处理总耗时: {total_time*1000:.2f}ms ({total_time:.2f}秒)")
            print(f"{'='*60}\n")

            # 调度主线程更新UI（传递两个数据库路径 + 降级消息 + 数据源类型标识 + txt 参与状态）
            ui_start = time.time()
            self.root.after(0, lambda: self._update_ui_with_data(
                data_to_insert, status_msg, main_db_short_path, contact_db_short_path,
                ui_start, degradation_messages, is_using_backup_db, contact_db_source,
                txt_status
            ))

        except sqlite3.OperationalError as e:
            # 表不存在或SQL错误 - 调度主线程更新UI
            error_msg = f"查询失败: {e}"
            detail_msg = f"无法读取表数据:\n\n{error_msg}\n\n可能原因:\n- 表 'AutoAgreeAddFriendTask' 不存在\n- 列名不正确"
            self.root.after(0, lambda: self._on_load_error("查询错误", detail_msg))

        except Exception as e:
            # 其他错误 - 调度主线程更新UI
            error_msg = f"未知错误: {type(e).__name__}: {e}"
            self.root.after(0, lambda: self._on_load_error("错误", error_msg))

        finally:
            # 关闭主数据库连接（contact.db 连接保留用于后续数据交叉）
            # 重要：确保数据库连接被正确关闭，释放资源
            try:
                if conn:
                    conn.close()
            except Exception:
                # 静默处理关闭异常，避免影响错误处理流程
                pass

    def _deduplicate_data(self, data_list: list) -> list:
        """
        对数据进行去重，保留添加时间最早的记录

        去重规则：
            - 第2列（意向学员微信ID）和第8列（来源微信ID）完全一致视为重复
            - 只对添加时间非空的数据进行去重
            - 保留添加时间最早的一条记录
            - 添加时间为空的数据不参与去重，全部保留

        Args:
            data_list: 待去重的数据列表，每项包含 'values' 和 'tag'

        Returns:
            去重后的数据列表

        性能:
            - 时间复杂度: O(n)，使用字典去重
            - 空间复杂度: O(n)，需要存储去重字典

        注意:
            - 时间字符串比较使用字典序（YYYY-MM-DD HH:MM:SS格式）
        """
        # 构建去重字典：key=(col2, col8), value=最早的记录
        unique_records = {}
        non_duplicate_records = []  # 不参与去重的记录（添加时间为空）

        for item in data_list:
            values = item['values']
            # 使用列索引常量，提升代码可读性和可维护性
            if len(values) > COL_SOURCE_WXID:
                col_wxid = str(values[COL_OBJ_WXID]).strip()  # 意向学员微信ID
                col_source_username = str(values[COL_SOURCE_WXID]).strip()  # 来源微信ID
                col_time = str(values[COL_OBJ_TIME]).strip()  # 添加时间

                # 只对添加时间非空的数据进行去重
                if col_time and col_time != "":
                    key = (col_wxid, col_source_username)

                    if key not in unique_records:
                        # 第一次出现，直接保存
                        unique_records[key] = item
                    else:
                        # 比较添加时间，保留更早的
                        existing_time = str(unique_records[key]['values'][COL_OBJ_TIME]).strip()
                        if col_time < existing_time:  # 字符串比较（YYYY-MM-DD HH:MM:SS格式）
                            unique_records[key] = item
                else:
                    # 添加时间为空，不参与去重，直接保留
                    non_duplicate_records.append(item)

        # 合并去重后的数据和不参与去重的数据
        result = list(unique_records.values()) + non_duplicate_records

        return result

    def _update_ui_with_data(
        self,
        data_to_insert: list,
        status_msg: str,
        main_db_short_path: str = "",
        contact_db_short_path: str = "",
        ui_start: float = 0,
        degradation_messages: Optional[List[str]] = None,
        is_using_backup_db: bool = False,
        contact_db_source: str = "",
        txt_status: str = "loaded",
    ):
        """在主线程中更新UI(插入数据到表格)

        Args:
            degradation_messages: 本次加载发生的"降级 / 回退 / 失败"消息，
                                  会合并成一条 toast 显示给用户（5 秒）
            is_using_backup_db: 是否使用了超微备用库，用于状态栏标注
            contact_db_source: contact.db 数据源类型 "网络同步副本" / "本地旧副本" / "未连接"
            txt_status: 售前通讯录.txt 本次参与状态 "loaded" / "missing" / "failed"
        """

        # 清洗前的数据条数（即原始汇总条数）
        original_count = len(data_to_insert)

        # ========== 运行时清洗流水线（与 clean_all_in_one.py 对齐的唯一清洗入口） ==========
        # 注：原历史前置去重 self._deduplicate_data(...) 已被移除，避免与 step1~step7 取舍方向冲突
        #     （历史规则按 (obj_wxid, source_wxid) 保留 earliest，会"抢跑"删掉 step7 跨来源 8h 污染判定
        #      所必需的 latest 样本，导致运行时与 clean_all_in_one.py 不等价）。
        # 现在 7 步流水线直接吃原始汇总数据，与离线版输入一致。
        # 仅依据 12 列业务数据判定；原 item['tag']（含 deleted_/warning_/txt_file_/even_/odd_）
        # 随保留行一并保留，不影响表格行颜色与 UI 标记。
        clean_start = time.time()
        data_to_insert = run_clean_pipeline(data_to_insert)
        dedup_count = len(data_to_insert)
        removed_count = original_count - dedup_count
        print(f"[性能日志] 16. 运行时清洗流水线 (清洗前: {original_count}, 清洗后: {dedup_count}, 移除: {removed_count}): {(time.time() - clean_start)*1000:.2f}ms")

        # 保存完整数据集（用于搜索过滤、手动导出 _do_export_full、自动导出 self.auto_excel_backup.trigger()）
        self.all_data = data_to_insert.copy()

        # 插入所有数据到表格
        insert_start = time.time()
        for item in data_to_insert:
            self.tree.insert("", tk.END, values=item['values'], tags=(item['tag'],))

        print(f"[性能日志] 17. UI插入数据 ({dedup_count} 条): {(time.time() - insert_start)*1000:.2f}ms")
        if ui_start > 0:
            print(f"[性能日志] 18. UI更新总耗时: {(time.time() - ui_start)*1000:.2f}ms")

        # 更新状态消息，显示去重信息
        if removed_count > 0:
            # 使用正则表达式准确替换总条数
            import re
            status_msg = re.sub(
                r'共\s*\d+\s*条',
                f'去重前 {original_count} 条，去重后 {dedup_count} 条',
                status_msg
            )

        # 更新状态
        self.status_label.config(text=status_msg, fg="#2ecc71")

        # ========== 更新"本次刷新链路摘要"（一句话总结实际链路） ==========
        # 三段式：超微来源 + 内部库来源 + 售前通讯录参与情况
        # 超微段
        if main_db_short_path:
            wx_part = "超微备用库" if is_using_backup_db else "超微主库"
        else:
            wx_part = "超微未连接"
        # 内部库段（contact_db_source 取值: 网络同步副本 / 本地旧副本 / 未连接）
        contact_part_map = {
            "网络同步副本": "内部库网络同步副本",
            "本地旧副本": "内部库本地旧副本",
            "未连接": "内部库未连接",
        }
        contact_part = contact_part_map.get(contact_db_source, "内部库未连接")
        # 售前通讯录段
        txt_part_map = {
            "loaded": "售前通讯录已参与",
            "missing": "售前通讯录未参与",
            "failed": "售前通讯录读取失败",
        }
        txt_part = txt_part_map.get(txt_status, "售前通讯录未参与")

        summary_text = f"本次刷新链路：{wx_part} + {contact_part} + {txt_part}"

        # 颜色规则：发生任何降级则用警告橙；全部正常则用深蓝灰
        # 降级判定：备用库 / 本地旧副本 / 未连接 / txt 未参与 / txt 失败
        is_degraded = (
            is_using_backup_db
            or contact_db_source in ("本地旧副本", "未连接")
            or txt_status in ("missing", "failed")
            or not main_db_short_path
        )
        summary_color = "#e67e22" if is_degraded else "#34495e"
        if self.summary_label is not None:
            self.summary_label.config(text=summary_text, fg=summary_color)

        # 更新"最近刷新时间"：仅在 UI 真正成功更新时刷新
        # 失败路径走 _on_load_error，不会到这里，所以上一次成功值会自然保留
        if self.last_refresh_label is not None:
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.last_refresh_label.config(text=f"最近刷新时间：{now_str}")

        # 更新"内部库副本时间"：取当前 self.contact_db_path 的文件 mtime
        # - 文件存在：显示 YYYY-MM-DD HH:MM:SS
        # - 文件不存在 / 路径为空 / 读取异常：显示"暂无"
        # 注意：失败路径走 _on_load_error，不会到这里，因此本字段在刷新失败时
        #       会保留上一轮成功值，与"最近刷新时间"行为一致
        if self.contact_mtime_label is not None:
            mtime_text = "暂无"
            try:
                if self.contact_db_path and os.path.exists(self.contact_db_path):
                    mtime_ts = os.path.getmtime(self.contact_db_path)
                    mtime_text = datetime.fromtimestamp(mtime_ts).strftime('%Y-%m-%d %H:%M:%S')
            except Exception as _mtime_err:
                # 读取失败不影响主流程，仅保留"暂无"显示
                print(f"[状态栏] 读取 contact.db 副本时间失败（已忽略）: {_mtime_err}")
            self.contact_mtime_label.config(text=f"内部库副本时间：{mtime_text}")

        # 更新原有数据库路径显示（精简路径 + 主备标识）
        if main_db_short_path:
            db_tag = "[备用路径]" if is_using_backup_db else "[主路径]"
            self.main_db_label.config(text=f"超微数据库路径 {db_tag}: {main_db_short_path}")
        else:
            self.main_db_label.config(text="")

        # 更新 contact.db 路径显示（精简路径 + 数据源标识）
        if contact_db_short_path and contact_db_source != "未连接":
            source_tag = f"[{contact_db_source}]" if contact_db_source else ""
            self.contact_db_label.config(text=f"内部数据库路径 {source_tag}: {contact_db_short_path}")
        else:
            self.contact_db_label.config(text="内部数据库路径 [未连接]")

        # 隐藏Loading动画
        self._hide_loading()

        # 性能优化：触发垃圾回收，释放临时对象占用的内存
        gc.collect()

        # ========== 合并展示本次降级消息 + 上一轮异步备份失败 ==========
        # 合并到一条 toast，避免刷屏；同一条消息去重
        all_messages: List[str] = list(degradation_messages or [])
        # 取出并清空上一轮挂起的异步备份失败
        with self._pending_backup_errors_lock:
            if self._pending_backup_errors:
                all_messages.extend(self._pending_backup_errors)
                self._pending_backup_errors.clear()

        # 去重并保持顺序
        seen = set()
        unique_messages: List[str] = []
        for m in all_messages:
            if m not in seen:
                seen.add(m)
                unique_messages.append(m)

        # 首次加载时主库不存在的提示，并入降级提示一起显示（避免重复 toast）
        if self.need_show_db_warning:
            self.need_show_db_warning = False
            warn_msg = "首次启动检测到超微主库不存在"
            if warn_msg not in seen:
                unique_messages.insert(0, warn_msg)

        if unique_messages:
            # 合并文案，最多显示前 4 条，避免过长
            shown = unique_messages[:4]
            more = len(unique_messages) - len(shown)
            text = "本次刷新发生降级：" + "；".join(shown)
            if more > 0:
                text += f"（另有 {more} 项，详见日志）"
            # 延迟 500ms 显示，等 Loading 完全消失
            self.root.after(500, lambda t=text: self._show_warning_toast(t, duration_ms=5000))

        # ========== 更新"最近刷新时间"（仅成功路径，失败走 _on_load_error 不会更新） ==========
        # 完整日期时间避免跨天误解
        if self.last_refresh_label is not None:
            now_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.last_refresh_label.config(text=f"最近刷新时间：{now_text}")

        # ========== 启动定时自动刷新（每5分钟） ==========
        self._start_auto_refresh()

        # ========== 自动静默导出到 NAS ==========
        # 时机：UI 已用最新数据更新完毕后再触发，确保导出的就是当前界面最新数据
        # 失败不会影响主刷新流程，模块内部已处理异常隔离与失败提示去重
        self.auto_excel_backup.trigger()

    def _on_load_error(self, title: str, message: str):
        """在主线程中处理加载错误"""

        # 隐藏Loading
        self._hide_loading()

        # 更新状态
        self.status_label.config(text=message.split('\n')[0], fg="#e74c3c")

        # 显示错误对话框
        messagebox.showerror(title, message)

    # ------------------------------------------------------------------
    # 定时自动刷新 —— 实现已迁移至 modules.prospective.tasks.AutoRefreshScheduler
    # 这里保留 _start_auto_refresh / _stop_auto_refresh 作为 thin shim，
    # 旧调用方（如 _update_ui_in_main_thread / __del__）保持不变
    # 原 _auto_refresh_callback 已不再需要：调度器内部直接回调 self.load_data
    # ------------------------------------------------------------------
    def _start_auto_refresh(self):
        """启动（或重启）自动刷新定时器。转发到 auto_refresh_scheduler。"""
        self.auto_refresh_scheduler.start()

    def _stop_auto_refresh(self):
        """停止自动刷新定时器。转发到 auto_refresh_scheduler。"""
        if hasattr(self, "auto_refresh_scheduler") and self.auto_refresh_scheduler is not None:
            self.auto_refresh_scheduler.stop()

    def run(self):
        """运行GUI主循环"""
        self.root.mainloop()

    def __del__(self):
        """
        析构函数，确保关闭所有数据库连接和停止定时器

        重要性:
            - 防止数据库连接泄漏
            - 释放系统资源
            - 避免文件锁定问题
            - 停止定时刷新任务

        注意:
            - 使用 try-except 防止析构时出错
            - 静默处理异常，避免影响程序退出
        """
        try:
            # 停止定时自动刷新
            self._stop_auto_refresh()
        except Exception:
            # 静默处理
            pass

        try:
            # 关闭 contact.db 连接（contact.db 现在已是用完即关 + 置 None，
            # 这里仅作为兜底；正常情况下 self.contact_conn 已为 None）
            if hasattr(self, 'contact_conn') and self.contact_conn:
                self.contact_conn.close()
        except Exception:
            # 静默处理，避免析构时抛出异常
            pass

        # 注：超微数据库连接已在 _load_data_in_thread() 的 finally 中关闭，
        #     不再持有实例字段引用，此处无需再做兜底关闭


def main():
    """主函数"""
    root = tk.Tk()
    app = DatabaseViewer(root)
    app.run()


if __name__ == "__main__":
    main()
