import pandas as pd
from collections import Counter
def get_keys_by_value(dict_data, target_value):
    """
    根据值查找所有对应的键（值重复场景）
    :param dict_data: 目标字典
    :param target_value: 要查找的值
    :return: 所有匹配的键组成的列表，没找到返回空列表
    """
    matched_keys = []
    for key, value in dict_data.items():
        if value == target_value:
            matched_keys.append(key)
    return matched_keys

df1 = pd.read_excel('C:\\Users\\LENOVO\\Desktop\\报名录入.xlsx', sheet_name='Sheet2',header=1)
df2 = pd.read_excel('C:\\Users\\LENOVO\\Desktop\\报名录入.xlsx', sheet_name='宝妈',header=1)['宝妈']
l3 = [i for i in df2 if pd.notna(i) and i != 'nan' and i is not None and i != '']
# 编号映射宝妈名字
bianhao2baoma = {}
for i in df2:
    # print(i)
    if pd.notna(i) and i != 'nan' and i is not None and i != '':  # 排除 NaN, None, 空字符串等空值
        try:
            mingzi = ' '.join(sorted(i.split('-')[1].split(' ')))  # 对分割后的列表进行排序，然后连接
        except:
            # print(i)
            mingzi = i.split('-')[1]
        bianhao2baoma[i.split('-')[0]] = mingzi
# print(baoma)
# 所有的名字和地址信息
l2 = []
for i in df1.values:
    if pd.notna(i[0]) and i[0] != 'nan' and i[0] is not None and i[0] != '':  # 排除 NaN, None, 空字符串等空值
        if len(i) >= 2:  # 确保有足够的列
            try:
                if "姓名"  in i[0] or '技术助理' in i[0] or '编号' in i[0] or '总管' in i[0]:
                    next
                if "¿¿¿" not in i[0]:
                    l2.append(['',i[0],i[1]])
                else:
                    l2.append([i[0].split('-')[0],' '.join(sorted(i[0].split('-')[1].split(' '))),i[1]])
            except:
                print(f"无法处理行: {i}")
# print(l2)
c = 0
l4 = []

d4 = {}
for i in l3:
    k1 = i.split('-')[0]
    z = i.split('-')[1]
    if ' ' in z:
        z = ' '.join(sorted(z.split(' ')))
    d4[k1 + '-' + z] = '空'
for i in l2:
    if i[0] in bianhao2baoma.keys():
        # print(i)
        k = i[0] + '-' + i[1]
        if d4[k] == '空':
            d4[k] = i[2]
        elif d4[k] == '无':
            d4[k] = i[2]
        else:
            d4[k] = d4[k]
        l4.append(k + "\t" + i[2])
        c += 1
    if i[1] in bianhao2baoma.values():
        # print(i)
        k = get_keys_by_value(bianhao2baoma,i[1])[0] + '-' + i[1]
        if d4[k] == '空':
            d4[k] = i[2]
        elif d4[k] == '无':
            d4[k] = i[2]
        else:
            d4[k] = d4[k]
        # print(get_keys_by_value(bianhao2baoma,i[0])[0],i[0],i[1])
        l4.append(get_keys_by_value(bianhao2baoma,i[1])[0] + '-' + i[1] + "\t" + i[2])
        c += 1
# print("="*20)
# print(d4)
# print("="*20)
with open('C:\\Users\\LENOVO\\Desktop\\报名录入-各城市的宝妈.txt','w',encoding='utf-8') as f:
    pass
for i in d4.keys():
    # print(i)
    with open('C:\\Users\\LENOVO\\Desktop\\报名录入-各城市的宝妈.txt','a',encoding='utf-8') as f:
        f.write(i + '\t' + d4[i] + '\n')
dizhi = ['西安','乌兰浩特','乌鲁木齐','包头','上海','呼伦贝尔']
for i in dizhi:
    c = 0
    for j in d4.values():
        if i in j:
            print(i,get_keys_by_value(d4,j)[0])
            c += 1
    print(i,c)
