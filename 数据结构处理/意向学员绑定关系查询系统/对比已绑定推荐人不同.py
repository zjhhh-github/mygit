import json
import sqlite3
from pathlib import Path


# 文件 A：通常是从下载目录拿到的现有意向学员数据。
DOWNLOAD_FILE = Path(r"C:\Users\LENOVO\Downloads\意向学员数据_2026-05-19 (1).json")

# 文件 B：由桌面 txt 解析生成的新结果。
PARSED_FILE = Path(r"C:\Users\LENOVO\Desktop\_脚本输出_1_解析结果.json")

# 微信通讯录数据库：用于判断 username 和 alias 是否属于同一个推荐人。
CONTACT_DB_FILE = Path(r"C:\Users\LENOVO\Desktop\contact.db")

# 输出文件：只保存“两边都已绑定，但是推荐人微信号不同”的学员。
OUTPUT_FILE = Path(r"C:\Users\LENOVO\Desktop\意向学员_已绑定但推荐人不同_对比结果.json")


def load_json(file_path: Path):
    """读取 JSON 文件，并兼容带 BOM 的 UTF-8 文件。"""
    if not file_path.exists():
        raise FileNotFoundError(f"找不到文件：{file_path}")

    with file_path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def build_student_map(data):
    """
    按“意向学员微信号”建立索引。

    如果同一个微信号重复出现，后出现的数据会覆盖前面的数据。
    当前两个文件都是列表结构，正常情况下每个意向学员只会出现一次。
    """
    result = {}

    for item in data:
        student_wechat = (item.get("意向学员微信号") or "").strip()
        if student_wechat:
            result[student_wechat] = item

    return result


def load_contact_alias_map(db_file: Path):
    """
    读取 contact 表中的 username 和 alias，建立推荐人标准化映射。

    规则：
    - username 视为“推荐人微信原始ID”
    - alias 视为“推荐人总微信号”
    - 如果某个推荐人在一个文件里用 username 表示、另一个文件里用 alias 表示，
      这里会把它们识别为同一个推荐人，避免误判为不同人。
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
            "标准比较键": username,
            "推荐人微信原始ID": username,
            "推荐人总微信号": alias,
        }

        # username 和 alias 都映射到同一份标准联系人信息。
        contact_map[username] = contact_info
        if alias:
            contact_map[alias] = contact_info

    return contact_map


def normalize_referrer(referrer_wechat: str, referrer_original_id: str, contact_map: dict):
    """
    标准化推荐人信息。

    优先使用 contact 表匹配：
    - 匹配成功：推荐人微信原始ID 写 username，推荐人总微信号写 alias
    - 匹配失败：保留原文件里的微信号和原始 ID，避免丢失无法匹配的数据
    """
    referrer_wechat = (referrer_wechat or "").strip()
    referrer_original_id = (referrer_original_id or "").strip()

    contact_info = contact_map.get(referrer_wechat) or contact_map.get(referrer_original_id)
    if contact_info:
        total_wechat = contact_info["推荐人总微信号"] or referrer_wechat
        original_id = contact_info["推荐人微信原始ID"]
        return {
            "标准比较键": contact_info["标准比较键"],
            "推荐人总微信号": total_wechat,
            "推荐人微信原始ID": original_id,
        }

    # 数据库中找不到时，用文件原始值兜底；标准比较键优先使用微信号。
    return {
        "标准比较键": referrer_wechat or referrer_original_id,
        "推荐人总微信号": referrer_wechat,
        "推荐人微信原始ID": referrer_original_id,
    }


def get_active_referrers(item, list_key: str, wechat_key: str, original_id_key: str, contact_map: dict):
    """
    提取某个学员当前处于“有绑定”的推荐人列表。

    参数说明：
    - list_key：下载文件中是“来源”，解析结果中是“推荐”
    - wechat_key：推荐人微信号字段名
    - original_id_key：推荐人微信原始 ID 字段名
    """
    active_referrers = []

    for referrer in item.get(list_key, []) or []:
        bind_status = (referrer.get("绑定状态") or "").strip()
        referrer_wechat = (referrer.get(wechat_key) or "").strip()
        referrer_original_id = (referrer.get(original_id_key) or "").strip()

        if bind_status != "有绑定" or not referrer_wechat:
            continue

        normalized_referrer = normalize_referrer(referrer_wechat, referrer_original_id, contact_map)

        active_referrers.append(
            {
                "标准比较键": normalized_referrer["标准比较键"],
                "推荐人总微信号": normalized_referrer["推荐人总微信号"],
                "推荐人微信原始ID": normalized_referrer["推荐人微信原始ID"],
                "绑定日期": (referrer.get("绑定日期") or "").strip(),
                "解绑日期": (referrer.get("解绑日期") or "").strip(),
                "绑定状态": bind_status,
            }
        )

    return active_referrers


def compare_active_referrers(download_data, parsed_data, contact_map: dict):
    """
    对比两个文件中“已绑定”的推荐人是否一致。

    只输出两边都有同一个意向学员、且两边都存在“有绑定”推荐人、
    但推荐人微信号集合不一致的记录。
    """
    download_map = build_student_map(download_data)
    parsed_map = build_student_map(parsed_data)
    common_students = sorted(set(download_map) & set(parsed_map))

    diff_results = []

    for student_wechat in common_students:
        download_referrers = get_active_referrers(
            download_map[student_wechat],
            list_key="来源",
            wechat_key="来源微信号",
            original_id_key="来源微信原始ID",
            contact_map=contact_map,
        )
        parsed_referrers = get_active_referrers(
            parsed_map[student_wechat],
            list_key="推荐",
            wechat_key="推荐人总微信号",
            original_id_key="推荐人微信原始ID",
            contact_map=contact_map,
        )

        download_wechat_set = {item["标准比较键"] for item in download_referrers}
        parsed_wechat_set = {item["标准比较键"] for item in parsed_referrers}

        # “推荐人不同”限定为：两边都有已绑定推荐人，并且经过 contact 表标准化后仍不一致。
        if download_wechat_set and parsed_wechat_set and download_wechat_set != parsed_wechat_set:
            diff_results.append(
                {
                    "意向学员微信号": student_wechat,
                    "下载文件_已绑定来源": download_referrers,
                    "桌面解析结果_已绑定推荐": parsed_referrers,
                    "仅下载文件有": sorted(download_wechat_set - parsed_wechat_set),
                    "仅桌面解析结果有": sorted(parsed_wechat_set - download_wechat_set),
                }
            )

    stats = {
        "下载文件学员数量": len(download_map),
        "桌面解析结果学员数量": len(parsed_map),
        "两边都有的学员数量": len(common_students),
        "已绑定但推荐人不同数量": len(diff_results),
        "通讯录可匹配键数量": len(contact_map),
    }

    return diff_results, stats


def main():
    """脚本入口：读取两个 JSON，输出推荐人不同的学员清单。"""
    download_data = load_json(DOWNLOAD_FILE)
    parsed_data = load_json(PARSED_FILE)
    contact_map = load_contact_alias_map(CONTACT_DB_FILE)

    diff_results, stats = compare_active_referrers(download_data, parsed_data, contact_map)

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(diff_results, file, ensure_ascii=False, indent=2)

    print("对比完成")
    for name, value in stats.items():
        print(f"{name}：{value}")
    print(f"输出文件：{OUTPUT_FILE}")

    if diff_results:
        print("前 10 个推荐人不同的意向学员微信号：")
        for item in diff_results[:10]:
            print(item["意向学员微信号"])


if __name__ == "__main__":
    main()
