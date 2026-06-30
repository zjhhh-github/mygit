import sqlite3
import os

def main():
    # 检查数据库文件是否存在
    db_path = 'C:\\Users\\LENOVO\\Desktop\\contact.db'
    if not os.path.exists(db_path):
        print(f"数据库文件不存在: {db_path}")
        return
    
    try:
        # 连接数据库
        conn = sqlite3.connect(db_path)  # 本地文件数据库
        cursor = conn.cursor()
        
        # 检查输入文件是否存在
        input_file = 'C:\\Users\\LENOVO\\Desktop\\_脚本输入_1.txt'
        if not os.path.exists(input_file):
            print(f"输入文件不存在: {input_file}")
            conn.close()
            return
        
        # 一次性读取所有输入数据
        with open(input_file,'r',encoding='utf-8') as f1:
            data = f1.readlines()
        
        # 处理数据并一次性写入结果
        results = []
        for line in data:
            line = line.strip()
            if line:  # 确保行不是空的
                try:
                    sql = f"SELECT remark FROM contact WHERE remark like '¿%-{line}%';"
                    cursor.execute(sql)
                    result = cursor.fetchall()
                    
                    if len(result) == 0 or result is None:
                        results.append("❌\n")
                    elif len(result) > 1:
                        results.append("⚠️\n")
                    else:
                        results.append(f"{result[0][0]}\n")
                except sqlite3.Error as e:
                    print(f"数据库查询错误: {e}")
                    results.append("❌\n")
        
        # 一次性写入所有结果
        output_file = "C:\\Users\\LENOVO\\Desktop\\_输出结果_1.txt"
        with open(output_file,'w',encoding='utf-8') as f:
            f.writelines(results)
        
        print("脚本执行完成")
        
    except sqlite3.Error as e:
        print(f"数据库错误: {e}")
    except Exception as e:
        print(f"其他错误: {e}")
    finally:
        # 确保关闭数据库连接
        try:
            conn.close()
        except:
            pass

if __name__ == "__main__":
    main()



