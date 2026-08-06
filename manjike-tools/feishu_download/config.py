# -*- coding: utf-8 -*-
"""飞书多维表格批量下载配置。

说明：
- APP_ID / APP_SECRET 用于自动获取 tenant_access_token，无需手动填写 token。
- 可通过环境变量 FEISHU_APP_ID、FEISHU_APP_SECRET 覆盖下方默认值。
- TABLE_MAPPING 以 table_id 为唯一 key；表格中文名称写在 table_name 及行尾注释中。
- fields 为 CSV 导出字段列表，严格按配置顺序输出；设为 None 则导出该表全部字段。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, TypedDict


class TableConfig(TypedDict):
    """单张飞书数据表的定位信息（table_id 作为 TABLE_MAPPING 的 key，此处不再重复）。"""

    app_token: str
    table_name: str
    fields: Optional[List[str]]


# 飞书自建应用凭证（与 manjike-tools 其他飞书脚本共用同一应用）
APP_ID: str = os.environ.get("FEISHU_APP_ID", "cli_a96f36ed1538dbcf")
APP_SECRET: str = os.environ.get("FEISHU_APP_SECRET", "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB")

# 需要批量下载的 table_id 列表（按此顺序依次下载）
DOWNLOAD_TABLES: List[str] = [
    "tblNIWZ1EsDyZ1ug",  # 意向通讯录
    # "tblzUayWLgynug56",  # 推荐人更换
    "tblfoJucuZkeL9L1",  # 内部通讯录
    "tblKa8wryhV4d7F4",  # 合伙宝妈
    "tblCYxTgp87U0Y4x", # 固定渠道B带领B
    "tblWCSOwwmykQrbp", # 通用更换带领B
    "tblkdS7RKI9xV4dO", # 个性更换带领B
]

# table_id -> app_token / table_name / fields（行尾注释标明表格中文名）
TABLE_MAPPING: Dict[str, TableConfig] = {
    "tblNIWZ1EsDyZ1ug": {
        "app_token": "Zk05bwki2abD8XsBBOccaFsPn8e",
        "table_name": "意向通讯录",
        "fields": 
        None,
        # [
        #     "意向学员总微信号",
        #     "意向学员微信原始ID",
        #     "报名状态",
        #     "绑定日期",
        #     "绑定状态",
        #     "推荐人总微信号",
        # ],
    },  # 意向通讯录
    "tblzUayWLgynug56": {
        "app_token": "Zk05bwki2abD8XsBBOccaFsPn8e",
        "table_name": "推荐记录",
        "fields": [
            "学员微信号",
            "新推荐人微信号",
            "申请日期",
            "意向学员报名状态",
            "学员编号",
            "是否处理",
        ],
    },  # 推荐记录
    "tblfoJucuZkeL9L1": {
        "app_token": "Zk05bwki2abD8XsBBOccaFsPn8e",
        "table_name": "内部通讯录",
        "fields": None,  # None = 下载全部字段
    },  # 内部通讯录
    "tblKa8wryhV4d7F4": {
        "app_token": "Zk05bwki2abD8XsBBOccaFsPn8e",
        "table_name": "合伙宝妈",
        "fields": [
            "编号",
            "孩子中文全名",
            "学员1编号",
            "学员1孩子中文全名",
            "学员2编号",
            "学员2孩子中文全名",
            "学员3编号",
            "学员3孩子中文全名",
            "学员4编号",
            "学员4孩子中文全名",
            "学员5编号",
            "学员5孩子中文全名",
            "带领B编号",
            "带领B孩子中文全名",
            "意向通讯录密码",
            "合伙宝妈总微信号",
            "合伙宝妈微信原始ID"
        ],  # None = 下载全部字段
    },  # 合伙宝妈
    "tblCYxTgp87U0Y4x": {
        "app_token": "Zk05bwki2abD8XsBBOccaFsPn8e",
        "table_name": "固定渠道B带领B",
        "fields": 
        # None,  # None = 下载全部字段
        [
            "编号",
            "孩子中文全名",
            "渠道B编号",
            "渠道B孩子中文全名",
            "带领B编号",
            "带领B孩子中文全名",
            "备注",
        ]
    },  # 固定渠道B带领B
    "tblWCSOwwmykQrbp": {
        "app_token": "Zk05bwki2abD8XsBBOccaFsPn8e",
        "table_name": "通用更换带领B",
        "fields": None,  # None = 下载全部字段
    },  # 通用更换带领B
    "tblkdS7RKI9xV4dO": {
        "app_token": "Zk05bwki2abD8XsBBOccaFsPn8e",
        "table_name": "个性更换带领B",
        "fields": 
        # None,  # None = 下载全部字段
        [
            "编号",
            "孩子中文全名",
            "新带领B编号",
            "新带领B孩子中文全名",
        ]
    },  # 个性更换带领B
}

# CSV 输出目录（相对 feishu_download/ 目录）
OUTPUT_DIR: str = "output"

# 分页大小（飞书接口最大 500）
PAGE_SIZE: int = 500

# 多表并发下载线程数（并发单位为「表」，表内分页仍顺序读取）
MAX_WORKERS: int = 3
