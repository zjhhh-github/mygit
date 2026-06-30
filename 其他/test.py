import re
import sqlite3
from collections import defaultdict


输入文件路径 = r"C:\Users\LENOVO\Desktop\_脚本输入_1.txt"
数据库路径 = r"C:\Users\LENOVO\Desktop\contact.db"


def 读取学员remark集合(文件路径):
    """
    从文本读取学员 remark，支持换行/空格/中英文逗号/分号分隔。
    """
    with open(文件路径, "r", encoding="utf-8") as f:
        原始文本 = f.read()

    # 统一按常见分隔符切分，过滤空字符串，避免把空白写入集合
    切分结果 = re.split(r"[\s,，;；]+", 原始文本.strip())
    return {项.strip() for 项 in 切分结果 if 项.strip()}


def 根据remark查询username集合(conn, remark集合):
    """
    根据输入的 remark 集合，从 contact 表查询对应 username。
    说明：
    - 同一个 remark 可能对应多个 username，因此返回 username 集合。
    - 为空的 username 会被过滤，避免影响后续集合匹配。
    """
    if not remark集合:
        return set(), set()

    占位符 = ",".join(["?"] * len(remark集合))
    sql = f"""
    SELECT remark, username
    FROM contact
    WHERE remark IN ({占位符});
    """
    cursor = conn.cursor()
    cursor.execute(sql, tuple(remark集合))
    rows = cursor.fetchall()
    cursor.close()

    命中remark集合 = set()
    username集合 = set()
    for remark, username in rows:
        if remark:
            命中remark集合.add(remark)
        if username:
            username集合.add(username)

    未命中remark集合 = set(remark集合) - 命中remark集合
    return username集合, 未命中remark集合


def 查询目标群成员(conn):
    """
    查询所有满足以下条件的群成员：
    1) 群 nick_name 以 XXJ（不区分大小写）结尾；
    2) 群 nick_name 包含 ' CT10'。
    返回值：[(群ID, 群名, 成员username), ...]
    """
    sql = """
    SELECT
        cr.username_ AS room_username,
        COALESCE(g.nick_name, cr.username_) AS group_name,
        m.username AS member_username
    FROM chat_room_info_detail AS cr
    JOIN chatroom_member AS cm ON cm.room_id = cr.room_id_
    JOIN name2id AS n ON n.rowid = cm.member_id
    JOIN contact AS m ON m.username = n.username
    LEFT JOIN contact AS g ON g.username = cr.username_
    WHERE LOWER(COALESCE(g.nick_name, '')) LIKE ? OR COALESCE(g.nick_name, '') LIKE ?;
    """
    参数 = ("%xxj", "% CT10%")
    cursor = conn.cursor()
    cursor.execute(sql, 参数)
    结果 = cursor.fetchall()
    cursor.close()
    return 结果


def main():
    学员remark集合 = 读取学员remark集合(输入文件路径)
    if not 学员remark集合:
        print("输入文件中未读取到任何学员 remark。")
        return

    conn = sqlite3.connect(数据库路径)
    try:
        # 先把输入的 remark 映射为学员 username，再参与群成员匹配
        学员username集合, 未命中remark集合 = 根据remark查询username集合(conn, 学员remark集合)
        if not 学员username集合:
            print("输入 remark 未在 contact 中查到对应 username。")
            return

        查询结果 = 查询目标群成员(conn)
    finally:
        conn.close()

    # 按群聚合成员 username，后续用集合交集快速判断命中
    群到成员集合 = defaultdict(set)
    群到群名 = {}
    for 群ID, 群名, 成员username in 查询结果:
        群到成员集合[群ID].add(成员username)
        群到群名[群ID] = 群名

    命中条数 = 0
    for 群ID, 成员集合 in 群到成员集合.items():
        命中学员 = sorted(学员username集合 & 成员集合)
        if not 命中学员:
            continue

        群名 = 群到群名.get(群ID, 群ID)
        for 学员username in 命中学员:
            # 逐条打印“群名 + 学员名”，满足你的输出要求
            print(f"群名: {群名} | 学员名: {学员username}")
            命中条数 += 1

    if 命中条数 == 0:
        print("未发现仍在群内的目标学员。")
    else:
        print(f"\n共命中 {命中条数} 条记录。")

    if 未命中remark集合:
        print(f"有 {len(未命中remark集合)} 个 remark 未在 contact 中匹配到 username。")


if __name__ == "__main__":
    main()
