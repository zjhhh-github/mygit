# -*- coding: utf-8 -*-

from operator import index
import sqlite3
import pandas as pd
import math

# 定义获取SQLite连接的函数
def get_sqlite_connection(db_path):
    """
    获取SQLite数据库连接
    :param db_path: 数据库文件路径
    :return: sqlite3.Connection对象
    """
    try:
        conn = sqlite3.connect(db_path)
        # 设置行工厂，让查询结果可以通过列名访问（可选，提升易用性）
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"连接数据库失败: {e}")
        raise

def query_sqlite(conn, sql, params=None):
    """
    执行SQL查询并返回pandas DataFrame
    :param conn: 数据库连接对象
    :param sql: 查询SQL语句（含占位符?）
    :param params: 查询参数（元组/列表，可选）
    :return: pandas.DataFrame
    """
    try:
        # 使用pandas的read_sql_query，支持参数化查询
        if params is None:
            df = pd.read_sql_query(sql, conn)
        else:
            df = pd.read_sql_query(sql, conn, params=params)
        return df
    except sqlite3.Error as e:
        print(f"查询失败: {e}")
        raise

def to_str(v):
    if isinstance(v, float) and math.isnan(v):
        return ""
    return str(v)

df_nbtx = pd.read_excel(r"C:\Users\LENOVO\AppData\Roaming\kingsoft\office6\templates\et\zh_CN\报名录入.xlsx", sheet_name='内部通讯录', header=1)

bm = pd.read_excel(r"C:\Users\LENOVO\AppData\Roaming\kingsoft\office6\templates\et\zh_CN\报名录入.xlsx", sheet_name='宝妈', header=1)
baoma2qunming = {}

for i in bm.index:
    baoma2qunming[bm.iloc[i]['宝妈']] = bm.iloc[i]['专属带领群']

baoma2xueyuan = {}
for i in bm['宝妈']:
    baoma2xueyuan[i] = []
for i in df_nbtx.index:
    if df_nbtx.iloc[i]['带领C'] in baoma2xueyuan:
        baoma2xueyuan[df_nbtx.iloc[i]['带领C']].append(df_nbtx.iloc[i]['学员'])

conn = get_sqlite_connection(r"C:\Users\LENOVO\Desktop\contact.db")

dailinqun2chengyuan = {}

PREFIX = "\u00bf\u00bf\u00bf"  # ¿¿¿ 前缀，Python 3.6 源码不支持直接写 ¿
for i in bm['专属带领群']:
    if pd.isna(i):  # 跳过空的群名
        continue
    df = query_sqlite(conn, "SELECT \
                                DISTINCT remark,\
                                CASE \
                                    WHEN INSTR(remark, '-') > 0\
                                    THEN SUBSTR(remark, 1, INSTR(remark, '-') - 1)\
                                    ELSE remark\
                                END AS number\
                            FROM contact WHERE username in \
                                (SELECT username \
                                FROM name2id WHERE rowid IN \
                                    (SELECT member_id \
                                    FROM chatroom_member WHERE room_id in \
                                        (SELECT room_id_ \
                                        FROM chat_room_info_detail WHERE username_ in \
                                            (SELECT username \
                                            FROM contact WHERE nick_name like ?))))\
                            AND remark like ? ORDER BY number ASC;", (i + "%", PREFIX + "%"))
    df_list = df['remark'].values.tolist()
    dailinqun2chengyuan[i] = df_list
jishu = 0
jishu2 = 0
with open(r"C:\Users\LENOVO\Desktop\_输出结果_2.txt", 'w+', encoding='utf-8') as f:
    f.write("")
for baoma,qunming in baoma2qunming.items():
    if pd.isna(qunming):  # 没有专属带领群，跳过
        continue
    suoyouxueyuan = baoma2xueyuan[baoma]
    qunchengyuan = dailinqun2chengyuan[qunming]
    result = [item for item in suoyouxueyuan if item not in qunchengyuan]
    if len(result) > 0:
        print(f"不在{baoma}专属带领群的成员有：{result}") 
        print(f"{len(result)}个")
        print()
        danhangshuju = '\t'.join([to_str(v) for v in bm[bm['宝妈'] == baoma].values.tolist()[0]])
        with open(r"C:\Users\LENOVO\Desktop\_输出结果_2.txt", 'a', encoding='utf-8') as f:
            f.write(f"{danhangshuju}\n")
        jishu2 += 1
        jishu += len(result)
        
    # else:
    #     print(f"{baoma}专属带领群的成员全部在群中") 

print(f"不在专属带领群的成员总数为：{jishu}")
# print(f"群成员不全的有：{jishu2}个")
