# -*- coding: utf-8 -*-
"""对比：成功账号 vs 失败账号 调用 RPC 的差异（只读探测 + 可选写入测试账号）"""
import json
import random
import requests

SUPABASE_URL = "https://backend.appmiaoda.com/projects/supabase293970823448936448"
ANON = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoyMDg5NTE1MzA2LCJpc3MiOiJzdXBhYmFzZSIsInJvbGUiOiJhbm9uIiwic3ViIjoiYW5vbiJ9."
    "Z19rhe7D6v4pXthoontMmG_C1U3yW6DTTSyFOKYvs54"
)

def login():
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": ANON, "Content-Type": "application/json"},
        json={"email": "15648230994@miaoda.com", "password": "028056hQ@"},
        timeout=30,
    )
    return r.json()["access_token"]

def rpc(token, username, role, password="Test1234"):
    url = f"{SUPABASE_URL}/rest/v1/rpc/upsert_user_by_admin"
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": ANON,
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "p_username": username,
        "p_password": password,
        "p_role": role,
        "p_wechat_id": "probe_wx",
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return requests.post(url, headers=headers, data=body, timeout=30)

token = login()
cases = [
    ("006051", "普通用户", "失败样本"),
    ("002981", "普通用户", "已成功存在的 profile 样本"),
    (f"probe_{random.randint(100000,999999)}", "普通用户", "全新账号"),
    ("006051", "管理员", "失败样本换角色"),
]
for username, role, note in cases:
    resp = rpc(token, username, role)
    print(f"[{note}] user={username} role={role} -> {resp.status_code}")
    print(" ", resp.text[:250])
    print()
