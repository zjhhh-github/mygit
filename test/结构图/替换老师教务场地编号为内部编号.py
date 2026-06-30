from pathlib import Path
import re
import sys
from typing import Dict


def build_mapping(mapping_file: Path) -> Dict[str, str]:
    """
    从映射文件构建替换字典：原始 -> 新。
    映射文件示例：
    原始  新
    XXXJW0001-乌兰老师  XXX000111-乌兰老师
    """
    text = mapping_file.read_text(encoding="utf-8")
    mapping: Dict[str, str] = {}

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("原始"):
            continue

        # 兼容两种常见分隔：Tab 或多个空格
        parts = re.split(r"\t+|\s{2,}", line)
        if len(parts) < 2:
            continue

        old_value = parts[0].strip()
        new_value = parts[1].strip()

        # 跳过异常行（例如❌）
        if old_value == "❌" or new_value == "❌":
            continue

        # 如果同一原始值重复出现，后出现的值覆盖前值
        mapping[old_value] = new_value

    return mapping


def replace_by_mapping(source_file: Path, output_file: Path, mapping: Dict[str, str]) -> None:
    """
    按映射对源文件进行全文精确替换，并输出到新文件。
    为避免短字符串先替换导致长字符串被破坏，按长度倒序替换。
    """
    content = source_file.read_text(encoding="utf-8")

    # 关键逻辑：长串优先替换，避免子串冲突
    for old_value in sorted(mapping.keys(), key=len, reverse=True):
        content = content.replace(old_value, mapping[old_value])

    output_file.write_text(content, encoding="utf-8")


def main() -> None:
    # 默认使用你给的两个输入路径；也支持命令行传参覆盖
    default_mapping = Path(r"C:\Users\LENOVO\Desktop\_脚本输入_2.txt")
    default_source = Path(r"C:\Users\LENOVO\Desktop\_脚本输入_1.txt")
    default_output = Path(r"C:\Users\LENOVO\Desktop\_脚本输出_2_已替换.txt")

    mapping_file = Path(sys.argv[1]) if len(sys.argv) > 1 else default_mapping
    source_file = Path(sys.argv[2]) if len(sys.argv) > 2 else default_source
    output_file = Path(sys.argv[3]) if len(sys.argv) > 3 else default_output

    if not mapping_file.exists():
        raise FileNotFoundError(f"映射文件不存在: {mapping_file}")
    if not source_file.exists():
        raise FileNotFoundError(f"待替换文件不存在: {source_file}")

    mapping = build_mapping(mapping_file)
    if not mapping:
        raw_preview = mapping_file.read_text(encoding="utf-8")[:120]
        if "小组结构图" in raw_preview or "教务小组" in raw_preview:
            raise ValueError(
                "当前映射文件看起来是'结构图数据'，不是'原始-新'映射表。"
                "请传入包含两列(原始/新)的映射文件。"
            )
        raise ValueError(
            "未从映射文件中解析到有效映射，请检查格式是否为两列：原始<Tab或多个空格>新。"
        )

    replace_by_mapping(source_file, output_file, mapping)
    print(f"替换完成，输出文件: {output_file}")
    print(f"映射条数: {len(mapping)}")


if __name__ == "__main__":
    main()
