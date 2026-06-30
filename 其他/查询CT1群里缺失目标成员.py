import sqlite3
from collections import defaultdict
from typing import Dict, List, Set, Tuple


# 默认数据库路径；如需更换，可在 main() 中改成你的路径
数据库路径 = r"C:\Users\LENOVO\Desktop\contact.db"

# 目标成员（username/wxid）；用于统计每个群命中了几人
目标成员列表 = [
    "wxid_8w7pkdmaogs412",  # Paisley
    "wxid_3c0kg5hp6eft22",  # Nina
    "wxid_nuwg0t3iutkt22",  # Lily CT教务
    "wxid_jz6w1ttgbgpq22",  # Thea
]

# 目标成员名称映射；用于输出“缺少哪个人”时展示更直观的姓名
目标成员姓名映射: Dict[str, str] = {
    "wxid_8w7pkdmaogs412": "Paisley",
    "wxid_3c0kg5hp6eft22": "Nina",
    "wxid_nuwg0t3iutkt22": "Lily CT教务",
    "wxid_jz6w1ttgbgpq22": "Thea",
}


def 查询CT1群目标成员命中明细(conn: sqlite3.Connection) -> List[Tuple[str, str, str]]:
    """
    查询规则：
    1) 群范围：contact.nick_name LIKE '% CT1%' 且 contact.username LIKE '%@chatroom%'
    2) 群成员映射：chatroom_member -> name2id -> contact
    3) 查询每个群命中的目标成员明细（后续由 Python 计算缺失成员）

    返回：
    - [(群名称, 群账号, 命中成员username或空值), ...]
    """
    # 使用 CTE 让查询结构更清晰，后续维护时更容易定位问题
    sql = """
    WITH target_users AS (
        SELECT ? AS username
        UNION ALL SELECT ?
        UNION ALL SELECT ?
        UNION ALL SELECT ?
    ),
    candidate_groups AS (
        SELECT
            c.username AS room_username,
            c.nick_name AS group_name,
            d.room_id_ AS room_id
        FROM contact c
        LEFT JOIN chat_room_info_detail d
            ON d.username_ = c.username
        WHERE c.nick_name LIKE '% CT1%'
          AND c.username LIKE '%@chatroom%'
    ),
    group_hit_detail AS (
        SELECT
            g.room_username,
            g.group_name,
            m.username AS hit_username
        FROM candidate_groups g
        LEFT JOIN chatroom_member cm
            ON cm.room_id = g.room_id
        LEFT JOIN name2id n
            ON n.rowid = cm.member_id
        LEFT JOIN contact m
            ON m.username = n.username
           AND m.username IN (SELECT username FROM target_users)
    )
    SELECT
        COALESCE(group_name, room_username) AS 群名称,
        room_username AS 群账号,
        hit_username AS 命中成员
    FROM group_hit_detail
    ORDER BY 群名称, 群账号, 命中成员;
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql, tuple(目标成员列表))
        return cursor.fetchall()
    finally:
        cursor.close()


def 打印结果(rows: List[Tuple[str, str, str]]) -> None:
    """
    逐行打印“哪个群缺少哪个人”：
    - 每一行对应一个缺失成员
    - 同一群若缺 2 人，则会打印 2 行
    """
    if not rows:
        print("未找到符合条件的群（在 CT1 群范围内，目标4人均已在群内）。")
        return

    # Windows 控制台默认常见为 gbk 编码；先做安全转换，避免特殊字符导致打印报错
    控制台编码 = "gbk"

    def 安全控制台文本(text: str) -> str:
        # 先替换常见的非断行空格，再把不可编码字符替换为可显示占位符
        规范文本 = text.replace("\xa0", " ")
        return 规范文本.encode(控制台编码, errors="replace").decode(控制台编码)

    # 先按群聚合“已命中目标成员”，再据此反推“缺失成员”
    群到命中成员: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    for 群名称, 群账号, 命中成员 in rows:
        if 命中成员:
            群到命中成员[(群名称, 群账号)].add(命中成员)
        else:
            # 即使命中为空，也要确保该群被记录，后续才能输出“缺4人”
            群到命中成员[(群名称, 群账号)] |= set()

    print("查询结果：CT1 群范围内，逐行打印“哪个群缺少哪个人”")
    print("-" * 130)
    print(f"{'群名称':<36} {'群账号':<40} {'缺少成员':<46}")
    print("-" * 130)

    结果行数 = 0
    符合群数 = 0
    # 单独收集“缺失4人”的群，便于最后独立打印
    缺失4人群列表: List[Tuple[str, str]] = []
    for (群名称, 群账号), 命中集合 in sorted(群到命中成员.items(), key=lambda x: (x[0][0], x[0][1])):
        缺失成员列表 = [u for u in 目标成员列表 if u not in 命中集合]
        if not 缺失成员列表:
            # 4人都在群内时不输出（与“缺少谁”场景一致）
            continue

        符合群数 += 1
        if len(缺失成员列表) == len(目标成员列表):
            缺失4人群列表.append((群名称, 群账号))

        for wxid in 缺失成员列表:
            姓名 = 目标成员姓名映射.get(wxid, "")
            缺少成员文本 = f"{姓名}({wxid})" if 姓名 else wxid

            # 控制输出宽度，避免超长文本撑乱表格
            显示群名称 = (群名称[:33] + "...") if len(群名称) > 36 else 群名称
            显示群账号 = (群账号[:37] + "...") if len(群账号) > 40 else 群账号
            显示缺少成员 = (缺少成员文本[:43] + "...") if len(缺少成员文本) > 46 else 缺少成员文本

            显示群名称 = 安全控制台文本(显示群名称)
            显示群账号 = 安全控制台文本(显示群账号)
            显示缺少成员 = 安全控制台文本(显示缺少成员)

            print(f"{显示群名称:<36} {显示群账号:<40} {显示缺少成员:<46}")
            结果行数 += 1

    print("-" * 130)
    print(f"共 {符合群数} 个群存在缺失，共打印 {结果行数} 行“群-缺失成员”明细。")

    # 按你的要求：把“缺失4个”的群单独打印
    print("\n缺失4个成员的群（单独打印）")
    print("-" * 100)
    print(f"{'群名称':<36} {'群账号':<40} {'缺失情况':<20}")
    print("-" * 100)
    if not 缺失4人群列表:
        print("无（当前 CT1 群范围内，没有群同时缺失这4人）")
    else:
        for 群名称, 群账号 in 缺失4人群列表:
            显示群名称 = (群名称[:33] + "...") if len(群名称) > 36 else 群名称
            显示群账号 = (群账号[:37] + "...") if len(群账号) > 40 else 群账号
            显示群名称 = 安全控制台文本(显示群名称)
            显示群账号 = 安全控制台文本(显示群账号)
            print(f"{显示群名称:<36} {显示群账号:<40} {'缺失4/4':<20}")
    print("-" * 100)
    print(f"共 {len(缺失4人群列表)} 个群缺失4个目标成员。")


def main() -> None:
    # 只读查询，不修改数据库内容
    conn = sqlite3.connect(数据库路径)
    try:
        rows = 查询CT1群目标成员命中明细(conn)
    finally:
        conn.close()

    打印结果(rows)


if __name__ == "__main__":
    main()
