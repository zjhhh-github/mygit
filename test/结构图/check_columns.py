import pandas as pd

df = pd.read_excel(r'C:\Users\LENOVO\Desktop\微伴-客户列表数据导出.xlsx', header=3)
df.columns = df.iloc[0]
df = df[1:].reset_index(drop=True)

print('所属客服列的唯一值:')
print(df['所属客服'].unique())

print('\n\n标签组(宝妈合伙人标签组)列的前20个值:')
print(df['标签组(宝妈合伙人标签组)'].head(20).tolist())

print('\n\n添加渠道列的前20个值:')
print(df['添加渠道'].head(20).tolist())
