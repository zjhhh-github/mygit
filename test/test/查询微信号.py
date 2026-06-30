# 1. 导入内置的sqlite3库
import sqlite3

# 2. 连接SQLite3数据库
# 说明：
# - 若文件存在（如test.db），直接连接该本地数据库文件
# - 若文件不存在，会在当前Python脚本目录下自动创建该数据库文件
# - 若想创建内存数据库（关闭后数据丢失），可将路径改为 :memory:
conn = sqlite3.connect('C:\\Users\\LENOVO\\Desktop\\contact.db')  # 本地文件数据库（推荐）
# conn = sqlite3.connect(':memory:')  # 内存数据库（临时使用）

# 3. 创建游标对象（用于执行SQL语句）
cursor = conn.cursor()
create_table_sql = '''
SELECT username , alias ,remark FROM contact WHERE remark like '¿¿¿%';
'''
d = {i[2]:[i[0],i[1]] for i in cursor.execute(create_table_sql).fetchall()}
print(d)