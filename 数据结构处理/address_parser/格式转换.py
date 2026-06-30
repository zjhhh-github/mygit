import json

with open(r"D:\桌面文件\新建文件夹\数据结构处理\地址映射.json", "r", encoding="utf8") as f:
    data = json.load(f)

result = []

for province, cities in data.items():

    result.append({
        "name": province,
        "level": "province",
        "province": province
    })

    for city, districts in cities.items():

        result.append({
            "name": city,
            "level": "city",
            "province": province
        })

        for district in districts:

            result.append({
                "name": district,
                "level": "district",
                "parent": city,
                "province": province
            })


with open(r"D:\桌面文件\新建文件夹\address_parser\district_db.json", "w", encoding="utf8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print("转换完成")