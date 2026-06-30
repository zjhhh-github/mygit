import pandas as pd
import os

def extract_chinese_from_b_to_e(excel_file_path, output_file_path):
    """
    从Excel表格的B列提取汉字字符并放置到E列
    
    Args:
        excel_file_path (str): Excel文件路径（聚合结果文件）
        output_file_path (str): 输出Excel文件路径
    """
    import re
    
    try:
        # 读取Excel文件
        df = pd.read_excel(excel_file_path)
        
        print(f"数据形状: {df.shape}")
        
        # 检查是否存在B列
        if df.shape[1] < 2:
            raise ValueError("Excel文件中没有B列")
        
        # 获取B列数据（第2列，索引为1）
        b_column = df.iloc[:, 1]  # B列是第2列（索引为1）
        
        # 提取B列中的汉字字符并放置到E列
        chinese_chars_list = []
        for cell in b_column:
            if pd.notna(cell):
                # 使用正则表达式提取汉字字符
                chinese_chars = ''.join(re.findall(r'[\u4e00-\u9fff]+', str(cell)))
                chinese_chars_list.append(chinese_chars)
            else:
                chinese_chars_list.append('')
        
        # 确保DataFrame有足够的列来容纳E列
        while df.shape[1] < 5:  # 至少要有5列(A, B, C, D, E)
            df[f'Column_{df.shape[1]}'] = ''
        
        # 将提取的汉字字符放入E列
        df.iloc[:, 4] = chinese_chars_list  # E列是第5列（索引为4）
        
        # 保存结果到新的Excel文件
        df.to_excel(output_file_path, index=False)
        
        print(f"提取结果已保存到: {output_file_path}")
        print(f"从B列成功提取汉字字符并放置到E列")
        
        # 显示部分结果以供验证
        print("前5行结果预览:")
        preview_df = df.iloc[:, [1, 4]].head()  # 显示B列和E列
        preview_df.columns = ['B列原始内容', 'E列提取的汉字']
        print(preview_df)
        
        return df
        
    except FileNotFoundError:
        print(f"错误: 找不到Excel文件 {excel_file_path}")
        return None
    except Exception as e:
        print(f"处理过程中出现错误: {str(e)}")
        return None

def extract_chinese_chars_to_e_column(excel_file_path, output_file_path):
    """
    从Excel文件的B列提取中文字符并放置到E列
    
    Args:
        excel_file_path (str): Excel文件路径
        output_file_path (str): 输出Excel文件路径
    """
    import re
    
    try:
        # 读取Excel文件
        df = pd.read_excel(excel_file_path)
        
        print(f"数据形状: {df.shape}")
        
        # 检查是否存在B列
        if df.shape[1] < 2:
            raise ValueError("Excel文件中没有B列")
        
        # 获取B列数据（第2列，索引为1）
        b_column = df.iloc[:, 1]  # B列是第2列（索引为1）
        
        # 提取B列中的中文字符并放置到E列
        chinese_chars_list = []
        for cell in b_column:
            if pd.notna(cell):
                # 使用正则表达式提取中文字符
                chinese_chars = ''.join(re.findall(r'[\u4e00-\u9fff]+', str(cell)))
                chinese_chars_list.append(chinese_chars)
            else:
                chinese_chars_list.append('')
        
        # 确保DataFrame有足够的列来容纳E列
        while df.shape[1] < 5:  # 至少要有5列(A, B, C, D, E)
            df[f'Column_{df.shape[1]}'] = ''
        
        # 将提取的中文字符放入E列
        df.iloc[:, 4] = chinese_chars_list  # E列是第5列（索引为4）
        
        # 保存结果到新的Excel文件
        df.to_excel(output_file_path, index=False)
        
        print(f"提取结果已保存到: {output_file_path}")
        print(f"从B列成功提取中文字符并放置到E列")
        
        # 显示部分结果以供验证
        print("前5行结果预览:")
        preview_df = df.iloc[:, [1, 4]].head()  # 显示B列和E列
        preview_df.columns = ['B列原始内容', 'E列提取的中文']
        print(preview_df)
        
        return df
        
    except FileNotFoundError:
        print(f"错误: 找不到Excel文件 {excel_file_path}")
        return None
    except Exception as e:
        print(f"处理过程中出现错误: {str(e)}")
        return None

def extract_names_from_excel_better_method(excel_file_path, output_file_path):
    """
    更精确地从Excel文件的B列提取姓名数据
    """
    import re
    
    try:
        # 读取Excel文件
        df = pd.read_excel(excel_file_path, header=None)  # 不假设有标题行
        
        # 获取B列数据（第2列，索引为1）
        if df.shape[1] < 2:
            raise ValueError("Excel文件中没有B列")
        
        b_column = df.iloc[:, 1]  # B列是第2列（索引为1）
        
        # 提取有效的姓名数据（去除空值）
        names = []
        for cell in b_column:
            if pd.notna(cell) and str(cell).strip() != '':
                name = str(cell).strip()
                
                # 去除姓名中的英文字母
                cleaned_name = re.sub(r'[a-zA-Z]', '', name)
                
                # 去除可能因移除英文字母后产生的多余空格
                cleaned_name = ' '.join(cleaned_name.split())
                
                # 只有当清理后的名字不为空时才添加
                if cleaned_name:
                    names.append(cleaned_name)
        
        # 将提取的姓名写入文本文件，使用Windows兼容的换行符
        with open(output_file_path, 'w', encoding='utf-8', newline='') as f:
            for name in names:
                f.write(name + '\n')
        
        print(f"成功提取了 {len(names)} 个姓名")
        print(f"姓名已保存到: {output_file_path}")
        
        # 验证输出文件中的姓名数量
        with open(output_file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            print(f"输出文件中的行数: {len(lines)}")
            
        # 显示前几个提取的姓名以供确认
        if names:
            print("前5个提取的姓名:")
            for i, name in enumerate(names[:5]):
                print(f"  {i+1}. {name}")
                
        return names
        
    except FileNotFoundError:
        print(f"错误: 找不到Excel文件 {excel_file_path}")
        return []
    except Exception as e:
        print(f"处理过程中出现错误: {str(e)}")
        return []

if __name__ == "__main__":
    # 根据用户需求选择要执行的功能
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "extract_chinese_b_to_e":
        # 执行从B列提取汉字到E列的操作
        excel_path = input("请输入要处理的Excel文件路径: ").strip('"')
        output_path = input("请输入输出Excel文件路径: ").strip('"')
        
        # 检查Excel文件是否存在
        if not os.path.exists(excel_path):
            print(f"错误: Excel文件不存在于路径: {excel_path}")
            print("请确认文件路径是否正确。")
        else:
            print("开始执行从B列提取汉字到E列的操作...")
            result = extract_chinese_from_b_to_e(excel_path, output_path)
            
            if result is not None:
                print("\n汉字提取任务完成！")
            else:
                print("\n汉字提取任务失败。")
    elif len(sys.argv) > 1 and sys.argv[1] == "auto_extract_chinese_b_to_e":
        # 自动执行从B列提取汉字到E列的操作，使用默认聚合结果文件
        excel_path = r"C:\Users\LENOVO\Desktop\_聚合结果.xlsx"
        output_path = r"C:\Users\LENOVO\Desktop\_聚合结果汉字提取.xlsx"
        
        # 检查Excel文件是否存在
        if not os.path.exists(excel_path):
            print(f"错误: Excel文件不存在于路径: {excel_path}")
            print("请确认文件路径是否正确。")
        else:
            print("开始执行从B列提取汉字到E列的操作...")
            result = extract_chinese_from_b_to_e(excel_path, output_path)
            
            if result is not None:
                print("\n汉字提取任务完成！")
            else:
                print("\n汉字提取任务失败。")
    elif len(sys.argv) > 1 and sys.argv[1] == "extract_chinese":
        # 执行中文字符提取操作
        excel_path = input("请输入要处理的Excel文件路径: ").strip('"')
        output_path = input("请输入输出Excel文件路径: ").strip('"')
        
        # 检查Excel文件是否存在
        if not os.path.exists(excel_path):
            print(f"错误: Excel文件不存在于路径: {excel_path}")
            print("请确认文件路径是否正确。")
        else:
            print("开始执行中文字符提取操作...")
            result = extract_chinese_chars_to_e_column(excel_path, output_path)
            
            if result is not None:
                print("\n中文字符提取任务完成！")
            else:
                print("\n中文字符提取任务失败。")
    elif len(sys.argv) > 1 and sys.argv[1] == "auto_extract_chinese":
        # 自动执行中文字符提取操作，使用默认文件
        excel_path = r"C:\Users\LENOVO\Desktop\2号平台充值记录.xlsx"
        output_path = r"C:\Users\LENOVO\Desktop\_中文提取结果.xlsx"
        
        # 检查Excel文件是否存在
        if not os.path.exists(excel_path):
            print(f"错误: Excel文件不存在于路径: {excel_path}")
            print("请确认文件路径是否正确。")
        else:
            print("开始执行中文字符提取操作...")
            result = extract_chinese_chars_to_e_column(excel_path, output_path)
            
            if result is not None:
                print("\n中文字符提取任务完成！")
            else:
                print("\n中文字符提取任务失败。")
    else:
        # 执行姓名提取操作
        excel_path = r"C:\Users\LENOVO\Desktop\2号平台充值记录.xlsx"
        output_path = r"C:\Users\LENOVO\Desktop\_脚本输出_1.txt"
        
        # 检查Excel文件是否存在
        if not os.path.exists(excel_path):
            print(f"错误: Excel文件不存在于路径: {excel_path}")
            print("请确认文件路径是否正确。")
        else:
            print("开始处理Excel文件...")
            extracted_names = extract_names_from_excel_better_method(excel_path, output_path)
            
            if extracted_names:
                print("\n任务完成！姓名已成功提取并保存。")
            else:
                print("\n任务完成，但未找到有效的姓名数据。")