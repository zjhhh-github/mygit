# -*- coding: utf-8 -*-
"""临时测试脚本：验证 'beijin海淀区西二旗小米科技园' 的解析结果"""
import sys
import io
# 强制输出 UTF-8，避免 Windows 控制台乱码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import logging
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")

from address_parser.parser import AddressParser

parser = AddressParser(
    r"D:\桌面文件\新建文件夹\数据结构处理\address_parser\district_db.json",
    abbrev_path=r"D:\桌面文件\新建文件夹\数据结构处理\简称映射.json",
)

addr = "张三 13800138000 beijin海淀区西二旗小米科技园"
result = parser.parse(addr)

print("===== 解析结果 =====")
for k, v in result.items():
    print(f"  {k}: {v!r}")
print()
print("期望结果：")
print("  province: '北京市'")
print("  city:     ''  (直辖市 city 置空)")
print("  district: '海淀区'")
print("  detail_address: '西二旗小米科技园'")
