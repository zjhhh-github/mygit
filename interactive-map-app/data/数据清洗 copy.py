import pandas as pd
import json
import re

def load_address_mapping(json_file_path):
    """
    从JSON文件加载地址映射
    """
    with open(json_file_path, 'r', encoding='utf-8') as f:
        mapping_data = json.load(f)
    return mapping_data

def clean_address_with_mapping(address, mapping_data):
    """
    使用地址映射数据清洗地址
    """
    province = ""
    city = ""
    district = ""
    detail_address = address
    
    # 遍历省份
    for prov_name, cities in mapping_data.items():
        if prov_name in address:
            province = prov_name
            # 移除省份名称，获取剩余部分
            remaining_address = address.replace(prov_name, '', 1)
            
            # 遍历城市
            for city_name, districts in cities.items():
                if city_name in remaining_address:
                    city = city_name
                    # 移除城市名称，获取剩余部分
                    remaining_address = remaining_address.replace(city_name, '', 1)
                    
                    # 遍历区县
                    for dist_name in districts:
                        if dist_name in remaining_address:
                            district = dist_name
                            # 移除区县名称，获取详细地址
                            detail_address = remaining_address.replace(dist_name, '', 1)
                            break
                    
                    break
            
            break
    
    # 如果没匹配到省份，尝试直接匹配城市（适用于直辖市等）
    if not province:
        for prov_name, cities in mapping_data.items():
            for city_name, districts in cities.items():
                if city_name in address:
                    province = prov_name  # 根据城市反推省份
                    city = city_name
                    remaining_address = address.replace(city_name, '', 1)
                    
                    # 在该城市的区县中查找
                    for dist_name in districts:
                        if dist_name in remaining_address:
                            district = dist_name
                            detail_address = remaining_address.replace(dist_name, '', 1)
                            break
                    
                    break
            if city:  # 如果找到了城市就跳出循环
                break
    
    # 构建标准地址
    standard_address = province + city + district + detail_address
    
    return {
        'original_address': address,
        'province': province,
        'city': city,
        'district': district,
        'detail_address': detail_address.strip(),
        'standard_address': standard_address
    }

def process_and_save_addresses(csv_file_path, json_mapping_path, output_file_path=None):
    """
    处理CSV文件中的地址并保存结果到新的CSV文件
    """
    # 加载地址映射
    mapping_data = load_address_mapping(json_mapping_path)
    
    # 读取CSV文件 - 尝试不同的编码
    encodings = ['utf-8', 'gbk', 'gb2312', 'ansi']
    df = None
    for encoding in encodings:
        try:
            df = pd.read_csv(csv_file_path, encoding=encoding)
            print(f"成功使用 {encoding} 编码读取文件")
            break
        except UnicodeDecodeError:
            continue
    
    if df is None:
        print("无法读取CSV文件，请检查文件格式和编码")
        return
    
    print(f"总共读取了 {len(df)} 条地址记录")
    print("="*80)
    
    # 创建一个新的DataFrame来存储清洗结果
    results_list = []
    
    # 处理每一条地址
    for index, row in df.iterrows():
        # 获取A列（第一列）的值，用于与原始数据对应
        col_a_value = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ""

        # 获取地址字段，假设地址在第二列（索引为1）
        address = ""
        if len(row) > 1:
            address = str(row.iloc[1]) if pd.notna(row.iloc[1]) else ""
        elif len(row) > 0:
            address = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ""
        
        if address.strip():  # 如果地址非空
            cleaned = clean_address_with_mapping(address, mapping_data)
            
            # 添加清洗结果到列表，包含A列原始值
            result_row = {
                'col_a': col_a_value,
                'original_address': cleaned['original_address'],
                'province': cleaned['province'] or '未识别',
                'city': cleaned['city'] or '未识别',
                'district': cleaned['district'] or '未识别',
                'detail_address': cleaned['detail_address'] or '未识别',
                'standard_address': cleaned['standard_address']
            }
            results_list.append(result_row)
            
            print(cleaned)
            print("-" * 80)
        else:
            print(f"第 {index+1} 行地址为空，跳过")
    
    # 将清洗结果转换为DataFrame
    results_df = pd.DataFrame(results_list)
    
    # 基于A列（col_a）去重，保留首次出现的记录
    before_dedup = len(results_df)
    results_df = results_df.drop_duplicates(subset=['col_a'], keep='first')
    after_dedup = len(results_df)
    print(f"去重完成：去重前 {before_dedup} 条，去重后 {after_dedup} 条，移除 {before_dedup - after_dedup} 条重复记录")
    
    # 如果提供了输出路径，则保存结果
    if output_file_path:
        results_df.to_csv(output_file_path, index=False, encoding='utf-8')
        print(f"清洗结果已保存到: {output_file_path}")
    
    return results_df

# 主程序执行
if __name__ == "__main__":
    import os
    # 获取当前脚本所在的目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    csv_file_path = os.path.join(current_dir, "地址.csv")
    json_mapping_path = os.path.join(current_dir, "地址映射.json")
    output_file_path = os.path.join(current_dir, "清洗后地址.csv")
    
    print("开始清洗地址数据...")
    result_df = process_and_save_addresses(csv_file_path, json_mapping_path, output_file_path)
    print("地址清洗完成！")