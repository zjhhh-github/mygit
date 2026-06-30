#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
阶段A：筛选并分配“非内部直播群成员”到指定群（仅生成清单，不执行拉群）

功能目标：
1) 读取名单文本（默认：C:\\Users\\LENOVO\\Desktop\\_脚本输入_1.txt）
2) 读取微信 SQLite 数据库（默认：C:\\Users\\LENOVO\\Desktop\\contact.db）
3) 找出“名单中可匹配联系人”且“当前不在任意内部直播群”的成员
4) 按每批 40 人分配到：内部直播群⑰、内部直播群⑱、内部直播群⑲
5) 每个群最多 500 人（会扣除当前群人数，按剩余容量分配）

注意：
- 本脚本不操作微信 UI，仅输出结果文件，便于人工核对后再执行下一步。
- 若目标群名在数据库中不存在，脚本会直接报错并提示可选群名。
"""

import argparse
import csv
import re
import sqlite3
from collections import defaultdict
from collections import namedtuple
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

# 使用 namedtuple 兼容较低 Python 版本（如 3.6）
Contact = namedtuple("Contact", ["id", "username", "nick_name", "remark", "alias", "delete_flag"])
GroupInfo = namedtuple("GroupInfo", ["room_id", "room_username", "group_name", "current_count"])


def 标准化文本(text: str) -> str:
    """
    做基础标准化：
    - 去首尾空白
    - 转小写（兼容英文名）
    - 去除中英文空格（减少因格式差异导致的未匹配）
    """
    if text is None:
        return ""
    return re.sub(r"\s+", "", str(text).strip().lower())


def 从名单行提取候选姓名(line: str) -> List[str]:
    """
    从名单每行中提取姓名列表。

    输入常见格式：
    - ¿¿¿000027-杜启钰 杜启铭
    - ¿¿¿000001-Lily老师

    处理逻辑：
    1) 优先取最后一个 '-' 后面的内容
    2) 按空白分割为多个姓名
    """
    text = str(line).strip()
    if not text:
        return []

    # 兼容“编号-姓名”格式，取最后一个短横线后的正文
    payload = text.split("-")[-1].strip() if "-" in text else text
    if not payload:
        return []

    names = [n.strip() for n in re.split(r"\s+", payload) if n.strip()]
    return names


def 读取名单姓名(名单路径: Path) -> Tuple[List[str], List[str]]:
    """
    读取名单文件并抽取候选姓名。

    返回：
    - 所有提取到的姓名（保留顺序，允许重复）
    - 被忽略的占位词（例如“空”）
    """
    if not 名单路径.exists():
        raise FileNotFoundError(f"未找到名单文件：{名单路径}")

    忽略词 = {"空"}
    extracted: List[str] = []
    ignored: List[str] = []

    with 名单路径.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            for name in 从名单行提取候选姓名(line):
                if name in 忽略词:
                    ignored.append(name)
                    continue
                extracted.append(name)

    return extracted, ignored


def 连接数据库(db_path: Path) -> sqlite3.Connection:
    """创建 SQLite 连接。"""
    if not db_path.exists():
        raise FileNotFoundError(f"未找到数据库文件：{db_path}")
    return sqlite3.connect(str(db_path))


def 查询所有内部直播群(conn: sqlite3.Connection) -> List[GroupInfo]:
    """
    查询所有“内部直播群”及当前成员数。
    成员数来自 chatroom_member 表。
    """
    sql = """
    SELECT
        cr.id AS room_id,
        c.username AS room_username,
        c.nick_name AS group_name,
        COUNT(cm.member_id) AS current_count
    FROM chat_room cr
    JOIN contact c
      ON c.username = cr.username
    LEFT JOIN chatroom_member cm
      ON cm.room_id = cr.id
    WHERE c.nick_name LIKE '内部直播群%'
    GROUP BY cr.id, c.username, c.nick_name
    ORDER BY c.nick_name;
    """
    rows = conn.execute(sql).fetchall()
    groups: List[GroupInfo] = []
    for room_id, room_username, group_name, current_count in rows:
        groups.append(
            GroupInfo(
                room_id=int(room_id),
                room_username=str(room_username or ""),
                group_name=str(group_name or ""),
                current_count=int(current_count or 0),
            )
        )
    return groups


def 选择目标群(全部群: List[GroupInfo], 目标群名列表: Iterable[str]) -> List[GroupInfo]:
    """
    从全部内部群中筛选目标群；若缺失则报错并给出候选群名。
    """
    by_name = {g.group_name: g for g in 全部群}
    selected: List[GroupInfo] = []
    missing: List[str] = []
    for name in 目标群名列表:
        if name in by_name:
            selected.append(by_name[name])
        else:
            missing.append(name)

    if missing:
        all_names = "、".join(g.group_name for g in 全部群) if 全部群 else "（数据库未找到任何内部直播群）"
        raise ValueError(
            f"目标群不存在：{missing}。\n数据库中可用内部直播群：{all_names}"
        )
    return selected


def 查询任意内部群成员ID集合(conn: sqlite3.Connection, 全部内部群: List[GroupInfo]) -> Set[int]:
    """
    获取“任意内部直播群”成员 ID 集合。
    业务定义：只要在任意一个内部直播群中，就视为“已在内部群”。
    """
    room_ids = [g.room_id for g in 全部内部群]
    if not room_ids:
        return set()

    placeholders = ",".join("?" for _ in room_ids)
    sql = f"SELECT DISTINCT member_id FROM chatroom_member WHERE room_id IN ({placeholders})"
    rows = conn.execute(sql, room_ids).fetchall()
    return {int(r[0]) for r in rows if r and r[0] is not None}


def 读取联系人(conn: sqlite3.Connection) -> List[Contact]:
    """
    读取联系人（排除群本身）。
    - username 以 @chatroom 结尾的通常是群，不参与“成员匹配”。
    """
    sql = """
    SELECT id, username, nick_name, remark, alias, delete_flag
    FROM contact
    WHERE username NOT LIKE '%@chatroom'
    """
    rows = conn.execute(sql).fetchall()
    contacts: List[Contact] = []
    for row in rows:
        contacts.append(
            Contact(
                id=int(row[0]),
                username=str(row[1] or ""),
                nick_name=str(row[2] or ""),
                remark=str(row[3] or ""),
                alias=str(row[4] or ""),
                delete_flag=int(row[5] or 0),
            )
        )
    return contacts


def 构建联系人索引(contacts: List[Contact]) -> Dict[str, List[Contact]]:
    """
    构建“标准化姓名 -> 联系人列表”索引。
    匹配字段：nick_name、remark、alias
    """
    idx: Dict[str, List[Contact]] = defaultdict(list)
    for c in contacts:
        for raw in (c.nick_name, c.remark, c.alias):
            key = 标准化文本(raw)
            if key:
                idx[key].append(c)
    return idx


def 分配成员到目标群(
    candidates: List[Contact],
    targets: List[GroupInfo],
    batch_size: int,
    max_group_size: int,
) -> Tuple[List[dict], List[Contact], Dict[str, int]]:
    """
    将候选成员按“群顺序 + 容量上限”分配到目标群。

    返回：
    - 分配明细记录
    - 未分配成员（容量不足）
    - 分配后各群预计人数
    """
    if batch_size <= 0:
        raise ValueError("batch_size 必须大于 0")
    if max_group_size <= 0:
        raise ValueError("max_group_size 必须大于 0")

    # 计算每个群的可用容量
    remain: Dict[str, int] = {}
    final_count: Dict[str, int] = {}
    for g in targets:
        cap = max_group_size - g.current_count
        remain[g.group_name] = max(cap, 0)
        final_count[g.group_name] = g.current_count

    allocated_rows: List[dict] = []
    unallocated: List[Contact] = []
    ptr = 0

    for c in candidates:
        # 跳到下一个有容量的群
        while ptr < len(targets) and remain[targets[ptr].group_name] <= 0:
            ptr += 1

        if ptr >= len(targets):
            # 全部目标群无容量，剩余成员无法分配
            unallocated.append(c)
            continue

        g = targets[ptr]
        group_name = g.group_name
        remain[group_name] -= 1
        final_count[group_name] += 1

        # 批次号按“该群已分配人数 / batch_size”计算
        allocated_in_group = final_count[group_name] - g.current_count
        batch_no = (allocated_in_group - 1) // batch_size + 1

        allocated_rows.append(
            {
                "member_id": c.id,
                "username": c.username,
                "display_name": c.nick_name or c.remark or c.alias,
                "target_group": group_name,
                "target_room_id": g.room_id,
                "target_room_username": g.room_username,
                "batch_no": batch_no,
            }
        )

    return allocated_rows, unallocated, final_count


def 写CSV(path: Path, rows: List[dict], fieldnames: List[str]) -> None:
    """统一写 CSV（UTF-8 BOM，便于 Excel 打开中文）。"""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="筛选并分配非内部直播群成员（阶段A）")
    parser.add_argument(
        "--input-txt",
        default=r"C:\Users\LENOVO\Desktop\_脚本输入_1.txt",
        help="名单文本路径",
    )
    parser.add_argument(
        "--db-path",
        default=r"C:\Users\LENOVO\Desktop\contact.db",
        help="微信 SQLite 数据库路径",
    )
    parser.add_argument(
        "--target-groups",
        nargs="+",
        default=["内部直播群⑰"],
        help="目标群名列表，按分配顺序生效",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=40,
        help="每批人数",
    )
    parser.add_argument(
        "--max-group-size",
        type=int,
        default=500,
        help="每个群最大人数",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="输出目录（默认当前目录）",
    )
    args = parser.parse_args()

    名单路径 = Path(args.input_txt)
    db_path = Path(args.db_path)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 读取名单
    extracted_names, ignored_tokens = 读取名单姓名(名单路径)
    if not extracted_names:
        raise ValueError("名单中未提取到有效姓名，请检查输入文件格式。")

    # 2) 连接数据库并读取基础数据
    conn = 连接数据库(db_path)
    try:
        all_internal_groups = 查询所有内部直播群(conn)
        target_groups = 选择目标群(all_internal_groups, args.target_groups)
        internal_member_ids = 查询任意内部群成员ID集合(conn, all_internal_groups)
        contacts = 读取联系人(conn)
    finally:
        conn.close()

    # 3) 构建匹配索引
    contact_idx = 构建联系人索引(contacts)

    matched_unique: Dict[int, Contact] = {}
    detail_rows: List[dict] = []
    unmatched_names: List[str] = []
    ambiguous_names: List[str] = []

    for raw_name in extracted_names:
        key = 标准化文本(raw_name)
        hits = contact_idx.get(key, [])

        if not hits:
            unmatched_names.append(raw_name)
            detail_rows.append(
                {
                    "input_name": raw_name,
                    "match_status": "未匹配",
                    "matched_count": 0,
                    "member_id": "",
                    "username": "",
                    "display_name": "",
                    "in_any_internal_group": "",
                }
            )
            continue

        # 去重同一联系人（同一 key 可能来自 nick_name/remark/alias 多字段）
        uniq_by_id = {}
        for c in hits:
            uniq_by_id[c.id] = c
        uniq_hits = list(uniq_by_id.values())

        if len(uniq_hits) > 1:
            ambiguous_names.append(raw_name)
            detail_rows.append(
                {
                    "input_name": raw_name,
                    "match_status": "多重匹配",
                    "matched_count": len(uniq_hits),
                    "member_id": "",
                    "username": "",
                    "display_name": "",
                    "in_any_internal_group": "",
                }
            )
            continue

        c = uniq_hits[0]
        in_internal = "是" if c.id in internal_member_ids else "否"
        detail_rows.append(
            {
                "input_name": raw_name,
                "match_status": "唯一匹配",
                "matched_count": 1,
                "member_id": c.id,
                "username": c.username,
                "display_name": c.nick_name or c.remark or c.alias,
                "in_any_internal_group": in_internal,
            }
        )

        # 候选条件：唯一匹配且当前不在任意内部直播群
        if c.id not in internal_member_ids:
            matched_unique[c.id] = c

    candidates = list(matched_unique.values())

    # 4) 分配到目标群（仅计划，不执行）
    allocated_rows, unallocated_contacts, final_count = 分配成员到目标群(
        candidates=candidates,
        targets=target_groups,
        batch_size=args.batch_size,
        max_group_size=args.max_group_size,
    )

    # 5) 输出文件
    detail_path = out_dir / "内部直播群_筛选明细.csv"
    allocation_path = out_dir / "内部直播群_分配结果.csv"
    summary_path = out_dir / "内部直播群_汇总统计.csv"

    写CSV(
        detail_path,
        detail_rows,
        fieldnames=[
            "input_name",
            "match_status",
            "matched_count",
            "member_id",
            "username",
            "display_name",
            "in_any_internal_group",
        ],
    )
    写CSV(
        allocation_path,
        allocated_rows,
        fieldnames=[
            "member_id",
            "username",
            "display_name",
            "target_group",
            "target_room_id",
            "target_room_username",
            "batch_no",
        ],
    )

    summary_rows: List[dict] = []
    for g in target_groups:
        assigned_count = sum(1 for row in allocated_rows if row["target_group"] == g.group_name)
        summary_rows.append(
            {
                "group_name": g.group_name,
                "current_count": g.current_count,
                "assigned_count": assigned_count,
                "expected_final_count": final_count[g.group_name],
                "max_group_size": args.max_group_size,
            }
        )
    summary_rows.append(
        {
            "group_name": "全局统计",
            "current_count": "",
            "assigned_count": len(allocated_rows),
            "expected_final_count": "",
            "max_group_size": "",
        }
    )
    summary_rows.append(
        {
            "group_name": "未分配(容量不足)",
            "current_count": "",
            "assigned_count": len(unallocated_contacts),
            "expected_final_count": "",
            "max_group_size": "",
        }
    )
    summary_rows.append(
        {
            "group_name": "未匹配姓名数",
            "current_count": "",
            "assigned_count": len(unmatched_names),
            "expected_final_count": "",
            "max_group_size": "",
        }
    )
    summary_rows.append(
        {
            "group_name": "多重匹配姓名数",
            "current_count": "",
            "assigned_count": len(ambiguous_names),
            "expected_final_count": "",
            "max_group_size": "",
        }
    )
    summary_rows.append(
        {
            "group_name": "忽略词条数(如空)",
            "current_count": "",
            "assigned_count": len(ignored_tokens),
            "expected_final_count": "",
            "max_group_size": "",
        }
    )
    写CSV(
        summary_path,
        summary_rows,
        fieldnames=["group_name", "current_count", "assigned_count", "expected_final_count", "max_group_size"],
    )

    print("执行完成（仅阶段A，不含微信拉群操作）")
    print(f"名单文件：{名单路径}")
    print(f"数据库文件：{db_path}")
    print(f"筛选明细：{detail_path}")
    print(f"分配结果：{allocation_path}")
    print(f"汇总统计：{summary_path}")
    print(f"唯一匹配且不在任意内部群的候选人数：{len(candidates)}")
    print(f"成功分配人数：{len(allocated_rows)}")
    print(f"未分配人数（容量不足）：{len(unallocated_contacts)}")
    print(f"未匹配姓名数：{len(unmatched_names)}")
    print(f"多重匹配姓名数：{len(ambiguous_names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
