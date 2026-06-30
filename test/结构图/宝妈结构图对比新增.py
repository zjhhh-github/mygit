import re
import pandas as pd

"""
脚本用途：

对比两个树状结构的文本文件：
- 文件1：_输出结果_1.txt 当前宝妈结构图
- 文件2：_脚本输入_1.txt 小组结构图

每一行的格式类似：¿¿¿ID-名字-序号
通过缩进来表示层级（父子关系）。

本脚本找出：
- “在文件1 中出现，但在文件2 中没有”的 ID（视为新增宝妈合伙人）
- 同时向上回溯，提取这条记录最多 4 级父节点（父级1~父级4）

最终将结果导出为：新增宝妈合伙人.xlsx
"""


def remove_indent(line):
    """
    去掉行首缩进，只保留实际内容。
    用于判断/比较缩进层级时更方便。
    """
    return line.lstrip()


def extract_ids(line):
    """
    从一行中提取 ID。
    匹配形如：¿¿¿1234-名字-1
    提取“1234”这一段作为 ID。
    """
    match = re.search(r"¿¿¿(\S+?)-", line)
    if match:
        return match.group(1)
    return None


def tiqumingzi(line):
    """
    从一行中提取“名字”部分。
    例如：¿¿¿1234-张三-1    提取“张三”。
    """
    match = re.search(r"¿¿¿.*?-(.*?)-\d", line)
    if match:
        return match.group(1)
    return None


def get_parent_names(lines, current_idx):
    """
    根据当前行的缩进，向上回溯获取所有父级“名字”列表。
    示例：当前行为三级节点，则会向上找到二级、一级节点的名字。
    返回顺序： [父级1, 父级2, ...]（从离它最近的上级开始）。
    """
    # 当前行的缩进长度
    indent = len(lines[current_idx]) - len(lines[current_idx].lstrip())
    parents = []
    # 从当前行往上找父级
    for i in range(current_idx - 1, -1, -1):
        line = lines[i]
        line_stripped = remove_indent(line)
        # 父级行也必须包含 “¿¿¿” 标记
        if "¿¿¿" in line_stripped:
            parent_indent = len(line) - len(line.lstrip())
            # 比当前缩进更小，说明是上层父节点
            if parent_indent < indent:
                parents.append(tiqumingzi(line_stripped))
                indent = parent_indent
    return parents


def get_parent_ids(lines, current_idx):
    """
    根据当前行的缩进，向上回溯获取所有父级“ID”列表。
    与 get_parent_names 逻辑类似，只是提取的是 ID。
    """
    indent = len(lines[current_idx]) - len(lines[current_idx].lstrip())
    parent_ids = []
    for i in range(current_idx - 1, -1, -1):
        line = lines[i]
        line_stripped = remove_indent(line)
        if "¿¿¿" in line_stripped:
            parent_indent = len(line) - len(line.lstrip())
            if parent_indent < indent:
                parent_id = extract_ids(line_stripped)
                if parent_id:
                    parent_ids.append(parent_id)
                indent = parent_indent
    return parent_ids


# 读取两个输入文件（树状结构文本）
# 输出结果1为宝妈合伙人结构图，脚本输入1为小组结构图
with open("C:\\Users\\LENOVO\\Desktop\\_输出结果_1.txt", "r", encoding="utf-8") as f1:
    lines1 = f1.readlines()

with open("C:\\Users\\LENOVO\\Desktop\\_脚本输入_1.txt", "r", encoding="utf-8") as f2:
    lines2 = f2.readlines()

# ids1：文件1中出现的所有 ID
# ids2：文件2中出现的所有 ID
# ids3：ID -> 名字 的映射（仅从文件1中提取）
ids1 = set()
ids2 = set()
ids3 = dict()

# 扫描文件1，提取每条节点的 ID 和名字
for line in lines1:
    line_stripped = remove_indent(line)
    if "¿¿¿" in line_stripped:
        id_num = extract_ids(line_stripped)
        if id_num:
            ids1.add(id_num)
            mingzi = tiqumingzi(line_stripped)
            if mingzi:
                ids3[id_num] = mingzi

# 扫描文件2，只需要收集 ID 集合
for line in lines2:
    line_stripped = remove_indent(line)
    if "¿¿¿" in line_stripped:
        id_num = extract_ids(line_stripped)
        if id_num:
            ids2.add(id_num)

# 差集：只在文件1中出现、但文件2中没有的 ID，即“新增宝妈合伙人”
diff_ids = ids1 - ids2

# 组装导出到 Excel 的数据
data = []
print(diff_ids)
for id_num in sorted(diff_ids):
    
    name = ids3[id_num]  # 新增人的名字
    # 在文件1中找到该 ID 所在的具体行，然后回溯父级
    for i, line in enumerate(lines1):
        line_stripped = remove_indent(line)
        if id_num in line_stripped and "¿¿¿" in line_stripped:
            parents = get_parent_names(lines1, i)
            parent_ids = get_parent_ids(lines1, i)
            row = {
                "新增宝妈合伙人": f"¿¿¿{id_num}-{name}"
            }
            # 最多写出 4 级父级：父级1 ~ 父级4
            for j in range(4):
                if j < len(parent_ids):
                    row[f"父级{j+1}"] = f"¿¿¿{parent_ids[j]}-{parents[j]}"
                else:
                    row[f"父级{j+1}"] = ""
            data.append(row)
            break  # 找到对应行后即可停止内层循环

# 写出结果到 Excel 表格
df = pd.DataFrame(data)
print(df)
df.to_excel("C:\\Users\\LENOVO\\Desktop\\新增宝妈合伙人.xlsx", index=False, engine="openpyxl")
print("=" * 50)
print("数据已保存到: C:\\Users\\LENOVO\\Desktop\\新增宝妈合伙人.xlsx")