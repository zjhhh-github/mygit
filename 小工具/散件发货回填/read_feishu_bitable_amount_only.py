#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
只做“散件总金额”回填的独立脚本：
1) 读取目标表和映射表
2) 计算每条记录的散件总金额
3) 写回目标表的“散件总金额”字段

说明：
- 本脚本不处理“散件编号(最终)”。
- 本脚本不做拆分新增行。
"""

import argparse
import json
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


FEISHU_BASE_URL = "https://open.feishu.cn"
DEFAULT_APP_ID = "cli_a96f36ed1538dbcf"
DEFAULT_APP_SECRET = "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB"
DEFAULT_APP_TOKEN = "Zk05bwki2abD8XsBBOccaFsPn8e"
DEFAULT_TARGET_TABLE_ID = "tblxePMrI4Aot32D"
DEFAULT_MAPPING_TABLE_ID = "tblxAECIL3MnGkKr"
DEFAULT_TARGET_VIEW_ID = "vewhJjlRz7"
DEFAULT_MAPPING_VIEW_ID = "vew2jIlNpI"

# 统一请求参数：默认禁用代理并带重试，降低环境差异和瞬时网络失败影响。
REQUEST_TIMEOUT = 30
RETRY_TIMES = 5
RETRY_DELAY_SECONDS = 1
DISABLE_PROXY = True


def build_headers(tenant_access_token: str) -> Dict[str, str]:
    """构造飞书 API 请求头。"""
    return {
        "Authorization": f"Bearer {tenant_access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def build_http_session() -> requests.Session:
    """创建 HTTP 会话，并按配置禁用系统代理。"""
    session = requests.Session()
    if DISABLE_PROXY:
        session.trust_env = False
        session.proxies.update({"http": None, "https": None})
    return session


HTTP_SESSION = build_http_session()


def request_with_retry(method: str, url: str, **kwargs: Any) -> requests.Response:
    """带重试的 HTTP 请求，处理网络抖动/5xx/429。"""
    if DISABLE_PROXY and "proxies" not in kwargs:
        kwargs["proxies"] = {"http": None, "https": None}

    last_error: Optional[Exception] = None
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            response = HTTP_SESSION.request(
                method=method,
                url=url,
                timeout=REQUEST_TIMEOUT,
                **kwargs,
            )
            if response.status_code >= 500 or response.status_code == 429:
                last_error = RuntimeError(
                    f"HTTP {response.status_code}: {response.text[:200]}"
                )
                if attempt < RETRY_TIMES:
                    time.sleep(RETRY_DELAY_SECONDS * attempt)
                    continue
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < RETRY_TIMES:
                time.sleep(RETRY_DELAY_SECONDS * attempt)
                continue

    raise RuntimeError(f"请求失败：{method} {url}，原因：{last_error}")


def feishu_api_call(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """统一调用飞书 API，要求 code=0。"""
    response = request_with_retry(
        method=method,
        url=url,
        headers=headers or {},
        params=params,
        json=json_body,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"读取多维表格失败：{payload}")
    return payload.get("data", {}) or {}


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    """获取飞书 tenant_access_token。"""
    url = f"{FEISHU_BASE_URL}/open-apis/auth/v3/tenant_access_token/internal/"
    payload = {"app_id": app_id, "app_secret": app_secret}
    response = request_with_retry("POST", url, json=payload)
    response.raise_for_status()
    result = response.json()

    if result.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败：{result}")

    token = result.get("tenant_access_token")
    if not token:
        raise RuntimeError("接口返回成功但未拿到 tenant_access_token。")
    return token


def list_records(
    tenant_access_token: str,
    app_token: str,
    table_id: str,
    page_size: int,
    view_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """分页读取飞书多维表格记录。"""
    records: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    search_url = f"{FEISHU_BASE_URL}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
    headers = build_headers(tenant_access_token)

    while True:
        params: Dict[str, Any] = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token

        body: Dict[str, Any] = {"automatic_fields": True}
        if view_id:
            body["view_id"] = view_id

        data = feishu_api_call(
            method="POST",
            url=search_url,
            headers=headers,
            params=params,
            json_body=body,
        )
        records.extend(data.get("items", []))

        if not data.get("has_more", False):
            break
        page_token = data.get("page_token")

    return records


def update_record_fields(
    tenant_access_token: str,
    app_token: str,
    table_id: str,
    record_id: str,
    fields_to_update: Dict[str, Any],
) -> None:
    """更新指定记录字段。"""
    url = f"{FEISHU_BASE_URL}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    payload = {"fields": fields_to_update}
    response = request_with_retry(
        method="PUT",
        url=url,
        headers=build_headers(tenant_access_token),
        json=payload,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("code") != 0:
        raise RuntimeError(f"更新记录失败，record_id={record_id}，错误：{result}")


def to_text(value: Any) -> str:
    """把飞书字段值尽量转为纯文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, dict):
        text = value.get("text")
        if text is not None:
            return str(text).strip()
        return str(value).strip()
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            item_text = to_text(item)
            if item_text:
                parts.append(item_text)
        return ",".join(parts).strip(", ")
    return str(value).strip()


def parse_quantity(value: Any) -> float:
    """把数量字段解析为数字，空值/非法值按 0 处理。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = to_text(value)
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_unit_price(text: str) -> float:
    """
    从文本提取金额：
    - 优先匹配【50元】
    - 回退匹配第一个数字（如 50元 / 50）
    """
    match = re.search(r"【\s*([0-9]+(?:\.[0-9]+)?)\s*元\s*】", text)
    if match:
        return float(match.group(1))

    fallback = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if fallback:
        return float(fallback.group(1))
    return 0.0


def build_quantity_field_name(item_name: str) -> str:
    """根据散件名称推导数量字段名：把最后一个【...】替换为【数量】。"""
    return re.sub(r"【[^】]*】\s*$", "【数量】", item_name)


def split_item_names(raw_name: str) -> List[str]:
    """拆分“散件名称”中的多个子项。"""
    parts = re.split(r"[,\n，、；;]+", raw_name)
    return [part.strip() for part in parts if part and part.strip()]


def build_item_name_to_prices(mapping_records: List[Dict[str, Any]]) -> Dict[str, List[float]]:
    """
    构建“散件名称 -> 单价列表”映射。
    说明：允许同名命中多条，金额会累计。
    """
    name_to_prices: Dict[str, List[float]] = {}

    for record in mapping_records:
        fields = record.get("fields", {})
        item_name_text = (
            to_text(fields.get("散件名称"))
            or to_text(fields.get("物品名称"))
            or to_text(fields.get("商品名称"))
            or to_text(fields.get("名称"))
        )
        if not item_name_text:
            continue

        amount_text = (
            to_text(fields.get("金额(对外展示)"))
            or to_text(fields.get("金额"))
            or to_text(fields.get("价格"))
        )
        price = parse_unit_price(amount_text) if amount_text else 0.0
        if price <= 0:
            price = parse_unit_price(item_name_text)
        if price <= 0:
            continue

        if item_name_text not in name_to_prices:
            name_to_prices[item_name_text] = []
        name_to_prices[item_name_text].append(price)

    return name_to_prices


def format_number(value: float) -> str:
    """格式化金额文本，整数去掉 .0。"""
    if value.is_integer():
        return str(int(value))
    return str(value)


def calculate_total_amount(
    fields: Dict[str, Any],
    name_to_prices: Dict[str, List[float]],
) -> Tuple[float, List[str]]:
    """计算单条记录的散件总金额，并返回警告信息。"""
    warnings: List[str] = []
    raw_names = to_text(fields.get("散件名称"))
    if not raw_names:
        return 0.0, warnings

    total_amount = 0.0
    item_names = split_item_names(raw_names)
    for item_name in item_names:
        quantity_field_name = build_quantity_field_name(item_name)
        quantity = parse_quantity(fields.get(quantity_field_name))
        if quantity <= 0:
            warnings.append(f"物品[{item_name}]数量字段[{quantity_field_name}]为空或<=0，已跳过")
            continue

        prices = [price for price in name_to_prices.get(item_name, []) if price > 0]
        if not prices:
            warnings.append(f"映射表散件名称未找到金额映射：[{item_name}]")
            continue

        if len(prices) > 1:
            warnings.append(f"映射表散件名称[{item_name}]命中 {len(prices)} 条金额，已累计")

        total_amount += sum(prices) * quantity

    return total_amount, warnings


def print_debug_payload(payload: Dict[str, Any]) -> None:
    """统一打印调试信息，方便核对。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="只回填飞书多维表格散件总金额")
    parser.add_argument(
        "--app-id",
        default=DEFAULT_APP_ID,
        help="飞书应用 App ID（默认使用脚本内置值）",
    )
    parser.add_argument(
        "--app-secret",
        default=DEFAULT_APP_SECRET,
        help="飞书应用 App Secret（默认使用脚本内置值）",
    )
    parser.add_argument("--app-token", default=DEFAULT_APP_TOKEN, help="飞书多维表格 App Token")
    parser.add_argument(
        "--target-table-id",
        default=DEFAULT_TARGET_TABLE_ID,
        help="回填目标表 Table ID",
    )
    parser.add_argument(
        "--mapping-table-id",
        default=DEFAULT_MAPPING_TABLE_ID,
        help="散件信息映射表 Table ID（含散件名称和金额信息）",
    )
    parser.add_argument(
        "--target-view-id",
        default=DEFAULT_TARGET_VIEW_ID,
        help="回填目标表 View ID（为空表示不按视图筛选）",
    )
    parser.add_argument(
        "--mapping-view-id",
        default=DEFAULT_MAPPING_VIEW_ID,
        help="映射表 View ID（为空表示不按视图筛选）",
    )
    parser.add_argument("--page-size", type=int, default=100, help="分页大小，范围 1~500")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印计算结果，不实际写回飞书",
    )
    parser.add_argument(
        "--print-debug",
        action="store_true",
        help="打印读取与待写入数据（用于核对）",
    )
    return parser.parse_args()


def main() -> int:
    """主流程：鉴权 -> 读取两张表 -> 计算金额 -> 写回金额。"""
    args = parse_args()

    if args.page_size < 1 or args.page_size > 500:
        print("--page-size 必须在 1~500 之间。", file=sys.stderr)
        return 1

    try:
        token = get_tenant_access_token(args.app_id, args.app_secret)
        target_view_id = args.target_view_id.strip() if args.target_view_id else None
        mapping_view_id = args.mapping_view_id.strip() if args.mapping_view_id else None

        target_records = list_records(
            tenant_access_token=token,
            app_token=args.app_token,
            table_id=args.target_table_id,
            page_size=args.page_size,
            view_id=target_view_id,
        )
        mapping_records = list_records(
            tenant_access_token=token,
            app_token=args.app_token,
            table_id=args.mapping_table_id,
            page_size=args.page_size,
            view_id=mapping_view_id,
        )
        name_to_prices = build_item_name_to_prices(mapping_records)

        success_count = 0
        skip_count = 0
        warning_count = 0

        for record in target_records:
            record_id = to_text(record.get("record_id"))
            fields = record.get("fields", {})
            if not record_id:
                skip_count += 1
                continue

            total_amount, warnings = calculate_total_amount(fields, name_to_prices)
            warning_count += len(warnings)
            for warning in warnings:
                print(f"[警告][{record_id}] {warning}")

            # 飞书里该字段通常是文本列，统一写字符串避免类型转换错误。
            amount_to_write = format_number(total_amount)
            update_fields = {"散件总金额": amount_to_write}

            if args.print_debug:
                debug_payload = {
                    "record_id": record_id,
                    "读取数据": {"散件名称": fields.get("散件名称")},
                    "待写入数据": update_fields,
                }
                print_debug_payload(debug_payload)

            if args.dry_run:
                print(f"[预览][{record_id}] {update_fields}")
            else:
                update_record_fields(
                    tenant_access_token=token,
                    app_token=args.app_token,
                    table_id=args.target_table_id,
                    record_id=record_id,
                    fields_to_update=update_fields,
                )

            success_count += 1

        action = "预览完成" if args.dry_run else "写回完成"
        print(
            f"{action}：共处理 {len(target_records)} 条，成功 {success_count} 条，"
            f"跳过 {skip_count} 条，警告 {warning_count} 条。"
        )
        return 0
    except requests.RequestException as exc:
        print(f"网络请求失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - 统一兜底，便于脚本用户排查
        print(f"执行失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

