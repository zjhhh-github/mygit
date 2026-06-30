import csv
import json
from collections import OrderedDict
from datetime import datetime
from pathlib import Path


# 源文件路径：这里读取桌面上的制表符文本文件。
INPUT_FILE = Path(r"C:\Users\LENOVO\Desktop\_脚本输出_1.txt")

# 输出文件路径：解析完成后，会在源文件旁边生成这个 JSON 文件。
OUTPUT_FILE = Path(r"C:\Users\LENOVO\Desktop\_脚本输出_1_解析结果.json")


def calculate_bind_days(bind_date: str, unbind_date: str):
    """
    计算绑定周期。

    参数：
    - bind_date：绑定日期，格式通常为 20250506
    - unbind_date：解绑日期，格式通常为 20250507

    返回：
    - 两个日期之间相差的天数
    - 如果解绑日期为空，说明还没有明确结束日期，返回 None，写入 JSON 后会显示为 null
    """
    bind_date = (bind_date or "").strip()
    unbind_date = (unbind_date or "").strip()

    if not bind_date or not unbind_date:
        return None

    try:
        start_date = datetime.strptime(bind_date, "%Y%m%d")
        end_date = datetime.strptime(unbind_date, "%Y%m%d")
        return (end_date - start_date).days
    except ValueError:
        # 日期格式异常时不强行猜测，保留为空，方便后续人工排查源数据。
        return None


def parse_relation_file(input_file: Path):
    """
    将原始文本解析成按“意向学员微信号”分组的嵌套结构。

    源文件是制表符分隔数据，并且表头中存在重复列名，
    所以这里按固定列位置读取，避免 DictReader 因重复表头覆盖字段。
    """
    students = OrderedDict()
    row_count = 0
    recommend_count = 0
    skipped_short_rows = 0

    with input_file.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file, delimiter="\t")

        # 第一行是表头，本脚本按列下标解析，所以这里只跳过不使用。
        next(reader, None)

        for row in reader:
            row_count += 1

            # 正常数据应有 10 列；不足 10 列时补空字符串，避免脚本中断。
            if len(row) < 10:
                skipped_short_rows += 1
                row = row + [""] * (10 - len(row))

            student_wechat = row[0].strip()
            signup_status = row[1].strip()
            bind_status = row[3].strip()
            referrer_wechat = row[4].strip()
            bind_date = row[5].strip()
            unbind_date = row[6].strip()
            student_original_id = row[7].strip()
            referrer_original_id = row[9].strip()

            # 没有学员微信号的行无法作为主记录，直接跳过。
            if not student_wechat:
                continue

            if student_wechat not in students:
                students[student_wechat] = {
                    "意向学员微信号": student_wechat,
                    "意向学员微信原始ID": student_original_id,
                    "是否报名": signup_status,
                    "推荐": [],
                }
            else:
                # 同一个学员可能多行出现；如果首行缺失信息，使用后续非空值补齐。
                if not students[student_wechat]["意向学员微信原始ID"] and student_original_id:
                    students[student_wechat]["意向学员微信原始ID"] = student_original_id
                if not students[student_wechat]["是否报名"] and signup_status:
                    students[student_wechat]["是否报名"] = signup_status

            # 没有推荐人微信号时，保留学员主记录，但不新增推荐关系。
            if referrer_wechat:
                students[student_wechat]["推荐"].append(
                    {
                        "推荐人总微信号": referrer_wechat,
                        "推荐人微信原始ID": referrer_original_id,
                        "绑定日期": bind_date,
                        "绑定周期": calculate_bind_days(bind_date, unbind_date),
                        "解绑日期": unbind_date,
                        "绑定状态": bind_status,
                    }
                )
                recommend_count += 1

    return list(students.values()), row_count, recommend_count, skipped_short_rows


def main():
    """脚本入口：读取源文件，解析数据，并写入 JSON 文件。"""
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"找不到源文件：{INPUT_FILE}")

    result, row_count, recommend_count, skipped_short_rows = parse_relation_file(INPUT_FILE)

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    print("解析完成")
    print(f"读取数据行数：{row_count}")
    print(f"输出学员数量：{len(result)}")
    print(f"输出推荐关系数量：{recommend_count}")
    print(f"列数不足但已补空处理的行数：{skipped_short_rows}")
    print(f"输出文件：{OUTPUT_FILE}")


if __name__ == "__main__":
    main()
