import pandas as pd

file_path = r'C:\Users\LENOVO\Desktop\内部通讯录.xlsx'

df = pd.read_excel(file_path, header=0)

df.columns = df.iloc[0]
df = df[1:].reset_index(drop=True)

original_df = df.copy()

print("列名:", df.columns.tolist())
print("\n数据形状:", df.shape)

recommend_counts = df['推荐'].value_counts()

max_iterations = 10
iteration = 0
filled_count_total = 0
final_filled_records = {}

while iteration < max_iterations:
    iteration += 1
    print(f"\n第 {iteration} 轮迭代")
    
    recommend_counts = df['推荐'].value_counts()
    baoma = set(recommend_counts[recommend_counts > 5].index.tolist())
    print(f"宝妈（推荐次数>5）人数: {len(baoma)}")
    
    channel_c_map = {}
    lead_c_map = {}
    
    for idx, row in df.iterrows():
        student = row['学员']
        channel_c = row['渠道C']
        lead_c = row['带领C']
        
        if pd.notna(channel_c):
            channel_c_map[student] = channel_c
        if pd.notna(lead_c):
            lead_c_map[student] = lead_c
    
    print(f"已有渠道C信息的人数: {len(channel_c_map)}")
    print(f"已有带领C信息的人数: {len(lead_c_map)}")
    
    filled_count = 0
    for idx, row in df.iterrows():
        student = row['学员']
        recommend = row['推荐']
        
        if pd.isna(row['渠道C']) or pd.isna(row['带领C']) or row['渠道C'] == '⚠️' or row['带领C'] == '⚠️':
            if recommend in baoma:
                df.at[idx, '渠道C'] = recommend
                df.at[idx, '带领C'] = recommend
                filled_count += 1
                final_filled_records[idx] = {
                    'original_index': idx,
                    '学员': student,
                    '推荐': recommend,
                    '渠道C': recommend,
                    '带领C': recommend,
                    '类型': '宝妈'
                }
            elif recommend in channel_c_map and recommend in lead_c_map:
                df.at[idx, '渠道C'] = channel_c_map[recommend]
                df.at[idx, '带领C'] = lead_c_map[recommend]
                filled_count += 1
                final_filled_records[idx] = {
                    'original_index': idx,
                    '学员': student,
                    '推荐': recommend,
                    '渠道C': channel_c_map[recommend],
                    '带领C': lead_c_map[recommend],
                    '类型': '非宝妈'
                }
            elif pd.isna(row['渠道C']) or pd.isna(row['带领C']):
                original_channel_c = row['渠道C']
                original_lead_c = row['带领C']
                
                if pd.isna(original_channel_c) or original_channel_c == '⚠️':
                    df.at[idx, '渠道C'] = '⚠️'
                if pd.isna(original_lead_c) or original_lead_c == '⚠️':
                    df.at[idx, '带领C'] = '⚠️'
                filled_count += 1
                final_filled_records[idx] = {
                    'original_index': idx,
                    '学员': student,
                    '推荐': recommend,
                    '渠道C': '⚠️' if pd.isna(original_channel_c) or original_channel_c == '⚠️' else original_channel_c,
                    '带领C': '⚠️' if pd.isna(original_lead_c) or original_lead_c == '⚠️' else original_lead_c,
                    '类型': '未找到'
                }
    
    print(f"本轮填充了 {filled_count} 条记录")
    filled_count_total += filled_count
    
    if filled_count == 0:
        print("没有新数据需要填充，迭代结束")
        break

print(f"\n总共填充了 {filled_count_total} 条记录")

filled_records_sorted = sorted(final_filled_records.values(), key=lambda x: x['original_index'])

if filled_records_sorted:
    print("\n填充的记录详情:")
    for i, record in enumerate(filled_records_sorted, 1):
        print(f"{i}. 学员: {record['学员']}, 推荐: {record['推荐']}, 渠道C: {record['渠道C']}, 带领C: {record['带领C']}, 类型: {record['类型']}")

print("\n填充后渠道C为空的行数:", df['渠道C'].isna().sum())
print("填充后带领C为空的行数:", df['带领C'].isna().sum())

output_path = r'd:\桌面文件\新建文件夹\test\内部通讯录_已填充.xlsx'
df.to_excel(output_path, index=False)
print(f"\n结果已保存到: {output_path}")

txt_output_path = r'C:\Users\LENOVO\Desktop\_输出结果_1.txt'
with open(txt_output_path, 'w', encoding='utf-8') as f:
    f.write("渠道C\t带领C\n")
    f.write("-" * 40 + "\n")
    for record in filled_records_sorted:
        f.write(f"{record['渠道C']}\t{record['带领C']}\n")
print(f"填充数据已保存到: {txt_output_path}")
