# -*- coding: utf-8 -*-

"""
用途：
1. 读取 Markdown 结构图，解析出合法的“下级 -> 上级”关系；
2. 读取 Excel（A 列下级，C 列上级）；
3. 校验 Excel 中每一行关系是否与 Markdown 一致；
4. 导出错误明细到新的 Excel 文件，便于人工复核。
"""

import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pandas as pd


# -----------------------------
# 可按需修改的输入输出路径
# -----------------------------
EXCEL_PATH = r"C:\Users\LENOVO\Desktop\工作簿.xlsx"
MARKDOWN_PATH = r"D:\桌面文件\宝妈结构图\宝妈结构图20260326.md"
OUTPUT_PATH = r"d:\桌面文件\新建文件夹\上下级关系错误明细.xlsx"


def 规范化姓名(text: object) -> str:
    """
    将名称做统一清洗，减少因空格/格式差异导致的误判。
    """
    if text is None:
        return ""

    value = str(text).strip()
    if not value:
        return ""

    # 统一全角空格和多余空白
    value = value.replace("\u3000", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def 从节点文本提取姓名(raw_text: str) -> str:
    """
    将 Markdown 节点文本中的统计尾缀去掉。
    例如：'¿¿¿000030-韩鹤天 韩鹤鸣-358-334' -> '¿¿¿000030-韩鹤天 韩鹤鸣'
    """
    text = 规范化姓名(raw_text)
    if not text:
        return ""

    # 去除尾部 “-数字-数字” 统计信息
    match = re.match(r"^(.*?)-\d+-\d+$", text)
    if match:
        text = match.group(1).strip()

    return 规范化姓名(text)


def 解析_markdown关系(markdown_path: str) -> Tuple[Set[Tuple[str, str]], Dict[str, Set[str]]]:
    """
    从 Markdown 层级中提取“下级 -> 上级”合法关系。
    返回：
    - valid_pairs: {(下级, 上级), ...}
    - child_to_parents: {下级: {上级1, 上级2, ...}}
    """
    valid_pairs: Set[Tuple[str, str]] = set()
    child_to_parents: Dict[str, Set[str]] = {}

    md_file = Path(markdown_path)
    if not md_file.exists():
        raise FileNotFoundError(f"未找到 Markdown 文件：{markdown_path}")

    lines = md_file.read_text(encoding="utf-8").splitlines()

    # 栈下标代表层级，值为当前层节点名称
    level_stack: List[str] = []

    for raw_line in lines:
        # 匹配 Markdown 列表项，例如：'    - 节点内容'
        m = re.match(r"^(\s*)-\s+(.*?)\s*$", raw_line)
        if not m:
            continue

        indent = len(m.group(1).replace("\t", "    "))
        level = indent // 2  # 按 2 空格作为一级缩进
        node_raw = m.group(2)
        node_name = 从节点文本提取姓名(node_raw)

        if not node_name:
            continue

        # 截断到当前层级，避免旧分支节点残留
        if level < len(level_stack):
            level_stack = level_stack[:level]

        # 若缩进跳级，按当前可用最深层处理，保证脚本稳健不崩
        if level > len(level_stack):
            level = len(level_stack)

        # 记录父子关系（根节点无父节点，不参与校验）
        if level >= 1 and level - 1 < len(level_stack):
            parent_name = level_stack[level - 1]
            # 根标题通常是“宝妈结构图”，不属于人员关系，跳过
            if "结构图" not in parent_name:
                valid_pairs.add((node_name, parent_name))
                child_to_parents.setdefault(node_name, set()).add(parent_name)

        # 更新当前层节点
        if level == len(level_stack):
            level_stack.append(node_name)
        else:
            level_stack[level] = node_name

    return valid_pairs, child_to_parents


def 是表头行(下级: str, 上级: str) -> bool:
    """
    粗略识别表头，避免把“下级/上级”等标题误判为错误数据。
    """
    header_tokens = {"下级", "上级", "下属", "直属上级", "姓名"}
    return 下级 in header_tokens or 上级 in header_tokens


def 校验_excel关系(
    excel_path: str,
    valid_pairs: Set[Tuple[str, str]],
    child_to_parents: Dict[str, Set[str]],
) -> pd.DataFrame:
    """
    校验 Excel 中 A/C 列关系是否在 Markdown 合法关系集合中。
    返回错误明细 DataFrame。
    """
    excel_file = Path(excel_path)
    if not excel_file.exists():
        raise FileNotFoundError(f"未找到 Excel 文件：{excel_path}")

    # 不设表头，直接按列索引读取（A=0, C=2）
    df = pd.read_excel(excel_path, header=None)

    error_rows: List[dict] = []

    for idx, row in df.iterrows():
        excel_row_num = idx + 1

        child_raw = row.iloc[0] if len(row) > 0 else None
        parent_raw = row.iloc[2] if len(row) > 2 else None

        child = 规范化姓名(child_raw)
        parent = 规范化姓名(parent_raw)

        # A/C 任一为空时跳过，不当作关系错误
        if not child or not parent:
            continue

        if 是表头行(child, parent):
            continue

        pair = (child, parent)
        if pair in valid_pairs:
            continue

        # 细分错误原因，方便你后续快速修正
        if child not in child_to_parents:
            reason = "下级在Markdown中不存在"
            suggest = ""
        else:
            reason = "上级与Markdown不一致"
            suggest = " / ".join(sorted(child_to_parents[child]))

        error_rows.append(
            {
                "Excel行号": excel_row_num,
                "下级(A列)": child,
                "上级(C列)": parent,
                "错误原因": reason,
                "Markdown中该下级允许上级": suggest,
            }
        )

    return pd.DataFrame(error_rows)


def main() -> None:
    """
    主流程：解析 Markdown -> 校验 Excel -> 输出错误明细。
    """
    valid_pairs, child_to_parents = 解析_markdown关系(MARKDOWN_PATH)
    error_df = 校验_excel关系(EXCEL_PATH, valid_pairs, child_to_parents)

    # 输出结果到 Excel，便于你直接筛选/批注
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
        error_df.to_excel(writer, index=False, sheet_name="错误明细")

    print("校验完成。")
    print(f"Markdown合法关系数: {len(valid_pairs)}")
    print(f"Excel中检测到错误关系数: {len(error_df)}")
    print(f"错误明细文件: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
