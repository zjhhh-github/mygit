import pandas as pd
import plotly.graph_objects as go  # pyright: ignore[reportMissingImports]
from plotly.subplots import make_subplots  # pyright: ignore[reportMissingImports]
import os
import glob
import re

# 读取"D:\桌面文件\宝妈结构图"目录下的所有.md文件
md_files = glob.glob(r"D:\桌面文件\宝妈结构图\*.md")

print(f"找到 {len(md_files)} 个.md文件:")
for file in md_files:
    print(os.path.basename(file))

# 读取每个.md文件的内容
md_contents = {}
for file in md_files:
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()
        md_contents[os.path.basename(file)] = content

# 解析.md文件内容，提取合伙人信息
def parse_md_content(content):
    """解析.md文件内容，提取合伙人信息"""
    lines = content.split('\n')
    partners = []
    
    for line in lines:
        # 匹配格式：¿¿¿000030-韩鹤天 韩鹤鸣-331-308
        # 或者：¿¿¿000122-王雨桐-55-55
        match = re.search(r'¿¿¿(\d+)-([^-]+)(?:\s+([^-]+))?-(\d+)-(\d+)', line)
        if match:
            partner_id = match.group(1)
            name1 = match.group(2)
            name2 = match.group(3) if match.group(3) else ""
            num1 = int(match.group(4))
            num2 = int(match.group(5))
            
            partner_info = {
                'partner_id': partner_id,
                'name1': name1.strip(),
                'name2': name2.strip() if name2 else "",
                'number1': num1,
                'number2': num2
            }
            partners.append(partner_info)
    
    return partners

# 按日期处理每个.md文件
results = {}
for filename, content in md_contents.items():
    date_match = re.search(r'(\d{8})', filename)
    if date_match:
        date = date_match.group(1)
        partners = parse_md_content(content)
        results[date] = partners
        print(f"\n{date} 有 {len(partners)} 个合伙人记录")

# 统计新增合伙人
all_partners = {}  # 存储所有出现过的合伙人
new_partners_by_date = {}  # 存储每天新增的合伙人

dates_sorted = sorted(results.keys())

for date in dates_sorted:
    current_partners = results[date]
    new_partners = []
    
    for partner in current_partners:
        partner_key = partner['partner_id']
        
        # 如果这个合伙人之前没有出现过，则为新增
        if partner_key not in all_partners:
            new_partners.append(partner)
            all_partners[partner_key] = {'first_seen': date, 'info': partner}
    
    new_partners_by_date[date] = new_partners
    print(f"{date}: 新增 {len(new_partners)} 个合伙人")

# 创建统计表格
stats_data = []
for date in dates_sorted:
    total_partners = len(results[date])
    new_partners_count = len(new_partners_by_date[date])
    stats_data.append({
        'date': date,
        'total_partners': total_partners,
        'new_partners': new_partners_count
    })

df_stats = pd.DataFrame(stats_data)
print("\n统计结果:")
df_stats.at[0, 'new_partners'] = 0  # 20251204 初始数据不计入新增
df_stats['new_partners_pct'] = df_stats['new_partners'] / df_stats['total_partners'] * 100
print(df_stats)

# 使用pandas和plotly生成图表
fig = make_subplots(specs=[[{"secondary_y": True}]])

fig.add_trace(
    go.Bar(x=df_stats['date'], y=df_stats['total_partners'], name="总合伙人数量", marker_color='blue'),
    secondary_y=False,
)

fig.add_trace(
    go.Scatter(x=df_stats['date'], y=df_stats['new_partners'], name="新增合伙人数量", 
               line=dict(color='red', width=2), mode='lines+markers'),
    secondary_y=True,
)

# fig.add_trace(
#     go.Scatter(x=df_stats['date'], y=df_stats['new_partners_pct'], name="新增合伙人占比", 
#                line=dict(color='green', width=2), mode='lines+markers'),
#     secondary_y=True,
# )
fig.update_layout(
    title_text="宝妈合伙人统计 - 总数与新增趋势",
    xaxis_title="日期",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

fig.update_yaxes(title_text="总合伙人数量", secondary_y=False)
fig.update_yaxes(title_text="新增合伙人数量", secondary_y=True)

# 将图表保存为HTML文件（保存到当前脚本所在目录）
output_html = os.path.join(os.path.dirname(os.path.abspath(__file__)), "统计新增合伙宝妈_统计图.html")
fig.write_html(output_html)
print(f"\n图表已保存为HTML文件: {output_html}")

fig.show()

print(f"\n总共发现了 {len(all_partners)} 个不同的合伙人")
print("\n=== 每次新增的合伙人详情 ===\n")
for date in dates_sorted:
    if date != '20251204' and new_partners_by_date[date]:  # 排除20251204的数据
        print(f"[{date}] 新增 {len(new_partners_by_date[date])} 个合伙人:")
        for partner in new_partners_by_date[date]:
            name = f"{partner['name1']} {partner['name2']}".strip()
            print(f"¿¿¿{partner['partner_id']}-{name}")
        print()
    elif date == '20251204':
        print(f"[{date}] 初始数据 {len(new_partners_by_date[date])} 个合伙人 (不计入新增)")
        print()

