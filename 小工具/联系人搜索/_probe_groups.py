# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

db = Path(r"C:\Users\LENOVO\Desktop\contact_内部专用.db")
conn = sqlite3.connect(str(db))
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("tables:", [r[0] for r in cur.fetchall()])
for t in ["chat_room", "chatroom_member"]:
    cur.execute(f"PRAGMA table_info({t})")
    print(t, [r[1] for r in cur.fetchall()])

# 找一个有 remark 的学员，查其所在群
cur.execute("""
SELECT c.id, c.username, c.remark
FROM contact c
WHERE c.remark GLOB '¿¿¿[0-9][0-9][0-9][0-9][0-9][0-9]-*'
LIMIT 3
""")
samples = cur.fetchall()
print("sample count", len(samples))
for cid, uname, remark in samples:
    cur.execute("""
    SELECT cg.nick_name
    FROM chatroom_member cm
    JOIN chat_room cr ON cr.id = cm.room_id
    JOIN contact cg ON cg.username = cr.username
    WHERE cm.member_id = ? AND cg.nick_name LIKE '内部直播群%'
    """, (cid,))
    live = [r[0] for r in cur.fetchall()]
    cur.execute("""
    SELECT cg.nick_name
    FROM chatroom_member cm
    JOIN chat_room cr ON cr.id = cm.room_id
    JOIN contact cg ON cg.username = cr.username
    WHERE cm.member_id = ? AND cg.nick_name LIKE '专属带领群%'
    """, (cid,))
    lead = [r[0] for r in cur.fetchall()]
    print("member", cid, uname, "live", live, "lead", lead)
conn.close()
