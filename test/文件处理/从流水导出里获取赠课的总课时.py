import pandas as pd
import os
df = pd.read_excel("C:\\Users\\LENOVO\\Desktop\\工作簿1.xlsx", sheet_name="Sheet1")
beizhu = df["内部备注"]
d = {}
for i in beizhu:
    if pd.notna(i):
        d[i] = 0
# print(d)
for i in df.index:
    if pd.notna(df.loc[i,"内部备注"]):
        # 先检查备注是否为字符串类型，避免在NaN值上使用in操作符
        if (pd.isna(df.loc[i,"备注"]) or isinstance(df.loc[i,"备注"], str) or  ("赠" not in df.loc[i,"备注"] and "试" not in df.loc[i,"备注"] and "转" not in df.loc[i,"备注"]and "从" not in df.loc[i,"备注"]and "错" not in df.loc[i,"备注"] and "复" not in df.loc[i,"备注"] and "培训" not in df.loc[i,"备注"] and "固定" not in df.loc[i,"备注"])) and df.loc[i,"次数"] in [10,20,30,40,50]:
            d[df.loc[i,"内部备注"]] += df.loc[i,"次数"]
        else:
            d[df.loc[i,"内部备注"]] += 0
with open("C:\\Users\\LENOVO\\Desktop\\工作簿1.txt","w",encoding="utf-8") as f:
    for i in d:
        f.write(i+"\t"+str(d[i])+"\n")
# 如果要写入 Excel 文件
df_result = pd.DataFrame(list(d.items()), columns=['备注', '赠课信息(流水导出)'])
df_result.to_excel("C:\\Users\\LENOVO\\Desktop\\工作簿1.xlsx", sheet_name="Sheet1", index=False)