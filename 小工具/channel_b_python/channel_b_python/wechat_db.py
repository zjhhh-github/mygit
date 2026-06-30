# -*- coding: utf-8 -*-
"""读取微信 contact.db，替换影刀 sqlite3 节点。"""
from __future__ import annotations

import os
import sqlite3
from typing import Dict, List, Tuple

from utils import 去重列表

# CONTACT_SQL_INTERNAL = r"""
# SELECT username, alias, remark
# FROM contact
# WHERE username NOT LIKE '%@chatroom'
#   AND username NOT LIKE '%@openim'
#   AND username NOT LIKE 'gh\_%' ESCAPE '\'
#   AND (
#         remark GLOB '¿¿¿[0-9][0-9][0-9][0-9][0-9][0-9]-*'
#         OR
#         remark GLOB '!!![0-9][0-9][0-9][0-9][0-9][0-9]-*'
#       )
# ORDER BY CAST(substr(remark, 4, 6) AS INTEGER) ASC;
# """

# CONTACT_SQL_PROSPECT = r"""
# SELECT username, alias, remark
# FROM contact
# WHERE username NOT LIKE '%@chatroom'
#   AND username NOT LIKE '%@openim'
#   AND username NOT LIKE 'gh\_%' ESCAPE '\'
# ORDER BY rowid ASC;
# """

CONTACT_SQL_INTERNAL = r"""
SELECT username, alias, remark
FROM contact
WHERE username NOT LIKE '%@chatroom'
  AND username NOT LIKE '%@openim'
  AND username NOT LIKE 'gh\_%' ESCAPE '\'
  AND (
        remark GLOB '¿¿¿[0-9][0-9][0-9][0-9][0-9][0-9]-*'
      )
ORDER BY CAST(substr(remark, 4, 6) AS INTEGER) ASC;
"""

CONTACT_SQL_PROSPECT = r"""
SELECT username, alias, remark
FROM contact
WHERE username NOT LIKE '%@chatroom'
  AND username NOT LIKE '%@openim'
  AND username NOT LIKE 'gh\_%' ESCAPE '\'
ORDER BY rowid ASC;
"""

def 查询_contact_db(db_path: str, sql: str) -> List[Tuple[str, str, str]]:
    if not os.path.exists(db_path):
        raise FileNotFoundError("数据库文件不存在：" + db_path)
    conn = sqlite3.connect(db_path)
    try:
        return [(r[0] or "", r[1] or "", r[2] or "") for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def 读取内部微信通讯录(db_paths: List[str]) -> Dict[str, List[str]]:
    """返回：微信原始ID -> [微信号, 备注]。"""
    rows = []
    for path in db_paths:
        if not path:
            continue
        rows.extend(查询_contact_db(path, CONTACT_SQL_INTERNAL))
    rows = 去重列表(rows)

    result: Dict[str, List[str]] = {}
    for 微信原始ID, 微信号, 备注 in rows:
        result[微信原始ID] = [微信号, 备注]
    return result


def 读取意向微信通讯录(db_path: str, base: Dict[str, List[str]] | None = None) -> Dict[str, List[str]]:
    """意向库只补充 微信原始ID -> [微信号, '']，不覆盖已有备注。"""
    result = dict(base or {})
    if not db_path:
        return result
    for 微信原始ID, 微信号, _备注 in 查询_contact_db(db_path, CONTACT_SQL_PROSPECT):
        if 微信原始ID not in result:
            result[微信原始ID] = [微信号, ""]
    return result


def 解析内部备注(备注: str):
    编号 = ""
    孩子中文全名 = ""
    if (备注.startswith("¿¿¿") or 备注.startswith("!!!")) and "-" in 备注:
        编号 = 备注.split("-", 1)[0][3:]
        孩子中文全名 = 备注.split("-", 1)[1]
    return 编号, 孩子中文全名


def 是大号备注(备注: str) -> bool:
    """¿¿¿ 开头为大号（主微信），仅大号参与渠道 / 带领计算。"""
    return bool(备注) and 备注.startswith("¿¿¿")


def 是小号备注(备注: str) -> bool:
    """!!! 开头为小号（附属微信），不参与渠道 / 带领计算，写回时相关字段留空。"""
    return bool(备注) and 备注.startswith("!!!")


def 是内部业务备注(备注: str) -> bool:
    """是否为内部通讯录有效备注（大号或小号）。"""
    return 是大号备注(备注) or 是小号备注(备注)


def 收集渠道计算学员编号(微信原始ID映射微信号_备注: Dict[str, List[str]]) -> List[str]:
    """
    收集需要参与渠道 / 带领计算的学员编号。

    仅大号 (¿¿¿) 参与计算；小号 (!!!) 完全不进入计算列表。
    且备注必须满足：
    - 包含 "-"
    - 不包含 "-删除"
    - 不包含 "-空"
    同编号去重（一个大号只算一次）。
    """
    结果: List[str] = []
    已存在: set = set()

    for 微信号_备注 in 微信原始ID映射微信号_备注.values():
        备注 = 微信号_备注[1] if len(微信号_备注) > 1 else ""
        if not 是大号备注(备注):
            continue
        if "-" not in 备注:
            continue
        if "-删除" in 备注 or "-空" in 备注:
            continue
        编号, _姓名 = 解析内部备注(备注)
        if not 编号 or 编号 in 已存在:
            continue
        结果.append(编号)
        已存在.add(编号)

    return 结果
