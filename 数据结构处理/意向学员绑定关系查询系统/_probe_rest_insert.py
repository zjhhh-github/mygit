# -*- coding: utf-8 -*-
"""探测：能否绕过 RPC，直接 REST 写入 profiles（新建用户）"""
import json
import random
import uuid
import requests

SUPABASE_URL = "https://backend.appmiaoda.com/projects/supabase293970823448936448"
ANON = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoyMDg5NTE1MzA2LCJpc3MiOiJzdXBhYmFzZSIsInJvbGUiOiJhbm9uIiwic3ViIjoiYW5vbiJ9."
    "Z19rhe7D6v4pXthoontMmG_C1U3yW6DTTSyFOKYvs54"
)

login = requests.post(
    f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
    headers={"apikey": ANON, "Content-Type": "application/json"},
    json={"email": "15648230994@miaoda.com", "password": "028056hQ@"},
    timeout=30,
)
token = login.json()["access_token"]
headers = {
    "Authorization": f"Bearer {token}",
    "apikey": ANON,
    "Content-Type": "application/json; charset=utf-8",
    "Prefer": "return=representation",
}

username = f"probe_rest_{random.randint(100000,999999)}"
payload = {
    "id": str(uuid.uuid4()),
    "username": username,
    "role": "普通用户",
    "wechat_id": "probe_wx",
}
resp = requests.post(
    f"{SUPABASE_URL}/rest/v1/profiles",
    headers=headers,
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    timeout=30,
)
print("POST profiles:", resp.status_code, resp.text[:400])
