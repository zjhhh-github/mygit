# -*- coding: utf-8 -*-
"""写回飞书 / 保存本地检查结果：替换影刀 process9 / process10 / process13。"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Any, Dict, List

from feishu_client import FeishuBitableClient
from utils import 备份_json
from wechat_db import 是大号备注, 是小号备注, 是内部业务备注, 解析内部备注


def _满足备注写入条件(备注: str) -> bool:
    """
    写入前统一备注过滤规则：
    - 必须包含 "-"
    - 不能包含 "-删除"
    - 不能包含 "-空"
    """
    if "-" not in 备注:
        return False
    if "-删除" in 备注 or "-空" in 备注:
        return False
    return True


def 构造汇总通讯录数据(微信原始ID映射微信号_备注: Dict[str, List[str]]) -> List[dict]:
    待写入数据 = []
    for 微信原始ID, 微信号_备注 in 微信原始ID映射微信号_备注.items():
        微信号 = 微信号_备注[0] if len(微信号_备注) > 0 else ""
        备注 = 微信号_备注[1] if len(微信号_备注) > 1 else ""
        if not _满足备注写入条件(备注):
            continue
        编号, 孩子中文全名 = 解析内部备注(备注)
        待写入数据.append({
            "编号": 编号,
            "孩子中文全名": 孩子中文全名,
            "微信号": 微信号,
            "微信原始ID": 微信原始ID,
        })
    return 待写入数据


def 构造内部通讯录数据(
    微信原始ID映射微信号_备注: Dict[str, List[str]],
    学员编号映射编号列表_推荐人_渠道B_带领B: Dict[str, List[str]],
) -> List[dict]:
    def _内部通讯录排序键(行: dict):
        """
        输出排序规则：
        1. 同编号放在一起（按编号数值升序）
        2. 同编号下大号(¿¿¿)在前，小号(!!!)在后
        3. 其余情况按备注文本兜底，保证顺序稳定
        """
        编号文本 = str(行.get("编号", "") or "").strip()
        if 编号文本.isdigit():
            编号排序值 = int(编号文本)
        else:
            编号排序值 = 10**12

        备注 = str(行.get("备注", "") or "")
        if 是大号备注(备注):
            账号类型排序值 = 0
        elif 是小号备注(备注):
            账号类型排序值 = 1
        else:
            账号类型排序值 = 2

        return (编号排序值, 编号文本, 账号类型排序值, 备注)

    待写入数据 = []
    for 微信原始ID, 微信号_备注 in 微信原始ID映射微信号_备注.items():
        微信号 = 微信号_备注[0] if len(微信号_备注) > 0 else ""
        备注 = 微信号_备注[1] if len(微信号_备注) > 1 else ""

        if not 是内部业务备注(备注):
            continue
        if not _满足备注写入条件(备注):
            continue

        编号, 孩子中文全名 = 解析内部备注(备注)

        # 小号不参与渠道 / 带领计算，写回时相关字段全部留空
        if 是小号备注(备注):
            推荐人编号 = ""
            渠道B编号 = ""
            带领B编号 = ""
            渠道A编号 = ""
            带领A编号 = ""
        else:
            value = 学员编号映射编号列表_推荐人_渠道B_带领B.get(编号, ["", "", "", "", ""])
            推荐人编号 = value[0] if len(value) > 0 else ""
            渠道B编号 = value[1] if len(value) > 1 else ""
            带领B编号 = value[2] if len(value) > 2 else ""
            渠道A编号 = value[3] if len(value) > 3 else ""
            带领A编号 = value[4] if len(value) > 4 else ""

        待写入数据.append({
            "备注": 备注,
            "编号": 编号,
            "孩子中文全名": 孩子中文全名,
            "微信号": 微信号,
            "微信原始ID": 微信原始ID,
            "推荐人编号": 推荐人编号,
            "渠道B编号": 渠道B编号,
            "带领B编号": 带领B编号,
            "渠道A编号": 渠道A编号,
            "带领A编号": 带领A编号,
        })

    # 导出前统一排序，确保同编号的大号/小号相邻，且大号在前。
    待写入数据.sort(key=_内部通讯录排序键)
    return 待写入数据


def 重写飞书表(client: FeishuBitableClient, table_id: str, records: List[dict], view_id: str = "", view_type: str = "ID"):
    deleted = client.delete_all_records(table_id, view_id, view_type)
    added = client.add_records(table_id, records)
    print(f"飞书表 {table_id} 重写完成：删除 {deleted} 条，新增 {added} 条")


def 写入新增合伙宝妈(client: FeishuBitableClient, table_id: str, 新增合伙宝妈编号映射前5编号: Dict[str, List[str]]):
    待写入数据 = 构造新增合伙宝妈数据(新增合伙宝妈编号映射前5编号)
    for payload in 待写入数据:
        client.add_record(table_id, payload)
    print(f"新增合伙宝妈写入完成：{len(待写入数据)} 条")


def 备份内部通讯录(backup_dir: str, data: List[dict]):
    path = 备份_json(backup_dir, "内部通讯录备份", data)
    if path:
        print("内部通讯录备份完成：", path)


def _写入_json(路径: str, 数据: Any):
    """将数据写入 JSON 文件，便于本地核对。"""
    with open(路径, "w", encoding="utf-8") as f:
        json.dump(数据, f, ensure_ascii=False, indent=2)


def _写入_csv(路径: str, 行列表: List[dict]):
    """将字典列表写入 CSV，方便用 Excel 打开检查。"""
    if not 行列表:
        with open(路径, "w", encoding="utf-8-sig", newline="") as f:
            f.write("")
        return
    字段名 = list(行列表[0].keys())
    with open(路径, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=字段名)
        writer.writeheader()
        writer.writerows(行列表)


def 构造新增合伙宝妈数据(新增合伙宝妈编号映射前5编号: Dict[str, List[str]]) -> List[dict]:
    """构造待写入合伙宝妈表的数据结构，供飞书写回或本地保存复用。"""
    待写入数据 = []
    for 合伙宝妈编号, 前五学员编号列表 in 新增合伙宝妈编号映射前5编号.items():
        if not isinstance(前五学员编号列表, list) or len(前五学员编号列表) < 5:
            continue
        待写入数据.append({
            "编号": 合伙宝妈编号,
            "学员1编号": 前五学员编号列表[0],
            "学员2编号": 前五学员编号列表[1],
            "学员3编号": 前五学员编号列表[2],
            "学员4编号": 前五学员编号列表[3],
            "学员5编号": 前五学员编号列表[4],
        })
    return 待写入数据


def 保存本地检查结果(
    输出目录: str,
    汇总数据: List[dict],
    内部数据: List[dict],
    新增合伙宝妈数据: List[dict],
    计算结果: dict,
    特殊渠道带领指定数据: List[dict] | None = None,
):
    """
    将全部待写飞书的数据和计算统计保存到本地目录，便于人工核对后再决定是否写回。
    """
    os.makedirs(输出目录, exist_ok=True)
    时间戳 = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 汇总 / 内部通讯录：JSON + CSV（CSV 方便 Excel 打开）
    汇总_json = os.path.join(输出目录, f"汇总通讯录_{时间戳}.json")
    汇总_csv = os.path.join(输出目录, f"汇总通讯录_{时间戳}.csv")
    内部_json = os.path.join(输出目录, f"内部通讯录_{时间戳}.json")
    内部_csv = os.path.join(输出目录, f"内部通讯录_{时间戳}.csv")
    _写入_json(汇总_json, 汇总数据)
    _写入_csv(汇总_csv, 汇总数据)
    _写入_json(内部_json, 内部数据)
    _写入_csv(内部_csv, 内部数据)

    # 新增合伙宝妈
    合伙宝妈_json = os.path.join(输出目录, f"新增合伙宝妈_{时间戳}.json")
    合伙宝妈_csv = os.path.join(输出目录, f"新增合伙宝妈_{时间戳}.csv")
    _写入_json(合伙宝妈_json, 新增合伙宝妈数据)
    _写入_csv(合伙宝妈_csv, 新增合伙宝妈数据)

    # 特殊渠道带领指定（从飞书读取后、参与计算前的有效数据）
    特殊渠道_json = os.path.join(输出目录, f"特殊渠道带领指定_{时间戳}.json")
    特殊渠道_csv = os.path.join(输出目录, f"特殊渠道带领指定_{时间戳}.csv")
    特殊渠道列表 = 特殊渠道带领指定数据 or []
    _写入_json(特殊渠道_json, 特殊渠道列表)
    _写入_csv(特殊渠道_csv, 特殊渠道列表)

    # 计算统计与明细
    统计 = {
        "生成时间": 时间戳,
        "总数量": 计算结果.get("总数量"),
        "成功数量": 计算结果.get("成功数量"),
        "空结果数量": 计算结果.get("空结果数量"),
        "新增学员编号": 计算结果.get("新增学员编号", []),
        "新增合伙宝妈": 计算结果.get("新增合伙宝妈编号映射前5编号", {}),
        "特殊渠道带领指定有效条数": len(特殊渠道列表),
    }
    统计_json = os.path.join(输出目录, f"计算统计_{时间戳}.json")
    _写入_json(统计_json, 统计)

    渠道明细_json = os.path.join(输出目录, f"渠道计算明细_{时间戳}.json")
    _写入_json(渠道明细_json, 计算结果.get("学员编号映射编号列表_推荐人_渠道B_带领B", {}))

    # 原因日志单独保存，避免控制台刷屏后无法回看
    原因日志 = 计算结果.get("原因日志", [])
    日志_txt = os.path.join(输出目录, f"原因日志_{时间戳}.txt")
    with open(日志_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(原因日志))

    # 写一份索引文件，方便找到本次输出
    索引 = {
        "生成时间": 时间戳,
        "汇总通讯录_json": 汇总_json,
        "汇总通讯录_csv": 汇总_csv,
        "内部通讯录_json": 内部_json,
        "内部通讯录_csv": 内部_csv,
        "新增合伙宝妈_json": 合伙宝妈_json,
        "新增合伙宝妈_csv": 合伙宝妈_csv,
        "特殊渠道带领指定_json": 特殊渠道_json,
        "特殊渠道带领指定_csv": 特殊渠道_csv,
        "计算统计_json": 统计_json,
        "渠道计算明细_json": 渠道明细_json,
        "原因日志_txt": 日志_txt,
    }
    索引_json = os.path.join(输出目录, "最新输出索引.json")
    _写入_json(索引_json, 索引)

    print("本地检查结果已保存到：", 输出目录)
    print("  内部通讯录 CSV（推荐先看）：", 内部_csv)
    print("  新增合伙宝妈 CSV：", 合伙宝妈_csv)
    print("  特殊渠道带领指定 CSV：", 特殊渠道_csv)
    print("  计算统计：", 统计_json)
    print("  原因日志：", 日志_txt)
    print("  索引文件：", 索引_json)
    return 索引
