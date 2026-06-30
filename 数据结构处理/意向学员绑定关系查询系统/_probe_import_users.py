# -*- coding: utf-8 -*-
"""探测 import-users Edge Function 是否可用，以及合法 role 值"""
import json
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
}

# 干跑：只传 1 条测试用户，看 Edge Function 返回
test_batch = {
    "users": [
        {"username": "probe_edge_999001", "password": "Test1234", "role": "普通用户"},
    ]
}
url = f"{SUPABASE_URL}/functions/v1/import-users"
resp = requests.post(url, headers=headers, data=json.dumps(test_batch, ensure_ascii=False).encode("utf-8"), timeout=60)
print("import-users status:", resp.status_code)
print(resp.text[:800])
