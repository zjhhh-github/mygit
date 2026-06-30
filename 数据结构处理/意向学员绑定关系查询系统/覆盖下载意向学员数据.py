import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


# 目标文件：本脚本会覆盖这个下载文件，所以执行前会先自动生成备份。
DOWNLOAD_FILE = Path(r"C:\Users\LENOVO\Downloads\意向学员数据_2026-05-19 (1).json")

# 数据来源：由桌面 txt 重新解析得到的最新推荐关系数据。
PARSED_FILE = Path(r"C:\Users\LENOVO\Desktop\_脚本输出_1_解析结果.json")

# 通讯录数据库：用于把推荐人的 username / alias 统一成同一个联系人。
CONTACT_DB_FILE = Path(r"C:\Users\LENOVO\Desktop\contact.db")


def load_json(file_path: Path):
    """读取 JSON 文件，并兼容带 BOM 的 UTF-8 文件。"""
    if not file_path.exists():
        raise FileNotFoundError(f"找不到文件：{file_path}")

    with file_path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def load_contact_alias_map(db_file: Path):
    """
    读取 contact 表，建立推荐人标准化映射。

    规则：
    - contact.username 写入“来源微信原始ID”
    - contact.alias 写入“来源微信号”
    - 如果源数据里出现的是 username 或 alias，都能匹配到同一个联系人
    """
    if not db_file.exists():
        raise FileNotFoundError(f"找不到通讯录数据库：{db_file}")

    contact_map = {}

    with sqlite3.connect(str(db_file)) as connection:
        cursor = connection.cursor()
        rows = cursor.execute(
            """
            SELECT username, alias
            FROM contact
            WHERE username IS NOT NULL
              AND TRIM(username) != ''
            """
        ).fetchall()

    for username, alias in rows:
        username = (username or "").strip()
        alias = (alias or "").strip()

        if not username:
            continue

        contact_info = {
            "来源微信原始ID": username,
            "来源微信号": alias,
        }

        # username 和 alias 都指向同一份联系人资料，便于统一输出。
        contact_map[username] = contact_info
        if alias:
            contact_map[alias] = contact_info

    return contact_map


def normalize_source(referrer: dict, contact_map: dict):
    """
    将解析结果中的“推荐”对象转换成下载文件需要的“来源”对象。

    如果能在 contact.db 中匹配到联系人：
    - 来源微信原始ID = username
    - 来源微信号 = alias

    如果没有匹配到联系人：
    - 保留解析结果中的推荐人字段，避免未匹配数据丢失
    """
    referrer_wechat = (referrer.get("推荐人总微信号") or "").strip()
    referrer_original_id = (referrer.get("推荐人微信原始ID") or "").strip()

    contact_info = contact_map.get(referrer_wechat) or contact_map.get(referrer_original_id)

    if contact_info:
        source_wechat = contact_info["来源微信号"] or referrer_wechat
        source_original_id = contact_info["来源微信原始ID"]
    else:
        source_wechat = referrer_wechat
        source_original_id = referrer_original_id

    return {
        "来源微信号": source_wechat,
        "来源微信原始ID": source_original_id,
        "绑定日期": (referrer.get("绑定日期") or "").strip(),
        "解绑日期": (referrer.get("解绑日期") or "").strip(),
        "绑定状态": (referrer.get("绑定状态") or "").strip(),
    }


def convert_parsed_data(parsed_data, contact_map: dict):
    """
    把桌面解析结果转换回下载文件原来的字段结构。

    字段映射：
    - 推荐 -> 来源
    - 推荐人总微信号 -> 来源微信号
    - 推荐人微信原始ID -> 来源微信原始ID
    """
    converted_data = []
    source_count = 0

    for student in parsed_data:
        sources = []

        for referrer in student.get("推荐", []) or []:
            source = normalize_source(referrer, contact_map)
            sources.append(source)
            source_count += 1

        converted_data.append(
            {
                "意向学员微信号": (student.get("意向学员微信号") or "").strip(),
                "意向学员微信原始ID": (student.get("意向学员微信原始ID") or "").strip(),
                "是否报名": (student.get("是否报名") or "").strip(),
                "来源": sources,
            }
        )

    return converted_data, source_count


def backup_file(file_path: Path):
    """覆盖前备份原文件，避免误操作后无法恢复。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = file_path.with_name(f"{file_path.stem}_覆盖前备份_{timestamp}{file_path.suffix}")
    shutil.copy2(file_path, backup_path)
    return backup_path


def main():
    """脚本入口：备份下载文件，并用解析后的最新数据覆盖写回。"""
    parsed_data = load_json(PARSED_FILE)
    contact_map = load_contact_alias_map(CONTACT_DB_FILE)
    converted_data, source_count = convert_parsed_data(parsed_data, contact_map)

    backup_path = backup_file(DOWNLOAD_FILE)

    with DOWNLOAD_FILE.open("w", encoding="utf-8") as file:
        json.dump(converted_data, file, ensure_ascii=False, indent=2)

    print("覆盖完成")
    print(f"备份文件：{backup_path}")
    print(f"覆盖文件：{DOWNLOAD_FILE}")
    print(f"写入意向学员数量：{len(converted_data)}")
    print(f"写入来源关系数量：{source_count}")
    print(f"通讯录可匹配键数量：{len(contact_map)}")


if __name__ == "__main__":
    main()
