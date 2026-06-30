# -*- coding: utf-8 -*-
"""
配置文件：把影刀里的硬编码集中放在这里。

使用前请确认：
1. FEISHU_APP_ID / FEISHU_APP_SECRET（可与同目录其他飞书脚本共用）
2. 各个微信 contact.db 路径

也可通过环境变量覆盖（PowerShell 示例）：
    $env:FEISHU_APP_SECRET="你的 app_secret"
"""

import os

# =========================
# 飞书应用配置（与微伴助手/查询收件人地址等脚本同一应用）
# =========================
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a96f36ed1538dbcf")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB")
APP_TOKEN = "Zk05bwki2abD8XsBBOccaFsPn8e"

# =========================
# 飞书表 / 视图配置
# =========================
TABLES = {
    # 意向通讯录：读取 意向学员编号 -> 推荐人编号
    "意向通讯录": {"table_id": "tblNIWZ1EsDyZ1ug", "view_id": "vewiVYKpeU"},

    # 内部通讯录：读取 编号 -> 推荐人编号；最终重写内部通讯录
    "内部通讯录": {"table_id": "tblfoJucuZkeL9L1", "view_id": "vewQDcuhBV"},

    # 合伙宝妈：编号 + 学员1编号~学员5编号
    "合伙宝妈": {"table_id": "tblKa8wryhV4d7F4", "view_id": "vew7GtEotv"},

    # 特殊渠道带领指定：编号 + 渠道B编号 + 带领B编号
    "特殊渠道带领指定": {"table_id": "tblCYxTgp87U0Y4x", "view_id": "vewfh8IdsS"},

    # 个性带领B指定：编号 + 新带领B编号
    "个性带领B指定": {"table_id": "tblkdS7RKI9xV4dO", "view_id": "vew3hqlwua"},

    # 通用带领B指定：原带领B编号 + 新带领B编号
    "通用带领B指定": {"table_id": "tblWCSOwwmykQrbp", "view_id": "vew3hqlwua"},

    # 汇总通讯录：写入 微信原始ID / 微信号 / 备注解析结果
    "汇总通讯录": {"table_id": "tblqm4mkL4OgrMzO", "view_id": "全部", "view_type": "NAME"},
}

# =========================
# 微信 contact.db 路径
# =========================
# 内部专用两个库，对应影刀 process7 / process10
INTERNAL_CONTACT_DB_PATHS = [
    r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
]

# 意向专用库，对应影刀 process8
PROSPECT_CONTACT_DB_PATH = r"C:\Users\LENOVO\Desktop\contact_意向专用.db"

# 运行前如需从映射盘复制微信数据库，可填写这里；不需要复制就留空列表
COPY_TASKS = [
    # {
    #     "源文件路径": r"Z:\Documents\chatlog\xxx\db_storage\contact\contact.db",
    #     "目标文件路径": r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    # },
]

# =========================
# 输出配置
# =========================
# False = 只读飞书、不写回；结果保存到 LOCAL_OUTPUT_DIR 供人工检查
WRITE_TO_FEISHU = False

# 本地检查结果目录（WRITE_TO_FEISHU=False 时使用）
LOCAL_OUTPUT_DIR = r"C:\Users\LENOVO\Desktop\channel_b_检查结果"

# 飞书业务表本地缓存目录（每次运行会并发下载并写入 JSON）
LOCAL_FEISHU_CACHE_DIR = r"C:\Users\LENOVO\Desktop\channel_b_飞书缓存"

# 飞书业务表并发下载线程数
FEISHU_DOWNLOAD_WORKERS = 6

# 缓存复用窗口（分钟）：
# 主程序触发下载脚本时，若某表在该时间内已有本地缓存，则直接复用，不再请求飞书。
# 设为 0 表示每次都强制重新下载。
FEISHU_CACHE_REUSE_MINUTES = 20

# 渠道计算 tab 分隔结果（推荐人 / 渠道B / 带领B / 渠道A / 带领A）
OUTPUT_TXT = r"C:\Users\LENOVO\Desktop\_输出结果_1.txt"
BACKUP_DIR = r"X:\backup\内部通讯录备份"
BATCH_SIZE = 1000

# 调试日志开关：保留影刀里的“原因”输出
DEBUG_CHANNEL_B = True
