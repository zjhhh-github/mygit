# -*- coding: utf-8 -*-
"""
根据编号列表查询每个人的小组信息。

输入文件 1：C:/Users/LENOVO/Desktop/_脚本输入_2.txt      — 待查询编号列表（每行一个编号）
输入文件 2：C:/Users/LENOVO/Desktop/_脚本输出_合伙宝妈.csv — 上一步生成的小组成员数据
输出文件  ：C:/Users/LENOVO/Desktop/_脚本输出_查询结果.csv — 查询结果

查询逻辑：
  1. 读取 _脚本输出_合伙宝妈.csv，以"组员编号"为 key 建立字典
  2. 逐行读取 _脚本输入_2.txt 中的编号
  3. 在字典中查找每个编号：
     - 找到 → 输出该人姓名、组长编号、组长姓名、公司名称，备注"已找到"
     - 找不到 → 所有信息留空，备注"未找到"
"""

import csv

# ── 配置 ──────────────────────────────────────────────────────────
MEMBER_CSV   = r"C:\Users\LENOVO\Desktop\_脚本输出_合伙宝妈.csv"
QUERY_FILE   = r"C:\Users\LENOVO\Desktop\_脚本输入_2.txt"
OUTPUT_FILE  = r"C:\Users\LENOVO\Desktop\_脚本输出_查询结果.csv"
# ─────────────────────────────────────────────────────────────────


def load_member_dict(csv_path):
    """
    读取合伙宝妈 CSV，以"组员编号"为 key 建立查找字典。
    每个 key 对应一条记录（dict），包含：
      组员姓名、组长编号、组长姓名、公司名称
    注意：同一编号在 CSV 中只出现一次（每人只属于一个小组），
    如有重复则保留最后一条。
    自动尝试 utf-8-sig / gbk 两种编码，兼容不同 Windows 环境。
    """
    member_dict = {}
    # 依次尝试常见编码，直到成功为止
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            with open(csv_path, encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    mid = row["组员编号"].strip()
                    member_dict[mid] = {
                        "组员姓名": row["组员姓名"].strip(),
                        "组长编号": row["组长编号"].strip(),
                        "组长姓名": row["组长姓名"].strip(),
                        "公司名称": row["公司名称"].strip(),
                    }
            print(f"  CSV 读取成功（编码：{enc}）")
            break  # 读取成功，跳出循环
        except (UnicodeDecodeError, KeyError):
            member_dict = {}  # 清空，准备用下一种编码重试
            continue
    return member_dict


def load_query_ids(txt_path):
    """
    读取待查询编号文件，每行一个编号，返回去除空白后的列表。
    """
    ids = []
    with open(txt_path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                ids.append(stripped)
    return ids


def write_result_csv(query_ids, member_dict, output_path):
    """
    对每个待查询编号执行查找，将结果写入 CSV 文件。
    列：查询编号 / 姓名 / 组长编号 / 组长姓名 / 公司名称 / 备注
    """
    found_count   = 0
    missing_count = 0

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        # 表头
        writer.writerow(["查询编号", "姓名", "组长编号", "组长姓名", "公司名称", "备注"])

        for qid in query_ids:
            if qid in member_dict:
                info = member_dict[qid]
                writer.writerow([
                    qid,
                    info["组员姓名"],
                    info["组长编号"],
                    info["组长姓名"],
                    info["公司名称"],
                    "已找到"
                ])
                found_count += 1
            else:
                # 该编号在小组数据中不存在
                writer.writerow([qid, "", "", "", "", "未找到"])
                missing_count += 1

    return found_count, missing_count


def main():
    print(f"加载小组成员数据：{MEMBER_CSV}")
    member_dict = load_member_dict(MEMBER_CSV)
    print(f"  共加载 {len(member_dict)} 条成员记录")

    print(f"读取待查询编号：{QUERY_FILE}")
    query_ids = load_query_ids(QUERY_FILE)
    print(f"  共 {len(query_ids)} 个编号待查询")

    found, missing = write_result_csv(query_ids, member_dict, OUTPUT_FILE)

    print(f"\n查询完成：")
    print(f"  已找到：{found} 条")
    print(f"  未找到：{missing} 条")
    print(f"\nCSV 已输出到：{OUTPUT_FILE}")

    # 打印未找到的编号，方便排查
    if missing > 0:
        print("\n以下编号在小组数据中未找到：")
        # 重新扫描一次，只打印未找到的
        for qid in query_ids:
            if qid not in member_dict:
                print(f"  {qid}")


if __name__ == "__main__":
    main()
