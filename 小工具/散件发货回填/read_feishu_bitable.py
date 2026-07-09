#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
按规则回填飞书多维表格中的“散件编号(最终)”与“散件总金额”。

默认读取/写回的表：
- 业务表（回填目标，即“散件发货客户自填”表）: tblxePMrI4Aot32D
- 映射表（散件信息表，散件名称 -> 编号/金额/发货仓）: tblxAECIL3MnGkKr

映射表的“发货仓”是公式字段（根据同表内编号内容自动判定属于
深圳云仓/王师傅下单/非快递订单/呼市云仓），作为唯一数据来源，脚本只读取不修改。
目标表的“发货仓”已改为普通文本字段，脚本会在“拆分新增”时，把每条拆分行对应编号
在映射表里的真实发货仓显式写进去；原始记录（未拆分的那一行）不写此字段。

示例：
python read_feishu_bitable.py ^
  --app-id "cli_xxx" ^
  --app-secret "xxx"
"""

import argparse
import json
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import requests


FEISHU_BASE_URL = "https://open.feishu.cn"
DEFAULT_APP_ID = "cli_a96f36ed1538dbcf"
DEFAULT_APP_SECRET = "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB"
DEFAULT_APP_TOKEN = "Zk05bwki2abD8XsBBOccaFsPn8e"
DEFAULT_TARGET_TABLE_ID = "tblxePMrI4Aot32D"
DEFAULT_MAPPING_TABLE_ID = "tblxAECIL3MnGkKr"
DEFAULT_TARGET_VIEW_ID = "vewiU45Piv"
DEFAULT_MAPPING_VIEW_ID = "vew2jIlNpI"

# 参考“补充飞书推送3”的请求策略：禁用代理 + 自动重试，减少环境干扰和瞬时失败。
REQUEST_TIMEOUT = 30
RETRY_TIMES = 5
RETRY_DELAY_SECONDS = 1
DISABLE_PROXY = True

# 这些散件无论是否重复，都单独拆成独立新增行（但不按编号逐条再拆）。
FORCE_SEPARATE_ITEM_NAMES = {
    "西游记绘本-套装【100元】",
    "动物街-L系列-台词书-套装【100元】",
    "小猪佩奇-L系列-台词书-套装【150元】",
    "小四件(蓝)【200元】",
    "小四件(粉)【200元】"
}

# 编号替换表：散件信息表“编号(对外展示)”里的部分编号，只是套装内某一本书的展示标签，
# 实际填到飞书“散件编号(最终)”里要用真实的 SKU 编号（可能是多个，写入时用逗号拼接）。
# key：散件信息表里的展示编号（原样，不带 *数量）
# value：要写入飞书的真实编号列表（顺序即写入顺序）
CODE_DISPLAY_TO_REAL_CODES: Dict[str, List[str]] = {
    "读写工具包-25本装-[1/6]": ["MJKD3P0136", "MJKD3P3772", "MJKD3P73108"],
}

def build_headers(tenant_access_token: str) -> Dict[str, str]:
    """构造飞书 API 请求头。"""
    return {
        "Authorization": f"Bearer {tenant_access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def build_http_session() -> requests.Session:
    """
    创建 HTTP 会话。
    参考补充飞书推送3：在部分自动化环境里默认代理会导致请求失败，默认禁用系统代理。
    """
    session = requests.Session()
    if DISABLE_PROXY:
        session.trust_env = False
        session.proxies.update({"http": None, "https": None})
    return session


HTTP_SESSION = build_http_session()


def request_with_retry(method: str, url: str, **kwargs: Any) -> requests.Response:
    """
    带重试的 HTTP 请求，处理网络抖动/5xx/429。
    """
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
    """
    统一调用飞书 API，要求 code=0 才算成功。
    """
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
    """
    获取飞书 tenant_access_token。
    - 输入：应用 app_id / app_secret
    - 输出：tenant_access_token 字符串
    """
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
    """
    分页读取飞书多维表格记录。
    - 输入：访问令牌、app_token、table_id、每页大小
    - 输出：完整记录列表
    """
    records: List[Dict[str, Any]] = []
    page_token: Optional[str] = None

    # 参考补充飞书推送3：优先走 records/search + view_id，确保读取口径和页面视图一致。
    search_url = f"{FEISHU_BASE_URL}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
    headers = build_headers(tenant_access_token)

    # 循环分页直到 has_more=False，避免只读取第一页导致漏数。
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
    """
    更新指定记录字段。
    """
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


def create_record(
    tenant_access_token: str,
    app_token: str,
    table_id: str,
    fields_to_create: Dict[str, Any],
) -> str:
    """
    新增一条记录，返回新记录的 record_id。
    """
    url = f"{FEISHU_BASE_URL}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    payload = {"fields": fields_to_create}
    response = request_with_retry(
        method="POST",
        url=url,
        headers=build_headers(tenant_access_token),
        json=payload,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("code") != 0:
        raise RuntimeError(f"新增记录失败，错误：{result}")
    data = result.get("data", {})
    record = data.get("record", {})
    return to_text(record.get("record_id"))


def normalize_field_value_for_create(value: Any) -> Any:
    """
    规范化字段值，尽量兼容 records/create 入参格式。
    - 多行文本富文本结构 -> 普通字符串
    - 复杂未知结构 -> 兜底转字符串
    """
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        if value.get("type") == "text" and "text" in value:
            return to_text(value.get("text"))
        if "text" in value and len(value.keys()) <= 3:
            return to_text(value.get("text"))
        # 其他字典结构（如部分关联字段）先保留原样。
        return value
    if isinstance(value, list):
        if not value:
            return []
        # 富文本常见结构：[{"type":"text","text":"xxx"}]
        if all(isinstance(item, dict) and "text" in item for item in value):
            return "".join(to_text(item.get("text")) for item in value)
        # 基础类型数组直接保留
        if all(isinstance(item, (str, int, float, bool)) for item in value):
            return value
        # 其他复杂数组先保留，避免误伤关联字段
        return value
    return to_text(value)


def build_fields_for_split_create(original_fields: Dict[str, Any]) -> Dict[str, Any]:
    """
    基于原记录字段构造“拆分新增”字段，做一次类型规范化，避免创建接口类型报错。
    """
    result: Dict[str, Any] = {}
    for field_name, field_value in original_fields.items():
        result[field_name] = normalize_field_value_for_create(field_value)
    return result


def extract_existing_multiselect_options(original_value: Any) -> List[str]:
    """
    从“散件名称”原字段里提取当前记录已存在的多选选项文本。
    目标：拆分新增时只使用已存在选项，避免新增新选项。
    """
    options: List[str] = []
    if isinstance(original_value, list):
        for item in original_value:
            option_text = to_text(item)
            if option_text and option_text not in options:
                options.append(option_text)
    return options


def build_split_item_name_field_value(original_value: Any, split_item_name: str) -> Any:
    """
    根据原字段（多选，如“散件名称”“手动特殊处理”）构造拆分后的字段值。
    - 若原字段本身为空（None）：说明这条拆分行的散件根本不是从这个字段选出来的
      （比如这条拆分行的散件其实来自另一个字段），直接返回空，不会把不相关的名称
      写进这个字段、也不会新增选项。
    - 若原字段是多选（列表）：仅使用原记录已有选项做匹配（不新增新选项）。
    - 其他情况（原字段不是列表也不是 None，数据异常兜底）：按“字符串列表”写入。
    """
    desired_names = [name.strip() for name in split_item_name.split(",") if name.strip()]

    if original_value is None:
        return []

    if isinstance(original_value, list):
        existing_options = extract_existing_multiselect_options(original_value)
        matched_names = [name for name in desired_names if name in existing_options]
        # 若未匹配到，返回空数组，避免写入新选项。
        return matched_names
    return desired_names


def should_expand_to_unit_rows(split_item_name: str) -> bool:
    """
    判断该拆分项是否需要把 *N 展开成 N 条 *1。
    规则：仅对指定散件名称展开。
    """
    normalized_name = split_item_name.strip()
    expand_item_names = {
        "牛津树-L系列-绘本-套装【300元】",
        "蛋壳阅读练习册-套装（1-12）【350元】",
        "读写工具包-套装【900元】",
        "西游记绘本-套装【100元】",
        "小猪佩奇-L系列-台词书-套装【150元】",
        "动物街-L系列-台词书-套装【100元】",
        "小四件(蓝)【200元】",
        "小四件(粉)【200元】"
    }
    return normalized_name in expand_item_names


def expand_code_by_quantity_to_unit_rows(code_text: str) -> List[str]:
    """
    把类似 “读写工具包-25本装-[1/6]*2” 展开为两条 “...*1”。
    若是多段且乘数一致（如 A*2,B*2），则展开成两条 “A*1,B*1”。
    不满足展开条件时，原样返回。
    """
    code_parts = split_codes(code_text)
    if not code_parts:
        return []

    parsed_parts: List[Tuple[str, int]] = []
    for part in code_parts:
        match = re.match(r"^(.*)\*(\d+)$", part)
        if not match:
            return [code_text]
        base = match.group(1).strip()
        qty = int(match.group(2))
        if qty <= 0:
            return [code_text]
        parsed_parts.append((base, qty))

    distinct_qty = {qty for _, qty in parsed_parts}
    if len(distinct_qty) != 1:
        return [code_text]

    expand_count = next(iter(distinct_qty))
    if expand_count <= 1:
        return [code_text]

    one_row_text = ",".join(f"{base}*1" for base, _ in parsed_parts)
    return [one_row_text for _ in range(expand_count)]


def resolve_split_flag_field_name(fields: Dict[str, Any], preferred_name: str) -> Optional[str]:
    """
    解析“拆分标记字段”真实字段名。
    优先使用用户给定字段名；若不存在，尝试在本行字段里自动匹配“文本5/文本 5”等变体。
    """
    if preferred_name in fields:
        return preferred_name

    normalized_preferred = "".join(preferred_name.split())
    for field_name in fields.keys():
        normalized_name = "".join(field_name.split())
        if normalized_name == normalized_preferred:
            return field_name

    for field_name in fields.keys():
        normalized_name = "".join(field_name.split())
        if normalized_name.startswith("文本") and normalized_name.endswith("5"):
            return field_name
    return None


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
        # 飞书富文本/关联字段常见结构：{"text": "..."}，优先取 text。
        text = value.get("text")
        if text is not None:
            return str(text).strip()
        # 公式字段常见结构：{"type": 1, "value": [{"type": "text", "text": "..."}]}
        # “发货仓”就是这种公式字段，这里补充解析，否则会读成整个字典的字符串。
        formula_value = value.get("value")
        if isinstance(formula_value, list):
            return to_text(formula_value)
        return str(value).strip()
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            item_text = to_text(item)
            if item_text:
                parts.append(item_text)
        return ",".join(parts).strip(", ")
    return str(value).strip()


def parse_quantity_with_default_one(value: Any) -> float:
    """
    把数量字段解析为数字：空值按 1，非法值按 0。
    """
    if value is None:
        return 1.0
    if isinstance(value, (int, float)):
        return float(value)
    text = to_text(value)
    if not text:
        return 1.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_quantity_strict(value: Any) -> float:
    """
    把数量字段解析为数字：空值按 0，非法值按 0。
    """
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


def parse_unit_price(item_name: str) -> float:
    """
    从文本里提取单价。
    优先匹配【50元】这类格式，匹配不到时回退提取第一个数字（如 50元 / 50）。
    """
    match = re.search(r"【\s*([0-9]+(?:\.[0-9]+)?)\s*元\s*】", item_name)
    if match:
        return float(match.group(1))

    fallback = re.search(r"([0-9]+(?:\.[0-9]+)?)", item_name)
    if fallback:
        return float(fallback.group(1))
    return 0.0


def build_quantity_field_name(item_name: str) -> str:
    """
    根据散件名称推导数量字段名。
    规则：把最后一个【...】替换为【数量】。
    """
    return re.sub(r"【[^】]*】\s*$", "【数量】", item_name)


def split_item_names(raw_name: str) -> List[str]:
    """
    拆分“散件名称”里的多个物品，支持逗号/顿号/分号/换行。
    """
    parts = re.split(r"[,\n，、；;]+", raw_name)
    return [part.strip() for part in parts if part and part.strip()]


def split_codes(raw_code: str) -> List[str]:
    """
    拆分映射表中的“编号(对外展示)”，支持逗号/顿号/分号/换行。
    套装通常会有多个编号，拆分后统一拼接“*数量”。
    """
    parts = re.split(r"[,\n，、；;]+", raw_code)
    return [part.strip() for part in parts if part and part.strip()]


def resolve_real_codes(display_code: str) -> List[str]:
    """
    把散件信息表里的“展示编号”翻译成真正要写入飞书的编号列表。
    - 命中 CODE_DISPLAY_TO_REAL_CODES 的，返回替换后的多个真实编号。
    - 没命中的，原样返回（保持原有行为不变）。
    """
    return CODE_DISPLAY_TO_REAL_CODES.get(display_code, [display_code])


def build_exact_text_to_mapping_map(
    mapping_records: List[Dict[str, Any]]
) -> Dict[str, List[Dict[str, Any]]]:
    """
    从映射表（散件信息表）构建：散件名称(全部)文本 -> 映射行信息列表。
    说明：这里按文本做“完全匹配”，不做归一化处理。

    新增：读取映射表的“发货仓”列（该列是公式字段，由编号内容自动判定
    深圳云仓/王师傅下单/非快递订单/呼市云仓），记录到每条映射行信息里，
    供后续按发货仓类别拆分使用。
    """
    text_to_rows: Dict[str, List[Dict[str, Any]]] = {}

    for record in mapping_records:
        fields = record.get("fields", {})
        item_name_text = (
            to_text(fields.get("散件名称(全部)"))
            or to_text(fields.get("散件名称"))
            or to_text(fields.get("物品名称"))
            or to_text(fields.get("商品名称"))
            or to_text(fields.get("名称"))
        )
        amount_display_text = (
            to_text(fields.get("金额(对外展示)")) or to_text(fields.get("金额")) or to_text(fields.get("价格"))
        )
        code_text = to_text(fields.get("编号(对外展示)"))
        warehouse_text = to_text(fields.get("发货仓"))
        if not item_name_text or not code_text:
            continue

        codes = split_codes(code_text)
        if codes:
            row_info = {
                "codes": codes,
                # 金额用于最终金额汇总，优先取映射行金额列；没有则回退散件名称里的【xx元】。
                "unit_price": parse_unit_price(amount_display_text) if amount_display_text else 0.0,
                "amount_display_text": amount_display_text,
                # 发货仓类别：来自映射表同一行的公式结果，用于按仓拆分。
                "warehouse": warehouse_text,
            }
            # 金额列没解析到数字时，回退使用散件名称里的金额信息。
            if float(row_info["unit_price"]) <= 0:
                row_info["unit_price"] = parse_unit_price(item_name_text)
            if item_name_text not in text_to_rows:
                text_to_rows[item_name_text] = []
            text_to_rows[item_name_text].append(row_info)

    return text_to_rows


def build_duplicate_item_name_set(text_to_rows: Dict[str, List[Dict[str, Any]]]) -> set:
    """
    找出“散件信息表里散件名称重复”的名称集合。
    """
    duplicated_names = set()
    for item_name, rows in text_to_rows.items():
        if len(rows) > 1:
            duplicated_names.add(item_name)
    return duplicated_names


def format_number(value: float) -> str:
    """把数字格式化成更易读的文本，整数去掉 .0。"""
    if value.is_integer():
        return str(int(value))
    return str(value)


def build_target_item_entries(fields: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    拆分并保留散件来源字段。
    - 散件名称：数量字段缺失时不再默认 1
    - 手动特殊处理：数量字段缺失时默认 1
    """
    entries: List[Tuple[str, str]] = []
    normal_names = split_item_names(to_text(fields.get("散件名称")))
    entries.extend([(item_name, "散件名称") for item_name in normal_names])

    manual_names = split_item_names(to_text(fields.get("手动特殊处理")))
    entries.extend([(item_name, "手动特殊处理") for item_name in manual_names])
    return entries


def calculate_row_result(
    fields: Dict[str, Any],
    text_to_rows: Dict[str, List[Dict[str, Any]]],
    duplicated_item_names: set,
) -> Tuple[str, float, List[str], List[Dict[str, str]]]:
    """
    根据单行数据计算：
    - 散件编号(最终)
    - 散件总金额
    - 警告信息列表（如找不到映射）
    - 拆分新增用的编号/散件名称单元列表
    """
    item_entries = build_target_item_entries(fields)
    if not item_entries:
        return "", 0.0, [], []

    final_code_parts: List[str] = []
    split_entries: List[Dict[str, str]] = []
    # 按“发货仓”类别分桶存放未强制拆分、未重复的散件，
    # key 为发货仓类别文本（如“深圳云仓”“呼市云仓”），value 为该仓下累计的编号/名称/金额。
    # 用 dict 而不是单一列表，是为了让不同发货仓的散件各自合并成一条拆分行，不再混在一起。
    unsplit_by_warehouse: Dict[str, Dict[str, Any]] = {}
    total_amount = 0.0
    warnings: List[str] = []

    for item_name, source_field in item_entries:
        quantity_field_name = build_quantity_field_name(item_name)
        quantity_raw_value = fields.get(quantity_field_name)
        if source_field == "手动特殊处理":
            quantity = parse_quantity_with_default_one(quantity_raw_value)
        else:
            quantity = parse_quantity_strict(quantity_raw_value)
        if quantity <= 0:
            warnings.append(
                f"{source_field}物品[{item_name}]数量字段[{quantity_field_name}]为空或<=0，已跳过"
            )
            continue

        # 按“完全匹配”查映射：目标表（散件名称/手动特殊处理）子项 == 散件信息表.散件名称(全部)。
        # code_to_warehouse：记录每个编号真实来自哪条映射行、对应哪个发货仓，
        # 这样按编号逐条拆分时，每条编号才能读取散件信息表“发货仓”列的实际内容，
        # 而不是整个散件笼统取一个发货仓。
        matched_rows = text_to_rows.get(item_name, [])
        code_to_warehouse: Dict[str, str] = {}
        if len(matched_rows) == 1:
            row_info = matched_rows[0]
            codes = row_info.get("codes", [])
            unit_prices = [float(row_info.get("unit_price", 0.0))]
            item_warehouse = to_text(row_info.get("warehouse"))
            for code in codes:
                code_to_warehouse[code] = item_warehouse
        elif len(matched_rows) > 1:
            # 用户要求：多条命中不跳过，合并所有命中行的编号并继续计算。
            merged_codes: List[str] = []
            seen_codes = set()
            unit_prices: List[float] = []
            warehouse_candidates: List[str] = []
            for row_info in matched_rows:
                row_warehouse = to_text(row_info.get("warehouse"))
                for code in row_info.get("codes", []):
                    if code not in seen_codes:
                        merged_codes.append(code)
                        seen_codes.add(code)
                        # 每个编号各自记录来源映射行的发货仓，保留“实际内容”，不做归一化。
                        code_to_warehouse[code] = row_warehouse
                price = float(row_info.get("unit_price", 0.0))
                if price > 0:
                    unit_prices.append(price)
                if row_warehouse and row_warehouse not in warehouse_candidates:
                    warehouse_candidates.append(row_warehouse)

            codes = merged_codes
            warnings.append(
                f"映射表散件名称[{item_name}]命中 {len(matched_rows)} 条，已合并编号后继续"
            )
            # 注意：这里的“不一致”仅用于强制单独拆分项等无法按编号拆开的场景兜底提示；
            # 按编号逐条拆分时，每条编号会用 code_to_warehouse 里各自的真实发货仓，不受此影响。
            if len(warehouse_candidates) > 1:
                warnings.append(
                    f"映射表散件名称[{item_name}]命中的多条记录发货仓不一致：{warehouse_candidates}，"
                    f"按编号逐条拆分时会各自使用真实发货仓；若该散件是强制单独拆分项（无法按编号拆开），"
                    f"则按[{warehouse_candidates[0]}]处理"
                )
            item_warehouse = warehouse_candidates[0] if warehouse_candidates else ""
        else:
            warnings.append(f"映射表散件名称未找到完全匹配项：[{item_name}]")
            continue

        if not codes:
            warnings.append(f"映射表匹配项缺少编号(对外展示)：[{item_name}]")
            continue

        valid_prices = [price for price in unit_prices if price > 0]
        if not valid_prices:
            warnings.append(f"映射表匹配项金额无效：[{item_name}]，金额文本请包含数字")
            continue

        # 用户要求：多条命中时金额也全部累计。
        item_amount = sum(valid_prices) * quantity
        total_amount += item_amount

        # 无论是否套装，统一按“每个编号 * 数量”输出，多个编号用逗号分隔。
        # 部分展示编号（如“读写工具包-25本装-[1/6]”）会先按 CODE_DISPLAY_TO_REAL_CODES
        # 翻译成真实的多个 SKU 编号，再各自拼上“*数量”，用逗号拼接成同一条编号文本。
        quantity_text = format_number(quantity)
        item_code_parts: List[str] = []
        # 与 item_code_parts 一一对应，记录每个编号自己的真实发货仓（来自散件信息表）。
        item_code_warehouses: List[str] = []
        for code in codes:
            real_codes = resolve_real_codes(code)
            item_code_parts.append(
                ",".join(f"{real_code}*{quantity_text}" for real_code in real_codes)
            )
            item_code_warehouses.append(code_to_warehouse.get(code, item_warehouse))

        final_code_parts.extend(item_code_parts)

        # 规则（优先级从上到下）：
        # 1) 强制单独拆分项：始终独立成一行，且不按编号逐条拆（编号合并在一行里）。
        # 2) 散件信息表里“同名重复”的：按编号逐条拆分（这条规则要保留，优先级高于第 3 条）。
        #    例如“读写工具包-套装【900元】”在映射表里对应 6 个不同编号（6 本书各一个编号），
        #    重复命中会按编号拆成 6 行，再叠加下面的“*N 展开”规则，逐编号按数量展开成多行。
        # 3) 不属于第 1/2 条、但仍需要展开成单件多行的散件（见 should_expand_to_unit_rows），
        #    独立成一行，不与其他不同散件合并，避免合并后名称对不上导致后面展开失效。
        # 4) 其他散件默认合并，但会按“发货仓”类别再分组：
        #    不同发货仓的散件不会合并到同一条拆分行里，避免公式判断的发货仓被搞混。
        if item_name in FORCE_SEPARATE_ITEM_NAMES:
            split_entries.append(
                {
                    "codes": ",".join(item_code_parts),
                    "item_name": item_name,
                    "amount": format_number(item_amount),
                    "warehouse": item_warehouse,
                }
            )
        elif item_name in duplicated_item_names:
            # “判断该拆分项是否需要把 *N 展开成 N 条 *1”的规则保留：
            # 这里先按编号逐条拆分，每条编号各自的 *N 会在下面按 should_expand_to_unit_rows 展开。
            # 发货仓按编号各自的真实值（item_code_warehouses），而不是整个散件笼统取一个值；
            # 展开成 N 条 *1 时都是同一个编号复制出来的，天然会带着同一个正确的发货仓。
            split_count = len(item_code_parts) if item_code_parts else 1
            per_split_amount = item_amount / split_count
            for item_code, item_code_warehouse in zip(item_code_parts, item_code_warehouses):
                split_entries.append(
                    {
                        "codes": item_code,
                        "item_name": item_name,
                        "amount": format_number(per_split_amount),
                        "warehouse": item_code_warehouse,
                    }
                )
        elif should_expand_to_unit_rows(item_name):
            split_entries.append(
                {
                    "codes": ",".join(item_code_parts),
                    "item_name": item_name,
                    "amount": format_number(item_amount),
                    "warehouse": item_warehouse,
                }
            )
        else:
            warehouse_bucket = unsplit_by_warehouse.setdefault(
                item_warehouse,
                {"parts": [], "names": [], "amount": 0.0},
            )
            warehouse_bucket["parts"].extend(item_code_parts)
            warehouse_bucket["names"].append(item_name)
            warehouse_bucket["amount"] += item_amount

    # 按发货仓分组后的默认合并拆分行：每个发货仓类别单独一条，插入到最前面，
    # 使用 dict 的插入顺序（即散件在原始记录中出现的先后顺序）。
    warehouse_split_entries: List[Dict[str, str]] = []
    for warehouse_key, bucket in unsplit_by_warehouse.items():
        if not bucket["parts"]:
            continue
        warehouse_split_entries.append(
            {
                "codes": ",".join(bucket["parts"]),
                "item_name": ",".join(bucket["names"]),
                "amount": format_number(bucket["amount"]),
                "warehouse": warehouse_key,
            }
        )
    split_entries = warehouse_split_entries + split_entries

    return ",".join(final_code_parts), total_amount, warnings, split_entries


def extract_quantity_snapshot(fields: Dict[str, Any], item_entries: List[Tuple[str, str]]) -> Dict[str, Any]:
    """
    提取本行涉及到的数量字段快照，便于打印核对。
    """
    quantity_data: Dict[str, Any] = {}
    for item_name, _ in item_entries:
        quantity_field_name = build_quantity_field_name(item_name)
        quantity_data[quantity_field_name] = fields.get(quantity_field_name)
    return quantity_data


def print_debug_payload(payload: Dict[str, Any]) -> None:
    """
    统一打印调试信息，确保中文不转义，方便人工比对。
    """
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="回填飞书多维表格散件编号与金额")
    # 为了支持“双击 exe 或直接运行不传参”，这里提供默认凭证。
    # 如需切换环境，仍可通过命令行参数覆盖默认值。
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
        help="回填目标表 Table ID（默认是散件发货回填表）",
    )
    parser.add_argument(
        "--mapping-table-id",
        default=DEFAULT_MAPPING_TABLE_ID,
        help="散件信息表 Table ID（含散件名称/编号(对外展示)/金额(对外展示)）",
    )
    parser.add_argument(
        "--target-view-id",
        default=DEFAULT_TARGET_VIEW_ID,
        help="回填目标表 View ID（为空表示不按视图筛选）",
    )
    parser.add_argument(
        "--mapping-view-id",
        default=DEFAULT_MAPPING_VIEW_ID,
        help="名称编号映射表 View ID（为空表示不按视图筛选）",
    )
    parser.add_argument("--page-size", type=int, default=100, help="分页大小，建议 100，最大 500")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印计算结果，不实际写回飞书",
    )
    parser.add_argument(
        "--print-debug",
        action="store_true",
        help="打印读取到的数据与待写入数据（用于核对）",
    )
    parser.add_argument(
        "--split-flag-field",
        default="数据类型",
        help="拆分标记字段名（默认 数据类型，可按实际表头传入）",
    )
    return parser.parse_args()


def main() -> int:
    """主流程：鉴权 -> 读取映射 -> 计算结果 -> 写回目标表。"""
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
        text_to_rows = build_exact_text_to_mapping_map(mapping_records)
        duplicated_item_names = build_duplicate_item_name_set(text_to_rows)

        success_count = 0
        skip_count = 0
        warning_count = 0
        split_plan_count = 0
        split_created_count = 0

        for record in target_records:
            record_id = record.get("record_id", "")
            fields = record.get("fields", {})
            if not record_id:
                skip_count += 1
                continue

            split_flag_field_name = resolve_split_flag_field_name(fields, args.split_flag_field)

            # 拆分新增行不再重复处理，避免脚本重复执行时指数级新增。
            if split_flag_field_name and to_text(fields.get(split_flag_field_name)) == "拆分":
                skip_count += 1
                continue

            final_code_text, total_amount, warnings, split_entries = calculate_row_result(
                fields=fields,
                text_to_rows=text_to_rows,
                duplicated_item_names=duplicated_item_names,
            )
            warning_count += len(warnings)
            for warning in warnings:
                print(f"[警告][{record_id}] {warning}")

            # 飞书该列为文本类型，金额按字符串写回，避免 TextFieldConvFail。
            amount_to_write = format_number(total_amount)

            update_fields = {
                "散件编号(最终)": final_code_text,
                "散件总金额": amount_to_write,
            }

            if args.print_debug:
                item_entries = build_target_item_entries(fields)
                debug_payload = {
                    "record_id": record_id,
                    "读取数据": {
                        "散件名称": fields.get("散件名称"),
                        "手动特殊处理": fields.get("手动特殊处理"),
                        "数量字段快照": extract_quantity_snapshot(fields, item_entries),
                    },
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

            # 按“散件编号(最终)”拆分新增：有几条编号就新增几行，并把 文本5 写为“拆分”。
            split_entries_to_create = [entry for entry in split_entries if to_text(entry.get("codes"))]
            if not split_entries_to_create and final_code_text:
                split_entries_to_create = [
                    {
                        "codes": code_text,
                        "item_name": to_text(fields.get("散件名称")),
                        "amount": amount_to_write,
                    }
                    for code_text in split_codes(final_code_text)
                ]
            if split_entries_to_create:
                split_plan_count += len(split_entries_to_create)

            for split_entry in split_entries_to_create:
                split_code = to_text(split_entry.get("codes"))
                split_item_name = to_text(split_entry.get("item_name"))
                split_amount = to_text(split_entry.get("amount")) or amount_to_write
                split_warehouse = to_text(split_entry.get("warehouse"))

                expanded_codes = [split_code]
                if should_expand_to_unit_rows(split_item_name):
                    expanded_codes = expand_code_by_quantity_to_unit_rows(split_code)
                    if not expanded_codes:
                        expanded_codes = [split_code]

                # 展开后每条金额也需要对应拆开，按条数均摊。
                expanded_count = len(expanded_codes) if expanded_codes else 1
                try:
                    per_amount = float(split_amount) / expanded_count
                    expanded_amounts = [format_number(per_amount) for _ in range(expanded_count)]
                except ValueError:
                    expanded_amounts = [split_amount for _ in range(expanded_count)]

                for current_code, current_amount in zip(expanded_codes, expanded_amounts):
                # 需求是“写回后的完整记录拆分”，因此先复制原字段，再覆盖写回字段。
                    split_fields = build_fields_for_split_create(fields)
                    split_fields["散件编号(最终)"] = current_code
                    split_fields["散件总金额"] = current_amount
                    # 拆分后的散件名称同时写入多处：
                    # 1) “散件名称(脚本)”：始终是当前拆分行对应的散件名称（原样文本，便于核对）。
                    # 2) “散件名称”（原字段，多选）：按本条拆分只保留匹配上的选项，
                    #    不是这条拆分行涉及的散件会被去掉；只用原记录已有的选项，不新增新选项。
                    # 3) “手动特殊处理”（原字段，多选）：规则同上，两个字段各自独立按拆分过滤——
                    #    如果这条拆分行的散件来自“散件名称”，“手动特殊处理”会被过滤成空；
                    #    反过来如果来自“手动特殊处理”，“散件名称”会被过滤成空。
                    split_fields["散件名称(脚本)"] = split_item_name
                    split_fields["散件名称"] = build_split_item_name_field_value(
                        fields.get("散件名称"), split_item_name
                    )
                    split_fields["手动特殊处理"] = build_split_item_name_field_value(
                        fields.get("手动特殊处理"), split_item_name
                    )
                    # “发货仓”列现在是普通文本字段（不再是公式），由脚本按散件信息表读到的
                    # 真实发货仓显式写入；同一条拆分行内的编号已保证属于同一个发货仓。
                    split_fields["发货仓"] = split_warehouse
                    if split_flag_field_name:
                        split_fields[split_flag_field_name] = "拆分"
                    else:
                        warnings.append(
                            f"记录[{record_id}]未找到拆分标记字段[{args.split_flag_field}]，本次新增行不会写入“拆分”标记"
                        )

                    if args.dry_run:
                        print(
                            f"[拆分预览][{record_id}] 新增1行 -> 散件名称={split_item_name}，散件编号(最终)={current_code}，"
                            f"散件总金额={current_amount}，数据类型=拆分，发货仓(预计)={split_warehouse or '未知'}"
                        )
                    else:
                        create_record(
                            tenant_access_token=token,
                            app_token=args.app_token,
                            table_id=args.target_table_id,
                            fields_to_create=split_fields,
                        )
                    split_created_count += 1

            success_count += 1

        action = "预览完成" if args.dry_run else "写回完成"
        print(
            f"{action}：共处理 {len(target_records)} 条，成功 {success_count} 条，"
            f"跳过 {skip_count} 条，警告 {warning_count} 条，"
            f"计划拆分 {split_plan_count} 条，实际拆分 {split_created_count} 条。"
        )
        return 0
    except requests.RequestException as exc:
        print(f"网络请求失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - 这里统一兜底输出错误，便于脚本用户排查
        print(f"执行失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
