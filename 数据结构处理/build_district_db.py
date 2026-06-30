"""
将 pcas-code.json（省市区街道村5级嵌套结构）
转换为 address_parser 所需的扁平 district_db.json 格式。

输出字段：
  name     - 行政区名称
  code     - 行政区划代码（国标）
  level    - province / city / district / street / village
  province - 所属省份名
  parent   - 上级行政区名（province 级无此字段）
"""

import json
import os

# ── 路径配置 ──────────────────────────────────────────────
BASE_DIR  = r"D:\桌面文件\新建文件夹\数据结构处理"
SRC_FILE  = os.path.join(BASE_DIR, "pcas-code.json")
OUT_FILE  = os.path.join(BASE_DIR, "address_parser", "district_db.json")

# 是否包含街道/乡镇（第4级）和村/居委会（第5级）
# 开启后数据量约 70k+，关闭后约 4k（省市区三级）
INCLUDE_STREET  = True
INCLUDE_VILLAGE = True
# ─────────────────────────────────────────────────────────


def flatten(data: list) -> list:
    """递归展平5级嵌套行政区划数据。"""
    result = []

    for province_item in data:
        province_name = province_item["name"]
        province_code = province_item.get("code", "")

        # 省级
        result.append({
            "name":     province_name,
            "code":     province_code,
            "level":    "province",
            "province": province_name,
        })

        for city_item in province_item.get("children", []):
            city_name = city_item["name"]
            city_code = city_item.get("code", "")

            # 市级
            result.append({
                "name":     city_name,
                "code":     city_code,
                "level":    "city",
                "province": province_name,
                "parent":   province_name,
            })

            for district_item in city_item.get("children", []):
                district_name = district_item["name"]
                district_code = district_item.get("code", "")

                # 区/县级
                result.append({
                    "name":     district_name,
                    "code":     district_code,
                    "level":    "district",
                    "province": province_name,
                    "parent":   city_name,
                })

                if not INCLUDE_STREET:
                    continue

                for street_item in district_item.get("children", []):
                    street_name = street_item["name"]
                    street_code = street_item.get("code", "")

                    # 街道/乡镇级
                    result.append({
                        "name":     street_name,
                        "code":     street_code,
                        "level":    "street",
                        "province": province_name,
                        "parent":   district_name,
                    })

                    if not INCLUDE_VILLAGE:
                        continue

                    for village_item in street_item.get("children", []):
                        village_name = village_item["name"]
                        village_code = village_item.get("code", "")

                        # 村/居委会级
                        result.append({
                            "name":     village_name,
                            "code":     village_code,
                            "level":    "village",
                            "province": province_name,
                            "parent":   street_name,
                        })

    return result


def main():
    print(f"读取源文件: {SRC_FILE}")
    with open(SRC_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    print("展平数据中...")
    db = flatten(raw)

    # 统计各级别数量
    level_count: dict = {}
    for item in db:
        lv = item["level"]
        level_count[lv] = level_count.get(lv, 0) + 1

    print(f"总条数: {len(db)}")
    for lv, cnt in level_count.items():
        print(f"  {lv}: {cnt}")

    print(f"写出文件: {OUT_FILE}")
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    print("完成！")


if __name__ == "__main__":
    main()
