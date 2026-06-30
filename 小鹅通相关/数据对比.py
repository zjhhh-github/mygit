# -*- coding: utf-8 -*-
"""
数据对比脚本：
从推广员列表中筛选出"不在推广员信息表中"的记录。
对比规则：
  - 信息表A列（合伙宝妈）格式为"编号-姓名"，提取"-"后的姓名部分
  - 部分信息表条目含多个姓名（空格分隔），每个姓名单独纳入对比集合
  - 列表的B（推广员昵称）、C（账号姓名）、D（申请姓名）、U（合伙宝妈-孩子中文全名）
    只要有任一值出现在信息表姓名集合中，即视为"已在信息表中"
  - 四列值均不在信息表中的行，输出为"不在信息表里的数据"
"""

import pandas as pd

# ========== 文件路径配置 ==========
CSV_PATH = r'C:\Users\LENOVO\Downloads\推广员列表.csv'
XLSX_PATH = r'C:\Users\LENOVO\Desktop\推广员信息.xlsx'


# ========== 读取数据 ==========
# 读取推广员列表的 A,B,C,D,F,U 列（原始列索引：0,1,2,3,5,20）
df_list = pd.read_csv(CSV_PATH, encoding='utf-8', usecols=[0, 1, 2, 3, 5, 20])
print("推广员列表 读取完成，共 {} 行".format(len(df_list)))
print("列名: {}".format(df_list.columns.tolist()))

# 读取推广员信息表的 A,B 列（列索引：0,1）
df_info = pd.read_excel(XLSX_PATH, usecols=[0, 1])
print("推广员信息 读取完成，共 {} 行".format(len(df_info)))
print("列名: {}".format(df_info.columns.tolist()))

# ========== 构建对比集合 ==========
# 信息表A列格式为 "¿¿¿000024-孙一可"，提取"-"后面的姓名
# 部分条目含多个姓名（空格分隔），拆分后逐个加入集合
info_names = set()
for val in df_info.iloc[:, 0].dropna():
    val_str = str(val).strip()
    # 提取"-"后面的姓名部分
    if '-' in val_str:
        name_part = val_str.split('-', 1)[1].strip()
    else:
        name_part = val_str
    # 按空格拆分多个姓名，逐个加入
    for name in name_part.split():
        if name:
            info_names.add(name)

print("信息表提取姓名后，共 {} 个唯一姓名".format(len(info_names)))
print("姓名样例: {}".format(list(info_names)[:10]))

# ========== 确定对比列 ==========
# usecols=[0,1,2,3,5,20] 读入后，DataFrame列顺序为：
#   索引0=推广员id(A), 索引1=推广员昵称(B), 索引2=账号姓名(C),
#   索引3=申请姓名(D), 索引4=申请手机号(F), 索引5=合伙宝妈-孩子中文全名(U)
col_b = df_list.columns[1]  # 推广员昵称
col_c = df_list.columns[2]  # 账号姓名
col_d = df_list.columns[3]  # 申请姓名
col_u = df_list.columns[5]  # 合伙宝妈-孩子中文全名
compare_cols = [col_b, col_c, col_d, col_u]
print("用于对比的列: {}".format(compare_cols))

# ========== 对比逻辑 ==========
def val_in_info(val_str):
    """
    判断单个值是否能在信息表姓名集合中找到匹配。
    匹配方式：
      1. 完全匹配：值本身在集合中
      2. 子串匹配：集合中某个姓名是该值的子串（处理连写情况，如"武嘉琪武祚霆"包含"武嘉琪"）
    """
    if not val_str:
        return False
    # 完全匹配
    if val_str in info_names:
        return True
    # 子串匹配：信息表中的姓名是否出现在该值中
    for name in info_names:
        if len(name) >= 2 and name in val_str:
            return True
    return False

def is_not_in_info(row):
    """判断该行的B/C/D/U列值是否全部不在信息表姓名集合中"""
    for col in compare_cols:
        val = row[col]
        if pd.notna(val):
            val_str = str(val).strip()
            if val_in_info(val_str):
                return False
    return True

mask = df_list.apply(is_not_in_info, axis=1)
df_not_in_info = df_list[mask].copy()

print("\n===== 对比结果 =====")
print("列表总行数: {}".format(len(df_list)))
print("在信息表中的行数: {}".format(len(df_list) - len(df_not_in_info)))
print("不在信息表中的行数: {}".format(len(df_not_in_info)))

# ========== 打印结果 ==========
if len(df_not_in_info) > 0:
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 200)
    pd.set_option('display.max_colwidth', 40)
    print("\n不在信息表中的数据明细：")
    print(df_not_in_info.to_string(index=False))
else:
    print("\n列表中所有数据均在信息表中，无差异。")
