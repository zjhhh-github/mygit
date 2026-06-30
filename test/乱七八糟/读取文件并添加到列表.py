# 读取"C:\Users\LENOVO\Desktop\_脚本输入_1.txt",把每一行数据加到列表里

def read_file_to_list(file_path="C:\\Users\\LENOVO\\Desktop\\_脚本输入_1.txt"):
    """
    读取指定文件，并将每一行数据添加到列表中返回
    """
    lines_list = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            for line in file:
                # 去除行末的换行符并添加到列表
                lines_list.append(line.strip())
        
        return lines_list
    
    except FileNotFoundError:
        print(f"文件 {file_path} 未找到")
        return []
    except Exception as e:
        print(f"读取文件时发生错误: {e}")
        return []

# 使用示例
if __name__ == "__main__":
    file_lines = read_file_to_list()
    print(f"文件共有 {len(file_lines)} 行数据")
    print("前10行内容如下：")
    for i, line in enumerate(file_lines[:10]):
        print(f"{i+1}: {line}")
    
    # 可以通过 file_lines 访问整个列表
    # file_lines[0] 是第一行
    # file_lines[1] 是第二行
    # 以此类推