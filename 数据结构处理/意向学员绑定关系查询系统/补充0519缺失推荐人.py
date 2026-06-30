import json
import shutil
from datetime import datetime
from pathlib import Path


# 旧数据文件：用于查找 05-19 中缺失的来源关系。
OLD_FILE = Path(r"C:\Users\LENOVO\Downloads\意向学员数据_2026-05-15.json")

# 新数据文件：本脚本会在备份后补充写回这个文件。
NEW_FILE = Path(r"C:\Users\LENOVO\Downloads\意向学员数据_2026-05-19 (1).json")

# 补充明细：记录本次具体补充了哪些意向学员和来源关系，方便人工复查。
DETAIL_FILE = Path(r"C:\Users\LENOVO\Desktop\0519补充缺失推荐人明细.json")


def load_json(file_path: Path):
    """读取 JSON 文件，并兼容带 BOM 的 UTF-8 文件。"""
    if not file_path.exists():
        raise FileNotFoundError(f"找不到文件：{file_path}")

    with file_path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def backup_file(file_path: Path):
    """写入前先备份原文件，避免误操作后无法恢复。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = file_path.with_name(f"{file_path.stem}_补充推荐人前备份_{timestamp}{file_path.suffix}")
    shutil.copy2(file_path, backup_path)
    return backup_path


def build_student_map(data):
    """按意向学员微信号建立索引，方便快速匹配同一个学员。"""
    student_map = {}

    for item in data:
        student_wechat = (item.get("意向学员微信号") or "").strip()
        if student_wechat:
            student_map[student_wechat] = item

    return student_map


def normalize_source(source):
    """统一来源字段，去掉空格，避免格式差异影响去重。"""
    return {
        "来源微信号": (source.get("来源微信号") or "").strip(),
        "来源微信原始ID": (source.get("来源微信原始ID") or "").strip(),
        "绑定日期": (source.get("绑定日期") or "").strip(),
        "解绑日期": (source.get("解绑日期") or "").strip(),
        "绑定状态": (source.get("绑定状态") or "").strip(),
    }


def source_key(source):
    """
    生成来源关系去重键。

    这里使用完整关系字段去重，避免同一个推荐人有多段绑定历史时被误合并。
    """
    normalized = normalize_source(source)
    return (
        normalized["来源微信号"],
        normalized["来源微信原始ID"],
        normalized["绑定日期"],
        normalized["解绑日期"],
        normalized["绑定状态"],
    )


def fill_missing_sources(old_data, new_data):
    """
    用旧数据补充新数据中缺失的来源关系。

    只处理两个文件里都存在的意向学员；
    如果 05-15 中某条来源关系在 05-19 中没有，就追加到 05-19 的来源数组末尾。
    """
    old_map = build_student_map(old_data)
    new_map = build_student_map(new_data)

    detail = []
    added_count = 0
    common_count = 0

    for student_wechat, old_student in old_map.items():
        new_student = new_map.get(student_wechat)
        if not new_student:
            continue

        common_count += 1
        old_sources = old_student.get("来源", []) or []
        new_sources = new_student.setdefault("来源", [])
        existing_keys = {source_key(source) for source in new_sources}

        added_sources = []

        for old_source in old_sources:
            normalized_old_source = normalize_source(old_source)

            # 来源微信号和来源微信原始ID都为空时，认为这条关系无效，不补充。
            if not normalized_old_source["来源微信号"] and not normalized_old_source["来源微信原始ID"]:
                continue

            key = source_key(normalized_old_source)
            if key in existing_keys:
                continue

            new_sources.append(normalized_old_source)
            existing_keys.add(key)
            added_sources.append(normalized_old_source)
            added_count += 1

        if added_sources:
            detail.append(
                {
                    "意向学员微信号": student_wechat,
                    "补充来源数量": len(added_sources),
                    "补充来源": added_sources,
                }
            )

    stats = {
        "旧文件学员数量": len(old_map),
        "新文件学员数量": len(new_map),
        "两个文件都有的学员数量": common_count,
        "补充来源关系数量": added_count,
        "涉及意向学员数量": len(detail),
    }

    return new_data, detail, stats


def main():
    """脚本入口：备份 05-19 文件，并补充缺失来源关系。"""
    old_data = load_json(OLD_FILE)
    new_data = load_json(NEW_FILE)

    filled_data, detail, stats = fill_missing_sources(old_data, new_data)
    backup_path = backup_file(NEW_FILE)

    with NEW_FILE.open("w", encoding="utf-8") as file:
        json.dump(filled_data, file, ensure_ascii=False, indent=2)

    with DETAIL_FILE.open("w", encoding="utf-8") as file:
        json.dump(detail, file, ensure_ascii=False, indent=2)

    print("补充完成")
    for name, value in stats.items():
        print(f"{name}：{value}")
    print(f"备份文件：{backup_path}")
    print(f"补充明细：{DETAIL_FILE}")


if __name__ == "__main__":
    main()
