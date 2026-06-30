from pathlib import Path
from typing import Dict, List, Tuple
import re
import sys


def normalize_campus_name(name: str) -> str:
    """
    统一校区名称，提升匹配成功率。
    说明：
    1) 去除空白与常见分隔符；
    2) 兼容“豪/毫沁营”写法差异；
    3) 兼容“包头苏宁/包头昆北”与原始数据“苏宁/昆北”的差异。
    """
    cleaned = name.strip()
    cleaned = cleaned.replace("　", " ")
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = cleaned.replace("（", "(").replace("）", ")")
    cleaned = cleaned.replace("豪沁营", "毫沁营")

    # 关键兼容逻辑：查询列表中可能带“包头”前缀，原始数据中不一定带
    if cleaned.startswith("包头"):
        cleaned = cleaned[2:]

    # 去除常见符号，避免“万悦城-”之类差异影响匹配
    cleaned = re.sub(r"[、,，;；\-—_]", "", cleaned)
    return cleaned


def parse_raw_data(raw_text: str) -> Dict[str, Tuple[str, str]]:
    """
    从原始数据提取“校区 -> 地址”映射。
    原始数据结构为多组块，每组通常包含：
    01-校区名（地区）
    教务：...
    手机：...
    地址：...
    """
    campus_to_address: Dict[str, Tuple[str, str]] = {}
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    current_campus = ""
    for line in lines:
        # 匹配“序号-校区名（地区）”这一行
        campus_match = re.match(r"^\d+\s*-\s*([^（(]+)", line)
        if campus_match:
            current_campus = campus_match.group(1).strip()
            continue

        # 匹配地址行，并绑定到最近一次识别到的校区名
        if line.startswith("地址：") and current_campus:
            address = line.split("：", 1)[1].strip()
            normalized = normalize_campus_name(current_campus)
            # 若出现重复校区，采用后值覆盖，通常代表较新的信息
            campus_to_address[normalized] = (current_campus, address)

    return campus_to_address


def query_addresses(
    query_names: List[str], campus_map: Dict[str, Tuple[str, str]]
) -> List[str]:
    """
    对查询列表逐条查地址，保留输入顺序与重复项。
    """
    result_lines: List[str] = []
    for raw_name in query_names:
        query_name = raw_name.strip()
        if not query_name:
            continue

        normalized_query = normalize_campus_name(query_name)
        matched = campus_map.get(normalized_query)

        if matched:
            source_name, address = matched
            result_lines.append(
                f"校区：{query_name} | 匹配：{source_name} | 地址：{address}"
            )
        else:
            result_lines.append(f"校区：{query_name} | 地址：未找到")

    return result_lines


def main() -> None:
    # 默认路径：直接对应你的输入文件
    default_raw_file = Path(r"C:\Users\LENOVO\Desktop\_脚本输入_1.txt")
    default_query_file = Path(r"C:\Users\LENOVO\Desktop\_脚本输入_3.txt")
    default_output_file = Path(r"C:\Users\LENOVO\Desktop\_脚本输出_3_校区地址查询结果.txt")

    raw_file = Path(sys.argv[1]) if len(sys.argv) > 1 else default_raw_file
    query_file = Path(sys.argv[2]) if len(sys.argv) > 2 else default_query_file
    output_file = Path(sys.argv[3]) if len(sys.argv) > 3 else default_output_file

    if not raw_file.exists():
        raise FileNotFoundError(f"原始数据文件不存在: {raw_file}")
    if not query_file.exists():
        raise FileNotFoundError(f"查询列表文件不存在: {query_file}")

    raw_text = raw_file.read_text(encoding="utf-8")
    query_text = query_file.read_text(encoding="utf-8")

    campus_map = parse_raw_data(raw_text)
    query_names = query_text.splitlines()
    result_lines = query_addresses(query_names, campus_map)

    # 打印并保存，便于你直接查看和留档
    for line in result_lines:
        print(line)

    summary = (
        f"\n总查询数：{len([x for x in query_names if x.strip()])}，"
        f"匹配成功：{sum('未找到' not in x for x in result_lines)}，"
        f"未找到：{sum('未找到' in x for x in result_lines)}"
    )
    print(summary)

    # 仅保存“地址”字段：每行一个地址（未找到则写“未找到”）
    address_only_lines = [line.split("地址：", 1)[1].strip() for line in result_lines]
    output_content = "\n".join(address_only_lines) + "\n"
    output_file.write_text(output_content, encoding="utf-8")
    print(f"\n结果已保存到：{output_file}")


if __name__ == "__main__":
    main()
