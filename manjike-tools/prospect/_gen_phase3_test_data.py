#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成第三阶段链路验证用小规模意向学员测试 JSON（约 100 条）。"""
import json
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent / "test_phase3_students.json"
META = Path(__file__).resolve().parent / "test_phase3_meta.json"


def mysql_q(sql: str) -> str:
    cmd = [
        "docker", "exec", "prospect-standalone-mysql",
        "mysql", "-uroot", "-p123456", "manjike",
        "--default-character-set=utf8mb4", "-N", "-B", "-e", sql,
    ]
    return subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8").strip()


def parse_rows(raw: str):
    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        p = line.split("\t")
        rows.append(p)
    return rows


oid_rows = []
for p in parse_rows(mysql_q(
    "SELECT wechat_original_id,wechat_no,total_wechat_no,child_chinese_name,recommender_code "
    "FROM presale_internal_contact WHERE is_deleted=0 "
    "AND wechat_original_id IS NOT NULL AND wechat_original_id<>'' ORDER BY id LIMIT 20"
)):
    oid_rows.append({
        "oid": p[0],
        "wechat": "" if p[1] == "NULL" else p[1],
        "total": p[2],
        "name": p[3],
        "code": p[4],
    })

wx_rows = []
for p in parse_rows(mysql_q(
    "SELECT wechat_original_id,wechat_no,total_wechat_no,child_chinese_name,recommender_code "
    "FROM presale_internal_contact WHERE is_deleted=0 "
    "AND total_wechat_no IS NOT NULL AND total_wechat_no<>'' "
    "AND wechat_no IS NOT NULL AND wechat_no<>'' ORDER BY id LIMIT 30"
)):
    wx_rows.append({
        "oid": p[0],
        "wechat": p[1],
        "total": p[2],
        "name": p[3],
        "code": p[4],
    })

wx_pick = []
used_total = set()
for r in wx_rows:
    if r["total"] in used_total:
        continue
    used_total.add(r["total"])
    wx_pick.append(r)
    if len(wx_pick) >= 10:
        break

records = []
meta = {"new_pure": [], "oid_match": [], "wechat_match": []}

for i in range(1, 71):
    row = {
        "意向学员微信号": "test_phase3_wx_{:03d}".format(i),
        "意向学员微信原始ID": "test_phase3_oid_{:03d}".format(i),
        "是否报名": "已报名" if i % 2 == 0 else "未报名",
        "推荐人": [],
    }
    records.append(row)
    meta["new_pure"].append({
        "idx": len(records),
        "oid": row["意向学员微信原始ID"],
        "wechat": row["意向学员微信号"],
    })

for c in oid_rows:
    row = {
        "意向学员微信号": c["wechat"],
        "意向学员微信原始ID": c["oid"],
        "是否报名": "已报名",
        "推荐人": [],
    }
    records.append(row)
    meta["oid_match"].append({
        "idx": len(records),
        "oid": c["oid"],
        "code": c["code"],
        "name": c["name"],
        "total": c["total"],
    })

for i, c in enumerate(wx_pick, 1):
    row = {
        "意向学员微信号": c["total"],
        "意向学员微信原始ID": "test_wxonly_{:03d}".format(i),
        "是否报名": "已报名",
        "推荐人": [],
    }
    records.append(row)
    meta["wechat_match"].append({
        "idx": len(records),
        "student_oid": row["意向学员微信原始ID"],
        "wechat": c["total"],
        "contact_oid": c["oid"],
        "code": c["code"],
        "name": c["name"],
    })

assert len(records) == 100, len(records)
OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
print("generated {} records -> {}".format(len(records), OUT))
print("breakdown: new=70 oid_match=20 wechat_match=10")
