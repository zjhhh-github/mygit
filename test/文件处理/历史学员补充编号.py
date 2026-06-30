import pandas as pd

# 读取 Excel 表，第二行（index=1）为表头
df = pd.read_excel(r"C:\Users\LENOVO\Desktop\新建 XLSX 工作表.xlsx", sheet_name="Sheet1", header=1)
df2 = pd.read_excel(r"C:\Users\LENOVO\Desktop\新建 XLSX 工作表.xlsx", sheet_name="Sheet2", header=1)
xueyuanliebiao = df2['学员'].tolist()
mingzi2bianhao ={}
for i in xueyuanliebiao:
    mingzi = i.split('-')[1]
    mingzi2bianhao[mingzi] = None
for i in xueyuanliebiao:
    bianhao = i.split('-')[0]
    mingzi = i.split('-')[1]
    if mingzi2bianhao[mingzi] is None:
        mingzi2bianhao[mingzi] = [bianhao]
    else:
        mingzi2bianhao[mingzi].append(bianhao)

def 查编号(姓名):
    """根据姓名查编号：唯一返回编号，多个返回⚠️，找不到也返回⚠️"""
    结果 = mingzi2bianhao.get(姓名)
    if 结果 is None:
        return "⚠️"          # 字典中无此姓名
    if len(结果) == 1:
        return 结果[0]        # 唯一编号，直接返回
    return "⚠️"              # 同名多个编号，无法唯一确定

# 打印 Sheet2 字典中编号不唯一的姓名（同名存在多个编号）
不唯一列表 = [(姓名, 编号列表) for 姓名, 编号列表 in mingzi2bianhao.items() if 编号列表 and len(编号列表) > 1]
if 不唯一列表:
    print(f"⚠️ Sheet2 中发现 {len(不唯一列表)} 个编号不唯一的姓名：")
    for 姓名, 编号列表 in 不唯一列表:
        print(f"  【{姓名}】→ {编号列表}")
else:
    print("✅ Sheet2 中所有姓名编号均唯一。")
print()

# 保存原编号列（填充前），用于后续对比；转为字符串统一格式，空值统一为空字符串
df["_原编号"] = df["编号"].astype(str).str.strip().replace("nan", "")

# 对 Sheet1 的"孩子中文全名"列逐行查编号，填入"编号"列
df["编号"] = df["孩子中文全名"].apply(查编号)

# 填充"编号+孩子中文全名"列：格式为"编号-姓名"，编号为⚠️时整列也填⚠️
df["编号+孩子中文全名"] = df.apply(
    lambda row: f"{row['编号']}-{row['孩子中文全名']}" if row["编号"] != "⚠️" else "⚠️",
    axis=1
)

# 筛选：原编号列有值，且与查询出的新编号不同
冲突df = df[
    (df["_原编号"] != "") &          # 原本有编号
    (df["_原编号"] != "⚠️") &        # 原编号本身不是⚠️
    (df["编号"] != "⚠️") &           # 查询编号不是⚠️
    (df["_原编号"] != df["编号"])     # 与查询结果不一致
].copy()

if 冲突df.empty:
    print("✅ 无冲突：所有原有编号与查询结果一致。")
else:
    print(f"⚠️ 发现 {len(冲突df)} 条编号冲突（原编号与查询编号不同）：\n")
    print(冲突df[["孩子中文全名", "_原编号", "编号"]].rename(
        columns={"_原编号": "原编号", "编号": "查询编号"}
    ).to_string(index=False))
print()

# 删除临时列，不写入最终文件
df.drop(columns=["_原编号"], inplace=True)

def 生成编号格式列(源列名):
    """
    读取源列中的姓名，查字典得到唯一编号后返回"编号-姓名"格式；
    查不到或编号不唯一则保持源数据原值不变。
    """
    def _处理单行(原值):
        原值str = str(原值).strip()
        if not 原值str or 原值str == "nan":
            return 原值          # 空值直接返回
        编号 = 查编号(原值str)
        if 编号 == "⚠️":
            return 原值          # 查不到或不唯一，保持原值
        return f"{编号}-{原值str}"

    return df[源列名].apply(_处理单行)

# 推荐列：查编号后填入新列"推荐编号+推荐"
df["推荐编号+推荐"] = 生成编号格式列("推荐")

# 孵化列：查编号后填入新列"孵化编号+孵化"
df["孵化编号+孵化"] = 生成编号格式列("孵化")

def 打印冲突(源列名, 新列名):
    """对比源列与新列，打印原有编号-名字格式但查询结果不同的行"""
    原列str = df[源列名].astype(str).str.strip()
    新列str = df[新列名].astype(str).str.strip()

    冲突mask = (
        原列str.str.contains("-", na=False) &   # 原列已是"编号-名字"格式
        (原列str != 新列str)                     # 与新列结果不同
    )
    冲突df = df[冲突mask][[源列名, 新列名]].copy()

    if 冲突df.empty:
        print(f"✅ 【{源列名}】无冲突：原有编号与查询结果一致。")
    else:
        print(f"⚠️ 【{源列名}】发现 {len(冲突df)} 条冲突（原值与查询结果不同）：")
        print(冲突df.rename(columns={源列名: "原值", 新列名: "查询结果"}).to_string(index=False))
    print()

打印冲突("推荐", "推荐编号+推荐")
打印冲突("孵化", "孵化编号+孵化")

# 打印结果：显示三列方便核查
# print(df[["孩子中文全名", "编号", "编号+孩子中文全名"]].to_string(index=False))

# 保存结果到新文件，避免覆盖原表
df.to_excel(r"C:\Users\LENOVO\Desktop\新建 XLSX 工作表_补充编号.xlsx", index=False)