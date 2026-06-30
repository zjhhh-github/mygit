# -*- coding: utf-8 -*-
"""
补充飞书前三步：查库 -> 取飞书 max -> 对比；默认直接写入飞书。

用法：
    python compare_only.py                         # 对比并写入（默认）
    python compare_only.py --preview               # 仅对比，不写入
    python compare_only.py --output-json compare_result.json
"""

import argparse
import json
import sys
from pathlib import Path

from main import run_compare_only


def _save_json(result, output_path):
    # type: (dict, Path) -> None
    """保存对比结果为 JSON 文件。"""
    payload = {
        "成功": result.get("成功"),
        "消息": result.get("消息"),
        "数据库最大编号": result.get("数据库最大编号"),
        "飞书最大编号": result.get("飞书最大编号"),
        "待新增编号": result.get("待新增编号") or [],
        "已写入条数": result.get("已写入条数", 0),
        "读取模式": result.get("读取模式"),
        "报告": result.get("报告"),
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("已保存 JSON：{}".format(output_path))


def _save_txt(result, output_path):
    # type: (dict, Path) -> None
    """保存待新增编号列表为文本文件。"""
    lines = result.get("待新增编号") or []
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print("已保存待新增编号：{}（{} 条）".format(output_path, len(lines)))


def main():
    # type: () -> int
    parser = argparse.ArgumentParser(description="补充飞书前三步：默认对比并写入飞书")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="仅对比预览，不写入飞书（默认会写入）",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="兼容旧参数，与默认行为相同，可忽略",
    )
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help="对比阶段全量分页拉编号列（慢，仅核对用）",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="对比结果保存为 JSON 文件路径",
    )
    parser.add_argument(
        "--output-txt",
        default="",
        help="待新增编号保存为文本文件路径",
    )
    args = parser.parse_args()

    # 默认写入；只有 --preview 才不写
    do_write = not args.preview
    result = run_compare_only(full_scan=args.full_scan, write=do_write)
    print(result.get("消息") or "")

    if args.output_json:
        _save_json(result, Path(args.output_json))
    if args.output_txt:
        _save_txt(result, Path(args.output_txt))

    if not result.get("成功"):
        return 1
    if result.get("待新增编号") and args.preview:
        print()
        print("当前为预览模式，未写入飞书。去掉 --preview 再运行即可写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
