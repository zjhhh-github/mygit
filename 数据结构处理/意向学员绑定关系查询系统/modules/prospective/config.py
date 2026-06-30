# -*- coding: utf-8 -*-
"""
意向查询系统配置与常量
======================

本模块只承载静态信息：
    1. 路径常量（数据库主路径、备份路径、NAS 备份目录、自动导出目录等）
    2. 数据列索引常量（COL_OBJ_*, COL_SOURCE_*）
    3. 资源路径解析函数 get_resource_path（兼容 PyInstaller _MEIPASS）
    4. ProspectiveConfig 配置类：把"运行时可调参数"集中到一个对象，
       供后续 service / panel 注入使用，替代历史上"覆写模块全局变量"的做法。

注意：
    - 历史代码（db_viewer.py 中的 DatabaseViewer）仍直接读取自身模块顶部的
      同名常量，以保持「mod.CONTACT_NETWORK_BASE = xxx」这种动态覆写仍然生效。
    - 新代码请优先使用 ProspectiveConfig 实例传参的方式，不要再读模块全局。
"""

import os
import sys
from pathlib import Path


# ==================== 资源路径获取函数 ====================

def get_resource_path(relative_path: str) -> str:
    """
    获取资源文件的绝对路径（支持打包后的 exe）

    在开发环境中，返回相对于本文件所在目录的上层路径
    （即"项目根目录 / relative_path"）；
    在打包后的 exe 中，返回临时解压目录中的资源路径（_MEIPASS）。

    Args:
        relative_path: 相对路径（如 "售前通讯录.txt"）

    Returns:
        资源文件的绝对路径
    """
    try:
        # PyInstaller 打包后的临时文件夹路径
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    except AttributeError:
        # 开发环境：以"项目根目录"作为基准
        # 本文件位于 项目根/modules/prospective/config.py，因此向上 2 级
        base_path = os.path.abspath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
        )

    return os.path.join(base_path, relative_path)


# ==================== 路径常量 ====================

# 动态获取当前 Windows 用户主目录（避免写死用户名如 LENOVO）
# Path.home() 在 Windows 下等价于 C:\Users\<当前用户名>
_HOME = Path.home()

# 超微数据库路径配置（主路径 -> 备份路径）
# 注意：
#   1) db 文件名 db_wxid_3iidhz1xmnta22.db3 仍为硬编码，与具体机器人账号绑定，暂保留
#   2) 主库改为 Y: 盘下的固定路径（不再随当前用户主目录变化），
#      用于将主库读取转移到与 WxRobot 写入端相同的网络盘位置
PRIMARY_DB = r"Y:\AppData\Local\WxRobot\db_wxid_3iidhz1xmnta22.db3"
BACKUP_DB = str(_HOME / "Documents" / "wxid_3iidhz1xmnta22备份" / "db.db3")

# 内部数据库路径配置（网络路径 -> 本地备份）
CONTACT_NETWORK_BASE = r"X:\chatlog_backup"
CONTACT_LOCAL_BACKUP = str(_HOME / "Desktop" / "contact.db")

# 超微数据库网络备份路径
NETWORK_BACKUP_BASE = r"X:\backup\SuperXDatabase_Prospective"

# 自动导出目标目录（NAS 路径）
# 用途：每次数据刷新成功后，将"意向专用通讯录"静默导出到该目录
# 实际生效的目录由 modules.auto_excel_backup.AutoExcelBackup 持有，这里仅作默认值参考
AUTO_EXPORT_DIR = r"X:\backup\ProspectiveContacts"

# 售前通讯录文本文件名（位于项目根，运行时通过 get_resource_path 解析为绝对路径）
SALES_CONTACT_TXT_NAME = "售前通讯录.txt"


# ==================== 数据列索引常量 ====================
# 12 列业务数据；使用常量避免硬编码索引，提升可维护性

COL_OBJ_NICK = 0              # 意向学员(昵称)
COL_OBJ_WXID = 1              # 意向学员(微信ID)
COL_OBJ_NUMBER = 2            # 意向学员(微信号)
COL_OBJ_TOTAL_NUMBER = 3      # 意向学员(总微信号)
COL_OBJ_TIME = 4              # 意向学员(添加时间)
COL_OBJ_INTERNAL_NOTE = 5     # 意向学员(内部备注)
COL_OBJ_IS_DELETE = 6         # 意向学员(是否删除)
COL_SOURCE_NICK = 7           # 来源(昵称)
COL_SOURCE_WXID = 8           # 来源(微信ID)
COL_SOURCE_NUMBER = 9         # 来源(微信号)
COL_SOURCE_TOTAL_NUMBER = 10  # 来源(总微信号)
COL_SOURCE_INTERNAL_NOTE = 11 # 来源(内部备注)


# ==================== 运行时配置类 ====================

class ProspectiveConfig(object):
    """
    意向查询系统的运行时配置容器（普通类实现，兼容 Python 3.6）

    使用普通类 + __init__ 而不是 @dataclass，是因为本项目实际部署环境为
    Python 3.6.5，避免引入 dataclasses 后端依赖；行为与数据类等价。

    字段语义：
        primary_db / backup_db          : 超微 db3 主/备路径
        contact_network_base            : contact 网络根目录（X:\\chatlog_backup）
        contact_local_backup            : contact.db 本地副本路径
        network_backup_base             : 超微 db 的 NAS 备份目录
        auto_export_dir                 : 意向专用通讯录的自动导出目录
        sales_contact_txt               : 售前通讯录 .txt 的绝对路径
        skip_auto_contact_copy          : True 表示由外部"定时拷贝"统一调度，
                                          意向查询面板内部不再自动同步 contact.db
        auto_refresh_interval_ms        : 自动刷新间隔（毫秒），默认 5 分钟
    """

    def __init__(
        self,
        primary_db,
        backup_db,
        contact_network_base,
        contact_local_backup,
        network_backup_base,
        auto_export_dir,
        sales_contact_txt,
        skip_auto_contact_copy=False,
        auto_refresh_interval_ms=300000,
    ):
        self.primary_db = primary_db
        self.backup_db = backup_db
        self.contact_network_base = contact_network_base
        self.contact_local_backup = contact_local_backup
        self.network_backup_base = network_backup_base
        self.auto_export_dir = auto_export_dir
        self.sales_contact_txt = sales_contact_txt
        self.skip_auto_contact_copy = skip_auto_contact_copy
        self.auto_refresh_interval_ms = auto_refresh_interval_ms

    @classmethod
    def default(cls):
        """根据本模块的默认常量构造一份配置（与历史 db_viewer.py 行为一致）。"""
        return cls(
            primary_db=PRIMARY_DB,
            backup_db=BACKUP_DB,
            contact_network_base=CONTACT_NETWORK_BASE,
            contact_local_backup=CONTACT_LOCAL_BACKUP,
            network_backup_base=NETWORK_BACKUP_BASE,
            auto_export_dir=AUTO_EXPORT_DIR,
            sales_contact_txt=get_resource_path(SALES_CONTACT_TXT_NAME),
            skip_auto_contact_copy=False,
            auto_refresh_interval_ms=300000,
        )

    def copy(self):
        """返回一份浅拷贝，便于在不污染原对象的前提下修改若干字段。"""
        return ProspectiveConfig(
            primary_db=self.primary_db,
            backup_db=self.backup_db,
            contact_network_base=self.contact_network_base,
            contact_local_backup=self.contact_local_backup,
            network_backup_base=self.network_backup_base,
            auto_export_dir=self.auto_export_dir,
            sales_contact_txt=self.sales_contact_txt,
            skip_auto_contact_copy=self.skip_auto_contact_copy,
            auto_refresh_interval_ms=self.auto_refresh_interval_ms,
        )

    def __repr__(self):
        return (
            "ProspectiveConfig("
            "primary_db={!r}, backup_db={!r}, contact_network_base={!r}, "
            "contact_local_backup={!r}, network_backup_base={!r}, "
            "auto_export_dir={!r}, sales_contact_txt={!r}, "
            "skip_auto_contact_copy={!r}, auto_refresh_interval_ms={!r})"
        ).format(
            self.primary_db, self.backup_db, self.contact_network_base,
            self.contact_local_backup, self.network_backup_base,
            self.auto_export_dir, self.sales_contact_txt,
            self.skip_auto_contact_copy, self.auto_refresh_interval_ms,
        )
