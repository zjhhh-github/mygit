import pandas as pd


df = pd.read_excel(r"C:\Users\LENOVO\Desktop\宝妈.xlsx",header=1)
# print(df)
# 将“宝妈”列映射到“密码”列，构建姓名->密码字典
名称映射密码 = dict(zip(df["宝妈"], df["密码"]))
print(名称映射密码.keys())
with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", "w", encoding="utf-8") as f:
    f.write("")
# 读取输入文件内容，必须使用可读模式 "r"
with open(r"C:\Users\LENOVO\Desktop\_脚本输入_1.txt", "r", encoding="utf-8") as f:
    每行 = f.readlines()
    每行数据 = [line.strip().split("\t") for line in 每行]
    for i in 每行数据:
        姓名 = "¿¿¿"+i[0]+"-"+i[1]
        if 姓名 in 名称映射密码.keys():
            with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", "a", encoding="utf-8") as f:
                f.write(f"{名称映射密码[姓名]}\n")
        else:
            print(姓名)
            with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", "a", encoding="utf-8") as f:
                f.write(f"⚠️\n")
    # print(每行数据)