# -*- coding: utf-8 -*-
"""
全局配置
==============================================
所有路径、字段名、API 常量集中维护。
APP_ID / APP_SECRET 必须从环境变量读取，禁止写死。
"""

import os


# ────────────────────────── 飞书应用凭证 ──────────────────────────
# 与本项目其它脚本（飞书保存合伙宝妈个人码2.py / 同步意向学员到飞书.py）保持一致：
# 优先从环境变量读取，环境变量为空时回退到本地默认值，保证开箱即用。
# 如需保密，把下面两个默认值清空，并通过环境变量传入即可。
_DEFAULT_APP_ID     = "cli_a96f36ed1538dbcf"
_DEFAULT_APP_SECRET = "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB"

APP_ID     = os.environ.get("FEISHU_APP_ID", "").strip()     or _DEFAULT_APP_ID
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "").strip() or _DEFAULT_APP_SECRET


# ────────────────────────── 飞书多维表格定位 ──────────────────────────
APP_TOKEN = "Zk05bwki2abD8XsBBOccaFsPn8e"
TABLE_ID  = "tblKa8wryhV4d7F4"
# 视图 ID（仅读取该视图的数据）；置空字符串则读整张表
VIEW_ID   = "vewTVvKOzr"


# ────────────────────────── 字段名 ──────────────────────────
# 模糊匹配源字段：本地文件名（去扩展名）中是否"包含"该字段值
FIELD_NUMBER = "编号"

# 待写入字段（飞书附件字段，类型 = Attachment）
FIELD_IMAGE  = "个人码3"


# ────────────────────────── 本地文件 ──────────────────────────
# 默认扫描目录：步骤 3（生成个人码3.py）的输出目录
# 这里上传的是 PSD 合成后的"个人码3"，对应飞书表里的"个人码3"附件字段
INPUT_DIR  = r"C:\Users\LENOVO\Desktop\二维码输出"
# 支持的图片扩展名（小写，含点）
IMAGE_EXTS = {".png", ".jpg", ".jpeg"}


# ────────────────────────── 日志 ──────────────────────────
# 日志文件，每次运行会先清空再追加
LOG_PATH = r"C:\Users\LENOVO\Desktop\logs.txt"


# ────────────────────────── 网络 / API ──────────────────────────
FEISHU_HOST     = "https://open.feishu.cn"
REQUEST_TIMEOUT = 30          # 单次请求超时（秒）
RETRY_TIMES     = 3           # 单次接口失败的最大重试次数（含首次）
PAGE_SIZE       = 100         # 飞书 records 接口分页大小（最大 100）


# ────────────────────────── 自检 ──────────────────────────
def verify() -> None:
    """启动前的轻量自检；缺少必填配置时直接抛错"""
    if not APP_ID or not APP_SECRET:
        raise RuntimeError(
            "APP_ID / APP_SECRET 为空。请在 config.py 的 _DEFAULT_APP_ID / "
            "_DEFAULT_APP_SECRET 填写，或通过环境变量 FEISHU_APP_ID / "
            "FEISHU_APP_SECRET 提供。"
        )
    if not APP_TOKEN or not TABLE_ID:
        raise RuntimeError("APP_TOKEN / TABLE_ID 必填，请在 config.py 中设置。")
