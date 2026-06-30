import pandas as pd

df = pd.read_excel(r'D:\桌面文件\新建文件夹\interactive-map-app\data\地图帮地址解析结果数据-2026_0226_1814_22.xlsx')
df_list = df.values.tolist()

所有宝妈 = pd.read_csv(r'D:\桌面文件\新建文件夹\interactive-map-app\data\_脚本输入_1.txt', header=1, encoding='utf-8')

坐标地址列表 = []
baoma2zuobiao = {}
填充 = {}
l2 = []
for i in 所有宝妈['宝妈']:
    baoma2zuobiao[i] = ['无','无','无']
    填充[i] = ['无','无','无']
    # print(i)
    for j in df_list:
        # 每组坐标地址 = []
        # print(j[0])
        if j[0] != '无' and j[0] != 'nan' and j[0] != '' and j[0] is not None and type(j[0]) is not float:
            z = j[0].replace("???","¿¿¿")
            if i in z and j[1] != '无' :
                print(z,j[10],j[11])
                # 每组坐标地址.append(i)
                # 每组坐标地址.append(j[10])
                # 每组坐标地址.append(j[11])
                # 坐标地址列表.append(每组坐标地址)
                if j[2] != '未识别':
                    j[2] = '无'
                elif j[3] != '未识别':
                    j[3] = '无'
                elif j[4] != '未识别':
                    j[4] = '无'
                baoma2zuobiao[i] = [j[1],j[10],j[11]]
                填充[i] = [j[2],j[3],j[4]]
                continue
            else:
                # 每组坐标地址.append(i)
                # 每组坐标地址.append('无')
                # 每组坐标地址.append('无')
                # 坐标地址列表.append(每组坐标地址)
                continue

        else:
            # 每组坐标地址.append(i)
            # 每组坐标地址.append('无')
            # 每组坐标地址.append('无')
            # 坐标地址列表.append(每组坐标地址)
            continue
for i,j in baoma2zuobiao.items():
    坐标地址列表.append([i,j[0],j[1],j[2]])
for i,j in 填充.items():
    l2.append([i,j[0],j[1],j[2]])
all = pd.DataFrame(l2, columns = ['合伙宝妈','省','市','区/县'])
# print(len(坐标地址列表))
# print(坐标地址列表)
print(baoma2zuobiao)
所有坐标地址 = pd.DataFrame(坐标地址列表, columns=['姓名', '原始地址', '解析地址', '经纬度'])
所有坐标地址.to_csv('D:\桌面文件\新建文件夹\interactive-map-app\data\提取宝妈合伙人地址.csv', index=False, encoding='utf-8')
all.to_csv('D:\桌面文件\新建文件夹\interactive-map-app\data\提取宝妈合伙人地址_填充.csv', index=False, encoding='utf-8')
