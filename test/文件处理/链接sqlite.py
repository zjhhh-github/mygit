# -*- coding: utf-8 -*-
import sqlite3
from sqlite3 import Error
import pandas as pd
def create_connection(db_file):
    """创建数据库连接"""
    conn = None
    try:
        # 连接到 SQLite 数据库
        # 若文件不存在，会自动创建；:memory: 表示创建内存数据库
        conn = sqlite3.connect(db_file)
        print(f"SQLite 连接成功，版本：{sqlite3.version}")
        return conn
    except Error as e:
        print(e)
    # 返回连接对象，调用方需负责关闭连接
    return conn

def select_all_users(db_file):
    """查询所有用户"""
    conn = create_connection(db_file)
    cursor = conn.cursor()
    # cursor.execute("SELECT username FROM contact WHERE username LIKE '%chatroom' AND ( nick_name LIKE '%XXJ%'  COLLATE BINARY );")
    cursor.execute("SELECT remark FROM (SELECT DISTINCT remark,CASE WHEN INSTR (remark, '-') > 0 THEN SUBSTR (remark, 1, INSTR(remark, '-') - 1) ELSE remark END AS number,username FROM contact WHERE username in (SELECT username FROM name2id WHERE rowid IN (SELECT member_id FROM chatroom_member WHERE room_id in (SELECT room_id_ FROM chat_room_info_detail WHERE username_  in (SELECT username FROM contact WHERE username LIKE '%chatroom' AND ( nick_name LIKE '%XXJ%'  COLLATE BINARY ))))) AND remark like '¿¿¿%' ORDER BY number ASC);")
    # users = cursor.fetchone()
    users = cursor.fetchall()
    print(len(users))
    return users
    # print("所有用户数据：")
    # for user in users:
    #     print(user[0])
    cursor.close()
    conn.close()
def read_xueyuan(xueyuan):
    """读取学院"""
    df = pd.DataFrame("C:\Users\LENOVO\Desktop\工作簿2.xlsx",sheet_name='',columns=['备注'])
    
# connection = create_connection("C:\\Users\\LENOVO\\Desktop\\contact.db")
all_xueyuan = select_all_users("C:\\Users\\LENOVO\\Desktop\\contact.db")
