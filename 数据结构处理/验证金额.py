import json

with open(r'd:\桌面文件\新建文件夹\数据结构处理\转换结果.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print(f'总记录数: {len(data)}')
print('\n前10条金额:')
for i, r in enumerate(data[:10]):
    print(f"{i+1}. {r['孩子中文全名']}: {r['报名信息'][0]['金额']}")
