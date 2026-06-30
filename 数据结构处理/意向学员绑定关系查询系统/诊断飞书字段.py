# -*- coding: utf-8 -*-
"""一次性诊断：拉飞书前 3 条原始记录，打印所有字段名 + 值，定位'微信号'到底叫什么"""
import json, sys
sys.path.insert(0, r"D:\桌面文件\新建文件夹\数据结构处理\意向学员绑定关系查询系统")
from importlib.util import spec_from_file_location, module_from_spec

spec = spec_from_file_location("up", r"D:\桌面文件\新建文件夹\数据结构处理\意向学员绑定关系查询系统\上传用户结构.py")
up = module_from_spec(spec); spec.loader.exec_module(up)

token = up.获取_tenant_access_token()
records = up.拉取所有记录(token)
print(f"\n[诊断] 共拉到 {len(records)} 条；下面打印前 3 条的全部字段：\n")
for i, r in enumerate(records[:3], 1):
    print(f"── 第 {i} 条 ──")
    for k, v in (r.get("fields") or {}).items():
        print(f"  {k!r:30s} = {v!r}")
    print()
