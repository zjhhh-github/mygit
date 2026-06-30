import pandas as pd

# 读取 Excel 表，第二行（index=1）为表头
df = pd.read_excel(r"C:\Users\LENOVO\Desktop\新建 XLSX 工作表.xlsx", header=1)

# 查重依据的四列：交易时间、孩子姓名、订单金额、报名项 同时相同才视为重复
KEY_COLS = ["交易时间", "孩子中文全名", "订单金额", "报名项"]

# 标记所有重复行（keep=False：第一次出现也标记为重复）
dup_mask = df.duplicated(subset=KEY_COLS, keep=False)
dup_df = df[dup_mask].copy()

if dup_df.empty:
    print("未发现重复数据。")
else:
    # header=1 时数据实际从 Excel 第 3 行开始，故行号 = index + 3
    dup_df["Excel行号"] = dup_df.index + 3

    print(f"共发现 {len(dup_df)} 条重复记录，涉及以下分组：\n")

    # 按四列分组，逐组打印，方便对比
    for group_keys, group in dup_df.groupby(KEY_COLS, sort=False):
        交易时间, 孩子姓名, 订单金额, 报名项 = group_keys
        excel_rows = group["Excel行号"].tolist()
        print(f"【交易时间】{交易时间}  【孩子姓名】{孩子姓名}  【订单金额】{订单金额}  【报名项】{报名项}  → Excel行号：{excel_rows}")
        # to_string 输出整行所有列，index=False 不显示 DataFrame 索引
        print(group.to_string(index=False))
        print("-" * 60)