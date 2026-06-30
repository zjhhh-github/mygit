import random
import string
import os

def generate_unique_code():
    # 生成所有可用字符：数字 + 大写字母 + 小写字母
    all_chars = string.digits + string.ascii_uppercase + string.ascii_lowercase
    
    # 随机选择6个不重复的字符
    # random.sample 保证不会重复
    code_list = random.sample(all_chars, 6)
    
    # 把列表拼接成字符串
    code = ''.join(code_list)
    
    return code

# 桌面路径（兼容中文系统）
desktop = os.path.join(os.path.expanduser("~"), "Desktop")
out_file = os.path.join(desktop, "优惠码.txt")

# 生成优惠码并写入桌面文件
with open(out_file, "w", encoding="utf-8") as f:
    for i in range(1500):
        verification_code = generate_unique_code()
        f.write(verification_code + "\n")

print(f"已生成 1500 个优惠码，已保存到：{out_file}")