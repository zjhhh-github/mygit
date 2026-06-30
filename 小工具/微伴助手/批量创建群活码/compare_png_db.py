# -*- coding: utf-8 -*-
"""对比 Downloads 中专属带领群 PNG 与 contact 数据库记录。"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

DOWNLOADS_DIR = Path(r"C:\Users\LENOVO\Desktop\专属带领群二维码")
DB_PATH = Path(r"C:\Users\LENOVO\Desktop\contact_内部专用.db")
REPORT_PATH = Path(__file__).resolve().parent / "png_db_compare.txt"
PREFIX = "专属带领群"


def normalize_name(name: str) -> str:
    """归一化名称：去空格，统一特殊字符，便于对比文件名与数据库。"""
    text = re.sub(r"\s+", "", name.strip())
    text = text.replace("???", "¡¡¡")
    return text.lower()


def fetch_db_names() -> list[str]:
    """从数据库读取专属带领群名称列表。

    同时匹配 nick_name 和 remark 以「专属带领群」开头的记录。
    nick_name 有值时用 nick_name，为空时用 remark 代替，去重后返回。
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT nick_name, remark FROM contact
            WHERE nick_name LIKE ? OR remark LIKE ?
            """,
            (PREFIX + "%", PREFIX + "%"),
        ).fetchall()
    finally:
        conn.close()

    seen: set[str] = set()
    result: list[str] = []
    for nick_name, remark in rows:
        name = (nick_name or "").strip() or (remark or "").strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return sorted(result)


def fetch_png_stems() -> list[str]:
    """读取 Downloads 下专属带领群 PNG 文件名（不含扩展名）。"""
    stems: list[str] = []
    for file_path in DOWNLOADS_DIR.iterdir():
        if (
            file_path.is_file()
            and file_path.suffix.lower() == ".png"
            and file_path.name.startswith(PREFIX)
        ):
            stems.append(file_path.stem)
    return sorted(stems)


def main() -> int:
    db_names = fetch_db_names()
    png_stems = fetch_png_stems()

    db_set = set(db_names)
    png_set = set(png_stems)
    db_norm = {normalize_name(name): name for name in db_names}
    png_norm = {normalize_name(name): name for name in png_stems}

    exact_db_not_png = sorted(db_set - png_set)
    exact_png_not_db = sorted(png_set - db_set)
    norm_db_not_png = sorted(set(db_norm) - set(png_norm))
    norm_png_not_db = sorted(set(png_norm) - set(db_norm))

    fully_matched = (
        not norm_db_not_png
        and not norm_png_not_db
        and len(png_stems) == len(db_names)
    )

    lines = [
        "=== 专属带领群 PNG 与数据库对比 ===",
        f"数据库记录数: {len(db_names)}",
        f"Downloads PNG 数: {len(png_stems)}",
        f"精确字符串匹配数: {len(db_set & png_set)}",
        f"归一化后匹配数: {len(set(db_norm) & set(png_norm))}",
        "",
        f"结论: {'完全匹配' if fully_matched else '不完全匹配'}",
        "",
    ]

    if exact_db_not_png:
        lines.append(f"--- 数据库有、Downloads 缺 PNG（{len(exact_db_not_png)} 条）---")
        lines.extend(exact_db_not_png)
        lines.append("")

    if exact_png_not_db:
        lines.append(f"--- Downloads 有、数据库无（{len(exact_png_not_db)} 条）---")
        lines.extend(exact_png_not_db)
        lines.append("")

    if norm_db_not_png:
        lines.append(f"--- 归一化后仍缺 PNG（{len(norm_db_not_png)} 条）---")
        lines.extend(db_norm[key] for key in norm_db_not_png)
        lines.append("")

    if norm_png_not_db:
        lines.append(f"--- 归一化后多余 PNG（{len(norm_png_not_db)} 条）---")
        lines.extend(png_norm[key] for key in norm_png_not_db)
        lines.append("")

    if exact_db_not_png and not norm_db_not_png:
        lines.append("说明: 精确文件名与数据库略有字符差异（如空格、???/¡¡¡），归一化后已一一对应。")

    report_text = "\n".join(lines)
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    print(report_text)
    print()
    print(f"报告已保存: {REPORT_PATH}")
    return 0 if fully_matched else 1


if __name__ == "__main__":
    raise SystemExit(main())
