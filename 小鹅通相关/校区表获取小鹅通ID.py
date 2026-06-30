
# -*- coding: utf-8 -*-

import pandas as pd

# 使用原始字符串保存 Windows 路径，避免反斜杠被当作转义字符
查询的人路径 = r"C:\Users\LENOVO\Desktop\_脚本输入_1.txt"
ID表格路径 = r"C:\Users\LENOVO\Desktop\推广员信息.xlsx"

宝妈映射ID = {}
孩子中文全名映射内部备注 = {}
ID表格 = pd.read_excel(ID表格路径)
for index, row in ID表格.iterrows():
   宝妈映射ID[row['合伙宝妈'].split("-")[1]] = row['合伙宝妈ID']
   孩子中文全名映射内部备注[row['合伙宝妈'].split("-")[1]] = row['合伙宝妈']


# 读取待查询名单，并去掉每行首尾空白
with open(查询的人路径, 'r', encoding='utf-8') as f:
    查询的人 = f.readlines()
    查询的人列表 = [line.strip() for line in 查询的人]

# print(孩子中文全名映射内部备注)
with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'w', encoding='utf-8') as f:
    f.write("")
for i in 查询的人列表:
    if "｜" in i or " | " in i or "、" in i:
        if "｜" in i:
            i1, i2 = i.split("｜")
        elif " | " in i:
            i1, i2 = i.split(" | ")
        elif "、" in i:
            i1, i2 = i.split("、")
        if i1 in 宝妈映射ID.keys():
            if i2 in 孩子中文全名映射内部备注.keys():
                with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'a', encoding='utf-8') as f:
                    f.write(f"{孩子中文全名映射内部备注[i1]}\t{孩子中文全名映射内部备注[i2]}\t{宝妈映射ID[i1]}\t{宝妈映射ID[i2]}\n")
            else:
                with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'a', encoding='utf-8') as f:
                    f.write(f"⚠️\t{孩子中文全名映射内部备注[i1]}\t⚠️\t{宝妈映射ID[i1]}\n")
        else:
            with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'a', encoding='utf-8') as f:
                f.write(f"⚠️\t{孩子中文全名映射内部备注[i1]}\t⚠️\t⚠️\n")
    else:
        if i in 宝妈映射ID.keys():
            if i in 孩子中文全名映射内部备注.keys():
                with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'a', encoding='utf-8') as f:
                    f.write(f"{孩子中文全名映射内部备注[i]}\t\t{宝妈映射ID[i]}\t\n")
            else:
                with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'a', encoding='utf-8') as f:
                    f.write(f"{孩子中文全名映射内部备注[i]}\t\t{宝妈映射ID[i]}\t\n")
        else:
            with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'a', encoding='utf-8') as f:
                f.write(f"{孩子中文全名映射内部备注[i]}\t\t\t\n")