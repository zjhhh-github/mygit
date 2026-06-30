# -*- coding: utf-8 -*-
import json
import re
import requests

SUPABASE_URL = "https://backend.appmiaoda.com/projects/supabase293970823448936448"
ANON = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoyMDg5NTE1MzA2LCJpc3MiOiJzdXBhYmFzZSIsInJvbGUiOiJhbm9uIiwic3ViIjoiYW5vbiJ9."
    "Z19rhe7D6v4pXthoontMmG_C1U3yW6DTTSyFOKYvs54"
)
spec = requests.get(f"{SUPABASE_URL}/rest/v1/", headers={"apikey": ANON}, timeout=30).json()
paths = spec.get("paths") or {}
print("tables/views with role or staff:")
for path in sorted(paths.keys()):
    low = path.lower()
    if any(k in low for k in ("staff", "sales", "internal", "contact", "user", "profile", "role")):
        print(" ", path)

# 列出所有 definitions 名称
defs = spec.get("definitions") or {}
for name in sorted(defs.keys()):
    if any(k in name.lower() for k in ("staff", "sales", "internal", "contact", "user", "profile")):
        props = list((defs[name].get("properties") or {}).keys())
        print(f"{name}: {props}")
