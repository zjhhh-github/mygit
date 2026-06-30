import sqlite3, os

dbs = [
    r"C:\Users\LENOVO\Desktop\contact.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
]

for db in dbs:
    if not os.path.exists(db):
        print(db, "不存在")
        continue
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    # 列出所有表名
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    表名列表 = [r[0] for r in cur.fetchall()]
    print(f"\n{db}")
    print("  表：", 表名列表)
    if "ContactConfigTable" in 表名列表:
        cur.execute("PRAGMA table_info(ContactConfigTable)")
        cols = [(r[1], r[2]) for r in cur.fetchall()]
        print("  ContactConfigTable 字段：", cols)
        cur.execute("SELECT * FROM ContactConfigTable LIMIT 2")
        print("  样本数据：", cur.fetchall())
    conn.close()
