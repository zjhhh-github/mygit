# -*- coding: utf-8 -*-
"""
将 小组结构图.md 解析为 JSON 文件。

输出每个"组"的结构：
{
  "组长": "¿¿¿000024-孙一可",
  "校区": "包头苏宁",          # 可能为空字符串
  "公司名称": "内蒙古燕栖林信息咨询服务有限公司",
  "合伙宝妈小组": ["¿¿¿000024-孙一可", ...],
  "教务小组": {
    "教务": ["¿¿¿JW0013-白冰老师"],
    "老师": ["¿¿¿LS0020-Maggie"],
    "场地": ["¿¿¿CD0005-达布老师"]
  }
}
"""

import json
import re

INPUT_PATH  = r"D:\桌面文件\新建文件夹\数据结构处理\小组结构图.md"
OUTPUT_PATH = r"D:\桌面文件\新建文件夹\数据结构处理\小组结构图.json"

def is_company_line(line: str) -> bool:
    """无缩进、无 `-` 前缀，且包含"公司"二字，则视为公司名称行；否则视为校区名。"""
    stripped = line.strip()
    if not stripped or stripped.startswith("-"):
        return False
    return "公司" in stripped


def get_indent(line: str) -> int:
    """返回行的前导空格数。"""
    return len(line) - len(line.lstrip(" "))


def extract_name(line: str) -> str:
    """去掉前导 `- ` 和空格，返回成员名称（含 ¿¿¿ 前缀）。"""
    return line.strip().lstrip("- ").strip()


def parse_md(path: str):
    """
    逐行状态机解析 markdown 文件，返回组列表。

    行类型判断规则（按缩进和内容）：
      indent=0, 以 `- ` 开头        → 组长行，开启新组
      indent=0, 不以 `-` 开头       → 校区名 或 公司名称（按 is_company_line 区分）
      indent=2, 以 `- ` 开头        → 一级子组（合伙宝妈小组 / 教务小组）
      indent=4, 以 `- ` 开头        → 二级子组（教务 / 老师 / 场地）或合伙宝妈成员
      indent=6, 以 `- ` 开头        → 三级成员（教务/老师/场地下的具体人员）
    """
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    result = []

    # 当前正在构建的组
    current: dict = None
    # 当前所在的一级子组名称：'合伙宝妈小组' 或 '教务小组'
    current_l1: str = None
    # 当前所在的二级子组名称：'教务' / '老师' / '场地'
    current_l2: str = None

    def save_current():
        """将当前组存入结果列表（若存在）。"""
        if current is not None:
            result.append(current)

    for raw_line in lines:
        # 去掉行尾换行，保留前导空格用于判断缩进
        line = raw_line.rstrip("\n\r")

        # 忽略纯空行
        if not line.strip():
            continue

        indent = get_indent(line)
        stripped = line.strip()

        # ── 组长行（indent=0, 以 `- ` 开头）──────────────────────
        if indent == 0 and stripped.startswith("- "):
            # 保存上一个组
            save_current()
            # 初始化新组
            current = {
                "组长": extract_name(line),
                "校区": "",
                "公司名称": "",
                "合伙宝妈小组": [],
                "教务小组": {
                    "教务": [],
                    "老师": [],
                    "场地": []
                }
            }
            current_l1 = None
            current_l2 = None
            continue

        # 没有当前组时跳过（文件开头异常行）
        if current is None:
            continue

        # ── 无缩进的非 `-` 行：校区名 或 公司名称 ─────────────────
        if indent == 0 and not stripped.startswith("-"):
            if is_company_line(line):
                current["公司名称"] = stripped
            else:
                # 不是公司名，视为校区名（组长行和公司名之间可能有一行）
                current["校区"] = stripped
            continue

        # ── 一级子组（indent=2）：合伙宝妈小组 / 教务小组 ──────────
        if indent == 2 and stripped.startswith("- "):
            current_l1 = extract_name(line)
            current_l2 = None
            continue

        # ── 合伙宝妈小组下的成员（indent=4，当前一级为合伙宝妈小组）──
        if indent == 4 and stripped.startswith("- ") and current_l1 == "合伙宝妈小组":
            current["合伙宝妈小组"].append(extract_name(line))
            continue

        # ── 教务小组下的二级子组（indent=4，当前一级为教务小组）──────
        if indent == 4 and stripped.startswith("- ") and current_l1 == "教务小组":
            current_l2 = extract_name(line)
            continue

        # ── 教务/老师/场地下的成员（indent=6）──────────────────────
        if indent == 6 and stripped.startswith("- ") and current_l2 in ("教务", "老师", "场地"):
            current["教务小组"][current_l2].append(extract_name(line))
            continue

    # 保存最后一个组
    save_current()

    return result


def main():
    groups = parse_md(INPUT_PATH)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)

    print(f"解析完成，共 {len(groups)} 个组，已写入：{OUTPUT_PATH}")

    # 简单抽样打印前 2 个组，便于核查
    for g in groups[:2]:
        print(json.dumps(g, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
