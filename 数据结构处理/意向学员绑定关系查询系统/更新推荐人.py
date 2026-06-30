# -*- coding: utf-8 -*-
"""
根据 txt 中的（意向学员微信号、推荐人微信号、绑定日期），
更新 / 新增到原始意向学员 JSON 中。

匹配规则：
    以 "意向学员微信号" 为唯一匹配键。
        - 若 JSON 中已有该意向学员：更新其 "来源" 数组中第 0 条的
          "来源微信号"（即推荐人微信号）与 "绑定日期"，其他字段保留。
        - 若 JSON 中无该意向学员：新增一条记录，结构与原 JSON 保持一致。

注意：原始 JSON 字段使用的是 "来源微信号"（嵌套在 "来源" 数组中），
      用户口中的 "推荐人微信号" 在实际数据里就是该字段。
"""

import argparse
import io
import json
import os
import sys
from datetime import datetime

# Windows 控制台 GBK 编码无法输出部分 Unicode，统一切换 stdout 为 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from typing import Any, Dict, List, Tuple

# ===================== 配置 =====================
JSON_INPUT_PATH = r"C:\Users\LENOVO\Downloads\意向学员数据_2026-04-27.json"
TXT_INPUT_PATH = r"C:\Users\LENOVO\Desktop\_脚本输入_1.txt"
JSON_OUTPUT_PATH = r"C:\Users\LENOVO\Downloads\意向学员数据_更新后.json"

# 表头候选关键字（首行如果命中其一则视为表头跳过）
HEADER_KEYWORDS = ("意向学员微信号", "推荐人", "微信号")

# 默认绑定周期（天数）：解绑日期 = 绑定日期 + DEFAULT_UNBIND_DAYS
# 可通过命令行 --days N 覆盖，例如：python 更新推荐人.py --days 60
DEFAULT_UNBIND_DAYS = 30


# ===================== 工具函数 =====================
def read_json_file(path: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    读取 JSON 文件，自动兼容数组结构和对象结构。

    返回:
        (records, original_kind)
        records: 统一为列表，便于后续处理
        original_kind: "list" 或 "object"，便于写回时还原结构
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data, "list"
    if isinstance(data, dict):
        # 常见包装形式：{"data": [...]} 或 {"list": [...]}
        for key in ("data", "list", "records", "items"):
            if key in data and isinstance(data[key], list):
                # 用 tuple 把 wrapper key 一起带出去，便于还原
                return data[key], f"object:{key}"
        # 若是单条记录，则包装为列表
        return [data], "single_object"
    raise ValueError(f"不支持的 JSON 顶层结构: {type(data)}")


def write_json_file(path: str, records: List[Dict[str, Any]], original_kind: str) -> None:
    """按原始结构写回 JSON 文件。"""
    if original_kind == "list":
        payload: Any = records
    elif original_kind.startswith("object:"):
        key = original_kind.split(":", 1)[1]
        payload = {key: records}
    elif original_kind == "single_object":
        payload = records[0] if records else {}
    else:
        payload = records

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def read_txt_rows(path: str) -> List[Tuple[int, str, str, str]]:
    """
    读取 txt，自动跳过表头与空行/异常行。

    每行格式（制表符分隔）:
        意向学员微信号 \t 推荐人微信号 \t 绑定日期

    返回:
        [(行号, 意向学员微信号, 推荐人微信号, 绑定日期), ...]
    """
    # 优先 utf-8，失败时回退 gbk，兼容 Windows 导出
    raw_lines: List[str] = []
    for encoding in ("utf-8", "utf-8-sig", "gbk"):
        try:
            with open(path, "r", encoding=encoding) as f:
                raw_lines = f.readlines()
            break
        except UnicodeDecodeError:
            continue
    if not raw_lines:
        raise RuntimeError(f"无法以常见编码读取 txt 文件: {path}")

    rows: List[Tuple[int, str, str, str]] = []
    for idx, raw in enumerate(raw_lines, start=1):
        line = raw.strip()
        if not line:
            print(f"[跳过] 第 {idx} 行：空行")
            continue

        # 跳过表头
        if idx == 1 and any(kw in line for kw in HEADER_KEYWORDS):
            print(f"[信息] 第 {idx} 行识别为表头，已跳过：{line}")
            continue

        # 兼容制表符 / 多空格分隔
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < 2:
            parts = [p.strip() for p in line.split() if p.strip()]

        if len(parts) < 2:
            print(f"[跳过] 第 {idx} 行：字段不足，无法解析 -> {line}")
            continue

        intent_wx = parts[0]
        recommender_wx = parts[1]
        bind_date = parts[2] if len(parts) >= 3 else datetime.now().strftime("%Y%m%d")

        if not intent_wx or not recommender_wx:
            print(f"[跳过] 第 {idx} 行：关键字段为空 -> {line}")
            continue

        rows.append((idx, intent_wx, recommender_wx, bind_date))

    return rows


def calc_unbind_date(bind_date: str, days: int = DEFAULT_UNBIND_DAYS) -> str:
    """根据绑定日期推算解绑日期，格式 yyyymmdd；解析失败则返回空串。"""
    try:
        dt = datetime.strptime(bind_date, "%Y%m%d")
        from datetime import timedelta
        return (dt + timedelta(days=days)).strftime("%Y%m%d")
    except Exception:
        return ""


def build_new_record(intent_wx: str, recommender_wx: str, bind_date: str,
                     days: int = DEFAULT_UNBIND_DAYS) -> Dict[str, Any]:
    """构造一条与原 JSON 结构一致的新增记录。"""
    return {
        "意向学员微信号": intent_wx,
        "是否报名": "未报名",
        "来源": [
            {
                "来源微信号": recommender_wx,
                "绑定日期": bind_date,
                "解绑日期": calc_unbind_date(bind_date, days),
                "绑定状态": "有绑定",
            }
        ],
    }


def update_record_recommender(record: Dict[str, Any], recommender_wx: str, bind_date: str,
                              days: int = DEFAULT_UNBIND_DAYS) -> None:
    """
    在已有记录上更新推荐人信息：
        - 若已有 "来源" 数组：更新第 0 条的 来源微信号 / 绑定日期 / 解绑日期 / 绑定状态
        - 若没有 "来源" 数组：新建一条来源
    其他原字段一律保留。
    """
    sources = record.get("来源")
    new_source_item = {
        "来源微信号": recommender_wx,
        "绑定日期": bind_date,
        "解绑日期": calc_unbind_date(bind_date, days),
        "绑定状态": "有绑定",
    }
    if isinstance(sources, list) and sources:
        # 仅修改第 0 条的相关字段，保留其它原始字段
        first = sources[0]
        if isinstance(first, dict):
            first["来源微信号"] = recommender_wx
            first["绑定日期"] = bind_date
            first["解绑日期"] = calc_unbind_date(bind_date, days)
            first["绑定状态"] = "有绑定"
        else:
            sources[0] = new_source_item
    else:
        record["来源"] = [new_source_item]


# ===================== 主流程 =====================
def main() -> None:
    parser = argparse.ArgumentParser(
        description="意向学员推荐人更新脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n  python 更新推荐人.py\n  python 更新推荐人.py --days 60\n  python 更新推荐人.py --json 输入.json --txt 输入.txt --out 输出.json --days 45",
    )
    parser.add_argument("--json", dest="json_input", default=JSON_INPUT_PATH,
                        help=f"原始 JSON 文件路径（默认：{JSON_INPUT_PATH}）")
    parser.add_argument("--txt", dest="txt_input", default=TXT_INPUT_PATH,
                        help=f"输入 TXT 文件路径（默认：{TXT_INPUT_PATH}）")
    parser.add_argument("--out", dest="json_output", default=JSON_OUTPUT_PATH,
                        help=f"输出 JSON 文件路径（默认：{JSON_OUTPUT_PATH}）")
    parser.add_argument("--days", dest="unbind_days", type=int, default=DEFAULT_UNBIND_DAYS,
                        help=f"绑定周期（天），解绑日期 = 绑定日期 + N 天（默认：{DEFAULT_UNBIND_DAYS}）")
    args = parser.parse_args()

    json_input  = args.json_input
    txt_input   = args.txt_input
    json_output = args.json_output
    unbind_days = args.unbind_days

    print("=" * 60)
    print("意向学员推荐人更新脚本")
    print("=" * 60)
    print(f"原始 JSON: {json_input}")
    print(f"输入  TXT: {txt_input}")
    print(f"输出 JSON: {json_output}")
    print(f"绑定周期 : {unbind_days} 天")
    print("-" * 60)

    if not os.path.isfile(json_input):
        print(f"[错误] JSON 文件不存在: {json_input}")
        sys.exit(1)
    if not os.path.isfile(txt_input):
        print(f"[错误] TXT 文件不存在: {txt_input}")
        sys.exit(1)

    records, original_kind = read_json_file(json_input)
    print(f"[信息] 已加载 JSON，共 {len(records)} 条记录，结构类型: {original_kind}")

    # 建立索引：意向学员微信号 -> 记录引用
    index_map: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        if isinstance(rec, dict):
            wx = rec.get("意向学员微信号")
            if wx:
                index_map[str(wx).strip()] = rec

    txt_rows = read_txt_rows(txt_input)
    print(f"[信息] 已加载 TXT，有效数据行 {len(txt_rows)} 条")
    print("-" * 60)

    update_count = 0
    insert_count = 0

    for line_no, intent_wx, recommender_wx, bind_date in txt_rows:
        try:
            if intent_wx in index_map:
                update_record_recommender(index_map[intent_wx], recommender_wx, bind_date, unbind_days)
                update_count += 1
            else:
                new_rec = build_new_record(intent_wx, recommender_wx, bind_date, unbind_days)
                records.append(new_rec)
                index_map[intent_wx] = new_rec
                insert_count += 1
        except Exception as exc:
            print(f"[跳过] 第 {line_no} 行：处理出错 -> {exc}")

    write_json_file(json_output, records, original_kind)

    print("-" * 60)
    print("处理完成")
    print(f"  更新条数: {update_count}")
    print(f"  新增条数: {insert_count}")
    print(f"  最终总条数: {len(records)}")
    print(f"  输出文件: {json_output}")
    print("=" * 60)


if __name__ == "__main__":
    main()
