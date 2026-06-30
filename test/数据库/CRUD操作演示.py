import pymysql
from pymysql import Error

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
        cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 10")  # 限制显示前10条记录
        records = cursor.fetchall()
        
        # 获取列名
        cursor.execute(f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{table_name}' AND TABLE_SCHEMA = DATABASE() ORDER BY ORDINAL_POSITION")
        column_names = [row[0] for row in cursor.fetchall()]
        
        print(f"\n表 '{table_name}' 中的记录 (最多显示10条):")
        if records:
            print(" | ".join(column_names))
            print("-" * (len(" | ".join(column_names)) + len(column_names) * 2))
            
            for record in records:
                print(" | ".join(str(value) for value in record))
        else:
            print("表中没有记录")
        
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
        values = []
        placeholders = []
        
        for col_info in columns:
            col_name = col_info[0]
            col_type = col_info[1]
            
            value = input(f"输入 {col_name} ({col_type}) 的值: ")
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

def show_menu():
    """显示操作菜单"""
    print("\n--- 数据库操作菜单 ---")
    print("1. 查看所有记录")
    print("2. 插入新记录")
    print("3. 更新记录")
    print("4. 删除记录")
    print("5. 更换操作表")
    print("6. 退出")

def crud_operations(conn, table_name):
    """CRUD操作主循环"""
    while True:
        columns = describe_table(conn, table_name)
        if not columns:
            print(f"无法获取表 '{table_name}' 的结构信息")
            return
        
        show_menu()
        
        try:
            choice = int(input("\n请选择操作 (1-6): "))
            
            if choice == 1:
                show_all_records(conn, table_name)
            elif choice == 2:
                insert_record(conn, table_name, columns)
            elif choice == 3:
                update_record(conn, table_name, columns)
            elif choice == 4:
                delete_record(conn, table_name, columns)
            elif choice == 5:
                return True  # 返回到表选择界面
            elif choice == 6:
                print("退出操作")
                return False  # 完全退出
            else:
                print("无效选择，请输入 1-6 之间的数字")
        except ValueError:
            print("请输入有效的数字")

def demo_crud_operations():
    """演示CRUD操作的函数"""
    print("=== CRUD操作演示 ===")
    print("此演示不会实际执行数据库操作，仅展示功能")
    print("1. 查看记录 - SELECT * FROM table")
    print("2. 插入记录 - INSERT INTO table VALUES (...)")
    print("3. 更新记录 - UPDATE table SET ... WHERE ...")
    print("4. 删除记录 - DELETE FROM table WHERE ...")
    print("5. 更换表 - 选择不同的表进行操作")
    print("6. 退出 - 断开数据库连接")

# MySQL连接示例
if __name__ == '__main__':
    print("数据库管理系统 v2.0")
    print("功能：连接数据库并进行增删改查(CRUD)操作")
    print("注意：此系统将连接到指定的MySQL数据库并允许对现有表进行操作")
    
    # 请根据您的实际情况修改以下参数
    HOST = '100.66.1.1'          # 数据库主机地址
    DATABASE = 'Student'  # 数据库名称
    USERNAME = 'root'  # 用户名
    PASSWORD = 'MySql@123456'  # 密码
    PORT = 13306                 # 端口号，默认3306
    
    print("\n正在尝试连接到MySQL数据库...")
    print(f"主机: {HOST}:{PORT}, 数据库: {DATABASE}, 用户: {USERNAME}")
    
    # 连接MySQL数据库
    connection = connect_to_mysql(HOST, DATABASE, USERNAME, PASSWORD, PORT)
    
    if connection:
        print("MySQL数据库连接成功！")
        
        try:
            while True:
                # 获取数据库中的所有表
                tables = get_tables(connection)
                
                if not tables:
                    print("数据库中没有表")
                    break
                
                # 让用户选择要操作的表
                selected_table = select_table(tables)
                
                if selected_table:
                    print(f"\n您选择了表: {selected_table}")
                    
                    # 执行CRUD操作
                    should_continue = crud_operations(connection, selected_table)
                    
                    if not should_continue:  # 用户选择退出
                        break
                    # 否则继续选择表
                else:
                    print("无效的选择")
                    break
            
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