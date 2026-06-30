import pymysql
from pymysql import Error
import pandas as pd
import os
import pyperclip
import re
import sys
import logging
from pathlib import Path


def setup_logging():
    """设置日志记录"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('cli_db_manager.log', encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger(__name__)
    logger.info("CLI应用程序启动")
    return logger


def resource_path(relative_path):
    """获取资源文件的绝对路径，用于打包后获取文件"""
    try:
        # PyInstaller创建临时文件夹，将路径存储在_MEIPASS中
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def connect_to_mysql(host, database, username, password, port=3306):
    """
    连接到 MySQL 数据库
    :param host: 主机地址
    :param database: 数据库名
    :param username: 用户名
    :param password: 密码
    :param port: 端口，默认3306
    :return: Connection object 或 None
    """
    conn = None
    try:
        conn = pymysql.connect(
            host=host,
            database=database,
            user=username,
            password=password,
            port=port,
            charset='utf8mb4'
        )
        print('Successfully connected to MySQL database')
        return conn
    except Error as e:
        print(f'Error connecting to MySQL database: {e}')
        return None


def close_connection(conn):
    """关闭数据库连接"""
    if conn:
        conn.close()
        print("MySQL connection is closed.")


def get_tables(conn):
    """获取数据库中的所有表"""
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [table[0] for table in cursor.fetchall()]
        cursor.close()
        return tables
    except Error as e:
        print(f"Error getting tables: {e}")
        return []


def select_table(tables):
    """让用户选择一个表"""
    if not tables:
        print("数据库中没有表")
        return None
    
    print("\n数据库中的表:")
    for i, table in enumerate(tables, 1):
        print(f"{i}. {table}")
    
    while True:
        try:
            choice = int(input(f"\n请选择要操作的表 (1-{len(tables)}): "))
            if 1 <= choice <= len(tables):
                return tables[choice - 1]
            else:
                print(f"请输入 1 到 {len(tables)} 之间的数字")
        except ValueError:
            print("请输入有效的数字")


def describe_table(conn, table_name):
    """获取表结构信息"""
    try:
        cursor = conn.cursor()
        cursor.execute(f"DESCRIBE `{table_name}`")
        columns = cursor.fetchall()
        cursor.close()
        return columns
    except Error as e:
        print(f"Error describing table: {e}")
        return []


def show_all_records(conn, table_name):
    """显示表中的所有记录"""
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM `{table_name}`")
        records = cursor.fetchall()
        
        # 获取列名
        cursor.execute(f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{table_name}' AND TABLE_SCHEMA = DATABASE() ORDER BY ORDINAL_POSITION")
        column_names = [row[0] for row in cursor.fetchall()]
        
        print(f"\n表 '{table_name}' 中的所有记录:")
        print(" | ".join(column_names))
        print("-" * (len(" | ".join(column_names)) + len(column_names) * 2))
        
        for record in records:
            print(" | ".join(str(value) for value in record))
        
        cursor.close()
        return records
    except Error as e:
        print(f"Error showing records: {e}")
        return []


def insert_record(conn, table_name, columns):
    """插入新记录"""
    try:
        cursor = conn.cursor()
        
        print(f"\n输入 '{table_name}' 表的新记录数据:")
        print("提示: 在任意字段输入 'BACK' 可返回上级菜单，输入 'EDIT' 可查看和编辑已输入的数据")
        values = []
        placeholders = []
        
        for i, col_info in enumerate(columns):
            col_name = col_info[0]
            col_type = col_info[1]
            
            value = input(f"输入 {col_name} ({col_type}) 的值: ")
            
            # 检查是否要返回上级菜单
            if value.upper() == 'BACK':
                print("检测到返回指令，已输入的数据如下:")
                for j, (prev_col_info, prev_value) in enumerate(zip(columns[:i], values)):
                    print(f"  {prev_col_info[0]}: {prev_value}")
                
                confirm = input("是否确认返回上级菜单? (y/n): ").lower().startswith('y')
                if confirm:
                    print("返回上级菜单")
                    cursor.close()
                    return  # 返回上级菜单
                else:
                    # 用户决定继续输入
                    value = input(f"再次输入 {col_name} ({col_type}) 的值: ")
            
            # 检查是否要编辑已输入的数据
            elif value.upper() == 'EDIT':
                print("当前已输入的数据:")
                for j, (prev_col_info, prev_value) in enumerate(zip(columns[:i], values)):
                    print(f"  {j+1}. {prev_col_info[0]}: {prev_value}")
                
                if values:  # 如果已有数据
                    edit_choice = input("要编辑哪个字段? (输入数字，或按Enter继续): ")
                    if edit_choice.isdigit() and 1 <= int(edit_choice) <= len(values):
                        idx = int(edit_choice) - 1
                        old_val = values[idx]
                        new_val = input(f"编辑 '{columns[idx][0]}' (原值: {old_val}): ")
                        values[idx] = new_val
                        print(f"已更新 '{columns[idx][0]}' 为 '{new_val}'")
                        
                        # 重新提示当前字段
                        value = input(f"再次输入 {col_name} ({col_type}) 的值: ")
                    else:
                        # 用户决定继续输入
                        value = input(f"再次输入 {col_name} ({col_type}) 的值: ")
                else:
                    # 没有已输入的数据，继续输入当前字段
                    value = input(f"再次输入 {col_name} ({col_type}) 的值: ")
            
            values.append(value)
            placeholders.append('%s')
        
        sql = f"INSERT INTO `{table_name}` ({', '.join([f'`{col[0]}`' for col in columns])}) VALUES ({', '.join(placeholders)})"
        cursor.execute(sql, values)
        conn.commit()
        
        print(f"记录插入成功！影响的行数: {cursor.rowcount}")
        cursor.close()
    except Error as e:
        print(f"Error inserting record: {e}")


def update_record(conn, table_name, columns):
    """更新记录"""
    try:
        cursor = conn.cursor()
        
        # 显示所有记录，让用户选择要更新的记录
        records = show_all_records(conn, table_name)
        if not records:
            print("表中没有记录可更新")
            return
        
        primary_key_col = None
        for col_info in columns:
            if 'PRI' in col_info:  # 主键列
                primary_key_col = col_info[0]
                break
        
        if not primary_key_col:
            print("未找到主键列，无法执行更新操作")
            return
        
        pk_value = input(f"输入要更新记录的 {primary_key_col} 值: ")
        
        print(f"\n输入新的字段值:")
        updates = []
        values = []
        
        for col_info in columns:
            col_name = col_info[0]
            col_type = col_info[1]
            
            # 不更新主键
            if col_name != primary_key_col:
                value = input(f"输入 {col_name} ({col_type}) 的新值 (留空则不更新): ")
                if value.strip():  # 如果输入了值
                    updates.append(f"`{col_name}` = %s")
                    values.append(value)
        
        if not updates:
            print("没有输入任何要更新的字段")
            return
        
        values.append(pk_value)
        sql = f"UPDATE `{table_name}` SET {', '.join(updates)} WHERE `{primary_key_col}` = %s"
        cursor.execute(sql, values)
        conn.commit()
        
        print(f"记录更新成功！影响的行数: {cursor.rowcount}")
        cursor.close()
    except Error as e:
        print(f"Error updating record: {e}")


def delete_record(conn, table_name, columns):
    """删除记录"""
    try:
        cursor = conn.cursor()
        
        # 显示所有记录，让用户选择要删除的记录
        records = show_all_records(conn, table_name)
        if not records:
            print("表中没有记录可删除")
            return
        
        primary_key_col = None
        for col_info in columns:
            if 'PRI' in col_info:  # 主键列
                primary_key_col = col_info[0]
                break
        
        if not primary_key_col:
            print("未找到主键列，无法执行删除操作")
            return
        
        pk_value = input(f"输入要删除记录的 {primary_key_col} 值: ")
        
        sql = f"DELETE FROM `{table_name}` WHERE `{primary_key_col}` = %s"
        cursor.execute(sql, (pk_value,))
        conn.commit()
        
        print(f"记录删除成功！影响的行数: {cursor.rowcount}")
        cursor.close()
    except Error as e:
        print(f"Error deleting record: {e}")


def parse_text_to_fields(text, expected_columns):
    """
    解析文本内容到字段列表
    :param text: 输入的文本
    :param expected_columns: 期望的列结构
    :return: 解析后的字段值列表
    """
    # 常见分隔符：逗号、分号、制表符、竖线等
    separators = [',', ';', '\t', '|', '，']
    
    parsed_values = None
    best_match = 0
    
    # 尝试各种分隔符
    for sep in separators:
        parts = text.split(sep)
        # 计算匹配度（有多少个值与预期列数接近）
        matches = sum(1 for part in parts if part.strip())
        if matches > best_match and abs(len(parts) - len(expected_columns)) <= 1:  # 宽松匹配
            best_match = matches
            parsed_values = [part.strip() for part in parts]
    
    # 如果没找到合适的分隔符，尝试按空格分割
    if parsed_values is None or len(parsed_values) < len(expected_columns) / 2:
        parts = re.split(r'\s+', text.strip())
        if len(parts) >= len(expected_columns) / 2:  # 至少一半的匹配
            parsed_values = [part.strip() for part in parts]
    
    # 如果还是不行，直接返回原始文本作为单个字段
    if parsed_values is None:
        parsed_values = [text.strip()]
    
    return parsed_values


def validate_field_types(values, columns_info):
    """
    验证字段值是否符合列的数据类型
    :param values: 字段值列表
    :param columns_info: 列信息（名称和类型）
    :return: (验证结果, 错误信息列表)
    """
    errors = []
    
    for i, (value, col_info) in enumerate(zip(values, columns_info)):
        if i >= len(columns_info):
            break  # 如果值的数量超过列数，跳过
            
        col_name = col_info[0]
        col_type = col_info[1].lower()
        
        # 跳过主键列如果是自增的
        if 'auto_increment' in col_info[4].lower():
            continue
            
        # 验证数据类型
        if value == '' and 'not null' in col_info[2].lower() and 'auto_increment' not in col_info[4].lower():
            errors.append(f"列 '{col_name}' 是必需的，不能为空")
            continue
            
        if value != '':
            if 'int' in col_type:
                try:
                    int(value)
                except ValueError:
                    errors.append(f"列 '{col_name}' 应为整数类型，但输入的是: {value}")
            elif 'decimal' in col_type or 'double' in col_type or 'float' in col_type:
                try:
                    float(value)
                except ValueError:
                    errors.append(f"列 '{col_name}' 应为数值类型，但输入的是: {value}")
            elif 'date' in col_type or 'time' in col_type:
                # 简单验证日期时间格式
                if not re.match(r'^\d{4}-\d{2}-\d{2}(\s\d{2}:\d{2}:\d{2})?$', value):
                    errors.append(f"列 '{col_name}' 应为日期时间格式 (YYYY-MM-DD HH:MM:SS)，但输入的是: {value}")
    
    return len(errors) == 0, errors


def paste_and_insert_record(conn, table_name, columns_info):
    """
    从剪贴板获取文本并插入记录
    :param conn: 数据库连接
    :param table_name: 表名
    :param columns_info: 表的列信息
    :return: 是否成功插入
    """
    try:
        print(f"\n--- 通过粘贴文本插入记录到表 '{table_name}' ---")
        print("请将包含数据的单行文本复制到剪贴板，然后按回车继续...")
        input("按回车键继续...")
        
        # 从剪贴板获取文本
        clipboard_text = pyperclip.paste()
        print(f"从剪贴板获取到的文本: {clipboard_text}")
        
        if not clipboard_text.strip():
            print("剪贴板中没有文本内容，请先复制一些文本！")
            return False
        
        # 解析文本到字段
        parsed_values = parse_text_to_fields(clipboard_text, columns_info)
        print(f"解析得到的字段: {parsed_values}")
        
        # 验证数据类型
        is_valid, validation_errors = validate_field_types(parsed_values, columns_info)
        
        if not is_valid:
            print("数据验证失败，发现以下错误:")
            for error in validation_errors:
                print(f"- {error}")
            return False
        
        # 确认插入
        print("\n解析后的数据:")
        for i, (col_info, value) in enumerate(zip(columns_info, parsed_values)):
            if i < len(parsed_values):
                print(f"  {col_info[0]}: {value}")
        
        confirm = input("\n确认插入以上数据? (y/n): ").lower().startswith('y')
        if not confirm:
            print("已取消插入操作")
            return False
        
        # 准备插入数据
        cursor = conn.cursor()
        
        # 过滤掉空值和自增列
        filtered_cols = []
        filtered_values = []
        
        for i, (col_info, value) in enumerate(zip(columns_info, parsed_values)):
            col_name = col_info[0]
            extra_info = col_info[4].lower() if len(col_info) > 4 else ''
            
            # 跳过自增列
            if 'auto_increment' in extra_info:
                continue
                
            filtered_cols.append(f"`{col_name}`")
            # 如果值为空且列允许为空，则使用NULL
            if value == '' and 'not null' not in col_info[2].lower():
                filtered_values.append(None)
            else:
                filtered_values.append(value)
        
        if len(filtered_cols) == 0:
            print("没有有效的数据可以插入")
            return False
        
        # 构建SQL语句
        placeholders = ['%s'] * len(filtered_cols)
        sql = f"INSERT INTO `{table_name}` ({','.join(filtered_cols)}) VALUES ({','.join(placeholders)})"
        
        print(f"执行SQL: {sql}")
        
        cursor.execute(sql, filtered_values)
        conn.commit()
        
        print(f"成功插入记录！影响的行数: {cursor.rowcount}")
        cursor.close()
        return True
        
    except Exception as e:
        print(f"插入记录时出错: {e}")
        return False


def import_from_excel(conn, table_name):
    """从Excel文件导入数据到新创建的表"""
    try:
        # 获取当前目录下的Excel文件
        excel_files = []
        for file in os.listdir('.'):
            if file.lower().endswith(('.xlsx', '.xls')):
                excel_files.append(file)
        
        if not excel_files:
            print("当前目录下没有找到Excel文件 (.xlsx 或 .xls)")
            # 询问用户Excel文件路径
            file_path = input("请输入Excel文件的完整路径: ").strip()
            if not file_path or not os.path.exists(file_path):
                print("文件不存在！")
                return False
        else:
            print("\n在当前目录找到以下Excel文件:")
            for i, file in enumerate(excel_files, 1):
                print(f"{i}. {file}")
            
            choice = input(f"\n请选择要导入的Excel文件 (1-{len(excel_files)}) 或直接输入文件路径: ").strip()
            
            if choice.isdigit() and 1 <= int(choice) <= len(excel_files):
                file_path = excel_files[int(choice) - 1]
            elif os.path.exists(choice):
                file_path = choice
            else:
                print("无效的选择或文件不存在！")
                return False
        
        # 读取Excel文件
        try:
            df = pd.read_excel(file_path)
        except Exception as e:
            print(f"读取Excel文件时出错: {e}")
            return False
        
        print(f"\nExcel文件包含 {len(df)} 行数据和 {len(df.columns)} 列:")
        print("列名:", list(df.columns))
        
        # 获取表结构
        cursor = conn.cursor()
        cursor.execute(f"DESCRIBE `{table_name}`")
        table_columns = [col[0] for col in cursor.fetchall()]
        cursor.close()
        
        print(f"\n表 '{table_name}' 的列结构: {table_columns}")
        
        # 询问用户是否映射列
        map_columns = input("\n是否需要映射Excel列到表列? (y/n，默认为n): ").lower().startswith('y')
        
        if map_columns:
            column_mapping = {}
            print("\n请为每个表列指定对应的Excel列:")
            for tbl_col in table_columns:
                print(f"表列: {tbl_col}")
                print(f"可用Excel列: {list(df.columns)}")
                excel_col = input(f"请输入对应Excel列名 (或按Enter跳过 '{tbl_col}'):")
                if excel_col and excel_col in df.columns:
                    column_mapping[tbl_col] = excel_col
                else:
                    column_mapping[tbl_col] = tbl_col  # 默认同名列
            
            # 根据映射重命名DataFrame列
            reverse_mapping = {v: k for k, v in column_mapping.items()}
            df_rename = df.rename(columns=reverse_mapping)
        else:
            # 尝试按名称匹配列
            df_rename = df.copy()
            # 只保留与表结构匹配的列
            common_cols = [col for col in table_columns if col in df_rename.columns]
            df_rename = df_rename[common_cols]
        
        # 检查是否有匹配的列
        if df_rename.empty or len(df_rename.columns) == 0:
            print("没有匹配的列可以导入！")
            return False
        
        print(f"\n将导入 {len(df_rename)} 行数据到表 '{table_name}'")
        print("导入的列:", list(df_rename.columns))
        
        confirm = input("确认导入数据? (y/n): ").lower().startswith('y')
        if not confirm:
            print("已取消导入操作")
            return False
        
        # 插入数据
        cursor = conn.cursor()
        
        # 构建INSERT语句
        cols = [f"`{col}`" for col in df_rename.columns]
        placeholders = ['%s'] * len(df_rename.columns)
        sql = f"INSERT INTO `{table_name}` ({','.join(cols)}) VALUES ({','.join(placeholders)})"
        
        # 将DataFrame转换为元组列表
        data_tuples = [tuple(row) for row in df_rename.values]
        
        # 批量插入
        cursor.executemany(sql, data_tuples)
        conn.commit()
        
        print(f"成功导入 {cursor.rowcount} 行数据到表 '{table_name}'！")
        cursor.close()
        
        # 询问完成后是否返回上级菜单
        print("\n导入完成，是否返回上级菜单?")
        return True
        
    except Exception as e:
        print(f"导入Excel数据时出错: {e}")
        return False


def create_new_table(conn):
    """创建新表"""
    try:
        cursor = conn.cursor()
        
        print("\n--- 创建新表 ---")
        print("1. 手动定义表结构")
        print("2. 从Excel文件创建表结构")
        print("3. 返回上级菜单")
        
        choice = input("\n请选择创建方式 (1-3, 默认为1): ").strip()
        
        if choice == "3":
            print("返回上级菜单")
            return False  # 返回上级菜单
        
        table_name = ""
        columns = []
        
        if choice == "2":
            # 从Excel创建表结构
            print("\n从Excel文件创建表结构...")
            
            # 获取当前目录下的Excel文件
            excel_files = []
            for file in os.listdir('.'):
                if file.lower().endswith(('.xlsx', '.xls')):
                    excel_files.append(file)
            
            if not excel_files:
                print("当前目录下没有找到Excel文件 (.xlsx 或 .xls)")
                file_path = input("请输入Excel文件的完整路径: ").strip()
                if not file_path or not os.path.exists(file_path):
                    print("文件不存在！")
                    return False
            else:
                print("\n在当前目录找到以下Excel文件:")
                for i, file in enumerate(excel_files, 1):
                    print(f"{i}. {file}")
                
                file_choice = input(f"\n请选择Excel文件 (1-{len(excel_files)}): ").strip()
                if file_choice.isdigit() and 1 <= int(file_choice) <= len(excel_files):
                    file_path = excel_files[int(file_choice) - 1]
                else:
                    print("无效选择！")
                    return False
            
            # 读取Excel文件
            try:
                df = pd.read_excel(file_path)
            except Exception as e:
                print(f"读取Excel文件时出错: {e}")
                return False
            
            table_name = input(f"\n请输入新表的名称 (基于Excel: {os.path.basename(file_path)}): ").strip()
            if not table_name:
                print("表名不能为空！")
                return False
            
            # 检查表是否已存在
            cursor.execute("SHOW TABLES LIKE %s", (table_name,))
            if cursor.fetchone():
                print(f"表 '{table_name}' 已存在！")
                return False
            
            print(f"\nExcel文件包含 {len(df)} 行数据和 {len(df.columns)} 列:")
            print("列名:", list(df.columns))
            
            # 根据Excel数据推断列类型
            columns = []
            for col in df.columns:
                # 推断数据类型
                dtype = df[col].dtype
                if 'int' in str(dtype):
                    col_type = 'INT'
                elif 'float' in str(dtype):
                    col_type = 'DECIMAL(10,2)'
                elif 'datetime' in str(dtype):
                    col_type = 'DATETIME'
                else:
                    # 对于字符串类型，估算最大长度
                    max_len = df[col].astype(str).str.len().max()
                    if max_len > 255:
                        col_type = 'TEXT'
                    else:
                        col_type = f'VARCHAR({min(max(50, max_len + 10), 255)})'
                
                columns.append(f"`{col.replace(' ', '_')}` {col_type}")
            
            # 询问是否添加ID主键
            add_auto_id = input("是否添加自增ID主键列 (y/n)? ").lower().startswith('y')
            if add_auto_id:
                columns.insert(0, "id INT PRIMARY KEY AUTO_INCREMENT")
        
        else:
            # 手动定义表结构
            table_name = input("\n请输入新表的名称: ").strip()
            
            if not table_name:
                print("表名不能为空！")
                return False
            
            # 检查表是否已存在
            cursor.execute("SHOW TABLES LIKE %s", (table_name,))
            if cursor.fetchone():
                print(f"表 '{table_name}' 已存在！")
                return False
            
            print("请输入表的列定义 (输入 'done' 结束):")
            print("格式: 列名 数据类型 [约束], 如: id INT PRIMARY KEY AUTO_INCREMENT")
            
            columns = []
            primary_key_cols = []
            
            while True:
                col_def = input(f"请输入第 {len(columns) + 1} 列的定义 (或输入 'done' 结束): ").strip()
                
                if col_def.lower() == 'done':
                    if not columns:
                        print("至少需要定义一列！")
                        continue
                    else:
                        break
                
                if not col_def:
                    print("列定义不能为空，请重新输入")
                    continue
                
                # 检查是否为主键
                col_lower = col_def.lower()
                if 'primary key' in col_lower:
                    col_parts = col_def.split()
                    if len(col_parts) >= 1:
                        primary_key_cols.append(col_parts[0].replace('`', '').replace(',', ''))
                
                columns.append(col_def)
            
            # 如果没有定义主键，询问是否添加ID主键
            if not primary_key_cols:
                add_auto_id = input("是否添加自增ID主键列 (y/n)? ").lower().startswith('y')
                if add_auto_id:
                    columns.insert(0, "id INT PRIMARY KEY AUTO_INCREMENT")
                    primary_key_cols.append("id")
        
        # 构建CREATE TABLE语句
        columns_def = ', '.join(columns)
        
        # 如果有多个主键列，添加复合主键定义
        primary_key_cols_from_defs = []
        for col_def in columns:
            if 'PRIMARY KEY' in col_def.upper():
                col_name = col_def.split()[0].replace('`', '').replace(',', '')
                primary_key_cols_from_defs.append(col_name)
        
        # 如果有多个主键，添加复合主键定义
        if len(primary_key_cols_from_defs) > 1:
            columns_def += f", PRIMARY KEY ({', '.join([f'`{col}`' for col in primary_key_cols_from_defs])})"
        
        sql = f"CREATE TABLE `{table_name}` ({columns_def})"
        
        print(f"\n即将执行的SQL语句: {sql}")
        
        confirm = input("\n确认创建此表吗? (y/n): ").lower().startswith('y')
        if confirm:
            cursor.execute(sql)
            conn.commit()
            print(f"表 '{table_name}' 创建成功！")
            
            # 询问后续操作
            print("\n请选择后续操作:")
            print("1. 从Excel文件导入数据")
            print("2. 返回上级菜单")
            
            post_create_choice = input("请选择 (1-2): ").strip()
            
            if post_create_choice == "1":
                import_from_excel(conn, table_name)
                # 导入完成后询问是否返回上级菜单
                back_to_main = input("\n是否返回上级菜单? (y/n): ").lower().startswith('y')
                if back_to_main:
                    print("返回上级菜单")
                    cursor.close()
                    return False  # 返回上级菜单
            elif post_create_choice == "2":
                print("返回上级菜单")
                cursor.close()
                return False  # 返回上级菜单
            else:
                print("无效选择，默认返回上级菜单")
                cursor.close()
                return False  # 返回上级菜单
        else:
            print("已取消创建表操作")
        
        cursor.close()
        return True
    except Error as e:
        print(f"Error creating table: {e}")
        return False


def show_menu():
    """显示操作菜单"""
    print("\n--- 数据库操作菜单 ---")
    print("1. 查看所有记录")
    print("2. 插入新记录")
    print("3. 通过粘贴文本插入记录")
    print("4. 更新记录")
    print("5. 删除记录")
    print("6. 更换操作表")
    print("7. 返回上级菜单")
    print("8. 退出")


def crud_operations(conn, table_name):
    """CRUD操作主循环"""
    while True:
        columns = describe_table(conn, table_name)
        if not columns:
            print(f"无法获取表 '{table_name}' 的结构信息")
            return
        
        show_menu()
        
        try:
            choice = int(input("\n请选择操作 (1-8): "))
            
            if choice == 1:
                show_all_records(conn, table_name)
            elif choice == 2:
                insert_record(conn, table_name, columns)
            elif choice == 3:
                paste_and_insert_record(conn, table_name, columns)
            elif choice == 4:
                update_record(conn, table_name, columns)
            elif choice == 5:
                delete_record(conn, table_name, columns)
            elif choice == 6:
                return True  # 返回到表选择界面
            elif choice == 7:
                print("返回上级菜单")
                return True  # 返回到主菜单
            elif choice == 8:
                print("退出操作")
                return False  # 完全退出
            else:
                print("无效选择，请输入 1-8 之间的数字")
        except ValueError:
            print("请输入有效的数字")


def main_menu(conn):
    """主菜单 - 包含创建表选项"""
    while True:
        # 获取数据库中的所有表
        tables = get_tables(conn)
        
        if not tables:
            print("数据库中没有表")
        
        # 询问用户是要操作现有表还是创建新表
        print("\n--- 主菜单 ---")
        print("1. 选择现有表进行操作")
        print("2. 创建新表")
        print("3. 退出")
        
        try:
            choice = int(input("\n请选择操作 (1-3): "))
            
            if choice == 1:
                if not tables:
                    print("数据库中没有表")
                    continue
                
                # 让用户选择要操作的表
                selected_table = select_table(tables)
                
                if selected_table:
                    print(f"\n您选择了表: {selected_table}")
                    
                    # 执行CRUD操作
                    should_continue = crud_operations(conn, selected_table)
                    
                    if not should_continue:  # 用户选择退出
                        break
                    # 否则继续回到主菜单
                else:
                    print("无效的选择")
                    continue
                    
            elif choice == 2:
                # 创建新表
                create_new_table(conn)
                
            elif choice == 3:
                print("退出程序")
                break
            else:
                print("无效选择，请输入 1-3 之间的数字")
        except ValueError:
            print("请输入有效的数字")


# MySQL连接示例
if __name__ == '__main__':
    logger = setup_logging()
    
    # 请根据您的实际情况修改以下参数
    HOST = '127.0.0.1'          # 数据库主机地址
    DATABASE = 'test'  # 数据库名称
    USERNAME = 'root'  # 用户名
    PASSWORD = 'password'  # 密码
    PORT = 3306                 # 端口号，默认3306
    
    print("正在尝试连接到MySQL数据库...")
    print(f"主机: {HOST}:{PORT}, 数据库: {DATABASE}, 用户: {USERNAME}")
    
    # 连接MySQL数据库
    connection = connect_to_mysql(HOST, DATABASE, USERNAME, PASSWORD, PORT)
    
    if connection:
        print("MySQL数据库连接成功！")
        
        try:
            # 使用新的主菜单
            main_menu(connection)
            
        except KeyboardInterrupt:
            print("\n用户中断操作")
        except Exception as e:
            print(f"执行数据库操作时出错: {e}")
        finally:
            # 记得关闭连接
            close_connection(connection)
            print("MySQL数据库连接已关闭。")
    else:
        print("MySQL数据库连接失败！")
        print("请检查:")
        print("1. MySQL服务是否正在运行")
        print("2. 主机地址、端口是否正确")
        print("3. 数据库名称、用户名和密码是否正确")