#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
诊断专用脚本：定位“某个散件名称查不到金额”的具体原因。

只读取飞书数据并打印，不做任何写回操作。

用法（在本脚本所在目录执行）：
    python .\debug_amount_lookup.py --item-name "电视盒子遥控器新【30元】" --record-id "rec27K7gbKAXpt"

参数说明：
- --item-name：要排查的散件名称文本，必须和飞书表格里看到的完全一致（可直接复制粘贴）
- --record-id：可选。如果传入目标表的 record_id，会额外打印这条记录对应的数量字段原始值
"""

import argparse
import sys
from typing import Any, Dict, List, Optional

from read_feishu_bitable_amount_only import (
    DEFAULT_APP_ID,
    DEFAULT_APP_SECRET,
    DEFAULT_APP_TOKEN,
    DEFAULT_MAPPING_TABLE_ID,
    DEFAULT_MAPPING_VIEW_ID,
    DEFAULT_TARGET_TABLE_ID,
    DEFAULT_TARGET_VIEW_ID,
    build_quantity_field_name,
    get_tenant_access_token,
    list_records,
    to_text,
)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="排查散件名称金额查找失败的具体原因")
    parser.add_argument("--app-id", default=DEFAULT_APP_ID)
    parser.add_argument("--app-secret", default=DEFAULT_APP_SECRET)
    parser.add_argument("--app-token", default=DEFAULT_APP_TOKEN)
    parser.add_argument("--mapping-table-id", default=DEFAULT_MAPPING_TABLE_ID)
    parser.add_argument("--mapping-view-id", default=DEFAULT_MAPPING_VIEW_ID)
    parser.add_argument("--target-table-id", default=DEFAULT_TARGET_TABLE_ID)
    parser.add_argument("--target-view-id", default=DEFAULT_TARGET_VIEW_ID)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--item-name", required=True, help="要排查的散件名称，需与飞书表格中文本完全一致")
    parser.add_argument("--record-id", default=None, help="可选，目标表 record_id，用于顺带检查数量字段")
    return parser.parse_args()


def get_mapping_name_text(fields: Dict[str, Any]) -> str:
    """按脚本一致的优先级取映射表名称文本。"""
    return (
        to_text(fields.get("散件名称(全部)"))
        or to_text(fields.get("散件名称"))
        or to_text(fields.get("物品名称"))
        or to_text(fields.get("商品名称"))
        or to_text(fields.get("名称"))
    )


def get_mapping_amount_text(fields: Dict[str, Any]) -> str:
    """按脚本一致的优先级取映射表金额文本。"""
    return (
        to_text(fields.get("金额(对外展示)"))
        or to_text(fields.get("金额"))
        or to_text(fields.get("价格"))
    )


def check_mapping_table(
    token: str,
    args: argparse.Namespace,
    target_name: str,
) -> None:
    """检查映射表里是否存在与目标名称完全一致、且能解析出金额的行。"""
    mapping_view_id = args.mapping_view_id.strip() if args.mapping_view_id else None
    mapping_records = list_records(
        tenant_access_token=token,
        app_token=args.app_token,
        table_id=args.mapping_table_id,
        page_size=args.page_size,
        view_id=mapping_view_id,
    )
    print(f"\n映射表共读取到 {len(mapping_records)} 条记录。")
    print(f"目标名称（repr，可看出隐藏字符/空格）：{repr(target_name)}")

    exact_match_rows: List[Dict[str, Any]] = []
    # 用去除了所有空白字符后的文本做“宽松对比”，帮助定位是不是空格/换行导致的差异。
    normalized_target = "".join(target_name.split())
    similar_rows: List[Dict[str, Any]] = []

    for record in mapping_records:
        fields = record.get("fields", {})
        name_text = get_mapping_name_text(fields)
        if not name_text:
            continue

        if name_text == target_name:
            exact_match_rows.append(record)
        elif "".join(name_text.split()) == normalized_target:
            similar_rows.append(record)
        elif target_name.rstrip("】0123456789【元") and target_name.split("【")[0] in name_text:
            # 前缀相同（去掉【价格】部分后）但整体不完全一致，也列出来供人工核对。
            similar_rows.append(record)

    if exact_match_rows:
        print(f"\n[结果] 映射表中找到 {len(exact_match_rows)} 条名称完全一致的行：")
        for record in exact_match_rows:
            fields = record.get("fields", {})
            amount_text = get_mapping_amount_text(fields)
            print(f"  record_id={record.get('record_id')}")
            print(f"    金额(对外展示)原始值：{repr(fields.get('金额(对外展示)'))}")
            print(f"    金额原始值：{repr(fields.get('金额'))}")
            print(f"    价格原始值：{repr(fields.get('价格'))}")
            print(f"    最终取到的金额文本：{repr(amount_text) if amount_text else '（空，会回退从名称里解析）'}")
    else:
        print("\n[结果] 映射表中没有找到名称完全一致的行。")

    if similar_rows:
        print("\n[提示] 发现名称相似但不完全相等的行，很可能就是问题所在（请逐字核对）：")
        for record in similar_rows:
            fields = record.get("fields", {})
            name_text = get_mapping_name_text(fields)
            print(f"  record_id={record.get('record_id')}")
            print(f"    名称repr：{repr(name_text)}")
    elif not exact_match_rows:
        print("\n[提示] 连相似的名称都没找到，说明映射表里可能根本没配置这个散件，请确认是否漏配。")


def check_target_record_quantity(
    token: str,
    args: argparse.Namespace,
    target_name: str,
    record_id: str,
) -> None:
    """检查目标表指定记录的数量字段原始值。"""
    target_view_id = args.target_view_id.strip() if args.target_view_id else None
    target_records = list_records(
        tenant_access_token=token,
        app_token=args.app_token,
        table_id=args.target_table_id,
        page_size=args.page_size,
        view_id=target_view_id,
    )

    quantity_field_name = build_quantity_field_name(target_name)
    print(f"\n推导出的数量字段名：{repr(quantity_field_name)}")

    found = False
    for record in target_records:
        if to_text(record.get("record_id")) == record_id:
            found = True
            fields = record.get("fields", {})
            print(f"record_id={record_id} 的字段原始情况：")
            print(f"  散件名称：{repr(fields.get('散件名称'))}")
            print(f"  {quantity_field_name} 原始值：{repr(fields.get(quantity_field_name))}")
            if quantity_field_name not in fields:
                print(f"  [提示] 目标表记录里根本不存在字段 [{quantity_field_name}]，请检查表头是否有这一列，或列名是否有细微差异。")
            break

    if not found:
        print(f"[提示] 在目标表（当前视图）里没有找到 record_id={record_id} 的记录，请确认视图筛选范围是否包含这条记录。")


def main() -> int:
    """主流程：鉴权 -> 检查映射表 -> （可选）检查目标表数量字段。"""
    args = parse_args()
    target_name = args.item_name.strip()

    token = get_tenant_access_token(args.app_id, args.app_secret)

    check_mapping_table(token, args, target_name)

    if args.record_id:
        check_target_record_quantity(token, args, target_name, args.record_id.strip())

    return 0


if __name__ == "__main__":
    sys.exit(main())
