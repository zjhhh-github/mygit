import pandas as pd
import json
import re

file_path = r'C:\Users\LENOVO\Desktop\报名录入.xlsx'

print("读取数据...")

df = pd.read_excel(file_path, sheet_name='报名录入', header=1)
df_internal = pd.read_excel(file_path, sheet_name='内部通讯录', header=1)

print(f"报名录入: {len(df)}, 内部通讯录: {len(df_internal)}")

print("构建字典...")
student_dict = {}
for i in range(len(df_internal)):
    row = df_internal.iloc[i]
    student_name = str(row.get('学员', '')).strip()
    student_name_clean = re.sub(r'^¿¿¿\d+-', '', student_name)
    if student_name_clean and student_name_clean != 'nan':
        student_dict[student_name_clean] = {
            '渠道C': str(row.get('渠道C', '')) if pd.notna(row.get('渠道C', '')) else '',
            '带领C': str(row.get('带领C', '')) if pd.notna(row.get('带领C', '')) else ''
        }
        child_names = str(row.get('孩子中文全名', '')).split()
        for cn in child_names:
            if cn and cn != 'nan':
                student_dict[cn] = student_dict[student_name_clean]
print(f"完成，共 {len(student_dict)} 个学员")

print("转换...")

def extract_phone(text):
    if pd.isna(text) or text == '':
        return ''
    match = re.search(r'1[3-9]\d{9}', str(text))
    return match.group() if match else ''

def extract_amount(text):
    if pd.isna(text) or text == '':
        return 0
    text = str(text).strip()
    text = text.replace('×', '*')
    
    import re
    
    def eval_simple(s):
        s = s.strip()
        if not s:
            return 0
        try:
            return float(s)
        except:
            return 0
    
    tokens = re.findall(r'[\d.]+|\+|-|\*', text)
    
    if not tokens:
        return 0
    
    try:
        expr = text
        expr = re.sub(r'(\d+)\*(\d+)', lambda m: str(int(m.group(1)) * int(m.group(2))), expr)
        parts = expr.replace('+', ' + ').replace('-', ' - ').split()
        
        result = 0
        op = '+'
        for part in parts:
            if part in '+-':
                op = part
            else:
                val = eval_simple(part)
                if op == '+':
                    result += val
                else:
                    result -= val
                op = '+'
        
        return int(result)
    except:
        pass
    
    try:
        return int(float(text))
    except:
        return 0

def parse_time_to_date(time_str):
    if pd.isna(time_str) or time_str == '' or time_str == '❌':
        return None
    time_str = str(time_str).strip()
    if time_str == '❌':
        return '❌'
    try:
        dt = pd.to_datetime(time_str)
        return dt.strftime('%Y/%m/%d')
    except:
        return None

result = []
n = len(df)
for i in range(n):
    if i % 1000 == 0:
        print(f"处理中... {i}/{n}")
    
    row = df.iloc[i]
    child_name = str(row.get('孩子中文全名', '')).strip() if pd.notna(row.get('孩子中文全名', '')) else ''
    
    matched_info = student_dict.get(child_name, {})
    matched_channel = matched_info.get('渠道C', '')
    matched_leader = matched_info.get('带领C', '')
    
    record = {
        "ID": str(row.get('编号', '')) if pd.notna(row.get('编号', '')) else '',
        "中文名": child_name,
        "英文名": '',
        "孩子中文全名": child_name,
        "手机号": extract_phone(str(row.get('收件人电话', ''))),
        "身份证号": '',
        "微信原始ID": '',
        "微信号": '',
        "微信昵称": '',
        "微信备注": '',
        "地址": {
            "省": '',
            "市": '',
            "区/县": '',
            "具体": str(row.get('收件人地址', '')) if pd.notna(row.get('收件人地址', '')) else '',
            "全部": str(row.get('收件人地址', '')) if pd.notna(row.get('收件人地址', '')) else ''
        },
        "公司信息": {"公司名称": '', "公司税号": '', "公司开户银行": '', "公司银行账号": ''},
        "编号信息": {"慧分账编号": '', "拉卡拉编号": ''},
        "普通宝妈": '',
        "合伙宝妈": '',
        "老师": str(row.get('老师', '')) if pd.notna(row.get('老师', '')) else '',
        "教务": '',
        "场地": '',
        "推荐": str(row.get('推荐', '')) if pd.notna(row.get('推荐', '')) else '',
        "渠道": matched_channel,
        "带领": matched_leader,
        "线下剩余课时": '',
        "线上剩余课时": '',
        "孩子信息": [{
            "孩子中文全名": child_name,
            "孩子英文名": '',
            "孩子出生年月": '',
            "孩子性别": str(row.get('孩子性别', '')) if pd.notna(row.get('孩子性别', '')) else ''
        }],
        "报名信息": [{
            "订单号": str(row.get('交易订单编号', '')) if pd.notna(row.get('交易订单编号', '')) else '',
            "项目": str(row.get('报名项', '')) if pd.notna(row.get('报名项', '')) else '',
            "金额": extract_amount(row.get('订单金额', 0)),
            "日期": parse_time_to_date(row.get('交易时间', '')),
            "聚水潭单号": '',
            "快递单号": str(row.get('单号', '')) if pd.notna(row.get('单号', '')) else '',
            "激活码": str(row.get('激活码', '')) if pd.notna(row.get('激活码', '')) else '',
            "校区": str(row.get('校区', '')) if pd.notna(row.get('校区', '')) else '',
            "老师": str(row.get('老师', '')) if pd.notna(row.get('老师', '')) else '',
            "班级": ''
        }],
        "班级信息": []
    }
    result.append(record)

print("保存...")
output_path = r'd:\桌面文件\新建文件夹\数据结构处理\转换结果.json'
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"成功转换 {len(result)} 条记录")
print(f"已保存至: {output_path}")
