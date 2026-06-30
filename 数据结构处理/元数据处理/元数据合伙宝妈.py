# -*- coding: utf-8 -*-
"""
解析 _脚本输入_1.txt，提取每个小组的组长、公司名称和合伙宝妈小组成员，
输出为 CSV 文件（utf-8-sig 编码，可直接用 Excel 打开）。

输入文件：C:/Users/LENOVO/Desktop/_脚本输入_1.txt
输出文件：C:/Users/LENOVO/Desktop/_脚本输出_合伙宝妈.csv

文件结构规律（按缩进层级识别）：
  - ¿¿¿000024-孙一可          ← 2格缩进：组长（每组起始行）
    日照得悟信息咨询有限公司    ← 4格缩进纯文字：可能是地点名或公司名
    - 合伙宝妈小组              ← 合伙宝妈小组标记
      - ¿¿¿000024-孙一可       ← 6格缩进：组员（含组长本身）
"""

import csv
import re

# ── 配置 ──────────────────────────────────────────────────────────
INPUT_FILE  = r"C:\Users\LENOVO\Desktop\_脚本输入_1.txt"
OUTPUT_FILE = r"C:\Users\LENOVO\Desktop\_脚本输出_合伙宝妈.csv"

# 匹配成员行（含乱码前缀 ¿¿¿ + 编号 + 姓名）
# 示例：¿¿¿000024-孙一可
MEMBER_PATTERN = re.compile(r"¿¿¿(\d+)-(.+)")
# ─────────────────────────────────────────────────────────────────


def extract_id_and_name(text):
    """
    从类似 "¿¿¿000024-孙一可" 的字符串中提取编号和姓名。
    返回 (编号, 姓名) 元组，匹配失败返回 (None, None)。
    """
    m = MEMBER_PATTERN.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, None


def is_company_line(text):
    """
    判断一行是否为公司名称行：
    - 包含"公司"二字，或者
    - 文字就是"无"
    """
    t = text.strip()
    return "公司" in t or t == "无"


def parse_input(filepath):
    """
    逐行解析输入文件，返回所有小组列表。
    每个小组是一个字典：
      {
        "leader_id":   "000024",
        "leader_name": "孙一可",
        "company":     "日照得悟信息咨询有限公司",
        "members":     [("000024", "孙一可"), ("000116", "宋彧荨"), ...]
      }
    """
    groups = []          # 最终结果列表
    current = None       # 当前正在处理的小组
    in_hbm_group = False # 是否正在合伙宝妈小组内部收集成员

    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()

    for raw_line in lines:
        # 保留原始缩进，去掉行尾换行
        line = raw_line.rstrip("\n")

        # ── 判断缩进层级 ──────────────────────────────────────
        stripped = line.lstrip()
        indent   = len(line) - len(stripped)
        # ─────────────────────────────────────────────────────

        # ① 2格缩进 + ¿¿¿ → 新组长行，开始一个新小组
        if indent == 2 and stripped.startswith("- ¿¿¿"):
            # 保存上一个小组
            if current is not None:
                groups.append(current)
            leader_id, leader_name = extract_id_and_name(stripped)
            current = {
                "leader_id":   leader_id,
                "leader_name": leader_name,
                "company":     "（未知）",
                "members":     []
            }
            in_hbm_group = False

        # ② 4格缩进、无 - 开头 → 地点名或公司名
        elif indent == 4 and not stripped.startswith("-") and current is not None:
            text = stripped.strip()
            if is_company_line(text):
                # 明确是公司名行，直接记录
                current["company"] = text
            else:
                # 地点名行：先暂存，若后续无公司行则保留此行作备用
                # 但规则是：公司名出现时覆盖，所以这里只在公司名还是"（未知）"时暂存
                if current["company"] == "（未知）":
                    current["company"] = text  # 暂存，可能被后续公司名行覆盖

        # ③ 识别"- 合伙宝妈小组"标记
        elif "合伙宝妈小组" in stripped and current is not None:
            in_hbm_group = True

        # ④ 识别其他小组标记（教务小组等），退出合伙宝妈收集状态
        elif stripped.startswith("- ") and "小组" in stripped and "合伙宝妈" not in stripped:
            in_hbm_group = False

        # ⑤ 6格缩进 + ¿¿¿ → 合伙宝妈成员行
        elif indent == 6 and stripped.startswith("- ¿¿¿") and in_hbm_group and current is not None:
            member_id, member_name = extract_id_and_name(stripped)
            if member_id:
                current["members"].append((member_id, member_name))

    # 不要忘记最后一个小组
    if current is not None:
        groups.append(current)

    return groups


def write_csv(groups, filepath):
    """
    将解析结果写入 CSV 文件。
    每行代表一个组员，列：组长编号 / 组长姓名 / 公司名称 / 组员编号 / 组员姓名
    """
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        # 表头
        writer.writerow(["组长编号", "组长姓名", "公司名称", "组员编号", "组员姓名"])
        for g in groups:
            if g["members"]:
                # 有成员：每个成员写一行
                for (mid, mname) in g["members"]:
                    writer.writerow([
                        g["leader_id"],
                        g["leader_name"],
                        g["company"],
                        mid,
                        mname
                    ])
            else:
                # 没有成员（仅组长自己）：也输出一行，组员列用组长填充
                writer.writerow([
                    g["leader_id"],
                    g["leader_name"],
                    g["company"],
                    g["leader_id"],
                    g["leader_name"]
                ])


def main():
    print(f"读取文件：{INPUT_FILE}")
    groups = parse_input(INPUT_FILE)
    print(f"共解析到 {len(groups)} 个小组")

    for g in groups:
        member_count = len(g["members"])
        print(f"  组长：{g['leader_name']}（{g['leader_id']}）  "
              f"公司：{g['company']}  "
              f"成员数：{member_count}")

    write_csv(groups, OUTPUT_FILE)
    print(f"\nCSV 已输出到：{OUTPUT_FILE}")


if __name__ == "__main__":
    main()
