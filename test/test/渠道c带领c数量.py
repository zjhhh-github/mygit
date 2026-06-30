import pandas as pd
from collections import Counter

df = pd.read_excel(r'C:\Users\LENOVO\Desktop\报名录入.xlsx', sheet_name='内部通讯录', header=1)
df2 = pd.read_excel(r'C:\Users\LENOVO\Desktop\报名录入.xlsx', sheet_name='宝妈', header=1)['宝妈']
baoma = []
xueyuan = []
tuijian = []
qudao = []
dailing = []
for i in df2:
    if pd.notna(i):
        baoma.append(i)
for i in df['学员']:
    if pd.notna(i):
        xueyuan.append(i)
for i in df['推荐']:
    if pd.notna(i):
        tuijian.append(i)
for i in df['渠道C']:
    if pd.notna(i):
        qudao.append(i)
for i in df['带领C']:
    if pd.notna(i):
        dailing.append(i)

tuijian_count = Counter(tuijian)
tuijian_more_than_5 = [person for person, count in tuijian_count.items() if count > 5]

baoma_set = set(baoma)
tuijian_more_than_5_set = set(tuijian_more_than_5)

target_people = baoma_set | tuijian_more_than_5_set

qudao_count = Counter(qudao)
dailing_count = Counter(dailing)

print("统计结果：")
print("=" * 60)
print(f"{'姓名':<20}{'在qudao出现次数':<15}{'在dailing出现次数':<15}")
print("=" * 60)

for person in sorted(target_people):
    qudao_times = qudao_count.get(person, 0)
    dailing_times = dailing_count.get(person, 0)
    print(f"{str(person):<20}{qudao_times:<15}{dailing_times:<15}")

print("=" * 60)
print(f"总计统计人数: {len(target_people)}")
print(f"其中宝妈人数: {len(baoma_set)}")
print(f"其中推荐出现次数>5的人数: {len(tuijian_more_than_5_set)}")

