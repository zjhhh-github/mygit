import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import pymysql
import pandas as pd
import pyperclip
import os
import re
import sys
import logging
from pathlib import Path


class DataTable(ttk.Treeview):
    """自定义数据表格类，增强功能"""
    
    def __init__(self, parent, columns_info=None, data=None, **kwargs):
        super().__init__(parent, **kwargs)
        
        # 存储列信息和数据
        self.columns_info = columns_info or []
        self.original_data = data or []
        self.sorted_column = None
        self.sort_reverse = False
        
        # 设置表格样式
        self.style = ttk.Style()
        self.style.configure("Custom.Treeview", 
                            background="white",
                            foreground="black",
                            rowheight=25,
                            fieldbackground="white")
        self.style.map("Custom.Treeview", 
                      background=[('selected', '#396cd5')],
                      foreground=[('selected', 'white')])
        
        self.configure(style="Custom.Treeview")
        
        # 绑定事件
        self.bind("<Button-1>", self.on_click)
        self.bind("<Motion>", self.on_hover)
        
    def setup_columns(self, headers):
        """设置表格列"""
        # 清除现有列
        for col in self['columns']:
            self.heading(col, text="")
        
        # 设置新的列
        self['columns'] = headers
        self['show'] = 'headings'  # 只显示列标题
        
        # 配置列标题和宽度
        for i, header in enumerate(headers):
            self.heading(header, text=header, command=lambda c=header: self.sort_by_column(c))
            # 设置默认列宽，可以根据内容调整
            self.column(header, width=100, minwidth=50)
    
    def load_data(self, data):
        """加载数据到表格"""
        # 清除现有数据
        for item in self.get_children():
            self.delete(item)
        
        # 插入新数据
        for row_idx, row_data in enumerate(data):
            # 交替行颜色
            tag = 'evenrow' if row_idx % 2 == 0 else 'oddrow'
            self.insert('', 'end', values=row_data, tags=(tag,))
        
        # 配置行样式
        self.tag_configure('evenrow', background='#f0f0f0')
        self.tag_configure('oddrow', background='white')
    
    def sort_by_column(self, col):
        """按列排序"""
        # 获取当前数据
        data = []
        for child in self.get_children():
            data.append(self.item(child)['values'])
        
        # 根据列索引排序
        col_index = self['columns'].index(col)
        
        # 检查是否是同一列，如果是则切换升序/降序
        if self.sorted_column == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_reverse = False
        
        self.sorted_column = col
        
        # 执行排序
        try:
            # 尝试将数据转换为数字进行排序（如果可能的话）
            sorted_data = sorted(data, 
                                key=lambda x: (x[col_index] is None, x[col_index]), 
                                reverse=self.sort_reverse)
        except TypeError:
            # 如果不能比较，转换为字符串排序
            sorted_data = sorted(data, 
                                key=lambda x: (x[col_index] is None, str(x[col_index])), 
                                reverse=self.sort_reverse)
        
        # 重新加载排序后的数据
        self.load_data(sorted_data)
    
    def on_click(self, event):
        """处理点击事件"""
        region = self.identify_region(event.x, event.y)
        if region == "heading":
            return  # 点击标题栏由sort_by_column处理
        # 获取选中行
        item = self.identify_row(event.y)
        if item:
            # 切换选中状态
            self.selection_set(item)
    
    def on_hover(self, event):
        """处理鼠标悬停事件"""
        pass


class DatabaseManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("MySQL数据库管理工具")
        self.root.geometry("1200x800")
        
        # 设置日志
        self.setup_logging()
        
        # 数据库连接变量
        self.connection = None
        
        # 当前显示组件
        self.current_display_widget = None
        self.current_table = None  # 当前表格组件
        
        # 初始化界面
        self.create_widgets()
        
    def setup_logging(self):
        """设置日志记录"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('db_manager.log', encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info("应用程序启动")
    
    def resource_path(self, relative_path):
        """获取资源文件的绝对路径，用于打包后获取文件"""
        try:
            # PyInstaller创建临时文件夹，将路径存储在_MEIPASS中
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)
    
    def create_widgets(self):
        # 创建主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # 配置网格权重
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(2, weight=1)
        
        # 连接数据库区域
        conn_frame = ttk.LabelFrame(main_frame, text="数据库连接", padding="10")
        conn_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        ttk.Label(conn_frame, text="主机:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.host_var = tk.StringVar(value="127.0.0.1")
        ttk.Entry(conn_frame, textvariable=self.host_var, width=15).grid(row=0, column=1, padx=(0, 10))
        
        ttk.Label(conn_frame, text="端口:").grid(row=0, column=2, sticky=tk.W, padx=(0, 5))
        self.port_var = tk.StringVar(value="3306")
        ttk.Entry(conn_frame, textvariable=self.port_var, width=8).grid(row=0, column=3, padx=(0, 10))
        
        ttk.Label(conn_frame, text="数据库:").grid(row=0, column=4, sticky=tk.W, padx=(0, 5))
        self.database_var = tk.StringVar()
        ttk.Entry(conn_frame, textvariable=self.database_var, width=15).grid(row=0, column=5, padx=(0, 10))
        
        ttk.Label(conn_frame, text="用户名:").grid(row=1, column=0, sticky=tk.W, padx=(0, 5), pady=(5, 0))
        self.username_var = tk.StringVar(value="root")
        ttk.Entry(conn_frame, textvariable=self.username_var, width=15).grid(row=1, column=1, padx=(0, 10), pady=(5, 0))
        
        ttk.Label(conn_frame, text="密码:").grid(row=1, column=2, sticky=tk.W, padx=(0, 5), pady=(5, 0))
        self.password_var = tk.StringVar()
        ttk.Entry(conn_frame, textvariable=self.password_var, show="*", width=15).grid(row=1, column=3, padx=(0, 10), pady=(5, 0))
        
        self.connect_btn = ttk.Button(conn_frame, text="连接", command=self.connect_to_database)
        self.connect_btn.grid(row=1, column=5, pady=(5, 0))
        
        # 功能模块区域
        modules_frame = ttk.LabelFrame(main_frame, text="功能模块", padding="10")
        modules_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # 左侧功能按钮区域
        left_frame = ttk.Frame(modules_frame)
        left_frame.grid(row=0, column=0, sticky=(tk.W, tk.N))
        
        # 右侧显示区域
        right_frame = ttk.Frame(modules_frame)
        right_frame.grid(row=0, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(20, 0))
        modules_frame.columnconfigure(1, weight=1)
        
        # 功能按钮
        self.btn_view_tables = ttk.Button(left_frame, text="查看表", command=self.view_tables, state=tk.DISABLED)
        self.btn_view_tables.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=2)
        
        self.btn_create_table = ttk.Button(left_frame, text="创建表", command=self.create_table, state=tk.DISABLED)
        self.btn_create_table.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=2)
        
        self.btn_select_table = ttk.Button(left_frame, text="选择表操作", command=self.select_table_for_operation, state=tk.DISABLED)
        self.btn_select_table.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=2)
        
        self.btn_execute_sql = ttk.Button(left_frame, text="执行SQL", command=self.execute_sql_window, state=tk.DISABLED)
        self.btn_execute_sql.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=2)
        
        # 右侧显示区域 - 改进为支持表格显示
        # 创建带滚动条的框架
        display_container = ttk.Frame(right_frame)
        display_container.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        right_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(0, weight=1)
        
        # 创建滚动条
        v_scrollbar = ttk.Scrollbar(display_container, orient="vertical")
        h_scrollbar = ttk.Scrollbar(display_container, orient="horizontal")
        
        # 创建画布用于容纳表格
        canvas = tk.Canvas(display_container, yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        v_scrollbar.config(command=canvas.yview)
        h_scrollbar.config(command=canvas.xview)
        
        # 创建内部框架
        inner_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner_frame, anchor="nw")
        
        # 表格将被放置在此框架内
        self.table_frame = inner_frame
        
        # 布局滚动条和画布
        canvas.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        v_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        h_scrollbar.grid(row=1, column=0, sticky=(tk.W, tk.E))
        
        display_container.columnconfigure(0, weight=1)
        display_container.rowconfigure(0, weight=1)
        
        # 绑定滚动事件
        def configure_canvas(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        inner_frame.bind('<Configure>', configure_canvas)
        
        # 状态栏
        self.status_var = tk.StringVar(value="就绪")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 0))
        
    def connect_to_database(self):
        try:
            host = self.host_var.get()
            database = self.database_var.get()
            username = self.username_var.get()
            password = self.password_var.get()
            port = int(self.port_var.get())
            
            self.connection = pymysql.connect(
                host=host,
                database=database,
                user=username,
                password=password,
                port=port,
                charset='utf8mb4'
            )
            
            messagebox.showinfo("成功", "数据库连接成功！")
            self.status_var.set(f"已连接到 {host}:{port}/{database}")
            self.logger.info(f"成功连接到数据库 {host}:{port}/{database}")
            
            # 启用相关按钮
            self.btn_view_tables.config(state=tk.NORMAL)
            self.btn_create_table.config(state=tk.NORMAL)
            self.btn_select_table.config(state=tk.NORMAL)
            self.btn_execute_sql.config(state=tk.NORMAL)
            
        except Exception as e:
            error_msg = f"数据库连接失败: {str(e)}"
            messagebox.showerror("错误", error_msg)
            self.status_var.set("连接失败")
            self.logger.error(error_msg)
    
    def view_tables(self):
        if not self.connection:
            messagebox.showwarning("警告", "请先连接数据库")
            return
            
        try:
            cursor = self.connection.cursor()
            cursor.execute("SHOW TABLES")
            tables = [table[0] for table in cursor.fetchall()]
            cursor.close()
            
            # 清除当前显示内容
            self.clear_display()
            
            # 创建表格显示表列表
            headers = ["#", "表名"]
            data = [(i+1, table) for i, table in enumerate(tables)]
            
            self.create_data_table(headers, data)
                
        except Exception as e:
            error_msg = f"获取表信息失败: {str(e)}"
            messagebox.showerror("错误", error_msg)
            self.logger.error(error_msg)
    
    def create_data_table(self, headers, data):
        """创建数据表格"""
        # 清除当前显示组件
        self.clear_display()
        
        # 创建表格
        self.current_table = DataTable(self.table_frame, columns=headers)
        self.current_table.setup_columns(headers)
        self.current_table.load_data(data)
        
        # 添加滚动条
        tree_scroll = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.current_table.yview)
        self.current_table.configure(yscrollcommand=tree_scroll.set)
        
        # 布局表格和滚动条
        self.current_table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 配置表格的滚动
        self.current_table.configure(yscrollcommand=tree_scroll.set)
    
    def clear_display(self):
        """清除当前显示内容"""
        # 清除当前表格
        if self.current_table:
            self.current_table.destroy()
            self.current_table = None
        
        # 清除其他可能的显示组件
        for widget in self.table_frame.winfo_children():
            widget.destroy()
    
    def create_table(self):
        if not self.connection:
            messagebox.showwarning("警告", "请先连接数据库")
            return
            
        # 创建新窗口用于创建表
        create_window = tk.Toplevel(self.root)
        create_window.title("创建新表")
        create_window.geometry("600x500")
        
        # 表名输入
        ttk.Label(create_window, text="表名:").grid(row=0, column=0, sticky=tk.W, padx=10, pady=5)
        table_name_var = tk.StringVar()
        ttk.Entry(create_window, textvariable=table_name_var, width=30).grid(row=0, column=1, padx=10, pady=5)
        
        # 列定义输入区域
        ttk.Label(create_window, text="列定义 (每行列格式: 列名 数据类型 约束, 如: id INT PRIMARY KEY AUTO_INCREMENT):").grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), padx=10, pady=(10, 0))
        
        columns_text = scrolledtext.ScrolledText(create_window, height=15)
        columns_text.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)
        
        # 添加常用列类型按钮
        btn_frame = ttk.Frame(create_window)
        btn_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), padx=10, pady=5)
        
        def insert_common_column(col_def):
            columns_text.insert(tk.INSERT, col_def + "\n")
        
        ttk.Button(btn_frame, text="ID主键", command=lambda: insert_common_column("id INT PRIMARY KEY AUTO_INCREMENT")).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="VARCHAR(255)", command=lambda: insert_common_column("column_name VARCHAR(255)")).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="INT", command=lambda: insert_common_column("column_name INT")).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="DATE", command=lambda: insert_common_column("column_name DATE")).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="TEXT", command=lambda: insert_common_column("column_name TEXT")).pack(side=tk.LEFT, padx=2)
        
        # 创建表按钮
        def create_table_action():
            table_name = table_name_var.get().strip()
            if not table_name:
                messagebox.showwarning("警告", "请输入表名")
                return
                
            columns_def = columns_text.get(1.0, tk.END).strip()
            if not columns_def:
                messagebox.showwarning("警告", "请输入列定义")
                return
                
            try:
                cursor = self.connection.cursor()
                # 分割列定义
                columns_list = [line.strip() for line in columns_def.split('\n') if line.strip()]
                columns_str = ', '.join(columns_list)
                
                sql = f"CREATE TABLE `{table_name}` ({columns_str})"
                cursor.execute(sql)
                self.connection.commit()
                cursor.close()
                
                messagebox.showinfo("成功", f"表 '{table_name}' 创建成功！")
                create_window.destroy()
                self.logger.info(f"成功创建表 {table_name}")
            except Exception as e:
                error_msg = f"创建表失败: {str(e)}"
                messagebox.showerror("错误", error_msg)
                self.logger.error(error_msg)
        
        ttk.Button(create_window, text="创建表", command=create_table_action).grid(row=4, column=0, columnspan=2, pady=10)
        
        # 配置窗口权重
        create_window.columnconfigure(1, weight=1)
        create_window.rowconfigure(2, weight=1)
    
    def select_table_for_operation(self):
        if not self.connection:
            messagebox.showwarning("警告", "请先连接数据库")
            return
            
        try:
            cursor = self.connection.cursor()
            cursor.execute("SHOW TABLES")
            tables = [table[0] for table in cursor.fetchall()]
            cursor.close()
            
            if not tables:
                messagebox.showinfo("提示", "数据库中没有表")
                return
                
            # 创建选择表的窗口
            select_window = tk.Toplevel(self.root)
            select_window.title("选择表进行操作")
            select_window.geometry("400x300")
            
            ttk.Label(select_window, text="选择要操作的表:").pack(pady=10)
            
            # 创建列表框显示表
            listbox = tk.Listbox(select_window, selectmode=tk.SINGLE)
            for table in tables:
                listbox.insert(tk.END, table)
            listbox.pack(expand=True, fill=tk.BOTH, padx=10, pady=5)
            
            # 操作按钮框架
            btn_frame = ttk.Frame(select_window)
            btn_frame.pack(pady=10)
            
            def operate_on_selected():
                selection = listbox.curselection()
                if not selection:
                    messagebox.showwarning("警告", "请选择一个表")
                    return
                    
                selected_table = tables[selection[0]]
                self.table_operations_window(selected_table)
                select_window.destroy()
            
            ttk.Button(btn_frame, text="选择并操作", command=operate_on_selected).pack(side=tk.LEFT, padx=5)
            ttk.Button(btn_frame, text="取消", command=select_window.destroy).pack(side=tk.LEFT, padx=5)
            
        except Exception as e:
            error_msg = f"获取表列表失败: {str(e)}"
            messagebox.showerror("错误", error_msg)
            self.logger.error(error_msg)
    
    def table_operations_window(self, table_name):
        # 创建表操作窗口
        op_window = tk.Toplevel(self.root)
        op_window.title(f"表操作 - {table_name}")
        op_window.geometry("1000x700")
        
        # 操作按钮区域
        btn_frame = ttk.Frame(op_window)
        btn_frame.pack(pady=10)
        
        # 创建操作按钮
        ttk.Button(btn_frame, text="查看记录", command=lambda: self.view_records(table_name)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="插入记录", command=lambda: self.insert_record_gui(table_name)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="更新记录", command=lambda: self.update_record_gui(table_name)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="删除记录", command=lambda: self.delete_record_gui(table_name)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="导入Excel", command=lambda: self.import_from_excel_gui(table_name)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="粘贴插入", command=lambda: self.paste_insert_record_gui(table_name)).pack(side=tk.LEFT, padx=5)
        
        # 创建表格显示区域
        table_container = ttk.Frame(op_window)
        table_container.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)
        
        # 创建表格
        headers = []  # 初始为空，将在加载数据时设置
        self.current_table = DataTable(table_container, columns=headers)
        
        # 添加滚动条
        tree_scroll_y = ttk.Scrollbar(table_container, orient="vertical", command=self.current_table.yview)
        tree_scroll_x = ttk.Scrollbar(table_container, orient="horizontal", command=self.current_table.xview)
        self.current_table.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
        
        # 布局表格和滚动条
        self.current_table.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        tree_scroll_y.grid(row=0, column=1, sticky=(tk.N, tk.S))
        tree_scroll_x.grid(row=1, column=0, sticky=(tk.W, tk.E))
        
        # 配置容器网格权重
        table_container.columnconfigure(0, weight=1)
        table_container.rowconfigure(0, weight=1)
        
        # 保存表格引用
        self.current_display_widget = self.current_table
    
    def view_records(self, table_name):
        if not self.connection:
            return
            
        try:
            cursor = self.connection.cursor()
            cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 1000")  # 限制显示记录数
            records = cursor.fetchall()
            
            # 获取列名
            cursor.execute(f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{table_name}' AND TABLE_SCHEMA = DATABASE() ORDER BY ORDINAL_POSITION")
            column_names = [row[0] for row in cursor.fetchall()]
            cursor.close()
            
            # 创建表格显示数据
            self.create_data_table(column_names, records)
                
        except Exception as e:
            error_msg = f"查询记录失败: {str(e)}"
            messagebox.showerror("错误", error_msg)
            self.logger.error(error_msg)
    
    def insert_record_gui(self, table_name):
        if not self.connection:
            return
            
        try:
            cursor = self.connection.cursor()
            cursor.execute(f"DESCRIBE `{table_name}`")
            columns = cursor.fetchall()
            cursor.close()
            
            # 创建插入记录窗口
            insert_window = tk.Toplevel(self.root)
            insert_window.title(f"插入记录到 {table_name}")
            insert_window.geometry("500x500")
            
            # 创建滚动画布
            canvas = tk.Canvas(insert_window)
            scrollbar = ttk.Scrollbar(insert_window, orient="vertical", command=canvas.yview)
            scrollable_frame = ttk.Frame(canvas)

            scrollable_frame.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
            )

            canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)
            
            # 添加输入字段
            entries = []
            for i, col in enumerate(columns):
                col_name = col[0]
                col_type = col[1]
                
                ttk.Label(scrollable_frame, text=f"{col_name} ({col_type}):").grid(row=i, column=0, sticky=tk.W, padx=10, pady=2)
                entry_var = tk.StringVar()
                entry = ttk.Entry(scrollable_frame, textvariable=entry_var, width=40)
                entry.grid(row=i, column=1, padx=10, pady=2)
                entries.append((col_name, entry_var))
            
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
            
            def submit_insert():
                values = []
                for col_name, entry_var in entries:
                    values.append(entry_var.get())
                
                try:
                    cursor = self.connection.cursor()
                    placeholders = ['%s'] * len(columns)
                    sql = f"INSERT INTO `{table_name}` ({', '.join([f'`{col[0]}`' for col in columns])}) VALUES ({', '.join(placeholders)})"
                    cursor.execute(sql, values)
                    self.connection.commit()
                    cursor.close()
                    
                    messagebox.showinfo("成功", "记录插入成功！")
                    insert_window.destroy()
                    
                    # 重新加载表格数据
                    self.view_records(table_name)
                    
                    self.logger.info(f"成功插入记录到表 {table_name}")
                except Exception as e:
                    error_msg = f"插入记录失败: {str(e)}"
                    messagebox.showerror("错误", error_msg)
                    self.logger.error(error_msg)
            
            # 提交按钮
            ttk.Button(insert_window, text="提交", command=submit_insert).pack(pady=10)
            
        except Exception as e:
            error_msg = f"获取表结构失败: {str(e)}"
            messagebox.showerror("错误", error_msg)
            self.logger.error(error_msg)
    
    def paste_insert_record_gui(self, table_name):
        if not self.connection:
            return
            
        try:
            cursor = self.connection.cursor()
            cursor.execute(f"DESCRIBE `{table_name}`")
            columns = cursor.fetchall()
            cursor.close()
            
            # 创建粘贴插入窗口
            paste_window = tk.Toplevel(self.root)
            paste_window.title(f"粘贴文本插入记录到 {table_name}")
            paste_window.geometry("600x400")
            
            # 说明标签
            ttk.Label(paste_window, text="从剪贴板粘贴数据，系统将自动解析并插入:").pack(pady=5)
            
            # 文本输入区域
            text_area = scrolledtext.ScrolledText(paste_window, height=10)
            text_area.pack(expand=True, fill=tk.BOTH, padx=10, pady=5)
            
            # 尝试从剪贴板获取内容
            try:
                clipboard_content = self.root.clipboard_get()
                text_area.insert(tk.END, clipboard_content)
            except:
                pass  # 如果剪贴板为空或不可访问，忽略错误
            
            # 显示表结构
            ttk.Label(paste_window, text="表结构:").pack(pady=(10, 0))
            structure_text = scrolledtext.ScrolledText(paste_window, height=6)
            structure_text.pack(expand=True, fill=tk.BOTH, padx=10, pady=5)
            
            for col in columns:
                structure_text.insert(tk.END, f"{col[0]} ({col[1]})\n")
            
            def submit_paste_insert():
                text_content = text_area.get(1.0, tk.END).strip()
                if not text_content:
                    messagebox.showwarning("警告", "请输入要粘贴的文本")
                    return
                
                # 解析文本内容
                import re
                # 尝试不同的分隔符
                separators = [',', ';', '\t', '|', '，']
                parsed_values = None
                
                for sep in separators:
                    parts = text_content.split(sep)
                    if len(parts) == len(columns):
                        parsed_values = [part.strip() for part in parts]
                        break
                
                # 如果没有找到合适分隔符，尝试按空格分割
                if parsed_values is None:
                    parts = re.split(r'\s+', text_content.strip())
                    if len(parts) == len(columns):
                        parsed_values = [part.strip() for part in parts]
                
                # 如果仍然没有匹配，使用全部作为第一个字段
                if parsed_values is None:
                    parsed_values = [text_content]
                
                # 验证数据类型
                errors = []
                for i, (value, col) in enumerate(zip(parsed_values, columns)):
                    if i < len(columns) and value:
                        col_type = col[1].lower()
                        if 'int' in col_type:
                            try:
                                int(value)
                            except ValueError:
                                errors.append(f"列 '{col[0]}' 应为整数类型，但输入的是: {value}")
                        elif 'decimal' in col_type or 'double' in col_type or 'float' in col_type:
                            try:
                                float(value)
                            except ValueError:
                                errors.append(f"列 '{col[0]}' 应为数值类型，但输入的是: {value}")
                
                if errors:
                    error_msg = "数据验证失败：\n" + "\n".join(errors)
                    messagebox.showerror("错误", error_msg)
                    return
                
                # 确认插入
                confirm_msg = f"解析结果：\n"
                for i, (col, value) in enumerate(zip(columns, parsed_values)):
                    if i < len(parsed_values):
                        confirm_msg += f"{col[0]}: {value}\n"
                
                if messagebox.askyesno("确认", f"确认插入以下数据?\n{confirm_msg}"):
                    try:
                        cursor = self.connection.cursor()
                        placeholders = ['%s'] * len(parsed_values)
                        sql = f"INSERT INTO `{table_name}` ({', '.join([f'`{col[0]}`' for col in columns[:len(parsed_values)]])}) VALUES ({', '.join(placeholders)})"
                        cursor.execute(sql, parsed_values)
                        self.connection.commit()
                        cursor.close()
                        
                        messagebox.showinfo("成功", "记录插入成功！")
                        paste_window.destroy()
                        
                        # 重新加载表格数据
                        self.view_records(table_name)
                        
                        self.logger.info(f"通过粘贴成功插入记录到表 {table_name}")
                    except Exception as e:
                        error_msg = f"插入记录失败: {str(e)}"
                        messagebox.showerror("错误", error_msg)
                        self.logger.error(error_msg)
            
            ttk.Button(paste_window, text="解析并插入", command=submit_paste_insert).pack(pady=10)
            
        except Exception as e:
            error_msg = f"获取表结构失败: {str(e)}"
            messagebox.showerror("错误", error_msg)
            self.logger.error(error_msg)
    
    def execute_sql_window(self):
        if not self.connection:
            messagebox.showwarning("警告", "请先连接数据库")
            return
            
        # 创建执行SQL窗口
        sql_window = tk.Toplevel(self.root)
        sql_window.title("执行SQL")
        sql_window.geometry("900x700")
        
        # SQL输入区域
        ttk.Label(sql_window, text="请输入SQL语句:").pack(pady=5)
        sql_text = scrolledtext.ScrolledText(sql_window, height=8)
        sql_text.pack(expand=True, fill=tk.BOTH, padx=10, pady=5)
        
        # 按钮框架
        btn_frame = ttk.Frame(sql_window)
        btn_frame.pack(pady=5)
        
        ttk.Button(btn_frame, text="执行SQL", command=lambda: self.execute_sql(sql_text, sql_window)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="清空", command=lambda: sql_text.delete(1.0, tk.END)).pack(side=tk.LEFT, padx=5)
        
        # 结果显示区域
        ttk.Label(sql_window, text="执行结果:").pack(pady=(10, 0))
        
        # 创建结果表格
        result_frame = ttk.Frame(sql_window)
        result_frame.pack(expand=True, fill=tk.BOTH, padx=10, pady=5)
        
        # 创建表格
        self.sql_result_table = DataTable(result_frame)
        
        # 添加滚动条
        tree_scroll_y = ttk.Scrollbar(result_frame, orient="vertical", command=self.sql_result_table.yview)
        tree_scroll_x = ttk.Scrollbar(result_frame, orient="horizontal", command=self.sql_result_table.xview)
        self.sql_result_table.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
        
        # 布局表格和滚动条
        self.sql_result_table.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        tree_scroll_y.grid(row=0, column=1, sticky=(tk.N, tk.S))
        tree_scroll_x.grid(row=1, column=0, sticky=(tk.W, tk.E))
        
        # 配置容器网格权重
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
    
    def execute_sql(self, sql_text, window):
        sql_query = sql_text.get(1.0, tk.END).strip()
        if not sql_query:
            messagebox.showwarning("警告", "请输入SQL语句")
            return
            
        try:
            cursor = self.connection.cursor()
            cursor.execute(sql_query)
            
            # 如果是SELECT语句，显示结果
            if sql_query.strip().upper().startswith('SELECT'):
                rows = cursor.fetchall()
                # 获取列名
                col_names = [desc[0] for desc in cursor.description] if cursor.description else []
                
                # 清除现有数据
                for item in self.sql_result_table.get_children():
                    self.sql_result_table.delete(item)
                
                # 设置列标题
                if col_names:
                    self.sql_result_table.setup_columns(col_names)
                    self.sql_result_table.load_data(rows)
            else:
                # 其他语句显示影响的行数
                self.connection.commit()
                messagebox.showinfo("成功", f"SQL执行成功！影响行数: {cursor.rowcount}")
            
            cursor.close()
            self.logger.info(f"成功执行SQL: {sql_query[:50]}...")
        except Exception as e:
            error_msg = f"执行失败: {str(e)}"
            messagebox.showerror("错误", error_msg)
            self.logger.error(error_msg)
    
    def import_from_excel_gui(self, table_name):
        if not self.connection:
            return
            
        # 文件选择对话框
        file_path = filedialog.askopenfilename(
            title="选择Excel文件",
            filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")]
        )
        
        if not file_path:
            return  # 用户取消了操作
            
        try:
            # 读取Excel文件
            df = pd.read_excel(file_path)
            
            # 获取表结构
            cursor = self.connection.cursor()
            cursor.execute(f"DESCRIBE `{table_name}`")
            table_columns = [col[0] for col in cursor.fetchall()]
            cursor.close()
            
            # 检查列是否匹配
            excel_columns = list(df.columns)
            common_cols = [col for col in table_columns if col in excel_columns]
            
            if not common_cols:
                messagebox.showerror("错误", "Excel文件中的列与表结构不匹配")
                return
            
            # 确认导入
            if messagebox.askyesno("确认", f"将导入 {len(df)} 行数据到表 '{table_name}'，共 {len(common_cols)} 列，是否继续？"):
                try:
                    # 准备数据导入
                    cursor = self.connection.cursor()
                    df_filtered = df[common_cols]  # 只保留匹配的列
                    
                    # 构建INSERT语句
                    cols = [f"`{col}`" for col in common_cols]
                    placeholders = ['%s'] * len(common_cols)
                    sql = f"INSERT INTO `{table_name}` ({','.join(cols)}) VALUES ({','.join(placeholders)})"
                    
                    # 将数据转换为元组列表并插入
                    data_tuples = [tuple(row) for row in df_filtered.values]
                    cursor.executemany(sql, data_tuples)
                    self.connection.commit()
                    
                    messagebox.showinfo("成功", f"成功导入 {cursor.rowcount} 行数据到表 '{table_name}'")
                    cursor.close()
                    
                    # 重新加载表格数据
                    self.view_records(table_name)
                    
                    self.logger.info(f"成功从Excel导入 {cursor.rowcount} 行数据到表 {table_name}")
                except Exception as e:
                    error_msg = f"导入数据失败: {str(e)}"
                    messagebox.showerror("错误", error_msg)
                    self.logger.error(error_msg)
                    
        except Exception as e:
            error_msg = f"读取Excel文件失败: {str(e)}"
            messagebox.showerror("错误", error_msg)
            self.logger.error(error_msg)
    
    def update_record_gui(self, table_name):
        messagebox.showinfo("提示", f"'更新记录'功能待实现\n表名: {table_name}")
    
    def delete_record_gui(self, table_name):
        messagebox.showinfo("提示", f"'删除记录'功能待实现\n表名: {table_name}")


def main():
    root = tk.Tk()
    app = DatabaseManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()