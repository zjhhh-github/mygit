import sqlite3
from pathlib import Path

# 数据源路径
DB_MAIN = r'C:\Users\LENOVO\Desktop\contact.db'
DB_YIXIANG = r'C:\Users\LENOVO\Desktop\意向专用contact.db'

# 输出路径
OUT_PATH = Path(r'C:\Users\LENOVO\Desktop\汇总微信数据库.txt')


def score(username: str, alias: str) -> int:
    """
    计算一条记录的"完整度"得分，用于在重复 username 时保留更全的那条。
    username 和 alias 越长（内容越多）得分越高。
    """
    return len(username or '') + len(alias or '')


def read_db(db_path: str) -> dict[str, tuple[str, str]]:
    """
    读取 sqlite 数据库中的全部 username 和 alias。

    返回：
        以 username 为 key 的字典，value 为 (username, alias)
    """
    result: dict[str, tuple[str, str]] = {}

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        # 取全部记录，不过滤 remark
        cursor.execute("SELECT username, alias FROM contact")

        for row in cursor.fetchall():
            username = str(row[0]) if row[0] is not None else ''
            alias = str(row[1]) if row[1] is not None else ''

            if not username:
                continue  # 跳过没有 username 的行

            # 跳过群聊、企业IM账号和公众号
            if '@chatroom' in username or '@openim' in username or username.startswith('gh_'):
                continue

            # 如果已存在相同 username，保留完整度得分更高的那条
            if username in result:
                existing = result[username]
                if score(username, alias) > score(*existing):
                    result[username] = (username, alias)
            else:
                result[username] = (username, alias)

    finally:
        conn.close()

    return result


# ── 第一步：读取主通讯录（全部记录）──────────────────────────────────────
print(f'正在读取主通讯录：{DB_MAIN}')
main_data = read_db(DB_MAIN)
print(f'  共读取 {len(main_data)} 条')

# ── 第二步：读取意向专用通讯录（全部记录）────────────────────────────────
print(f'正在读取意向专用通讯录：{DB_YIXIANG}')
yixiang_data = read_db(DB_YIXIANG)
print(f'  共读取 {len(yixiang_data)} 条')

# ── 第三步：合并，重复 username 保留更完整的那条 ────────────────────────
merged: dict[str, tuple[str, str]] = dict(main_data)  # 先放入主通讯录数据

for username, record in yixiang_data.items():
    if username in merged:
        # 保留完整度更高的那条
        if score(*record) > score(*merged[username]):
            merged[username] = record
    else:
        merged[username] = record

print(f'合并后共 {len(merged)} 条（去重后）')

# ── 第四步：写入输出文件 ────────────────────────────────────────────────
with OUT_PATH.open('w', encoding='utf-8') as f:
    f.write('alias\tusername\n')
    for username, alias in sorted(merged.values(), key=lambda x: x[0]):
        f.write(f'{alias}\t{username}\n')

print(f'已输出到：{OUT_PATH}')
