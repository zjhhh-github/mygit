# -*- coding: utf-8 -*-
import json
import random
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

spec = requests.get(f"{SUPABASE_URL}/rest/v1/", headers={"apikey": ANON}, timeout=30).json()
schema = spec["paths"]["/rpc/create_user_by_admin"]["post"]["parameters"][0]["schema"]
print("create_user_by_admin schema:")
print(json.dumps(schema, ensure_ascii=False, indent=2))

for username in [f"probe_create_{random.randint(100000,999999)}", "006051"]:
    payload = {
        "p_username": username,
        "p_password": "Test1234",
        "p_role": "普通用户",
        "p_wechat_id": "probe_wx",
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/rpc/create_user_by_admin",
        headers=headers,
        data=body,
        timeout=30,
    )
    print(f"\n{username} -> {resp.status_code}: {resp.text[:300]}")
