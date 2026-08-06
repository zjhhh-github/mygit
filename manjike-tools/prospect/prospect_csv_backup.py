#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""意向学员导出/同步备份用的 CSV 工具（扁平化「学员 + 来源/推荐人」嵌套 JSON）。

【模块功能说明】
将 STUDENTS_IMPORT 风格的嵌套 JSON（学员 + 来源/推荐人数组）展平为 CSV 行，
或在「推荐人」（意向通讯录.json）与「来源」（后端导入）两种字段命名间互转。

主业务文件（如 upload_prospect.py 使用的 JSON）仍保持嵌套结构；
带时间戳的备份快照统一写成 CSV，便于 Excel 打开与归档。
"""

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 备份 CSV 列顺序：一行对应「一个学员 + 一条来源推荐记录」
CSV_BACKUP_COLUMNS: Sequence[str] = (
    "意向学员微信号",
    "意向学员微信原始ID",
    "是否报名",
    "来源微信号",
    "来源微信原始ID",
    "绑定日期",
    "解绑日期",
    "绑定状态",
)

# 来源对象字段的规范名 → 可能出现的别名（意向通讯录.json 用「推荐人*」）
SOURCE_FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "来源微信号": ("来源微信号", "推荐人微信号"),
    "来源微信原始ID": ("来源微信原始ID", "推荐人微信原始ID"),
    "绑定日期": ("绑定日期",),
    "解绑日期": ("解绑日期",),
    "绑定状态": ("绑定状态",),
}

# 意向通讯录.json 使用的嵌套数组 key；后端 JSON 导入识别「来源」
STUDENT_SOURCES_KEY_CONTACT_BOOK = "推荐人"
STUDENT_SOURCES_KEY_BACKEND = "来源"


def _text_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def get_student_sources(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """读取学员嵌套数组：优先「推荐人」（意向通讯录.json），否则「来源」。"""
    sources = row.get(STUDENT_SOURCES_KEY_CONTACT_BOOK)
    if sources is None:
        sources = row.get(STUDENT_SOURCES_KEY_BACKEND)
    if not isinstance(sources, list):
        return []
    return [item for item in sources if isinstance(item, dict)]


def get_source_field(source: Dict[str, Any], canonical_key: str) -> str:
    """按规范字段名读取来源对象，自动兼容「推荐人*」别名。"""
    aliases = SOURCE_FIELD_ALIASES.get(canonical_key, (canonical_key,))
    for key in aliases:
        value = _text_or_empty(source.get(key))
        if value:
            return value
    return ""


def normalize_students_for_backend(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把「推荐人」/「来源」混合格式统一为后端 STUDENTS_IMPORT 识别的「来源」结构。"""
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sources_norm: List[Dict[str, str]] = []
        for src in get_student_sources(row):
            item: Dict[str, str] = {}
            for canon_key in SOURCE_FIELD_ALIASES:
                value = get_source_field(src, canon_key)
                if value:
                    item[canon_key] = value
            sources_norm.append(item)
        normalized.append({
            "意向学员微信号": _text_or_empty(row.get("意向学员微信号")),
            "意向学员微信原始ID": _text_or_empty(row.get("意向学员微信原始ID")),
            "是否报名": _text_or_empty(row.get("是否报名")),
            STUDENT_SOURCES_KEY_BACKEND: sources_norm,
        })
    return normalized


def flatten_student_json_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """把嵌套 JSON 数组展平为 CSV 行列表（兼容「推荐人」与「来源」）。"""
    flat_rows: List[Dict[str, str]] = []
    for row in rows:
        student_wechat = _text_or_empty(row.get("意向学员微信号"))
        student_oid = _text_or_empty(row.get("意向学员微信原始ID"))
        enrolled = _text_or_empty(row.get("是否报名"))
        sources = get_student_sources(row)

        if not sources:
            flat_rows.append({
                "意向学员微信号": student_wechat,
                "意向学员微信原始ID": student_oid,
                "是否报名": enrolled,
                "来源微信号": "",
                "来源微信原始ID": "",
                "绑定日期": "",
                "解绑日期": "",
                "绑定状态": "",
            })
            continue

        for source in sources:
            flat_rows.append({
                "意向学员微信号": student_wechat,
                "意向学员微信原始ID": student_oid,
                "是否报名": enrolled,
                "来源微信号": get_source_field(source, "来源微信号"),
                "来源微信原始ID": get_source_field(source, "来源微信原始ID"),
                "绑定日期": get_source_field(source, "绑定日期"),
                "解绑日期": get_source_field(source, "解绑日期"),
                "绑定状态": get_source_field(source, "绑定状态"),
            })
    return flat_rows


def write_student_csv_backup(
    output_path: Path,
    rows: List[Dict[str, Any]],
    extra_fieldnames: Optional[Sequence[str]] = None,
) -> int:
    """写入 UTF-8 BOM CSV，返回文件字节数。"""
    flat_rows = flatten_student_json_rows(rows)
    fieldnames = list(extra_fieldnames or CSV_BACKUP_COLUMNS)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flat_rows)
    return output_path.stat().st_size


def write_records_csv_backup(output_path: Path, records: List[Dict[str, Any]]) -> int:
    """把任意字典列表（如飞书写入前的展平记录）备份为 CSV。"""
    if not records:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8-sig")
        return 0

    fieldnames: List[str] = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in record.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(str(key))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            if isinstance(record, dict):
                writer.writerow({key: _text_or_empty(record.get(key)) for key in fieldnames})
    return output_path.stat().st_size
