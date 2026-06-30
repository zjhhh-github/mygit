import os
import re
from collections import defaultdict

directory = r"D:\桌面文件\宝妈结构图"

date_counts = defaultdict(int)

for filename in sorted(os.listdir(directory)):
    if filename.endswith('.md'):
        date_match = re.search(r'\d{8}', filename)
        if date_match:
            date = date_match.group()
            filepath = os.path.join(directory, filename)
            
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            pattern = r'¿¿¿\d{6}-([^\s-]+(?: [^\s-]+)*)-?\d*'
            names = re.findall(pattern, content)
            
            date_counts[date] = len(names)
            print(f"{date}: {len(names)}人")

print("\n统计完成!")
