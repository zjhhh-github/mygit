# -*- coding: utf-8 -*-
import importlib.util
from pathlib import Path

p = Path(__file__).with_name("搜索联系人_GUI.py")
spec = importlib.util.spec_from_file_location("gui", p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

索引 = mod.构建群成员索引(mod.默认数据库列表)
print("indexed wxids:", len(索引))
# 找有群信息的样本
cnt_live = sum(1 for v in 索引.values() if v.get("internal_live"))
cnt_lead = sum(1 for v in 索引.values() if v.get("exclusive_lead"))
print("with live group:", cnt_live, "with lead group:", cnt_lead)
for wxid, info in list(索引.items())[:3]:
    if info.get("internal_live") or info.get("exclusive_lead"):
        print(wxid, info)
        break

结果 = mod.跨库搜索([mod.默认数据库列表[1]], "000024")
mod.填充联系人群信息(结果, 索引)
if 结果:
    r = 结果[0]
    print("sample keys", r.get("_binah"), r.get("_internal_live_group"), r.get("_exclusive_lead_group"))
