import os
import pandas as pd
import re

df = pd.read_excel("C:\\Users\\LENOVO\\Desktop\\内部通讯录.xlsx", header=1)
xueyuan = df["学员"]
tuijianlie = df["推荐"]
zenglelie = df["是否赠课"]

bianhao = []
xingming = []
bhxm = []
zk = []
xy =[]
for idx, i in enumerate(tuijianlie):
    # print(i)
    if pd.isna(i) or ("⚠️" in str(i) if not pd.isna(i) else False):
        # xy.append(i)
        # zk.append("")
        # bianhao.append("")
        # xingming.append("")
        continue
    elif "¿¿¿" not in str(i) if not pd.isna(i) else True:
        # xy.append(i)
        # zk.append("")
        # bianhao.append("")
        # xingming.append("")
        continue
    else:
        zk.append(zenglelie.iloc[idx])  # Use iloc to access by position
        bhxm.append(i)
        bianhao.append(str(i).split('-')[0])
        xingming.append(str(i).split('-')[1])

print(len(xy), len(bianhao), len(xingming), len(zk))
df2 = pd.DataFrame({"学员": bhxm, "编号": bianhao, "姓名": xingming, "是否赠课": zk})

# 对学员进行分组并统计赠课记录
# 首先创建一个新DataFrame用于统计
result_data = []

# 按学员分组
grouped = df2.groupby('学员')

for name, group in grouped:
    # 根据实际数据，统计该学员的赠课成功次数
    # 从输出看，"是否赠课"列包含⚠️、❌等标记，我们假设✅代表成功的赠课
    # 如果没有✅但有其他特定标记表示赠课，则可调整条件
    # 目前根据数据显示，可能需要根据⚠️、❌等符号定义赠课状态
    
    # 统计包含✅标记的记录（如果存在的话）
    success_records = group[group['是否赠课'].apply(lambda x: pd.notna(x) and ('✅' in str(x)))]
    success_count = len(success_records)
    
    # 另外统计其他类型的记录，例如⚠️可能表示待处理，❌可能表示失败
    warning_records = group[group['是否赠课'].apply(lambda x: pd.notna(x) and ('⚠️' in str(x)))]
    warning_count = len(warning_records)
    
    failure_records = group[group['是否赠课'].apply(lambda x: pd.notna(x) and ('❌' in str(x)))]
    failure_count = len(failure_records)
    
    # 添加到结果数据
    result_data.append({
        '学员姓名': name,
        '✅成功次数': success_count,
        '⚠️待处理次数': warning_count,
        '❌失败次数': failure_count,
        '总记录数': len(group),
        '关联学员': list(group['学员'])
    })

# 生成统计结果表
result_df = pd.DataFrame(result_data)

# 保存统计结果到桌面的Excel文件
desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
output_file = os.path.join(desktop_path, "学员赠课统计结果.xlsx")

with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
    # 保存原始数据
    df2.to_excel(writer, sheet_name='原始数据', index=False)
    
    # 保存统计结果
    result_df[['学员姓名', '✅成功次数', '⚠️待处理次数', '❌失败次数', '总记录数']].to_excel(
        writer, sheet_name='统计结果', index=False)

print("原始数据:")
print(df2)
print("\n按学员分组统计结果:")
print(result_df[['学员姓名', '✅成功次数', '⚠️待处理次数', '❌失败次数', '总记录数']])
print(f"\n统计结果已保存到: {output_file}")