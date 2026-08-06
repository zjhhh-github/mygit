#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书推荐人一致性检查脚本。

【脚本功能说明】
读取飞书多维表格中的两张数据表，校验「意向学员绑定表」里的
「意向学员总微信号 + 推荐人总微信号」是否与「推荐人变更对照表」中
同一学员的「新推荐人微信号」一致。

典型场景：
- 表1（tblNIWZ1EsDyZ1ug）：意向学员与推荐人的绑定明细
- 表2（tblzUayWLgynug56）：学员推荐人变更后的标准对照

用法（在 manjike-tools/prospect 目录）：
    python check_feishu_referrer.py
    python check_feishu_referrer.py --config check_feishu_referrer.config.json
    python check_feishu_referrer.py --preview 30

配置文件：
- check_feishu_referrer.config.json（默认同目录）
- 飞书凭证也可通过环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET 覆盖

输出：
- 控制台摘要
- logs/check_feishu_referrer_report.json 详细报告
- logs/check_feishu_referrer.log 运行日志
"""

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

SCRIPT_DIR = Path(__file__).resolve().parent
USERS_DIR = SCRIPT_DIR.parent / "users"
FEISHU_MODULE_PATH = USERS_DIR / "feishu-read-bitable.py"
DEFAULT_CONFIG_FILE = SCRIPT_DIR / "check_feishu_referrer.config.json"
SYNC_CONFIG_FILE = SCRIPT_DIR / "sync_to_feishu.config.json"


def load_feishu_module():
    """动态加载 users/feishu-read-bitable.py，复用其 OpenAPI 封装。"""
    if not FEISHU_MODULE_PATH.exists():
        raise FileNotFoundError(f"缺少飞书读取模块：{FEISHU_MODULE_PATH}")
    spec = importlib.util.spec_from_file_location("feishu_read_bitable", FEISHU_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> Dict[str, Any]:
    """读取 JSON 配置文件。"""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_path(path_str: str) -> Path:
    """相对路径锚定到脚本目录。"""
    path = Path(path_str)
    return path if path.is_absolute() else (SCRIPT_DIR / path)


def normalize_text(value: Any) -> str:
    """统一普通文本字段：去首尾空白，None 视为空字符串。"""
    if value is None:
        return ""
    return str(value).strip()


def should_skip_record(fields: Dict[str, Any], filter_cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """判断表1记录是否应跳过（未报名或无绑定）。

    返回 (是否跳过, 跳过原因)。
    """
    if not filter_cfg.get("enabled", True):
        return False, ""

    enrollment_field = filter_cfg.get("enrollment_status_field") or "报名状态"
    bind_status_field = filter_cfg.get("bind_status_field") or "绑定状态"
    skip_enrollment_values = filter_cfg.get("skip_enrollment_values") or ["未报名"]
    skip_bind_values = filter_cfg.get("skip_bind_values") or ["无绑定"]

    enrollment_status = normalize_text(fields.get(enrollment_field))
    bind_status = normalize_text(fields.get(bind_status_field))

    if enrollment_status in skip_enrollment_values:
        return True, f"{enrollment_field}={enrollment_status}"
    if bind_status in skip_bind_values:
        return True, f"{bind_status_field}={bind_status}"
    return False, ""


def normalize_wechat(value: Any) -> str:
    """统一微信号字符串：去首尾空白，None 视为空字符串。"""
    if value is None:
        return ""
    return str(value).strip()


def merge_config(config_path: Optional[Path]) -> Dict[str, Any]:
    """合并默认配置、sync 配置与专用配置文件。"""
    file_cfg = load_json(config_path or DEFAULT_CONFIG_FILE)
    sync_cfg = load_json(SYNC_CONFIG_FILE)

    feishu_cfg = dict(sync_cfg.get("飞书") or {})
    feishu_cfg.update(file_cfg.get("飞书") or {})

    # 环境变量优先级最高
    feishu_cfg["app_id"] = os.getenv("FEISHU_APP_ID") or feishu_cfg.get("app_id")
    feishu_cfg["app_secret"] = os.getenv("FEISHU_APP_SECRET") or feishu_cfg.get("app_secret")
    feishu_cfg["app_token"] = os.getenv("FEISHU_APP_TOKEN") or feishu_cfg.get("app_token")

    merged = {
        "飞书": feishu_cfg,
        "表1_意向学员绑定": file_cfg.get("表1_意向学员绑定") or {},
        "表2_推荐人变更对照": file_cfg.get("表2_推荐人变更对照") or {},
        "检查": file_cfg.get("检查") or {},
        "路径": file_cfg.get("路径") or {},
    }
    merged["检查"].setdefault(
        "推荐人比对",
        {
            "alternate_fields": ["推荐人微信号", "推荐人微信原始ID"],
            "ignore_custom_alias_mismatch": True,
        },
    )
    return merged


def fetch_all_records(
    feishu_mod: Any,
    token: str,
    app_token: str,
    table_id: str,
) -> List[Dict[str, Any]]:
    """分页拉取整张表，并拍平飞书多行文本字段。"""
    records = feishu_mod.list_bitable_records(token, app_token, table_id, page_size=500)
    return feishu_mod.flatten_record_fields(records)


def build_reference_map(
    records: List[Dict[str, Any]],
    student_field: str,
    new_referrer_field: str,
) -> Tuple[Dict[str, str], Dict[str, str], List[Dict[str, Any]]]:
    """从表2构建「学员微信号 -> 新推荐人微信号」对照表。

    同一学员若有多条记录，按飞书拉取顺序以最后一条为准（后者覆盖前者）。
    若被覆盖的值不同，记入 duplicate_overwritten 供人工参考，但不视为错误。
    """
    mapping: Dict[str, str] = {}
    source_record_map: Dict[str, str] = {}
    duplicate_overwritten: List[Dict[str, Any]] = []

    for record in records:
        fields = record.get("fields") or {}
        student = normalize_wechat(fields.get(student_field))
        new_referrer = normalize_wechat(fields.get(new_referrer_field))
        record_id = record.get("record_id")
        if not student:
            continue

        previous_referrer = mapping.get(student)
        if previous_referrer is not None and previous_referrer != new_referrer:
            duplicate_overwritten.append(
                {
                    "student_wechat": student,
                    "previous_new_referrer": previous_referrer,
                    "previous_record_id": source_record_map.get(student),
                    "final_new_referrer": new_referrer,
                    "final_record_id": record_id,
                }
            )

        mapping[student] = new_referrer
        if record_id:
            source_record_map[student] = record_id

    return mapping, source_record_map, duplicate_overwritten


def is_wxid_value(value: str) -> bool:
    """判断是否为微信原始 ID（wxid_ 前缀）。"""
    return bool(value) and value.startswith("wxid_")


def collect_referrer_candidates(
    fields: Dict[str, Any],
    primary_field: str,
    alternate_fields: List[str],
) -> List[str]:
    """收集表1中可用于比对的所有推荐人标识（去重、保序）。"""
    ordered: List[str] = []
    seen = set()
    for key in [primary_field] + list(alternate_fields or []):
        value = normalize_wechat(fields.get(key))
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def check_referrer_consistency(
    fields: Dict[str, Any],
    primary_field: str,
    expected_referrer: str,
    match_cfg: Dict[str, Any],
) -> Tuple[bool, str, List[str]]:
    """判断表1推荐人是否与表2期望值一致。

    除「推荐人总微信号」外，还会检查推荐人微信号 / 推荐人微信原始ID。
    下列情况视为一致（忽略差异）：
    1. 表2新推荐人与表1任一推荐人字段相同（常见于 wxid 与原始ID一致）
    2. 表1总微信号与表2新推荐人都是自定义微信号（非 wxid_），允许别名差异
    """
    candidates = collect_referrer_candidates(
        fields,
        primary_field,
        match_cfg.get("alternate_fields") or ["推荐人微信号", "推荐人微信原始ID"],
    )
    primary_value = normalize_wechat(fields.get(primary_field))

    if expected_referrer in candidates:
        if primary_value == expected_referrer:
            return True, "total_wechat_match", candidates
        return True, "alternate_field_match", candidates

    if match_cfg.get("ignore_custom_alias_mismatch", True):
        if (
            primary_value
            and expected_referrer
            and not is_wxid_value(primary_value)
            and not is_wxid_value(expected_referrer)
        ):
            return True, "custom_alias_tolerated", candidates

    return False, "mismatch", candidates


def compare_tables(
    table1_records: List[Dict[str, Any]],
    reference_map: Dict[str, str],
    student_field: str,
    referrer_field: str,
    ignore_empty_referrer: bool,
    filter_cfg: Dict[str, Any],
    match_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """逐行比对表1与表2对照结果。"""
    matched: List[Dict[str, Any]] = []
    ignored_alias_mismatch: List[Dict[str, Any]] = []
    mismatched: List[Dict[str, Any]] = []
    missing_reference: List[Dict[str, Any]] = []
    skipped_empty: List[Dict[str, Any]] = []
    skipped_by_filter: List[Dict[str, Any]] = []

    for record in table1_records:
        fields = record.get("fields") or {}
        record_id = record.get("record_id")

        should_skip, skip_reason = should_skip_record(fields, filter_cfg)
        if should_skip:
            skipped_by_filter.append(
                {
                    "record_id": record_id,
                    "reason": skip_reason,
                    "student_wechat": normalize_wechat(fields.get(student_field)),
                    "fields": fields,
                }
            )
            continue

        student = normalize_wechat(fields.get(student_field))
        actual_referrer = normalize_wechat(fields.get(referrer_field))

        if not student:
            skipped_empty.append(
                {
                    "record_id": record_id,
                    "reason": "表1意向学员总微信号为空",
                    "fields": fields,
                }
            )
            continue

        expected_referrer = reference_map.get(student)
        if expected_referrer is None:
            missing_reference.append(
                {
                    "record_id": record_id,
                    "student_wechat": student,
                    "actual_referrer": actual_referrer,
                    "fields": fields,
                }
            )
            continue

        ignore_students = {
            normalize_wechat(item)
            for item in (match_cfg.get("ignore_student_wechats") or [])
            if normalize_wechat(item)
        }
        if student in ignore_students:
            ignored_alias_mismatch.append(
                {
                    "record_id": record_id,
                    "student_wechat": student,
                    "actual_referrer": actual_referrer,
                    "expected_referrer": expected_referrer,
                    "referrer_candidates": collect_referrer_candidates(
                        fields,
                        referrer_field,
                        match_cfg.get("alternate_fields") or ["推荐人微信号", "推荐人微信原始ID"],
                    ),
                    "match_reason": "manual_ignore",
                    "fields": fields,
                }
            )
            continue

        if ignore_empty_referrer and not actual_referrer and not expected_referrer:
            matched.append(
                {
                    "record_id": record_id,
                    "student_wechat": student,
                    "actual_referrer": actual_referrer,
                    "expected_referrer": expected_referrer,
                    "match_reason": "both_empty",
                }
            )
            continue

        is_consistent, match_reason, candidates = check_referrer_consistency(
            fields, referrer_field, expected_referrer, match_cfg
        )
        result_item = {
            "record_id": record_id,
            "student_wechat": student,
            "actual_referrer": actual_referrer,
            "expected_referrer": expected_referrer,
            "referrer_candidates": candidates,
            "match_reason": match_reason,
            "fields": fields,
        }

        if is_consistent:
            if match_reason == "custom_alias_tolerated":
                ignored_alias_mismatch.append(result_item)
            else:
                matched.append(result_item)
        else:
            mismatched.append(result_item)

    # 表2中有、但表1有效记录里完全没出现的学员
    table1_students = set()
    for rec in table1_records:
        fields = rec.get("fields") or {}
        if should_skip_record(fields, filter_cfg)[0]:
            continue
        student = normalize_wechat(fields.get(student_field))
        if student:
            table1_students.add(student)
    only_in_reference = sorted(set(reference_map.keys()) - table1_students)

    return {
        "matched": matched,
        "ignored_alias_mismatch": ignored_alias_mismatch,
        "mismatched": mismatched,
        "missing_reference": missing_reference,
        "skipped_empty": skipped_empty,
        "skipped_by_filter": skipped_by_filter,
        "only_in_reference": only_in_reference,
    }


def write_log(log_path: Path, lines: List[str]) -> None:
    """追加写入简单文本日志。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(f"{timestamp} {line}\n")


def export_mismatch_results(
    mismatched: List[Dict[str, Any]],
    summary: Dict[str, Any],
    mismatch_json_path: Path,
    mismatch_txt_path: Path,
) -> None:
    """把不一致结果单独导出为 JSON 与可读 TXT 文件。"""
    mismatch_json_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "checked_at": summary.get("checked_at"),
        "mismatched_count": len(mismatched),
        "items": mismatched,
    }
    with mismatch_json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    lines: List[str] = []
    lines.append("飞书推荐人不一致明细")
    lines.append(f"检查时间: {summary.get('checked_at')}")
    lines.append(f"不一致数量: {len(mismatched)}")
    lines.append("=" * 60)

    for index, item in enumerate(mismatched, start=1):
        fields = item.get("fields") or {}
        lines.append(f"[{index}] 学员微信号: {item.get('student_wechat', '')}")
        lines.append(f"    表1推荐人总微信号: {item.get('actual_referrer') or '(空)'}")
        lines.append(f"    表2新推荐人微信号: {item.get('expected_referrer') or '(空)'}")
        lines.append(f"    报名状态: {normalize_text(fields.get('报名状态')) or '(空)'}")
        lines.append(f"    绑定状态: {normalize_text(fields.get('绑定状态')) or '(空)'}")
        lines.append(f"    绑定日期: {normalize_text(fields.get('绑定日期')) or '(空)'}")
        lines.append(f"    解绑日期: {normalize_text(fields.get('解绑日期')) or '(空)'}")
        lines.append(f"    推荐状态: {normalize_text(fields.get('推荐状态')) or '(空)'}")
        lines.append(f"    record_id: {item.get('record_id', '')}")
        lines.append("-" * 60)

    mismatch_txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_all_mismatched(mismatched: List[Dict[str, Any]]) -> None:
    """在控制台完整打印全部不一致记录。"""
    if not mismatched:
        print("\n无不一致记录。")
        return

    print(f"\n=== 不一致记录详情（共 {len(mismatched)} 条） ===")
    for index, item in enumerate(mismatched, start=1):
        fields = item.get("fields") or {}
        print(f"[{index}] 学员={item.get('student_wechat')}")
        print(f"      表1推荐人={item.get('actual_referrer') or '(空)'}")
        print(f"      表2新推荐人={item.get('expected_referrer') or '(空)'}")
        print(
            f"      报名={normalize_text(fields.get('报名状态')) or '(空)'} "
            f"绑定={normalize_text(fields.get('绑定状态')) or '(空)'} "
            f"绑定日期={normalize_text(fields.get('绑定日期')) or '(空)'}"
        )
        print(f"      record_id={item.get('record_id')}")


def export_mismatch_details(
    mismatched: List[Dict[str, Any]],
    summary: Dict[str, Any],
    mismatch_json_path: Path,
    mismatch_txt_path: Path,
) -> None:
    """把不一致结果单独写入 JSON 与 TXT，便于直接查看。"""
    mismatch_json_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "summary": {
            "checked_at": summary.get("checked_at"),
            "mismatched_count": len(mismatched),
        },
        "mismatched": mismatched,
    }
    with mismatch_json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    lines: List[str] = []
    lines.append("飞书推荐人不一致明细")
    lines.append(f"检查时间: {summary.get('checked_at')}")
    lines.append(f"不一致条数: {len(mismatched)}")
    lines.append("=" * 60)

    for index, item in enumerate(mismatched, start=1):
        fields = item.get("fields") or {}
        lines.append(f"[{index}] 学员微信号: {item.get('student_wechat', '')}")
        lines.append(f"    表1推荐人总微信号: {item.get('actual_referrer') or '(空)'}")
        lines.append(f"    表2新推荐人微信号: {item.get('expected_referrer') or '(空)'}")
        lines.append(f"    报名状态: {normalize_text(fields.get('报名状态')) or '(空)'}")
        lines.append(f"    绑定状态: {normalize_text(fields.get('绑定状态')) or '(空)'}")
        lines.append(f"    绑定日期: {normalize_text(fields.get('绑定日期')) or '(空)'}")
        lines.append(f"    解绑日期: {normalize_text(fields.get('解绑日期')) or '(空)'}")
        lines.append(f"    推荐状态: {normalize_text(fields.get('推荐状态')) or '(空)'}")
        lines.append(f"    record_id: {item.get('record_id', '')}")
        lines.append("-" * 60)

    mismatch_txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_mismatch_details(mismatched: List[Dict[str, Any]]) -> None:
    """在控制台完整打印不一致明细。"""
    if not mismatched:
        print("\n--- 不一致记录：无 ---")
        return

    print(f"\n--- 不一致记录（共 {len(mismatched)} 条） ---")
    for index, item in enumerate(mismatched, start=1):
        fields = item.get("fields") or {}
        print(f"[{index}] 学员={item.get('student_wechat')}")
        print(f"      表1推荐人={item.get('actual_referrer') or '(空)'}")
        print(f"      表2新推荐人={item.get('expected_referrer') or '(空)'}")
        print(f"      报名状态={normalize_text(fields.get('报名状态')) or '(空)'}")
        print(f"      绑定状态={normalize_text(fields.get('绑定状态')) or '(空)'}")
        print(f"      record_id={item.get('record_id')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="检查飞书两张表的推荐人微信号是否一致")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_FILE.name),
        help="配置文件路径，默认 check_feishu_referrer.config.json",
    )
    parser.add_argument(
        "--preview",
        type=int,
        default=None,
        help="控制台额外打印前 N 条不一致记录（覆盖配置文件）",
    )
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    cfg = merge_config(config_path)

    feishu_cfg = cfg["飞书"]
    table1_cfg = cfg["表1_意向学员绑定"]
    table2_cfg = cfg["表2_推荐人变更对照"]
    check_cfg = cfg["检查"]
    path_cfg = cfg["路径"]

    required = [
        feishu_cfg.get("app_id"),
        feishu_cfg.get("app_secret"),
        feishu_cfg.get("app_token"),
        table1_cfg.get("table_id"),
        table2_cfg.get("table_id"),
    ]
    if not all(required):
        print("缺少必要配置：飞书 app_id/app_secret/app_token 或 table_id")
        return 1

    report_path = resolve_path(path_cfg.get("report_file") or "logs/check_feishu_referrer_report.json")
    log_path = resolve_path(path_cfg.get("log_file") or "logs/check_feishu_referrer.log")
    mismatch_json_path = resolve_path(
        path_cfg.get("mismatch_file") or "logs/check_feishu_referrer_mismatched.json"
    )
    mismatch_txt_path = resolve_path(
        path_cfg.get("mismatch_txt_file") or "logs/check_feishu_referrer_mismatched.txt"
    )
    mismatch_json_path = resolve_path(
        path_cfg.get("mismatch_file") or "logs/check_feishu_referrer_mismatched.json"
    )
    mismatch_txt_path = resolve_path(
        path_cfg.get("mismatch_txt_file") or "logs/check_feishu_referrer_mismatched.txt"
    )
    preview_limit = args.preview
    if preview_limit is None:
        preview_limit = int(check_cfg.get("preview_mismatch_limit") or 20)
    ignore_empty_referrer = bool(check_cfg.get("ignore_empty_referrer", False))
    filter_cfg = check_cfg.get("过滤") or {
        "enabled": True,
        "enrollment_status_field": "报名状态",
        "bind_status_field": "绑定状态",
        "skip_enrollment_values": ["未报名"],
        "skip_bind_values": ["无绑定"],
    }
    match_cfg = check_cfg.get("推荐人比对") or {
        "alternate_fields": ["推荐人微信号", "推荐人微信原始ID"],
        "ignore_custom_alias_mismatch": True,
    }

    student_field_t1 = table1_cfg.get("student_wechat_field") or "意向学员总微信号"
    referrer_field_t1 = table1_cfg.get("referrer_wechat_field") or "推荐人总微信号"
    student_field_t2 = table2_cfg.get("student_wechat_field") or "学员微信号"
    new_referrer_field_t2 = table2_cfg.get("new_referrer_wechat_field") or "新推荐人微信号"

    feishu_mod = load_feishu_module()

    print("=" * 60)
    print("飞书推荐人一致性检查")
    print(f"  表1 table_id : {table1_cfg['table_id']}")
    print(f"  表2 table_id : {table2_cfg['table_id']}")
    print(f"  比对规则     : 表1[{student_field_t1}] + 表1[{referrer_field_t1}]")
    print(f"                 vs 表2[{student_field_t2}] -> 表2[{new_referrer_field_t2}]")
    if filter_cfg.get("enabled", True):
        print(
            "  过滤规则     : 跳过 "
            f"{filter_cfg.get('enrollment_status_field', '报名状态')}="
            f"{','.join(filter_cfg.get('skip_enrollment_values') or ['未报名'])}"
            f" 或 {filter_cfg.get('bind_status_field', '绑定状态')}="
            f"{','.join(filter_cfg.get('skip_bind_values') or ['无绑定'])}"
        )
    if match_cfg.get("ignore_custom_alias_mismatch", True):
        print("  别名容忍     : 表2为wxid且与表1原始ID一致，或双方均为自定义微信号时忽略")
    print("=" * 60)

    print("[1/3] 获取 tenant_access_token ...")
    token = feishu_mod.get_tenant_access_token(feishu_cfg["app_id"], feishu_cfg["app_secret"])

    print("[2/3] 拉取表2（推荐人变更对照） ...")
    table2_records = fetch_all_records(
        feishu_mod, token, feishu_cfg["app_token"], table2_cfg["table_id"]
    )
    reference_map, reference_record_map, duplicate_overwritten = build_reference_map(
        table2_records, student_field_t2, new_referrer_field_t2
    )
    print(
        f"      表2共 {len(table2_records)} 条，有效对照 {len(reference_map)} 个学员"
        f"（同一学员多条时取最后一条）"
    )

    print("[3/3] 拉取表1（意向学员绑定）并比对 ...")
    table1_records = fetch_all_records(
        feishu_mod, token, feishu_cfg["app_token"], table1_cfg["table_id"]
    )
    compare_result = compare_tables(
        table1_records,
        reference_map,
        student_field_t1,
        referrer_field_t1,
        ignore_empty_referrer=ignore_empty_referrer,
        filter_cfg=filter_cfg,
        match_cfg=match_cfg,
    )

    checked_count = (
        len(compare_result["matched"])
        + len(compare_result["ignored_alias_mismatch"])
        + len(compare_result["mismatched"])
        + len(compare_result["missing_reference"])
        + len(compare_result["skipped_empty"])
    )

    summary = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "table1_total": len(table1_records),
        "table1_checked_count": checked_count,
        "table1_skipped_by_filter_count": len(compare_result["skipped_by_filter"]),
        "table2_total": len(table2_records),
        "reference_student_count": len(reference_map),
        "matched_count": len(compare_result["matched"]),
        "ignored_alias_mismatch_count": len(compare_result["ignored_alias_mismatch"]),
        "mismatched_count": len(compare_result["mismatched"]),
        "missing_reference_count": len(compare_result["missing_reference"]),
        "skipped_empty_student_count": len(compare_result["skipped_empty"]),
        "only_in_reference_count": len(compare_result["only_in_reference"]),
        "duplicate_overwritten_count": len(duplicate_overwritten),
    }

    report = {
        "summary": summary,
        "config": {
            "table1": table1_cfg,
            "table2": table2_cfg,
            "ignore_empty_referrer": ignore_empty_referrer,
            "filter": filter_cfg,
            "referrer_match": match_cfg,
            "reference_rule": "同一学员在表2有多条记录时，以最后一条为准",
        },
        "duplicate_overwritten": duplicate_overwritten,
        "reference_record_map": reference_record_map,
        "ignored_alias_mismatch": compare_result["ignored_alias_mismatch"],
        "mismatched": compare_result["mismatched"],
        "missing_reference": compare_result["missing_reference"],
        "only_in_reference": compare_result["only_in_reference"],
        "skipped_empty_student": compare_result["skipped_empty"],
        "skipped_by_filter_count": len(compare_result["skipped_by_filter"]),
        "skipped_by_filter_preview": compare_result["skipped_by_filter"][:preview_limit],
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    export_mismatch_results(
        compare_result["mismatched"],
        summary,
        mismatch_json_path,
        mismatch_txt_path,
    )

    print("\n=== 检查结果摘要 ===")
    print(f"表1记录总数           : {summary['table1_total']}")
    print(f"表1参与检查数         : {summary['table1_checked_count']}")
    print(f"表1过滤跳过数         : {summary['table1_skipped_by_filter_count']}")
    print(f"表2记录总数           : {summary['table2_total']}")
    print(f"表2有效学员对照数     : {summary['reference_student_count']}")
    print(f"一致                  : {summary['matched_count']}")
    print(f"别名差异已忽略        : {summary['ignored_alias_mismatch_count']}")
    print(f"不一致                : {summary['mismatched_count']}")
    print(f"表1有记录但表2无对照  : {summary['missing_reference_count']}")
    print(f"表1学员微信号为空跳过 : {summary['skipped_empty_student_count']}")
    print(f"表2有但表1未出现      : {summary['only_in_reference_count']}")
    print(f"表2多条覆盖（取最后） : {summary['duplicate_overwritten_count']}")
    print(f"\n详细报告：{report_path}")
    print(f"不一致 JSON：{mismatch_json_path}")
    print(f"不一致 TXT ：{mismatch_txt_path}")

    log_lines = [
        "[INFO] "
        + json.dumps(summary, ensure_ascii=False),
    ]
    write_log(log_path, log_lines)

    if duplicate_overwritten:
        print(f"\n--- 表2同一学员多条，已取最后一条（前 {preview_limit} 条） ---")
        for item in duplicate_overwritten[:preview_limit]:
            print(
                f"  学员={item['student_wechat']} "
                f"前值={item['previous_new_referrer'] or '(空)'} "
                f"最终={item['final_new_referrer'] or '(空)'} "
                f"final_record_id={item['final_record_id']}"
            )

    if compare_result["mismatched"]:
        print_all_mismatched(compare_result["mismatched"])

    if compare_result["missing_reference"] and preview_limit > 0:
        print(f"\n--- 表2无对照（前 {min(preview_limit, len(compare_result['missing_reference']))} 条） ---")
        for item in compare_result["missing_reference"][:preview_limit]:
            print(
                f"  学员={item['student_wechat']} "
                f"表1推荐人={item['actual_referrer'] or '(空)'} "
                f"record_id={item['record_id']}"
            )

    return 1 if summary["mismatched_count"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(130)
    except Exception as exc:
        print(f"[error] {exc}")
        sys.exit(1)
