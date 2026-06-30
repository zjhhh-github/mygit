# -*- coding: utf-8 -*-
"""读取飞书业务表，生成原影刀全局变量对应的数据结构。"""
from __future__ import annotations

from typing import Dict, List

from feishu_client import FeishuBitableClient, 字段文本
from utils import 是合法编号


飞书业务字段白名单 = {
    "意向通讯录": ["意向学员编号", "推荐人编号", "绑定状态"],
    "内部通讯录": ["编号", "推荐人编号", "渠道B编号", "带领B编号"],
    "合伙宝妈": ["编号", "学员1编号", "学员2编号", "学员3编号", "学员4编号", "学员5编号"],
    "特殊渠道带领指定": ["编号", "渠道B编号", "带领B编号"],
    "个性带领B指定": ["编号", "新带领B编号"],
    "通用带领B指定": ["原带领B编号", "新带领B编号"],
}


def _取记录列表(
    client: FeishuBitableClient | None,
    table_id: str = "",
    view_id: str = "",
    records: List[dict] | None = None,
    field_names: List[str] | None = None,
) -> List[dict]:
    """
    统一记录读取入口：仅允许使用本地缓存 records。

    约束说明：
    - 飞书下载能力集中在 download_feishu_tables.py；
    - 其他脚本（含主流程）禁止直接发起飞书下载请求，避免逻辑分散。
    """
    if records is not None:
        return records
    raise RuntimeError(
        "未提供本地缓存 records。"
        "请先运行 download_feishu_tables.py 下载飞书数据，再把 records 传入读取函数。"
    )


def 读取意向通讯录(
    client: FeishuBitableClient | None,
    table_id: str = "",
    view_id: str = "",
    records: List[dict] | None = None,
) -> Dict[str, str]:
    映射 = {}
    for item in _取记录列表(client, table_id, view_id, records, 飞书业务字段白名单["意向通讯录"]):
        fields = item.get("fields", {})
        意向学员编号 = 字段文本(fields, "意向学员编号")
        推荐人编号 = 字段文本(fields, "推荐人编号")
        绑定状态 = 字段文本(fields, "绑定状态")
        if 意向学员编号 and 推荐人编号 and 绑定状态 == "有绑定":
            映射[意向学员编号] = 推荐人编号
    return 映射


def 读取内部通讯录(
    client: FeishuBitableClient | None,
    table_id: str = "",
    view_id: str = "",
    records: List[dict] | None = None,
):
    """
    读取内部通讯录。

    返回：
    - 学员编号 -> 推荐人编号
    - 推荐人编号 -> 其推荐的学员列表
    - 学员编号 -> [渠道B编号, 带领B编号]（飞书已填写的固定值，供上溯时推荐人为空则使用）
    """
    学员编号映射推荐人编号: Dict[str, str] = {}
    推荐人编号映射推荐的学员列表: Dict[str, List[str]] = {}
    学员编号映射固定渠道B带领B: Dict[str, List[str]] = {}

    for item in _取记录列表(client, table_id, view_id, records, 飞书业务字段白名单["内部通讯录"]):
        fields = item.get("fields", {})
        学员编号 = 字段文本(fields, "编号")
        推荐人编号 = 字段文本(fields, "推荐人编号")
        渠道B编号 = 字段文本(fields, "渠道B编号")
        带领B编号 = 字段文本(fields, "带领B编号")

        if not 学员编号:
            continue

        学员编号映射推荐人编号[学员编号] = 推荐人编号
        学员编号映射固定渠道B带领B[学员编号] = [渠道B编号, 带领B编号]

        if 推荐人编号:
            lst = 推荐人编号映射推荐的学员列表.get(推荐人编号, [])
            lst.append(学员编号)
            推荐人编号映射推荐的学员列表[推荐人编号] = lst

    return 学员编号映射推荐人编号, 推荐人编号映射推荐的学员列表, 学员编号映射固定渠道B带领B


def 读取合伙宝妈(
    client: FeishuBitableClient | None,
    table_id: str = "",
    view_id: str = "",
    records: List[dict] | None = None,
) -> Dict[str, List[str]]:
    映射 = {}
    for item in _取记录列表(client, table_id, view_id, records, 飞书业务字段白名单["合伙宝妈"]):
        fields = item.get("fields", {})
        编号 = 字段文本(fields, "编号")
        前五 = []
        for i in range(1, 6):
            学员编号 = 字段文本(fields, f"学员{i}编号")
            if 学员编号:
                前五.append(学员编号)
        if 编号:
            映射[编号] = 前五
    return 映射


def 读取特殊渠道带领指定(
    client: FeishuBitableClient | None,
    table_id: str = "",
    view_id: str = "",
    records: List[dict] | None = None,
):
    """
    读取飞书「特殊渠道带领指定」表，原样使用表中渠道B / 带领B 的值。

    规则：
    - 编号合法即入库（与飞书行一一对应）
    - 渠道B编号、带领B编号为空则保存为空字符串，后续计算也保持为空，不做补算
    """
    映射 = {}
    原始条数 = 0
    跳过_编号无效 = 0

    for item in _取记录列表(client, table_id, view_id, records, 飞书业务字段白名单["特殊渠道带领指定"]):
        原始条数 += 1
        fields = item.get("fields", {})
        学员编号 = 字段文本(fields, "编号")
        渠道B编号 = 字段文本(fields, "渠道B编号")
        带领B编号 = 字段文本(fields, "带领B编号")

        if not 是合法编号(学员编号):
            跳过_编号无效 += 1
            continue

        学员编号 = str(学员编号).strip()
        映射[学员编号] = [渠道B编号, 带领B编号]

    print(
        "特殊渠道带领指定读取：原始 {} 条，入库 {} 条，跳过编号无效 {} 条".format(
            原始条数, len(映射), 跳过_编号无效
        )
    )
    return 映射


def 特殊渠道映射转列表(映射: Dict[str, List[str]]) -> List[dict]:
    """将特殊渠道映射转为列表，便于导出 CSV / JSON 供人工核对。"""
    行列表 = []
    for 编号 in sorted(映射.keys()):
        渠道B编号, 带领B编号 = 映射[编号]
        行列表.append({
            "编号": 编号,
            "渠道B编号": 渠道B编号,
            "带领B编号": 带领B编号,
        })
    return 行列表


def 读取个性带领B指定(
    client: FeishuBitableClient | None,
    table_id: str = "",
    view_id: str = "",
    records: List[dict] | None = None,
):
    映射 = {}
    for item in _取记录列表(client, table_id, view_id, records, 飞书业务字段白名单["个性带领B指定"]):
        fields = item.get("fields", {})
        报名学员编号 = 字段文本(fields, "编号")
        新带领B编号 = 字段文本(fields, "新带领B编号")
        if 报名学员编号:
            映射[报名学员编号] = 新带领B编号
    return 映射


def 读取通用带领B指定(
    client: FeishuBitableClient | None,
    table_id: str = "",
    view_id: str = "",
    records: List[dict] | None = None,
):
    映射 = {}
    for item in _取记录列表(client, table_id, view_id, records, 飞书业务字段白名单["通用带领B指定"]):
        fields = item.get("fields", {})
        原带领B编号 = 字段文本(fields, "原带领B编号")
        新带领B编号 = 字段文本(fields, "新带领B编号")
        if 原带领B编号:
            映射[原带领B编号] = 新带领B编号
    return 映射
