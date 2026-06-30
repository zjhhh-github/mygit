
# -*- coding: utf-8 -*-

import sqlite3
import pandas as pd
import re

def 查询目标昵称(remark):
    conn = sqlite3.connect('C:\\Users\\LENOVO\\Desktop\\contact.db')
    sql = """
    SELECT nick_name FROM contact WHERE remark = ?;
    """
    参数 = (remark,)
    cursor = conn.cursor()
    cursor.execute(sql, 参数)
    结果 = cursor.fetchall()
    cursor.close()
    if remark == "❌":
        return "❌"
    else:
        if len(结果) == 0:
            return "⚠️"
        elif len(结果) > 1:
            return "⚠️"
        return 结果[0][0]


df = pd.read_csv(r"C:\Users\LENOVO\Downloads\用户列表导出.2026-03-26_13-16-05.vuKwLu.csv")
昵称映射ID = {}
姓名映射ID = {}
for index, row in df.iterrows():
    nick_name = str(row['昵称'])
    id = row['用户ID']
    name = str(row['姓名'])
    昵称映射ID[nick_name] = id
    姓名映射ID[name] = id
    result = re.findall(r'[\u4e00-\u9fa5]+', nick_name)
    if "/" in nick_name and len(result) > 0:
        姓名映射ID[result[0]] = id
with open(r"C:\Users\LENOVO\Desktop\_脚本输入_1.txt", 'r', encoding='utf-8') as f:
    输入 = f.readlines()
    输入列表 = [line.strip() for line in 输入]

备注映射昵称 = {}
备注映射孩子中文全名 = {}
for remark in 输入列表:
    结果 = 查询目标昵称(remark)
    备注映射昵称[remark] = 结果
    # print(结果)
    if "-" in remark:
        备注映射孩子中文全名[remark] = remark.split("-")[1]
    else:
        备注映射孩子中文全名[remark] = "⚠️"

with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'w', encoding='utf-8') as f:
    f.write("")
for remark in 输入列表:
    nick_name = 备注映射昵称[remark]
    孩子中文全名 = 备注映射孩子中文全名[remark]
    print(nick_name,孩子中文全名)
    if nick_name in 昵称映射ID.keys():
        print(昵称映射ID[nick_name])
        with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'a', encoding='utf-8') as f:
            f.write(f"{昵称映射ID[nick_name]}\n")
    elif 孩子中文全名 in 姓名映射ID.keys():
        print(姓名映射ID[孩子中文全名])
        with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'a', encoding='utf-8') as f:
            f.write(f"{姓名映射ID[孩子中文全名]}\n")
    else:
        with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'a', encoding='utf-8') as f:
            if remark == "❌":
                f.write(f"❌\n")
            else:   
                f.write(f"⚠️\n")
        print(remark)
    print("--------------------------------")




