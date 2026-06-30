import pandas as pd



df = pd.read_csv(r'D:\\桌面文件\\新建文件夹\\interactive-map-app\\data\\清洗后地址_带精确坐标.csv', encoding='gbk')
# print(df[['编号+孩子中文全名']])
编号列表 = []
姓名列表 = []
for i in df['编号+孩子中文全名']:
    try:
        if pd.isna(i) or i == '' or i == 'nan':
            编号列表.append(None)
            姓名列表.append(None)
        else:
            if '-' in str(i):
                parts = str(i).split('-', 1)  # 只分割第一个'-'，防止姓名中有'-'被误分割
                编号列表.append(parts[0])
                姓名列表.append(parts[1] if len(parts) > 1 else parts[0])
            else:
                if i.find(" ")==0:
                    姓名列表.append(i[1:])
                else:
                    姓名列表.append(str(i))
                编号列表.append(None)
    except Exception as e:
        print(f"Error processing value: {i}, Error: {e}")
        编号列表.append(None)
        姓名列表.append(None)
df['编号'] = 编号列表
df['姓名'] = 姓名列表

# 根据姓名和标准地址进行去重，保留第一次出现的行
print(f"去重前数据行数: {len(df)}")
df_unique = df.drop_duplicates(subset=['姓名', '标准地址'], keep='first')
print(f"根据姓名和标准地址去重后数据行数: {len(df_unique)}")

# 删除编号和姓名完全相同但标准地址为空的行
print(f"删除编号姓名相同但标准地址为空的行前数据行数: {len(df_unique)}")

# 为每行创建编号和姓名的组合键
df_with_key = df_unique.copy()
df_with_key['编号姓名组合'] = df_with_key['编号'].astype(str) + '_' + df_with_key['姓名'].astype(str)

# 找出那些编号和姓名相同但存在多条记录的情况
group_counts = df_with_key.groupby('编号姓名组合').size()
duplicate_groups = group_counts[group_counts > 1].index

# 在这些重复组中，找出标准地址为空的行
mask_empty_addr = df_with_key['标准地址'].isna() | (df_with_key['标准地址'] == '') | (df_with_key['标准地址'] == 'nan') | (df_with_key['标准地址'] == '无')
mask_in_duplicate_groups = df_with_key['编号姓名组合'].isin(duplicate_groups)

# 标记那些在重复组中且标准地址为空的行
rows_to_remove = mask_in_duplicate_groups & mask_empty_addr

# 统计要删除的行数
rows_to_delete_count = rows_to_remove.sum()

if rows_to_delete_count > 0:
    print(f"找到 {rows_to_delete_count} 行编号姓名相同但标准地址为空的记录，正在删除...")
    # 删除这些行
    df_final = df_with_key[~rows_to_remove].copy()
else:
    print("没有找到编号姓名相同但标准地址为空的记录，无需删除。")
    df_final = df_with_key.copy()

# 移除辅助列
df_final = df_final.drop(columns=['编号姓名组合'])

print(f"删除编号姓名相同但标准地址为空的行后数据行数: {len(df_final)}")

# 保存到新文件
df_final.to_csv(r'D:\\桌面文件\\新建文件夹\\interactive-map-app\\data\\清洗后地址_最终清理.csv', index=False)
print("处理后的数据已保存到: 清洗后地址_最终清理.csv")

print(df_unique)