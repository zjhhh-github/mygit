# -*- coding: utf-8 -*-
"""
意向查询系统 —— 通用工具函数
==============================

从 db_viewer.DatabaseViewer 中抽出的 4 个 @staticmethod 工具函数：
    - convert_timestamp(ms)        毫秒级时间戳 → "YYYY-MM-DD HH:MM:SS"
    - convert_time_format(s)       "2025年3月" → "2025-03-01 00:00:00"
    - validate_remark_format(s)    校验 ¿¿¿NNNNNN-xxx 备注格式
    - parse_xml_content(xml)       从 XML 字符串提取 sharecard 用户名/昵称

这些函数都是纯函数（无 Tk、无 self），适合任何调用方使用。
DatabaseViewer 类中保留同名 staticmethod，作为薄薄的转发层，确保历史调用：
    DatabaseViewer.convert_timestamp(123456789)
仍然有效。
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Tuple


def convert_timestamp(timestamp):
    """
    将 Unix 时间戳（毫秒级）转换为可读时间格式

    Args:
        timestamp: Unix 时间戳（毫秒）

    Returns:
        格式化的时间字符串，格式: YYYY-MM-DD HH:MM:SS；解析失败返回空串
    """
    try:
        # 毫秒级时间戳需要除以 1000 转换为秒级
        return datetime.fromtimestamp(int(timestamp) / 1000).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError, OSError, ZeroDivisionError):
        return ""


def convert_time_format(time_str):
    """
    转换时间格式：2025年3月 -> 2025-03-01 00:00:00

    Args:
        time_str: 原始时间字符串

    Returns:
        格式化后的时间字符串；不匹配则原样返回；空串返回空串
    """
    if not time_str:
        return ""

    # 已经是标准格式（包含 "-" 和 ":"）直接返回
    if "-" in time_str and ":" in time_str:
        return time_str

    # 匹配「YYYY年M月」或「YYYY年MM月」
    match = re.match(r'(\d{4})年(\d{1,2})月', time_str.strip())
    if match:
        year = match.group(1)
        month = match.group(2).zfill(2)
        return "{}-{}-01 00:00:00".format(year, month)

    return time_str


def validate_remark_format(remark):
    r"""
    验证 remark 格式是否符合要求

    格式要求：¿¿¿ + 连续 6 个数字 + - + 其他内容
    示例：¿¿¿000001-张三

    Returns:
        bool: 格式是否符合要求
    """
    if not remark or len(remark) < 10:
        return False

    if not remark.startswith("¿¿¿"):
        return False

    # 第 4-9 个字符（索引 3-8）是否为 6 个数字
    digits = remark[3:9]
    if not digits.isdigit() or len(digits) != 6:
        return False

    # 第 10 个字符（索引 9）是否为 -
    if len(remark) < 10 or remark[9] != '-':
        return False

    return True


def parse_xml_content(content):
    # type: (str) -> Tuple[str, str]
    """
    解析 XML 格式的 content 字段，提取 sharecardusername 和 sharecardnickname

    Args:
        content: XML 格式的字符串

    Returns:
        (sharecardusername, sharecardnickname) 元组；解析失败返回 ("", "")
    """
    if not content:
        return "", ""

    try:
        root = ET.fromstring(content)
        # sharecardusername 和 sharecardnickname 是 <msg> 标签的属性，不是子元素
        username = root.get('sharecardusername', '')
        nickname = root.get('sharecardnickname', '')
        return username or "", nickname or ""
    except ET.ParseError:
        return "", ""
    except Exception:
        return "", ""
