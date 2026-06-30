import pandas as pd

df = pd.read_excel(r'C:\Users\LENOVO\Desktop\微伴-客户列表数据导出.xlsx', header=3)
df.columns = df.iloc[0]
df = df[1:].reset_index(drop=True)

df['添加时间'] = pd.to_datetime(df['添加时间'])
df['添加日期'] = df['添加时间'].dt.date

df['添加人'] = df['标签组(宝妈合伙人标签组)'].str.replace('¿¿¿', '').str.strip()

result = df.groupby(['添加人', '添加日期']).size().reset_index(name='添加人数')

median_by_person = result.groupby('添加人')['添加人数'].median().reset_index(name='中位数')
median_by_person = median_by_person.sort_values('中位数', ascending=False)

print('每个人每天添加人数的中位数:')
print('=' * 50)
for _, row in median_by_person.iterrows():
    print(f"{row['添加人']}: {row['中位数']}人")

print('\n\n整体中位数:')
print('=' * 50)
overall_median = result['添加人数'].median()
print(f"所有记录的中位数: {overall_median}人")

print('\n\n统计信息:')
print('=' * 50)
print(f"总人数: {len(df)}人")
print(f"总天数: {df['添加日期'].nunique()}天")
print(f"参与添加的人数: {df['添加人'].nunique()}人")
print(f"平均每人每天添加: {result['添加人数'].mean():.2f}人")
print(f"每人每天添加人数的标准差: {result['添加人数'].std():.2f}人")
