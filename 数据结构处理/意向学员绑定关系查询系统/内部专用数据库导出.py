import sqlite3
import json
import re
from pathlib import Path


# 数据库路径：读取桌面上的微信通讯录数据库。
DB_FILE = Path(r"C:\Users\LENOVO\Desktop\contact.db")

# 输出路径：保存 remark 符合“¿¿¿002126-杨斯琛”这种格式的联系人。
OUTPUT_FILE = Path(r"C:\Users\LENOVO\Desktop\内部通讯录.json")


def fetch_special_remark_contacts(db_file: Path):
    """
    查询 contact 表中 remark 符合指定格式的记录，并转换成业务需要的字段名。

    匹配格式：
    - 以 3 个 ¿ 开头
    - 后面跟 6 位数字
    - 再跟一个横杠 -
    - 横杠后至少有 1 个字符

    示例：¿¿¿002126-杨斯琛
    """
    pattern = re.compile(r"^¿¿¿\d{6}-.+")

    with sqlite3.connect(str(db_file)) as connection:
        cursor = connection.cursor()
        rows = cursor.execute(
            """
            SELECT username, alias, remark
            FROM contact
            WHERE remark IS NOT NULL
              AND TRIM(remark) != ''
            """
        ).fetchall()

    result = []

    for username, alias, remark in rows:
        username = (username or "").strip()
        alias = (alias or "").strip()
        remark = (remark or "").strip()
        if not pattern.match(remark):
            continue

        # alias 是微信号；如果 alias 为空，总微信号就使用 username 兜底。
        total_wechat = alias if alias else username

        result.append(
            {
                "内部备注": remark,
                "微信号": alias,
                "微信ID": username,
                "总微信号": total_wechat,
            }
        )

    return result


def main():
    """脚本入口：查询数据库并导出 JSON。"""
    contacts = fetch_special_remark_contacts(DB_FILE)

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(contacts, file, ensure_ascii=False, indent=2)

    print("导出完成")
    print(f"匹配数量：{len(contacts)}")
    print(f"输出文件：{OUTPUT_FILE}")


if __name__ == "__main__":
    main()
