import pandas as pd
import json
import re
import os

# 全局变量：用于收集校验失败的地址信息
validation_failures = []

# 常见冗余字符和替换规则
CLEAN_RULES = {
    r'\s+': '',          # 移除所有空格
    r'[,，。.、；;：:]': '', # 移除常见标点
    r'（.*?）': '',      # 移除括号及括号内内容
    r'\(.*?\)': '',      # 移除英文括号及内容
    r'号(?!\d*楼|\d*单元|\d*栋|\d*室).*': '号',  # 移除"号"后不是楼/单元/栋/室的情况，保留"号楼"、"号单元"等
}



def load_address_mapping(json_file_path):
    """
    从JSON文件加载地址映射
    """
    with open(json_file_path, 'r', encoding='utf-8') as f:
        mapping_data = json.load(f)
    return mapping_data

def load_abbreviation_mapping(abbrev_json_path):
    """
    从JSON文件加载简称映射
    新JSON格式：省全称:[[省简称1,省简称2],{市全称:[[市简称1,市简称2],区全称:[区简称1,区简称2]]}]
    需要解析这种新的嵌套结构并创建从简称到完整名称的映射
    """
    import os
    import json
    
    with open(abbrev_json_path, 'r', encoding='utf-8') as f:
        abbrev_data = json.load(f)
    
    abbreviation_mapping = {}
    
    # 遍历新的格式：省全称:[[省简称1,省简称2],{市全称:[[市简称1,市简称2],区全称:[区简称1,区简称2]]}]
    for prov_full_name, prov_data in abbrev_data.items():
        if isinstance(prov_data, list) and len(prov_data) == 2:
            prov_abbrevs, city_data = prov_data  # [省简称列表, {城市数据}]
            
            # 处理省简称
            if isinstance(prov_abbrevs, list):
                for abbrev in prov_abbrevs:
                    if isinstance(abbrev, str):
                        abbreviation_mapping[abbrev] = prov_full_name
            
            # 处理城市数据
            if isinstance(city_data, dict):
                for city_full_name, city_item in city_data.items():
                    if isinstance(city_item, list) and len(city_item) == 2:
                        # [市简称列表, {区县数据}]
                        city_abbrevs, district_data = city_item
                        
                        # 处理市简称
                        if isinstance(city_abbrevs, list):
                            for abbrev in city_abbrevs:
                                if isinstance(abbrev, str):
                                    abbreviation_mapping[abbrev] = city_full_name
                        
                        # 处理区县数据
                        if isinstance(district_data, dict):
                            for district_full_name, district_abbrevs in district_data.items():
                                if isinstance(district_abbrevs, list):
                                    for abbrev in district_abbrevs:
                                        if isinstance(abbrev, str):
                                            abbreviation_mapping[abbrev] = district_full_name
                    elif isinstance(city_item, list):
                        # 这是直接隶属于省的区县简称，格式：区县名:[简称列表]
                        for abbrev in city_item:
                            if isinstance(abbrev, str):
                                abbreviation_mapping[abbrev] = city_full_name
    
    return abbreviation_mapping

def load_education_mapping(education_json_path):
    """
    从JSON文件加载教育机构映射
    JSON格式：教育机构名称:{'province': 省份, 'city': 城市}
    """
    if os.path.exists(education_json_path):
        with open(education_json_path, 'r', encoding='utf-8') as f:
            education_mapping = json.load(f)
        return education_mapping
    else:
        # 如果文件不存在，返回空字典
        return {}

def load_special_area_mapping(special_area_json_path):
    """
    从JSON文件加载特殊区域映射
    JSON格式：特殊区域名称:{'province': 省份, 'city': 城市, 'district': 区县}
    """
    if os.path.exists(special_area_json_path):
        with open(special_area_json_path, 'r', encoding='utf-8') as f:
            special_area_mapping = json.load(f)
        return special_area_mapping
    else:
        # 如果文件不存在，返回空字典
        return {}

def clean_address_with_mapping(address, mapping_data, abbreviation_mapping, education_mapping=None, special_area_mapping=None):
    """
    使用地址映射数据清洗地址
    匹配原则：
    1. 优先从原始地址中提取已明确的行政区划信息（省、市、区/县）
    2. 若原始地址中已明确包含省、市、区/县等行政区划信息，则直接使用，不再进行后续的地址匹配操作
    3. 仅当原始地址中存在行政区划信息不明确、缺失或模糊时，才启动后续的地址匹配流程
    """
    # 预处理：如果地址中包含"农大"或"农业大学"但不包含"内蒙古农业大学"，且能解析出内蒙古自治区或呼和浩特市，则替换为"内蒙古农业大学"
    if ("农大" in address or "农业大学" in address) and "内蒙古农业大学" not in address:
        has_inner_mongolia = "内蒙古" in address
        has_hohhot = "呼和浩特" in address
        
        if has_inner_mongolia or has_hohhot:
            # 先替换"农业大学"，再替换"农大"
            address = address.replace("农业大学", "内蒙古农业大学")
            address = address.replace("农大", "内蒙古农业大学")
            # 如果替换后地址中出现"内蒙古内蒙古农业大学"，则移除重复的"内蒙古"
            if "内蒙古内蒙古农业大学" in address:
                address = address.replace("内蒙古内蒙古农业大学", "内蒙古农业大学")
    
    # 初始化变量
    province = ""
    city = ""
    district = ""
    
    # 第一步：尝试从原始地址中提取已明确的行政区划信息
    # 按照"省→市→区/县"的层级顺序进行提取
    # 优先匹配完整名称，其次匹配简称
    
    # 1. 尝试匹配省份
    for prov_name in mapping_data.keys():
        if prov_name in address:
            province = prov_name
            break
    
    # 2. 尝试匹配城市（在已确定省份的情况下）
    if province and province in mapping_data:
        for city_name in mapping_data[province].keys():
            if city_name in address:
                city = city_name
                break
    else:
        # 如果省份未确定，遍历所有省份的城市
        for prov_name, cities in mapping_data.items():
            for city_name in cities.keys():
                if city_name in address:
                    city = city_name
                    province = prov_name
                    break
            if city:
                break
    
    # 3. 尝试匹配区/县（在已确定省份和城市的情况下）
    if province and city and province in mapping_data and city in mapping_data[province]:
        # 首先从地址中匹配区县信息
        for dist_name in mapping_data[province][city]:
            if dist_name in address:
                district = dist_name
                break
        
        # 如果地址中没有匹配到区县，且教育机构映射中有区县信息，则使用教育机构映射中的区县信息
        if not district:
            if education_mapping is None:
                education_mapping = load_education_mapping('interactive-map-app/data/教育机构映射.json')
            
            for school_name, location_info in education_mapping.items():
                if school_name in address:
                    edu_district = location_info.get('district', '')
                    if edu_district:
                        district = edu_district
                        break
                else:
                    # 尝试匹配教育机构名称的关键部分（如"农业大学"、"大学"等）
                    # 但要确保匹配的是完整的教育机构名称
                    import re
                    # 检查地址中是否包含教育机构名称的关键部分
                    # 例如"农业大学"、"大学"等
                    school_keywords = ["农业大学", "大学", "学院"]
                    for keyword in school_keywords:
                        if keyword in school_name and keyword in address:
                            # 检查是否是完整的教育机构名称
                            # 例如"内蒙古农业大学"包含"农业大学"
                            if keyword in school_name:
                                # 检查地址中是否包含教育机构名称的其他关键部分
                                # 例如"内蒙古"、"呼和浩特"等
                                if "内蒙古" in address or "呼和浩特" in address:
                                    # 检查是否匹配到教育机构名称
                                    # 例如"内蒙古农业大学"、"内蒙古大学"等
                                    for full_school_name in education_mapping.keys():
                                        if keyword in full_school_name and full_school_name in address:
                                            edu_district = education_mapping[full_school_name].get('district', '')
                                            if edu_district:
                                                district = edu_district
                                                break
                                    if district:
                                        break
    
    # 如果已成功提取到行政区划信息，则直接返回结果
    if province or city or district:
        # 从地址中移除已识别的行政区划，剩余部分作为详细地址
        detail_address = address
        if province:
            detail_address = detail_address.replace(province, '', 1).strip()
        if city:
            detail_address = detail_address.replace(city, '', 1).strip()
        if district:
            detail_address = detail_address.replace(district, '', 1).strip()
        
        # 清理多余的空白字符
        detail_address = detail_address.strip()
        
        # 如果省份是"内蒙古自治区"，从详细地址中移除重复的"内蒙古"
        # 但要确保不影响教育机构名称
        if province == "内蒙古自治区" and "内蒙古" in detail_address:
            # 检查教育机构名称是否在详细地址中
            if education_mapping is None:
                education_mapping = load_education_mapping('interactive-map-app/data/教育机构映射.json')
            
            # 检查详细地址中是否包含教育机构名称
            contains_edu_inst = False
            edu_institution_names = list(education_mapping.keys())
            edu_institution_names.sort(key=len, reverse=True)  # 按长度排序，优先匹配长名称
            
            for school_name in edu_institution_names:
                if school_name in detail_address:
                    # 找到教育机构名称，检查是否需要保留"内蒙古"
                    import re
                    # 查找"内蒙古"的位置
                    # 如果"内蒙古"是教育机构名称的一部分，则不应移除
                    # 我们可以通过检查"内蒙古"后面是否紧跟着教育机构名称的其余部分来判断
                    matches = re.finditer(r'内蒙古', detail_address)
                    for match in matches:
                        pos = match.start()
                        # 检查从这个位置开始是否能匹配到教育机构名称
                        remaining = detail_address[pos:]
                        if remaining.startswith(school_name):
                            # 这个"内蒙古"是教育机构名称的一部分，不应移除
                            contains_edu_inst = True
                            break
                    if contains_edu_inst:
                        break
            
            # 如果没有教育机构名称，或者教育机构名称不包含"内蒙古"，则移除重复的"内蒙古"
            if not contains_edu_inst:
                detail_address = detail_address.replace("内蒙古", "", 1).strip()
            else:
                # 如果有教育机构名称，但"内蒙古"在开头且重复了，则移除开头的"内蒙古"
                if detail_address.startswith("内蒙古") and detail_address != "内蒙古":
                    # 检查是否是重复的"内蒙古"
                    temp_address = detail_address.replace("内蒙古", "", 1).strip()
                    if temp_address and temp_address != detail_address:
                        # 检查移除后是否仍然包含教育机构名称
                        for school_name in edu_institution_names:
                            if school_name in temp_address:
                                # 移除开头的"内蒙古"
                                detail_address = temp_address
                                break
            if not detail_address and education_mapping is not None:
                for school_name in education_mapping.keys():
                    if school_name in address:
                        detail_address = address.replace(province, '', 1).replace(city, '', 1).replace(district, '', 1).strip()
                        if school_name in detail_address:
                            # 保留"内蒙古"
                            break
                        break
        
        # 检查是否可能有教育机构名称被错误分割了
        if education_mapping is None:
            education_mapping = load_education_mapping('interactive-map-app/data/教育机构映射.json')
        
        # 遍历教育机构名称，检查是否需要修复
        for school_name in education_mapping.keys():
            # 如果原始地址包含学校名称，但详细地址不包含完整的学校名称，可能被分割了
            if school_name in address and school_name not in detail_address:
                # 检查是否是因为省份或城市名称从学校名称中被移除导致的
                if school_name in address.replace(province, '', 1) if province else address:
                    # 修复详细地址，如果省份名称意外出现在学校名称中间
                    import re
                    # 检查是否是"内蒙古内蒙古农业大学"这样的情况
                    if province and f"{province}{school_name}" in address:
                        # 修复详细地址，移除重复的省份名称
                        pattern = f"{re.escape(province)}({re.escape(school_name)})"
                        detail_address = re.sub(pattern, r'\1', detail_address)
                        
                    if city and f"{city}{school_name}" in address:
                        # 修复详细地址，移除重复的城市名称
                        pattern = f"{re.escape(city)}({re.escape(school_name)})"
                        detail_address = re.sub(pattern, r'\1', detail_address)
        
        # 标准地址：行政区划 + 详细地址
        standard_address = f"{province}{city}{district}{detail_address}".strip()
        
        return {
            'original_address': address,
            'province': province,
            'city': city,
            'district': district,
            'detail_address': detail_address,
            'standard_address': standard_address,
            'validation_errors': None
        }
    
    # 第二步：如果原始地址中没有明确的行政区划信息，尝试教育机构匹配
    if education_mapping is None:
        education_mapping = load_education_mapping('interactive-map-app/data/教育机构映射.json')
    for school_name, location_info in education_mapping.items():
        if school_name in address:
            # 找到教育机构名称，返回对应的行政区划信息
            province = location_info.get('province', '')
            city = location_info.get('city', '')
            district = location_info.get('district', '')  # 从教育机构映射中获取区县信息
            # 从地址中移除教育机构名称，剩余部分作为详细地址
            detail_address = address.replace(school_name, '', 1).strip()
            
            # 从详细地址中移除已有的行政区划名称
            if province:
                detail_address = detail_address.replace(province, '').replace('自治区', '').strip()
            if city:
                detail_address = detail_address.replace(city, '').replace('市', '').strip()
            if district:
                detail_address = detail_address.replace(district, '').replace('区', '').replace('县', '').strip()
            
            # 清理多余的空白字符
            detail_address = detail_address.strip()
            
            # 标准地址：行政区划 + 教育机构名称 + 详细地址
            standard_address = f"{province}{city}{district}{school_name}{detail_address}".strip()
            
            return {
                'original_address': address,  # 添加原始地址
                'province': province,
                'city': city,
                'district': district,
                'detail_address': detail_address,
                'standard_address': standard_address
            }
    
    # 第三步：如果教育机构匹配失败，尝试特殊区域匹配
    if special_area_mapping is None:
        special_area_mapping = load_special_area_mapping('interactive-map-app/data/特殊区域映射.json')
    for area_name, location_info in special_area_mapping.items():
        if area_name in address:
            # 找到特殊区域名称，返回对应的行政区划信息
            province = location_info.get('province', '')
            city = location_info.get('city', '')
            district = location_info.get('district', '')
            # 从地址中移除特殊区域名称，剩余部分作为详细地址
            detail_address = address.replace(area_name, '', 1).strip()
            
            return {
                'original_address': address,  # 添加原始地址
                'province': province,
                'city': city,
                'district': district,
                'detail_address': detail_address,
                'standard_address': f"{city}{district}{detail_address}".strip() if province == "直辖市" else f"{province}{city}{district}{detail_address}".strip()
            }
    
    # 第四步：如果以上方法都失败，使用详细的匹配算法
    
    # 记录已使用的地址范围
    used_ranges = []
    
    # 收集所有可能的匹配
    all_possible_matches = []
    
    # 收集可能存在的重复简称以供输出
    duplicate_abbrevs = []
    
    # 第一步：精确匹配 - 省份匹配
    for prov_name in mapping_data.keys():
        start = 0
        while True:
            pos = address.find(prov_name, start)
            if pos == -1:
                break
            all_possible_matches.append(('province', prov_name, '', pos, pos + len(prov_name)))
            start = pos + 1
    
    # 第二步：精确匹配 - 城市匹配
    for prov_name, cities in mapping_data.items():
        for city_name in cities.keys():
            start = 0
            while True:
                pos = address.find(city_name, start)
                if pos == -1:
                    break
                all_possible_matches.append(('city', city_name, prov_name, pos, pos + len(city_name)))
                start = pos + 1
    
    # 第三步：精确匹配 - 区县匹配
    for prov_name, cities in mapping_data.items():
        for city_name, districts in cities.items():
            for dist_name in districts:
                start = 0
                while True:
                    pos = address.find(dist_name, start)
                    if pos == -1:
                        break
                    all_possible_matches.append(('district', dist_name, city_name, pos, pos + len(dist_name)))
                    start = pos + 1
                
                # 添加区县名的简写形式匹配（如"天河区" -> "天河"）
                if dist_name.endswith(('区', '县', '市')):
                    dist_short_name = dist_name[:-1]  # 去掉最后一个字（区/县/市）
                    if dist_short_name:  # 确保不为空
                        start = 0
                        while True:
                            pos = address.find(dist_short_name, start)
                            if pos == -1:
                                break
                            # 对于区县简写，增加额外检查以避免误匹配
                            # 特别是单字符区县简写，如："吉祥苑"中的"吉"不应该被识别为"吉县"
                            if len(dist_short_name) == 1:
                                # 检查该字符是否在合理的上下文中
                                prev_char = address[pos-1] if pos > 0 else ''
                                next_char = address[pos+len(dist_short_name)] if pos+len(dist_short_name) < len(address) else ''
                                
                                # 避免在词语中间匹配单字符（如"吉祥"中的"吉"、"单元"中的"单"）
                                # 如果前后都是中文字符，则很可能是一个词语内部，而不是行政区划简称
                                is_prev_chinese = (len(prev_char) > 0 and '\u4e00' <= prev_char <= '\u9fff')
                                is_next_chinese = (len(next_char) > 0 and '\u4e00' <= next_char <= '\u9fff')
                                is_word_internal = is_prev_chinese and is_next_chinese
                                
                                # 检查是否是常见方位词或其他非行政区划词
                                # 避免将"北"、"南"、"东"、"西"等方位词误认为是区县名
                                common_directions = ['东', '南', '西', '北', '中']
                                is_direction = dist_short_name in common_directions
                                
                                # 进一步检查是否是常见的非行政区划语境
                                # 检查是否在"单元"、"人民"、"出版"等常见词语中
                                context_start = max(0, pos - 5)  # 查找前面最多5个字符
                                context_end = min(len(address), pos + len(dist_short_name) + 5)  # 查找后面最多5个字符
                                context = address[context_start:context_end]
                                
                                # 避免匹配在常见非行政区划词语中的字符
                                skip_patterns = ["单元", "人民", "出版", "公司", "大学", "医院", "学校", "小区", "大厦", "广场"]
                                should_skip = any(skip_pattern in context for skip_pattern in skip_patterns)
                                
                                # 只有在不是方位词、不在词语内部、且不在常见非行政区划语境中时才匹配
                                if not is_direction and not is_word_internal and not should_skip:
                                    all_possible_matches.append(('district_short', dist_short_name, city_name, dist_name, pos, pos + len(dist_short_name)))
                            else:
                                # 对于多字符区县简写，检查是否在合理语境中
                                prev_char = address[pos-1] if pos > 0 else ''
                                next_char = address[pos+len(dist_short_name)] if pos+len(dist_short_name) < len(address) else ''
                                
                                is_prev_chinese = (len(prev_char) > 0 and '\u4e00' <= prev_char <= '\u9fff')
                                is_next_chinese = (len(next_char) > 0 and '\u4e00' <= next_char <= '\u9fff')
                                is_word_internal = is_prev_chinese and is_next_chinese
                                
                                # 对多字符简写也进行类似的上下文检查
                                context_start = max(0, pos - 3)
                                context_end = min(len(address), pos + len(dist_short_name) + 3)
                                context = address[context_start:context_end]
                                
                                skip_patterns = ["人民", "出版", "公司", "大学", "医院", "学校", "小区", "大厦", "广场"]
                                should_skip = any(skip_pattern in context for skip_pattern in skip_patterns)
                                
                                if not is_word_internal or not should_skip:
                                    all_possible_matches.append(('district_short', dist_short_name, city_name, dist_name, pos, pos + len(dist_short_name)))
                            start = pos + 1

    # 第四步：简称匹配 - 使用已扁平化的简称映射进行匹配
    # 记录可能的重复简称
    abbrev_matches = []
    
    # 遍历扁平化的简称映射
    for abbrev, full_name in abbreviation_mapping.items():
        start = 0
        found_positions = []
        while True:
            pos = address.find(abbrev, start)
            if pos == -1:
                break
            # 对于省份简称，增加上下文检查以避免误匹配
            # 例如，避免将"滨河南路"中的"河南"误认为是"河南省"
            # 例如，避免将"内蒙古师范大学"中的"内蒙古"误认为是"内蒙古自治区"
            if full_name in mapping_data:  # 这是省份简称
                # 检查上下文，避免在词语中间匹配省份简称
                prev_char = address[pos-1] if pos > 0 else ''
                next_char = address[pos+len(abbrev)] if pos+len(abbrev) < len(address) else ''
                
                is_prev_chinese = (len(prev_char) > 0 and '\u4e00' <= prev_char <= '\u9fff')
                is_next_chinese = (len(next_char) > 0 and '\u4e00' <= next_char <= '\u9fff')
                is_word_internal = is_prev_chinese and is_next_chinese
                
                # 避免匹配在常见非行政区划词语中的省份简称
                context_start = max(0, pos - 3)  # 查找前面最多3个字符
                context_end = min(len(address), pos + len(abbrev) + 3)  # 查找后面最多3个字符
                context = address[context_start:context_end]
                
                # 如果在词语内部且上下文看起来像是街道名、路名、教育机构名等，则跳过
                skip_contexts = ["路", "街", "巷", "道", "大道", "南路", "北路", "东路", "西路", "巷", "弄", "胡同", "开发区", "大学", "学院", "学校", "中学", "小学", "幼儿园", "师范", "理工", "财经", "医科", "农业", "工业", "技术", "职业"]
                should_skip = any(skip_ctx in context for skip_ctx in skip_contexts if len(skip_ctx) > 1)
                
                # 特别处理"南路"、"北路"、"东路"、"西路"等情况
                # 检查"河南"是否后面紧跟"南路"形成"河南南路"这样的路名
                if pos + len(abbrev) + 1 < len(address):
                    next_two_chars = address[pos+len(abbrev):pos+len(abbrev)+2]
                    if next_two_chars in ['南路', '北路', '东路', '西路']:
                        should_skip = True
                elif next_char in ['南', '北', '东', '西']:
                    # 如果下一个字符是方向词，再检查下下个字符
                    if pos + len(abbrev) + 1 < len(address):
                        next_next_char = address[pos+len(abbrev)+1]
                        if next_char + next_next_char in ['南路', '北路', '东路', '西路']:
                            should_skip = True
                
                # 检查是否在机构名称中，如"内蒙古师范大学"、"内蒙古大学"、"内蒙古人民出版社"等
                # 如果简称后面的文本包含机构相关词汇，则跳过
                institution_keywords = [
                    # 教育机构
                    "大学", "学院", "学校", "中学", "小学", "师范", "理工", "财经", "医科", "农业", "工业", "技术", "职业", "附中", "附小",
                    # 出版社及媒体机构
                    "出版社", "报社", "杂志社", "期刊", "传媒", "电台", "电视台", "新闻",
                    # 公司企业
                    "公司", "厂", "工厂", "集团", "企业", "有限公司", "股份公司", "有限责任公司",
                    # 政府机构
                    "政府", "局", "委员会", "厅", "办", "部", "署", "院", "所", "中心", "委", "厅", "办",
                    # 特殊处理"站"，避免将"驿站"、"车站"、"地铁站"等地名误判为机构
                    # 这些通常不是直接跟在省份简称后面的机构名称
                ]
                # 检查剩余部分是否包含机构关键词
                remaining_part = address[pos + len(abbrev):]
                for inst_keyword in institution_keywords:
                    if inst_keyword in remaining_part:
                        should_skip = True
                        break
                
                # 特殊处理"站"字，避免误判地名中的"站"
                # 如果是"站"字，需要更精确的上下文判断
                if should_skip and "站" in remaining_part:
                    # 只有当目前should_skip为True且包含"站"时，才进行特殊处理
                    # 检查"站"字出现的具体上下文
                    station_pos = address.find("站", pos + len(abbrev))
                    if station_pos != -1:
                        # 检查"站"字前的词语，如果是"驿站"、"车站"、"地铁站"等，则不太可能是机构名
                        # 检查前2-3个字符
                        station_context_start = max(0, station_pos - 3)
                        station_context = address[station_context_start:station_pos + 1]  # 包含"站"字
                        
                        # 常见的地名后缀，不应被视为机构
                        location_suffixes = ["驿站", "车站", "地铁站", "火车站", "高铁站", "汽车站", "公交站", "服务站", "收费站"]
                        
                        if any(suffix in station_context for suffix in location_suffixes):
                            # 如果是地名后缀，则不跳过省份简称匹配
                            should_skip = False  # 不跳过
                
                # 只有当不是词语内部 且 不需要跳过时，才添加到匹配位置
                if not is_word_internal and not should_skip:
                    found_positions.append(pos)
            else:
                found_positions.append(pos)
            start = pos + 1
        
        # 确定简称类型并添加到匹配列表
        if full_name in mapping_data:
            # 这是省份简称
            for pos in found_positions:
                abbrev_matches.append(('province_abbrev', abbrev, full_name, pos, pos + len(abbrev)))
        else:
            # 检查是否是城市简称
            is_city_abbrev = False
            prov_for_city = None
            for prov_name, cities in mapping_data.items():
                if full_name in cities:
                    is_city_abbrev = True
                    prov_for_city = prov_name
                    break
            
            if is_city_abbrev:
                for pos in found_positions:
                    abbrev_matches.append(('city_abbrev', abbrev, full_name, prov_for_city, pos, pos + len(abbrev)))
            else:
                # 检查是否是区县简称
                is_district_abbrev = False
                city_for_district = None
                prov_for_district = None
                
                for prov_name, cities in mapping_data.items():
                    for city_name, districts in cities.items():
                        if full_name in districts:
                            is_district_abbrev = True
                            city_for_district = city_name
                            prov_for_district = prov_name
                            break
                    if is_district_abbrev:
                        break
                
                if is_district_abbrev:
                    for pos in found_positions:
                        abbrev_matches.append(('district_abbrev', abbrev, full_name, city_for_district, pos, pos + len(abbrev)))

    # 检测可能的重复简称冲突
    # 按位置和长度分组来检测冲突
    position_groups = {}
    for match in abbrev_matches:
        start_pos = match[-2]
        end_pos = match[-1]
        key = (start_pos, end_pos)
        if key not in position_groups:
            position_groups[key] = []
        position_groups[key].append(match)
    
    # 检查同一位置的不同简称
    for pos_range, matches in position_groups.items():
        if len(matches) > 1:
            # 同一位置有多个简称匹配，可能有冲突
            abbrev_text = address[pos_range[0]:pos_range[1]]
            duplicate_abbrevs.append({
                'text': abbrev_text,
                'position': pos_range,
                'conflicts': [(m[0], m[1], m[2]) for m in matches]  # 类型, 简称, 完整名称
            })
    
    # 添加简称匹配到总匹配列表
    all_possible_matches.extend(abbrev_matches)

    # 按位置优先，然后按长度优先，最后按类型优先的顺序排序
    # 实现从前往后的匹配策略，这样更符合地址的实际组织方式
    def sort_key(match):
        length = len(match[1])  # 名称长度
        match_type = match[0]
        # 调整优先级：省全称 > 省简称 > 市全称 > 市简称 > 区县全称 > 区县简称
        # 按照要求的顺序：省全称,省简称,市全称,市简称,区县全称,区县简称
        type_priority = {'province': 6, 'province_abbrev': 5, 'city': 4, 'city_abbrev': 3, 'district': 2, 'district_abbrev': 1}.get(match_type, 0)
        pos = match[-2]  # 起始位置
        
        # 计算地理一致性权重（仅对区县类型有用）
        geo_consistency_weight = 0
        if match_type in ['district', 'district_abbrev', 'district_short']:
            # 对于区县匹配，提取其所属城市
            if match_type == 'district':
                district_city = match[2]  # 对应城市
            elif match_type == 'district_abbrev':
                district_city = match[3]  # 对应城市
            elif match_type == 'district_short':
                district_city = match[2]  # 对应城市
            
            # 在当前匹配集合中查找可能存在的城市匹配
            # 这是一种启发式方法：优先考虑与已找到的城市相匹配的区县
            # 但由于排序在前，我们无法访问已处理的匹配，只能通过一些启发式方法
            # 实际上，我们可以在排序前预处理，但那样改动太大
            
            # 作为替代，我们可以对不同区县之间的相对优先级进行调整
            # 但这需要更复杂的算法重构
            # 对于当前情况，我们可以使用一个简化版本：给所有区县匹配相同的地理权重
            # 实际上，这个问题需要重新设计算法
        
        return (pos, -length, -type_priority)  # 位置优先（从前往后），然后长度优先，最后类型优先
    
    all_possible_matches.sort(key=sort_key)
    
    # 使用贪心算法选择不重叠的匹配
    used_ranges = []  # 已使用的区间 [(start, end), ...]
    province, city, district = "", "", ""
    
    for match in all_possible_matches:
        match_type = match[0]
        name = match[1]
        start_pos = match[-2]
        end_pos = match[-1]
        
        # 根据匹配类型获取额外信息
        if match_type == 'province':
            extra_info = None
        elif match_type == 'province_abbrev':
            extra_info = match[2]  # 完整名称
        elif match_type == 'city':
            extra_info = match[2]  # 对应省份
        elif match_type == 'city_abbrev':
            extra_info = match[2]  # 完整城市名
            prov_info = match[3]  # 对应省份
        elif match_type == 'district':
            extra_info = match[2]  # 对应城市
        elif match_type == 'district_short':
            extra_info = match[2]  # 对应城市
            original_dist_name = match[3]  # 原始区县名
        elif match_type == 'district_abbrev':
            extra_info = match[3]  # 对应城市
            original_dist_name = match[2]  # 原始区县名
        
        # 检查是否与已使用区间重叠
        overlap = False
        for used_start, used_end in used_ranges:
            if start_pos < used_end and end_pos > used_start:
                overlap = True
                break
        
        if not overlap:
            if match_type == 'province':
                if not province:
                    province = name
                used_ranges.append((start_pos, end_pos))  # 总是添加到used_ranges，即使行政区划已存在
            elif match_type == 'province_abbrev':
                if not province:
                    province = extra_info  # 完整名称
                    # 检查是否是直辖市（省份名称也在城市列表中）
                    # 如果是直辖市，则同时设置城市字段
                    if extra_info in mapping_data and extra_info in mapping_data.get(extra_info, {}):
                        if not city:
                            city = extra_info  # 直辖市，省份名称就是城市名称
                    used_ranges.append((start_pos, end_pos))  # 总是添加到used_ranges，即使行政区划已存在
            elif match_type == 'city':
                if not city:
                    # 检查地理一致性：如果省份已经确定，只接受属于该省份的城市
                    if province and province != extra_info:
                        # 城市不属于已确定的省份，跳过这个匹配
                        pass
                    else:
                        city = name
                        if not province:
                            province = extra_info  # 对应省份
                        used_ranges.append((start_pos, end_pos))  # 总是添加到used_ranges，即使行政区划已存在
            elif match_type == 'city_abbrev':
                if not city:
                    # 检查地理一致性：如果省份已经确定，只接受属于该省份的城市
                    if province and province != prov_info:
                        # 城市不属于已确定的省份，跳过这个匹配
                        pass
                    else:
                        city = name  # 简称
                        if not province:
                            province = prov_info  # 对应省份
                        used_ranges.append((start_pos, end_pos))  # 总是添加到used_ranges，即使行政区划已存在
            elif match_type == 'district':
                # 检查地理一致性：如果城市已经确定，只接受属于该城市的区县
                if not district:
                    # 如果城市已经确定，检查这个区县是否属于该城市
                    if city and city in mapping_data.get(province, {}):
                        # 检查当前区县是否属于已确定的城市
                        city_districts = mapping_data.get(province, {}).get(city, [])
                        if name in city_districts or name.rstrip('区县市') in [d.rstrip('区县市') for d in city_districts]:
                            # 区县属于已确定城市，接受这个匹配
                            district = name
                            used_ranges.append((start_pos, end_pos))
                        else:
                            # 区县不属于已确定城市，跳过这个匹配
                            pass
                    else:
                        # 城市未确定或省份未确定，但省份已确定的情况下，只接受属于该省份的区县
                        if province and province != "未识别" and province != "直辖市":
                            # 检查区县所属城市是否属于当前省份
                            prov_data = mapping_data.get(province, {})
                            if extra_info in prov_data:  # extra_info是区县所属城市
                                # 区县所属城市属于当前省份，接受这个匹配
                                district = name
                                if not city:
                                    city = extra_info  # 对应城市
                                    # 在地址中查找城市名及其简称并标记为已使用
                                    # 首先查找完整城市名
                                    city_pos = address.find(city)
                                    if city_pos != -1:
                                        # 检查是否与已使用区间重叠
                                        city_overlap = False
                                        for used_start, used_end in used_ranges:
                                            if city_pos < used_end and city_pos + len(city) > used_start:
                                                city_overlap = True
                                                break
                                        if not city_overlap:
                                            used_ranges.append((city_pos, city_pos + len(city)))
                                    else:
                                        # 如果找不到完整城市名，尝试去掉"市"后缀
                                        city_name_without_suffix = city.rstrip('市')
                                        if city_name_without_suffix != city:
                                            city_pos = address.find(city_name_without_suffix)
                                            if city_pos != -1:
                                                city_overlap = False
                                                for used_start, used_end in used_ranges:
                                                    if city_pos < used_end and city_pos + len(city_name_without_suffix) > used_start:
                                                        city_overlap = True
                                                        break
                                                if not city_overlap:
                                                    used_ranges.append((city_pos, city_pos + len(city_name_without_suffix)))
                                
                                if not province:
                                    # 在这里查找对应的省份
                                    for prov_name, cities in mapping_data.items():
                                        if city in cities and name in cities.get(city, []):
                                            province = prov_name
                                            break
                            else:
                                # 区县所属城市不属于当前省份，跳过这个匹配
                                pass
                        else:
                            # 省份未确定或为直辖市，接受匹配
                            district = name
                            if not city:
                                city = extra_info  # 对应城市
                                # 在地址中查找城市名及其简称并标记为已使用
                                # 首先查找完整城市名
                                city_pos = address.find(city)
                                if city_pos != -1:
                                    # 检查是否与已使用区间重叠
                                    city_overlap = False
                                    for used_start, used_end in used_ranges:
                                        if city_pos < used_end and city_pos + len(city) > used_start:
                                            city_overlap = True
                                            break
                                    if not city_overlap:
                                        used_ranges.append((city_pos, city_pos + len(city)))
                                else:
                                    # 如果找不到完整城市名，尝试去掉"市"后缀
                                    city_name_without_suffix = city.rstrip('市')
                                    if city_name_without_suffix != city:
                                        city_pos = address.find(city_name_without_suffix)
                                        if city_pos != -1:
                                            city_overlap = False
                                            for used_start, used_end in used_ranges:
                                                if city_pos < used_end and city_pos + len(city_name_without_suffix) > used_start:
                                                    city_overlap = True
                                                    break
                                            if not city_overlap:
                                                used_ranges.append((city_pos, city_pos + len(city_name_without_suffix)))
                                
                                if not province:
                                    # 在这里查找对应的省份
                                    for prov_name, cities in mapping_data.items():
                                        if city in cities and name in cities.get(city, []):
                                            province = prov_name
                                            break
                        used_ranges.append((start_pos, end_pos))  # 总是添加到used_ranges，即使行政区划已存在
            elif match_type == 'district_short':
                # 检查地理一致性：如果城市已经确定，只接受属于该城市的区县
                if not district:
                    # 如果城市已经确定，检查这个区县是否属于该城市
                    if city and city in mapping_data.get(province, {}):
                        # 检查当前区县是否属于已确定的城市
                        city_districts = mapping_data.get(province, {}).get(city, [])
                        if original_dist_name in city_districts or original_dist_name.rstrip('区县市') in [d.rstrip('区县市') for d in city_districts]:
                            # 区县属于已确定城市，接受这个匹配
                            district = original_dist_name  # 使用原始区县名
                            used_ranges.append((start_pos, end_pos))
                        else:
                            # 区县不属于已确定城市，跳过这个匹配
                            pass
                    else:
                        # 城市未确定或省份未确定，但省份已确定的情况下，只接受属于该省份的区县
                        if province and province != "未识别" and province != "直辖市":
                            # 检查区县所属城市是否属于当前省份
                            prov_data = mapping_data.get(province, {})
                            if extra_info in prov_data:  # extra_info是区县所属城市
                                # 区县所属城市属于当前省份，接受这个匹配
                                district = original_dist_name  # 使用原始区县名
                                if not city:
                                    city = extra_info  # 对应城市
                                    # 在地址中查找城市名及其简称并标记为已使用
                                    # 首先查找完整城市名
                                    city_pos = address.find(city)
                                    if city_pos != -1:
                                        # 检查是否与已使用区间重叠
                                        city_overlap = False
                                        for used_start, used_end in used_ranges:
                                            if city_pos < used_end and city_pos + len(city) > used_start:
                                                city_overlap = True
                                                break
                                        if not city_overlap:
                                            used_ranges.append((city_pos, city_pos + len(city)))
                                    else:
                                        # 如果找不到完整城市名，尝试去掉"市"后缀
                                        city_name_without_suffix = city.rstrip('市')
                                        if city_name_without_suffix != city:
                                            city_pos = address.find(city_name_without_suffix)
                                            if city_pos != -1:
                                                city_overlap = False
                                                for used_start, used_end in used_ranges:
                                                    if city_pos < used_end and city_pos + len(city_name_without_suffix) > used_start:
                                                        city_overlap = True
                                                        break
                                                if not city_overlap:
                                                    used_ranges.append((city_pos, city_pos + len(city_name_without_suffix)))
                            
                            else:
                                # 区县所属城市不属于当前省份，跳过这个匹配
                                pass
                        else:
                            # 省份未确定或为直辖市，接受匹配
                            district = original_dist_name  # 使用原始区县名
                            if not city:
                                city = extra_info  # 对应城市
                                # 在地址中查找城市名及其简称并标记为已使用
                                # 首先查找完整城市名
                                city_pos = address.find(city)
                                if city_pos != -1:
                                    # 检查是否与已使用区间重叠
                                    city_overlap = False
                                    for used_start, used_end in used_ranges:
                                        if city_pos < used_end and city_pos + len(city) > used_start:
                                            city_overlap = True
                                            break
                                    if not city_overlap:
                                        used_ranges.append((city_pos, city_pos + len(city)))
                                else:
                                    # 如果找不到完整城市名，尝试去掉"市"后缀
                                    city_name_without_suffix = city.rstrip('市')
                                    if city_name_without_suffix != city:
                                        city_pos = address.find(city_name_without_suffix)
                                        if city_pos != -1:
                                            city_overlap = False
                                            for used_start, used_end in used_ranges:
                                                if city_pos < used_end and city_pos + len(city_name_without_suffix) > used_start:
                                                    city_overlap = True
                                                    break
                                            if not city_overlap:
                                                used_ranges.append((city_pos, city_pos + len(city_name_without_suffix)))
                            
                            if not province:
                                # 在这里查找对应的省份
                                for prov_name, cities in mapping_data.items():
                                    if city in cities and original_dist_name in cities.get(city, []):
                                        province = prov_name
                                        break
                        used_ranges.append((start_pos, end_pos))  # 总是添加到used_ranges，即使行政区划已存在
            elif match_type == 'district_abbrev':
                # 检查地理一致性：如果城市已经确定，只接受属于该城市的区县
                if not district:
                    # 如果城市已经确定，检查这个区县是否属于该城市
                    if city and city in mapping_data.get(province, {}):
                        # 检查当前区县是否属于已确定的城市
                        city_districts = mapping_data.get(province, {}).get(city, [])
                        if match[2] in city_districts or match[2].rstrip('区县市') in [d.rstrip('区县市') for d in city_districts]:  # match[2] 是区县名
                            # 区县属于已确定城市，接受这个匹配
                            district = match[2]  # 使用完整区县名（如"昆都仑区"）
                            used_ranges.append((start_pos, end_pos))
                        else:
                            # 区县不属于已确定城市，跳过这个匹配
                            pass
                    else:
                        # 城市未确定或省份未确定，接受匹配
                        district = match[2]  # 使用完整区县名（如"昆都仑区"）
                        if not city:
                            city = match[3]  # 使用对应城市名（如"包头市"）
                            # 在地址中查找城市名及其简称并标记为已使用
                            # 首先查找完整城市名
                            city_pos = address.find(city)
                            if city_pos != -1:
                                # 检查是否与已使用区间重叠
                                city_overlap = False
                                for used_start, used_end in used_ranges:
                                    if city_pos < used_end and city_pos + len(city) > used_start:
                                        city_overlap = True
                                        break
                                if not city_overlap:
                                    used_ranges.append((city_pos, city_pos + len(city)))
                            else:
                                # 如果找不到完整城市名，尝试去掉"市"后缀
                                city_name_without_suffix = city.rstrip('市')
                                if city_name_without_suffix != city:
                                    city_pos = address.find(city_name_without_suffix)
                                    if city_pos != -1:
                                        city_overlap = False
                                        for used_start, used_end in used_ranges:
                                            if city_pos < used_end and city_pos + len(city_name_without_suffix) > used_start:
                                                city_overlap = True
                                                break
                                        if not city_overlap:
                                            used_ranges.append((city_pos, city_pos + len(city_name_without_suffix)))
                        
                        if not province:
                            # 在这里查找对应的省份
                            for prov_name, cities in mapping_data.items():
                                if city in cities and match[2] in cities.get(city, []):  # match[2] 是区县名
                                    province = prov_name
                                    break
                    used_ranges.append((start_pos, end_pos))  # 总是添加到used_ranges，即使行政区划已存在
    
    # 对used_ranges进行合并，处理相邻或嵌套的区间
    if used_ranges:
        used_ranges.sort(key=lambda x: x[0])  # 按起始位置排序
        merged_ranges = [used_ranges[0]]
        for current in used_ranges[1:]:
            last = merged_ranges[-1]
            if current[0] <= last[1]:  # 重叠或相邻
                merged_ranges[-1] = (last[0], max(last[1], current[1]))
            else:
                merged_ranges.append(current)
        used_ranges = merged_ranges
    
    # 排序已使用的区间，准备构建detail_address
    used_ranges.sort(key=lambda x: x[0])

    # 注意：上面的算法已经处理了所有匹配，下面不再需要重复的城市和区县查找逻辑



    # 从原始地址中移除已识别的行政区划部分，构建detail_address
    # 按位置倒序排序，从后往前替换，避免位置偏移
    used_ranges_sorted = sorted(used_ranges, key=lambda x: x[0], reverse=True)
    cleaned_detail_address = address
    for start, end in used_ranges_sorted:
        cleaned_detail_address = cleaned_detail_address[:start] + cleaned_detail_address[end:]
    
    # 额外的清理：移除可能的重复词汇，如"自治区"、"省"、"市"、"区"、"县"等
    # 以及已识别的行政区划名称，避免在detail_address中重复
    if province:
        cleaned_detail_address = cleaned_detail_address.replace(province, "")
    if city:
        cleaned_detail_address = cleaned_detail_address.replace(city, "")
    if district:
        cleaned_detail_address = cleaned_detail_address.replace(district, "")
    
    # 移除连续的重复字符（如"内蒙古内蒙古"）
    import re
    cleaned_detail_address = re.sub(r'(.)\1+', r'\1', cleaned_detail_address)
    
    # 清理多余的空白字符和特殊符号
    cleaned_detail_address = re.sub(r'\s+', '', cleaned_detail_address)
    cleaned_detail_address = cleaned_detail_address.strip()
    
    # 打印检测到的重复简称（如果有）
    if duplicate_abbrevs:
        print(f"地址 '{address}' 中发现可能的简称冲突:")
        for dup in duplicate_abbrevs:
            print(f"  位置 {dup['position']}: '{dup['text']}' 可能指代:")
            for conflict_type, abbrev, full_name in dup['conflicts']:
                print(f"    - {conflict_type}: {abbrev} -> {full_name}")
    
    # 城市标准化
    standardized_city = city
    # 在扁平化的abbreviation_mapping中查找反向映射，将简称转换为全称
    # 但对直辖市的简称不进行转换
    if city not in ["北京", "上海", "天津", "重庆"]:  # 直辖市简称保持不变
        for abbrev, full_name in abbreviation_mapping.items():
            # 检查full_name是否是城市名（即在mapping_data中存在）
            is_city = False
            for _, cities in mapping_data.items():
                if full_name in cities:
                    is_city = True
                    break
            
            # 如果当前城市名是简称，且对应的完整名称是有效的城市名，则进行转换
            if is_city and standardized_city == abbrev:
                standardized_city = full_name
                break

    # 标准化地址
    # 如果省份是直辖市，则不在标准化地址中包含省份信息
    # 如果区县为"未识别"，则不在标准化地址中包含区县信息
    if province == "直辖市":
        if district == "未识别":
            standard_address = f"{standardized_city}{cleaned_detail_address}".strip()
        else:
            standard_address = f"{standardized_city}{district}{cleaned_detail_address}".strip()
    else:
        if district == "未识别":
            standard_address = f"{province}{standardized_city}{cleaned_detail_address}".strip()
        else:
            standard_address = f"{province}{standardized_city}{district}{cleaned_detail_address}".strip()

    # 应用CLEAN_RULES
    CLEAN_RULES = {
        r'\s+': '',          # 移除所有空格
        r'[,，。.、；;：:]': '', # 移除常见标点
        r'（.*?）': '',      # 移除括号及括号内内容
        r'\(.*?\)': '',      # 移除英文括号及内容
        r'号(?!\d*楼|\d*单元|\d*栋|\d*室).*': '号',  # 移除"号"后不是楼/单元/栋/室的情况，保留"号楼"、"号单元"等
    }
    
    for pattern, replacement in CLEAN_RULES.items():
        cleaned_detail_address = re.sub(pattern, replacement, cleaned_detail_address)
        standard_address = re.sub(pattern, replacement, standard_address)

    # 校验解析结果是否与地址映射匹配
    validation_errors = []
    
    # 检查省份是否存在（除非是直辖市）
    if province and province != "直辖市":
        if province not in mapping_data:
            validation_errors.append(f"省份 '{province}' 不存在于地址映射中")
    
    # 检查城市是否属于对应省份
    validation_city = standardized_city  # 使用标准化后的城市名进行校验
    if province and validation_city and province in mapping_data and province != "直辖市":
        province_data = mapping_data[province]
        if validation_city not in province_data:
            validation_errors.append(f"城市 '{validation_city}' 不属于省份 '{province}'")
    elif province == "直辖市" and validation_city:
        # 对于直辖市，检查直辖市是否在直辖市类别下
        if "直辖市" not in mapping_data or validation_city not in mapping_data["直辖市"]:
            validation_errors.append(f"城市 '{validation_city}' 不属于直辖市类别")
    
    # 检查区县是否属于对应城市
    district_valid = True  # 标记区县是否有效
    if province and validation_city and district:
        if province == "直辖市":
            # 对于直辖市
            if "直辖市" in mapping_data and validation_city in mapping_data["直辖市"] and district not in mapping_data["直辖市"][validation_city]:
                validation_errors.append(f"区县 '{district}' 不属于直辖市 '{validation_city}'")
                district_valid = False  # 标记区县无效
        else:
            # 对于普通省份
            if province in mapping_data and validation_city in mapping_data[province] and district not in mapping_data[province][validation_city]:
                validation_errors.append(f"区县 '{district}' 不属于城市 '{validation_city}'")
                district_valid = False  # 标记区县无效
    
    # 如果区县无效，将区县设为"未识别"，但需要更智能地处理详细地址
    original_district = district  # 保存原始区县名
    if not district_valid and district:
        # 重构详细地址，需要考虑原始地址的实际情况
        # 由于在解析过程中，原始地址被分割，我们需要重构详细地址部分
        # 保留错误识别的区县名作为详细地址的一部分
        
        # 方法：使用原始地址，移除已正确识别的省份和城市，保留原始的详细部分
        # 但要小心处理名称差异（如"内蒙古" vs "内蒙古自治区"）
        
        # 首先尝试找到原始地址中的实际匹配位置
        temp_address = address
        # 移除省份部分 - 尝试匹配可能的省份名称变体
        if province:
            # 尝试匹配省份的各种可能形式
            possible_province_names = [province]
            if province.endswith('自治区'):
                possible_province_names.append(province.replace('自治区', ''))
            elif province.endswith('省'):
                possible_province_names.append(province.replace('省', ''))
            elif province.endswith('市'):
                possible_province_names.append(province.replace('市', ''))
                
            for prov_name in possible_province_names:
                if prov_name in temp_address:
                    temp_address = temp_address.replace(prov_name, '', 1)
                    break
        
        # 移除城市部分 - 尝试匹配城市的可能形式
        if city:
            possible_city_names = [city]
            if city.endswith('市'):
                possible_city_names.append(city.replace('市', ''))
                
            for city_name in possible_city_names:
                if city_name in temp_address:
                    temp_address = temp_address.replace(city_name, '', 1)
                    break
        
        # 现在temp_address应该包含剩余的详细地址部分，包括错误识别的区县
        # 应用CLEAN_RULES清理
        for pattern, replacement in CLEAN_RULES.items():
            temp_address = re.sub(pattern, replacement, temp_address)
        
        cleaned_detail_address = temp_address
        district = "未识别"  # 将区县设为"未识别"
        
        # 重新生成标准地址以反映区县的更改
        # 如果区县为"未识别"，则不在标准化地址中包含区县信息
        if province == "直辖市":
            if district == "未识别":
                standard_address = f"{validation_city}{cleaned_detail_address}".strip()
            else:
                standard_address = f"{validation_city}{district}{cleaned_detail_address}".strip()
        else:
            if district == "未识别":
                standard_address = f"{province}{validation_city}{cleaned_detail_address}".strip()
            else:
                standard_address = f"{province}{validation_city}{district}{cleaned_detail_address}".strip()
    
    # 如果有校验错误，将信息添加到全局列表中
    if validation_errors:
        validation_failures.append({
            'original_address': address,
            'parsed_province': province,
            'parsed_city': standardized_city,
            'parsed_district': district,
            'standard_address': standard_address,
            'errors': validation_errors
        })
    
    return {
        'province': province,
        'city': standardized_city,  # 使用标准化城市名
        'district': district,
        'detail_address': cleaned_detail_address,
        'standard_address': standard_address,
        'original_address': address,  # 添加原始地址
        'validation_errors': validation_errors if validation_errors else None  # 添加校验结果
    }

def process_and_save_addresses(csv_file_path, json_mapping_path, abbrev_json_path, education_mapping, output_file_path=None):
    """
    处理CSV文件中的地址并保存结果到新的CSV文件
    """
    # 加载地址映射和简称映射
    mapping_data = load_address_mapping(json_mapping_path)
    abbreviation_mapping = load_abbreviation_mapping(abbrev_json_path)
    
    # 加载特殊区域映射
    special_area_mapping = load_special_area_mapping('interactive-map-app/data/特殊区域映射.json')
    
    # 读取CSV文件 - 尝试不同的编码
    encodings = ['utf-8', 'gbk', 'gb2312', 'ansi']
    df = None
    for encoding in encodings:
        try:
            df = pd.read_csv(csv_file_path, encoding=encoding,header=1)
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
    
    # 存储无法识别的地址
    unrecognizable_addresses = []
    
    # 处理每一条地址
    for index, row in df.iterrows():
        # 获取A列数据（第一列）
        a_column_value = ""
        if len(row) > 0:
            a_column_value = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ""
        
        # 获取地址字段（第二列）
        address = ""
        if len(row) > 1:
            address = str(row.iloc[1]) if pd.notna(row.iloc[1]) else ""
        elif len(row) > 0:
            address = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ""
        
        if address.strip():  # 如果地址非空
            cleaned = clean_address_with_mapping(address, mapping_data, abbreviation_mapping, education_mapping, special_area_mapping)
            
            # 添加清洗结果到列表
            result_row = {
                '编号+孩子中文全名': a_column_value,  # 添加A列数据
                '原始地址': cleaned['original_address'],
                '省份': cleaned['province'] or '未识别',
                '城市': cleaned['city'] or '未识别',
                '区县': cleaned['district'] or '未识别',
                '详细地址': cleaned['detail_address'] or '未识别',
                '标准地址': cleaned['standard_address']
            }
            results_list.append(result_row)
            
            # 检查是否为无法识别的地址
            if (cleaned['province'] == '' or cleaned['province'] == '未识别') and \
               (cleaned['city'] == '' or cleaned['city'] == '未识别') and \
               (cleaned['district'] == '' or cleaned['district'] == '未识别') and\
                (cleaned['original_address'] != '无'):
                unrecognizable_addresses.append({
                    '编号+孩子中文全名': a_column_value,  # 添加A列数据
                    '原始地址': cleaned['original_address'],
                    '省份': cleaned['province'],
                    '城市': cleaned['city'],
                    '区县': cleaned['district'],
                    '详细地址': cleaned['detail_address']
                })
            
            # 打印结果
            print(f"编号+孩子中文全名: {a_column_value}, 原始地址: {cleaned['original_address']}")
            print(f"解析结果:")
            print(f"  省份: {cleaned['province'] or '未识别'}")
            print(f"  城市: {cleaned['city'] or '未识别'}")
            print(f"  区县: {cleaned['district'] or '未识别'}")
            print(f"  详细地址: {cleaned['detail_address'] or '未识别'}")
            print(f"  标准化地址: {cleaned['standard_address']}")
            print("-" * 80)
        else:
            print(f"第 {index+1} 行地址为空，跳过")
    
    # 打印无法识别的地址
    print("\n无法识别的地址列表:")
    print("="*80)
    for idx, unrec_addr in enumerate(unrecognizable_addresses):
        print(f"{idx+1}. 编号+孩子中文全名: {unrec_addr['编号+孩子中文全名']}, 原始地址: {unrec_addr['原始地址']}")
        print(f"   省份: {unrec_addr['省份'] or '未识别'}")
        print(f"   城市: {unrec_addr['城市'] or '未识别'}")
        print(f"   区县: {unrec_addr['区县'] or '未识别'}")
        print(f"   详细地址: {unrec_addr['详细地址'] or '未识别'}")
        print("-" * 40)
    
    print(f"\n总共发现 {len(unrecognizable_addresses)} 条无法识别的地址")
    
    # 将清洗结果转换为DataFrame
    results_df = pd.DataFrame(results_list)
    
    # 如果提供了输出路径，则保存结果
    if output_file_path:
        results_df.to_csv(output_file_path, index=False, encoding='utf-8')
        print(f"清洗结果已保存到: {output_file_path}")
    
    # 二次校验：对处理结果进行再次验证
    print("\n" + "="*60)
    print("开始二次校验...")
    print("="*60)
    
    anomalies = []
    for index, row in results_df.iterrows():
        original_address = row['原始地址']
        province = row['省份']
        city = row['城市'] 
        district = row['区县']
        
        # 执行与原始校验相同的验证逻辑
        validation_errors = []
        
        # 检查省份是否存在（除非是未识别或直辖市）
        if province and province != "未识别" and province != "直辖市":
            if province not in mapping_data:
                validation_errors.append(f"省份 '{province}' 不存在于地址映射中")
        
        # 检查城市是否属于对应省份
        validation_city = city  # 在原始处理中，city可能经过标准化
        if province and validation_city and province in mapping_data and province != "直辖市" and province != "未识别" and validation_city != "未识别":
            province_data = mapping_data[province]
            if validation_city not in province_data:
                validation_errors.append(f"城市 '{validation_city}' 不属于省份 '{province}'")
        elif province == "直辖市" and validation_city and validation_city != "未识别":
            # 对于直辖市，检查直辖市是否在直辖市类别下
            if "直辖市" not in mapping_data or validation_city not in mapping_data["直辖市"]:
                validation_errors.append(f"城市 '{validation_city}' 不属于直辖市类别")
        
        # 检查区县是否属于对应城市
        if province and validation_city and district and district != "未识别":
            if province == "直辖市":
                # 对于直辖市
                if "直辖市" in mapping_data and validation_city in mapping_data["直辖市"] and district not in mapping_data["直辖市"][validation_city]:
                    validation_errors.append(f"区县 '{district}' 不属于直辖市 '{validation_city}'")
            else:
                # 对于普通省份
                if province in mapping_data and validation_city in mapping_data[province] and district not in mapping_data[province][validation_city]:
                    validation_errors.append(f"区县 '{district}' 不属于城市 '{validation_city}'")
        
        # 如果有验证错误，记录异常
        if validation_errors:
            anomaly_record = {
                'index': index,
                'original_address': original_address,
                'parsed_province': province,
                'parsed_city': city,
                'parsed_district': district,
                'errors': validation_errors
            }
            anomalies.append(anomaly_record)
    
    # 打印异常数据
    if anomalies:
        print(f"二次校验发现 {len(anomalies)} 条异常数据:")
        print("-" * 60)
        for i, anomaly in enumerate(anomalies, 1):
            print(f"\n{i}. 原始地址: {anomaly['original_address']}")
            print(f"   解析结果: 省份='{anomaly['parsed_province']}', 城市='{anomaly['parsed_city']}', 区县='{anomaly['parsed_district']}'")
            print(f"   二次校验错误:")
            for error in anomaly['errors']:
                print(f"     - {error}")
    else:
        print("二次校验完成，未发现异常数据。")
    
    print("="*60)
    
    return results_df

def print_validation_failures():
    """
    打印所有校验失败的地址信息
    """
    global validation_failures
    if validation_failures:
        print("\n" + "="*60)
        print("地址映射校验失败汇总:")
        print("="*60)
        for i, failure in enumerate(validation_failures, 1):
            print(f"\n{i}. 原始地址: {failure['original_address']}")
            print(f"   解析结果: 省份={failure['parsed_province']}, 城市={failure['parsed_city']}, 区县={failure['parsed_district']}")
            print(f"   错误信息:")
            for error in failure['errors']:
                print(f"     - {error}")
        print(f"\n总计: {len(validation_failures)} 条校验失败的地址")
        print("="*60)
    else:
        print("\n所有地址均通过映射校验，无错误。")

# 主程序执行
if __name__ == "__main__":
    import os
    # 获取当前脚本所在的目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    csv_file_path = os.path.join(current_dir, "地址.csv")
    json_mapping_path = os.path.join(current_dir, "地址映射.json")
    abbrev_json_path = os.path.join(current_dir, "简称映射.json")
    output_file_path = os.path.join(current_dir, "清洗后地址.csv")
    education_mapping = load_education_mapping('interactive-map-app/data/教育机构映射.json')
    print("开始清洗地址数据...")
    result_df = process_and_save_addresses(csv_file_path, json_mapping_path, abbrev_json_path, education_mapping, output_file_path)
    print("地址清洗完成！")
    
    # 打印校验失败的地址信息
    print_validation_failures()