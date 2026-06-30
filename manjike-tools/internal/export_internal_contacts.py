#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从一个或多个微信 contact.db 导出内部通讯录 JSON。

【数据来源】
    contact 表，筛选内部备注格式（¿¿¿ / !!! + 6 位数字 + - + 尾段），
    排除尾段为「空」「删除」的记录。支持多库读取并按「总微信号」去重合并。

【字段映射】（与 SQL 列一致）
    remark   → 内部备注
    username → 微信ID
    alias    → 微信号
    另生成「总微信号」：alias 非空用 alias，否则用 username（供 upload_internal 使用）

【输出】
    JSON 数组，可直接作为 upload_internal.py 的 source_file。

【配置】同目录 export_internal.config.json

CLI：
    python export_internal_contacts.py
    python export_internal_contacts.py --db "a.db;b.db" --out 内部通讯录.json
    python export_internal_contacts.py --dry-run
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "export_internal.config.json"

# 与「内部备注导入.py」一致的备注尾部清洗
_NON_SUFFIX_RE = re.compile(r"[（(]非[）)]\s*$")

CONTACT_QUERY_SQL = """
    SELECT remark, username, alias
    FROM contact
    WHERE (
            remark GLOB '¿¿¿[0-9][0-9][0-9][0-9][0-9][0-9]-*'
            OR
            remark GLOB '!!![0-9][0-9][0-9][0-9][0-9][0-9]-*'
          )
      AND TRIM(SUBSTR(remark, 11)) NOT IN ('空', '删除')
    ORDER BY remark;
"""

DEFAULT_CONFIG: Dict[str, Any] = {
    "数据源": {
        "db_path": (
            r"C:\Users\LENOVO\Desktop\contact_内部专用.db;"
            r"C:\Users\LENOVO\Desktop\contact_内部专用2.db"
        ),
        "db_paths": [
            r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
            r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
        ],
        "backup_root": r"X:\chatlog_backup",
        "folder_prefix": "wxid_42272spv9uq522_6ded_",
    },
    "导出": {
        "output_file": r"C:\Users\LENOVO\Desktop\内部通讯录.json",
        "clean_remark_suffix": True,
        "indent": 2,
    },
}


def _ensure_utf8_stdio() -> None:
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def deep_merge(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not override:
        return dict(base)
    result: Dict[str, Any] = {}
    for key, base_value in base.items():
        if key in override and not (isinstance(key, str) and key.startswith("_")):
            user_value = override[key]
            if isinstance(base_value, dict) and isinstance(user_value, dict):
                result[key] = deep_merge(base_value, user_value)
            else:
                result[key] = user_value
        else:
            result[key] = base_value if not isinstance(base_value, dict) else dict(base_value)
    for key, user_value in override.items():
        if isinstance(key, str) and key.startswith("_"):
            continue
        if key not in result:
            result[key] = user_value
    return result


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return dict(DEFAULT_CONFIG)
    user_cfg = json.loads(config_path.read_text(encoding="utf-8"))
    return deep_merge(DEFAULT_CONFIG, user_cfg)


def resolve_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (SCRIPT_DIR / p).resolve()


def clean_remark(remark: str, enabled: bool) -> str:
    if not remark:
        return remark
    s = remark.strip()
    if enabled:
        s = _NON_SUFFIX_RE.sub("", s).rstrip()
    return s


def _normalize_db_path_templates(value: Union[str, List[str]]) -> List[str]:
    """把单路径、分号/竖线分隔串或路径列表统一成去重后的模板列表。"""
    if isinstance(value, list):
        raw_parts = value
    else:
        raw_parts = re.split(r"[;|]", str(value or ""))
    paths: List[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        p = str(part or "").strip()
        if not p:
            continue
        key = os.path.normcase(os.path.abspath(p)) if "最新日期" not in p else p
        if key in seen:
            continue
        seen.add(key)
        paths.append(p)
    return paths


def _record_score(record: Dict[str, str]) -> int:
    """字段越完整得分越高，用于多库合并时保留更全的一条。"""
    return (
        len(record.get("内部备注") or "")
        + len(record.get("微信号") or "")
        + len(record.get("总微信号") or "")
    )


def resolve_contact_db(
    db_path_template: str,
    backup_root: str,
    folder_prefix: str,
) -> Path:
    """解析 contact.db 路径；模板含「最新日期」时自动选最新备份目录。"""
    if "最新日期" not in db_path_template:
        return Path(db_path_template)

    marker = "最新日期"
    before, after = db_path_template.split(marker, 1)
    # before 形如 X:\chatlog_backup\wxid_42272spv9uq522_6ded_
    before_path = Path(before)
    root = Path(backup_root) if backup_root else before_path.parent
    prefix = folder_prefix or before_path.name

    if not root.is_dir():
        raise FileNotFoundError(f"备份根目录不存在：{root}")

    candidates = [
        d for d in root.iterdir()
        if d.is_dir() and d.name.startswith(prefix)
    ]
    if not candidates:
        raise FileNotFoundError(
            f"在 {root} 下未找到以 {prefix!r} 开头的备份目录"
        )

    # 优先按目录名排序（通常含日期后缀），再按修改时间兜底
    latest_dir = max(candidates, key=lambda d: (d.name, d.stat().st_mtime))
    db_file = latest_dir / after.lstrip("\\/")
    if not db_file.is_file():
        raise FileNotFoundError(f"未找到 contact.db：{db_file}")
    return db_file


def resolve_contact_db_paths(
    db_path_templates: Union[str, List[str]],
    backup_root: str,
    folder_prefix: str,
) -> List[Path]:
    """解析一个或多个 contact.db 路径。"""
    return [
        resolve_contact_db(template, backup_root, folder_prefix)
        for template in _normalize_db_path_templates(db_path_templates)
    ]


def resolve_db_templates_from_config(
    src_cfg: Dict[str, Any],
    db_override: Optional[str] = None,
) -> List[str]:
    """从 CLI 覆盖项或配置中解析数据库模板列表。"""
    if db_override:
        return _normalize_db_path_templates(db_override)
    if src_cfg.get("db_paths"):
        return _normalize_db_path_templates(src_cfg["db_paths"])
    if src_cfg.get("db_path"):
        return _normalize_db_path_templates(src_cfg["db_path"])
    raise ValueError("未配置数据库路径（db_paths / db_path）")


def fetch_internal_contacts(db_file: Path, clean_suffix: bool) -> List[Dict[str, str]]:
    if not db_file.is_file():
        raise FileNotFoundError(f"数据库不存在：{db_file}")

    conn = sqlite3.connect(str(db_file))
    conn.text_factory = str
    try:
        cur = conn.cursor()
        cur.execute(CONTACT_QUERY_SQL)
        rows = cur.fetchall()
    finally:
        conn.close()

    records: List[Dict[str, str]] = []
    for remark, username, alias in rows:
        remark_val = clean_remark(remark or "", clean_suffix)
        username_val = (username or "").strip()
        alias_val = (alias or "").strip()
        total_wechat = alias_val if alias_val else username_val
        records.append({
            "内部备注": remark_val,
            "微信ID": username_val,
            "微信号": alias_val,
            "总微信号": total_wechat,
        })
    return records


def merge_internal_contact_records(
    record_lists: List[List[Dict[str, str]]],
) -> List[Dict[str, str]]:
    """按「总微信号」合并多库记录，冲突时保留字段更完整的一条。"""
    merged: Dict[str, Dict[str, str]] = {}
    for records in record_lists:
        for record in records:
            key = record.get("总微信号") or record.get("微信ID") or record.get("微信号")
            if not key:
                continue
            existing = merged.get(key)
            if existing is None or _record_score(record) >= _record_score(existing):
                merged[key] = record
    return sorted(merged.values(), key=lambda item: item.get("内部备注") or "")


def export_to_json(
    records: List[Dict[str, str]],
    output_file: Path,
    indent: int,
) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=indent)
        f.write("\n")


def run_pipeline(
    config_path: Optional[Path] = None,
    db_override: Optional[str] = None,
    out_override: Optional[str] = None,
    dry_run: bool = False,
) -> int:
    _ensure_utf8_stdio()
    config = load_config(config_path)
    src_cfg = config["数据源"]
    export_cfg = config["导出"]

    db_templates = resolve_db_templates_from_config(src_cfg, db_override=db_override)
    db_files = resolve_contact_db_paths(
        db_path_templates=db_templates,
        backup_root=str(src_cfg.get("backup_root") or ""),
        folder_prefix=str(src_cfg.get("folder_prefix") or ""),
    )
    if not db_files:
        raise ValueError("未提供有效的数据库路径")

    output_file = resolve_path(out_override or export_cfg["output_file"])
    clean_suffix = bool(export_cfg.get("clean_remark_suffix", True))
    indent = int(export_cfg.get("indent", 2))

    print("=" * 60)
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 内部通讯录导出")
    print(f"  数据源     : {len(db_files)} 个数据库")
    for db_file in db_files:
        print(f"    - {db_file}")
    print(f"  输出文件   : {output_file}")
    print(f"  清洗(非)尾 : {clean_suffix}")
    print("=" * 60)

    per_db_records: List[List[Dict[str, str]]] = []
    for db_file in db_files:
        records = fetch_internal_contacts(db_file, clean_suffix)
        print(f"  {db_file.name}: {len(records)} 条")
        per_db_records.append(records)
    records = merge_internal_contact_records(per_db_records)
    print(f"合并后 {len(records)} 条记录")

    if dry_run:
        print("dry-run：不写文件。前 3 条示例：")
        print(json.dumps(records[:3], ensure_ascii=False, indent=2))
        return 0

    export_to_json(records, output_file, indent)
    size_kb = output_file.stat().st_size / 1024
    print(f"导出完成：{output_file}（{size_kb:.1f} KB，{len(records)} 条）")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 contact.db 导出内部通讯录 JSON")
    parser.add_argument("--config", type=str, default=None, help="配置文件路径")
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="覆盖 db_path / db_paths（可含「最新日期」，多库用分号分隔）",
    )
    parser.add_argument("--out", type=str, default=None, help="覆盖 output_file")
    parser.add_argument("--dry-run", action="store_true", help="只查询不写文件")
    return parser.parse_args()


def _cli_main() -> int:
    args = _parse_args()
    try:
        return run_pipeline(
            config_path=Path(args.config) if args.config else None,
            db_override=args.db,
            out_override=args.out,
            dry_run=args.dry_run,
        )
    except FileNotFoundError as ex:
        print(f"[ERROR] {ex}", file=sys.stderr)
        return 2
    except sqlite3.Error as ex:
        print(f"[ERROR] SQLite：{ex}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    try:
        sys.exit(_cli_main())
    except KeyboardInterrupt:
        print("\n[CANCELLED]")
        sys.exit(130)
