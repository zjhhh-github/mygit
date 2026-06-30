import subprocess
import time
import sys
import ctypes
from ctypes import wintypes
import os
import shutil
import datetime
import hashlib
import time
import re
import sqlite3
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

class ToolTip:
    """
    创建一个简单的工具提示类
    """
    def __init__(self, widget):
        self.widget = widget
        self.tip_window = None
        self.id = None
        self.x = self.y = 0

    def showtip(self, text):
        """
        显示工具提示
        """
        self.text = text
        if self.tip_window or not self.text:
            return
        x, y, cx, cy = self.widget.bbox("insert")
        x = x + self.widget.winfo_rootx() + 20
        y = y + cy + self.widget.winfo_rooty() + 20
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry("+%d+%d" % (x, y))
        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                         font=("tahoma", "8", "normal"))
        label.pack(ipadx=1)

    def hidetip(self):
        """
        隐藏工具提示
        """
        tw = self.tip_window
        self.tip_window = None
        if tw:
            tw.destroy()

def create_tooltip(widget, text):
    """
    为控件创建工具提示
    """
    tool_tip = ToolTip(widget)
    
    def enter(event):
        x, y, cx, cy = widget.bbox("insert")
        x = x + widget.winfo_rootx() + 20
        y = y + cy + widget.winfo_rooty() + 20
        tool_tip.showtip(text)
    
    def leave(event):
        tool_tip.hidetip()
    
    widget.bind('<Enter>', enter)
    widget.bind('<Leave>', leave)

'''
v0.1 运行chatlog.exe,上传数据库文件到绿联云,保存concat数据库文件到桌面
v0.2 查询concat数据库文件是否有好友,查询微信号,输入为编码加名称;
v0.3 查询孵化人,输入1是报名录入,输入2是内部通讯录;改名为多功能集合;
'''
def send_key(key_code):
    """
    使用 Windows API 发送键盘按键
    """
    ctypes.windll.user32.keybd_event(key_code, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(key_code, 0, 2, 0)

def find_window_by_title(title):
    """
    通过窗口标题查找窗口句柄
    :param title: 窗口标题的关键词，用于匹配窗口
    :return: 找到的窗口句柄，如果未找到则返回0
    """
    def enum_windows_proc(hwnd, lparam):
        """
        枚举窗口的回调函数
        :param hwnd: 当前窗口句柄
        :param lparam: 用户定义参数（在此用作存储找到的句柄）
        :return: True继续枚举，False停止枚举
        """
        # 获取窗口标题长度
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            # 创建缓冲区来存储窗口标题
            buff = ctypes.create_unicode_buffer(length + 1)
            # 获取窗口标题
            ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
            # 检查窗口标题是否包含指定关键词（不区分大小写）
            if title.lower() in buff.value.lower():
                # 将找到的句柄存储在 lparam 指向的变量中
                ctypes.pointer(lparam)[0] = hwnd
                return False  # 停止枚举
        return True  # 继续枚举

    # 初始化句柄变量
    hwnd = wintypes.HWND(0)
    # 创建回调函数类型
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.LPARAM))(enum_windows_proc)
    # 枚举所有顶层窗口
    ctypes.windll.user32.EnumWindows(proc, ctypes.byref(hwnd))
    # 返回找到的窗口句柄
    return hwnd.value

def find_dialog_window():
    """
    查找对话框窗口
    """
    dialog_hwnd = None
    
    def enum_windows_proc(hwnd, lparam):
        nonlocal dialog_hwnd
        # 获取窗口类名
        class_name = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, class_name, 256)
        
        # 检查窗口是否可见且是否为对话框类
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            if "#32770" in class_name.value:  # Windows 对话框类
                dialog_hwnd = hwnd
                return False  # 找到对话框，停止枚举
        
        # 检查窗口标题是否包含对话框相关关键词
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            time.sleep(120)  # 等待窗口更新
            buff = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
            dialog_keywords = ["错误", "警告", "确认", "提示", "dialog", "alert", "message"]
            for keyword in dialog_keywords:
                if keyword in buff.value.lower():
                    dialog_hwnd = hwnd
                    return False  # 找到对话框，停止枚举
        return True  # 继续枚举

    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.LPARAM))(enum_windows_proc)
    ctypes.windll.user32.EnumWindows(proc, None)
    return dialog_hwnd

def find_ok_button_in_dialog(dialog_hwnd):
    """
    在对话框中查找OK按钮
    """
    if not dialog_hwnd:
        return None
    
    # 用于存储找到的按钮句柄
    ok_button_hwnd = None
    
    # 创建一个回调函数来枚举对话框中的控件
    def enum_child_proc(child_hwnd, lparam):
        nonlocal ok_button_hwnd
        # 获取控件类名
        class_name = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(child_hwnd, class_name, 256)
        
        # 检查是否是按钮控件
        if "button" in class_name.value.lower():
            # 获取按钮文本
            button_text = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(child_hwnd, button_text, 256)
            
            # 检查按钮文本是否包含"OK"或类似确认按钮的文本
            ok_texts = ["确定", "OK", "ok", "Ok", "是", "Yes", "yes", "Yes", "确定(", "OK)", "是)"]
            for text in ok_texts:
                if text in button_text.value:
                    ok_button_hwnd = child_hwnd
                    return False  # 找到按钮，停止枚举
        
        return True  # 继续枚举

    # 定义回调函数类型
    enum_child_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.LPARAM))
    
    proc = enum_child_proc_type(enum_child_proc)
    ctypes.windll.user32.EnumChildWindows(dialog_hwnd, proc, 0)
    
    return ok_button_hwnd

def click_ok_button(dialog_hwnd):
    """
    在对话框中查找并点击"OK"按钮
    """
    if not dialog_hwnd:
        return False
    
    # 激活对话框窗口并确保它在前台
    ctypes.windll.user32.ShowWindow(dialog_hwnd, 5)  # SW_SHOW
    ctypes.windll.user32.SetForegroundWindow(dialog_hwnd)
    ctypes.windll.user32.BringWindowToTop(dialog_hwnd)
    time.sleep(0.5)
    
    # 首先尝试查找实际的OK按钮
    ok_button_hwnd = find_ok_button_in_dialog(dialog_hwnd)
    
    if ok_button_hwnd:
        print("找到OK按钮，正在点击...")
        
        # 等待按钮完全就绪
        time.sleep(0.5)
        
        # 方法1: 发送BM_CLICK消息到按钮
        ctypes.windll.user32.PostMessageW(ok_button_hwnd, 0x00F5, 0, 0)  # BM_CLICK
        time.sleep(0.1)
        
        # 方法2: 使用鼠标点击
        # 获取按钮的位置和大小
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(ok_button_hwnd, ctypes.byref(rect))
        
        # 计算按钮中心坐标
        center_x = (rect.left + rect.right) // 2
        center_y = (rect.top + rect.bottom) // 2
        
        # 确保对话框窗口处于前台
        ctypes.windll.user32.SetForegroundWindow(dialog_hwnd)
        time.sleep(0.1)
        
        # 发送鼠标点击事件到按钮
        # 首先使用SetCursorPos移动光标到按钮中心
        ctypes.windll.user32.SetCursorPos(center_x, center_y)
        time.sleep(0.1)
        
        # 然后发送鼠标点击事件
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
        time.sleep(0.1)
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
        time.sleep(0.1)
        
        # 再次确保窗口在前台并发送回车键
        ctypes.windll.user32.SetForegroundWindow(dialog_hwnd)
        time.sleep(0.1)
        
        # 无论是否点击成功，都发送回车键以确保确认
        print("发送回车键以确保确认...")
        send_key(0x0D)  # 回车键
        time.sleep(0.5)
    else:
        print("未找到明确的OK按钮，尝试键盘操作...")
        # 如果找不到明确的OK按钮，则使用键盘操作
        time.sleep(0.5)
        # 尝试按Tab键选择按钮，然后按回车
        ctypes.windll.user32.SetForegroundWindow(dialog_hwnd)
        time.sleep(0.1)
        send_key(0x09)  # Tab键
        time.sleep(0.1)
        send_key(0x0D)  # 回车键
        
        # 等待窗口关闭
        time.sleep(1)
        
        # 如果窗口仍然存在，尝试其他方法
        if ctypes.windll.user32.IsWindow(dialog_hwnd):
            # 再次尝试回车
            send_key(0x0D)  # 回车键
            time.sleep(1)
    
    return True

def run_chatlog_automation():
    """
    自动化运行chatlog.exe程序
    通过模拟键盘操作控制chatlog.exe程序界面，执行微信数据库解密
    """
    # 定义程序路径和键盘键值
    exe_path = r"C:\\Users\\LENOVO\\Desktop\\chatlog.exe"
    
    # 定义键盘键值常量
    VK_DOWN = 0x28    # 向下箭头键
    VK_RETURN = 0x0D  # 回车键
    
    try:
        print("正在启动 chatlog.exe...")
        # 使用 CREATE_NEW_CONSOLE 标志启动独立窗口
        process = subprocess.Popen(exe_path, creationflags=subprocess.CREATE_NEW_CONSOLE)
        
        print("等待程序启动...")
        time.sleep(3)
        
        print("正在选择第二个功能选项...")
        # 模拟按下向下箭头键，选择第二个功能选项
        send_key(VK_DOWN)
        time.sleep(0.5)
        
        print("发送回车键确认选择...")
        # 模拟按下回车键确认选择
        send_key(VK_RETURN)
        
        print("等待功能执行完成，持续监控弹窗...")
        # 持续循环检查是否有弹窗出现，直到找到带有OK按钮的弹窗
        check_interval = 0.5
        
        # 循环检查弹窗，直到找到带有OK按钮的弹窗
        while True:
            time.sleep(check_interval)
            
            # 查找弹窗窗口 - 现在这个函数会等待直到找到带有OK按钮的对话框
            dialog_hwnd = find_dialog_window()
            if dialog_hwnd:
                print("检测到带有OK按钮的弹窗...")
                # 增加等待时间确保弹窗完全加载
                time.sleep(1)
                
                print("正在点击OK按钮...")
                # 点击OK按钮完成操作
                click_ok_button(dialog_hwnd)
                print("点击OK完成，正在退出程序...")
                # 强制终止chatlog.exe进程
                subprocess.run(['taskkill', '/f', '/im', 'chatlog.exe'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                print("自动化脚本因弹窗而退出！")
                return  # 正常退出函数
            
            # 检查进程是否仍然运行
            if process.poll() is not None:
                print("chatlog.exe进程已结束，退出监控")
            
            # 检查进程是否仍然运行
            if process.poll() is not None:
                print("chatlog.exe进程已结束，退出监控")
                break
        
    except FileNotFoundError:
        print(f"错误：找不到文件 {exe_path}")
        sys.exit(1)
    except subprocess.CalledProcessError:
        print("警告：未能终止 chatlog.exe 进程")
    except KeyboardInterrupt:
        print("用户中断了脚本执行")
        try:
            subprocess.run(['taskkill', '/f', '/im', 'chatlog.exe'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except:
            pass
        sys.exit(0)
    except Exception as e:
        print(f"执行过程中发生错误：{e}")
        try:
            subprocess.run(['taskkill', '/f', '/im', 'chatlog.exe'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except:
            pass
        sys.exit(1)

def calculate_md5(file_path):
    """
    计算文件的MD5哈希值
    :param file_path: 要计算MD5的文件路径
    :return: 文件的MD5哈希值（十六进制字符串）
    """
    # 创建MD5哈希对象
    hash_md5 = hashlib.md5()
    # 以二进制模式打开文件
    with open(file_path, "rb") as f:
        # 分块读取文件，避免大文件占用过多内存
        for chunk in iter(lambda: f.read(4096), b""):
            # 更新哈希值
            hash_md5.update(chunk)
    # 返回十六进制格式的MD5值
    return hash_md5.hexdigest()

def verify_file_integrity(src_path, dst_path):
    """
    验证源文件和目标文件是否完全一致
    """
    if not os.path.exists(src_path) or not os.path.exists(dst_path):
        return False
    
    # 首先比较文件大小
    if os.path.getsize(src_path) != os.path.getsize(dst_path):
        return False
    
    # 如果大小一致，再比较MD5哈希值
    src_md5 = calculate_md5(src_path)
    dst_md5 = calculate_md5(dst_path)
    
    return src_md5 == dst_md5

def copy_directory_with_progress(src, dst, progress_callback=None):
    """
    带进度显示的目录复制功能
    :param src: 源目录路径
    :param dst: 目标目录路径
    :param progress_callback: 进度回调函数，接收进度百分比和当前操作描述
    """
    # 如果目标目录不存在，创建目标目录
    if not os.path.exists(dst):
        os.makedirs(dst)
    
    # 收集所有要复制的项目（文件和目录）
    items = []
    for root, dirs, files in os.walk(src):
        # 添加目录到项目列表
        for dir in dirs:
            items.append((os.path.join(root, dir), True))  # (路径, 是否为目录)
        # 添加文件到项目列表
        for file in files:
            items.append((os.path.join(root, file), False))  # (路径, 是否为目录)
    
    # 计算总项目数和已复制项目数
    total_items = len(items)
    copied_items = 0
    
    # 逐个复制项目
    for item_path, is_dir in items:
        # 计算相对路径
        rel_path = os.path.relpath(item_path, src)
        # 构建目标路径
        dst_path = os.path.join(dst, rel_path)
        
        if is_dir:
            # 如果是目录，创建对应的目标目录
            if not os.path.exists(dst_path):
                os.makedirs(dst_path)
        else:
            # 如果是文件，复制文件到目标位置
            shutil.copy2(item_path, dst_path)
        
        # 更新已复制项目数
        copied_items += 1
        # 如果提供了进度回调函数，调用它更新进度
        if progress_callback:
            # 计算进度百分比
            progress = int((copied_items / total_items) * 100)
            # 调用回调函数，传入进度和当前操作描述
            progress_callback(progress, f"正在复制: {os.path.basename(item_path)}")

def main():
    # 源文件和文件夹路径
    src_db_path = r"C:\\Users\\LENOVO\\Documents\\chatlog\\wxid_42272spv9uq522_6ded\\db_storage\\contact\\contact.db"
    src_folder_path = r"C:\\Users\\LENOVO\\Documents\\chatlog\\wxid_42272spv9uq522_6ded"
    
    # 桌面路径
    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    dst_db_path = os.path.join(desktop_path, "contact.db")
    
    # 获取当前日期，格式化为YYYYMMDD
    current_date = datetime.datetime.now().strftime("%Y%m%d")
    
    # 网络共享文件夹路径
    network_dst_path = r"X:\\【技术】-专属共享文件夹\\chatlog\\内部专用号\\chatlog"
    
    # 获取文件夹名称并创建带日期的新名称
    folder_name = os.path.basename(src_folder_path)
    new_folder_name = f"{folder_name}_{current_date}"
    final_dst_path = os.path.join(network_dst_path, new_folder_name)
    
    try:
        print("开始执行文件操作...")
        
        # 1. 检查源文件和文件夹是否存在
        if not os.path.exists(src_db_path):
            print(f"✗ 源数据库文件不存在: {src_db_path}")
            return False
        
        if not os.path.exists(src_folder_path):
            print(f"✗ 源文件夹不存在: {src_folder_path}")
            return False
        
        # 2. 复制数据库文件到桌面
        print(f"正在复制数据库文件到桌面...")
        shutil.copy2(src_db_path, dst_db_path)
        
        # 验证数据库文件复制完整性
        if verify_file_integrity(src_db_path, dst_db_path):
            print(f"✓ 数据库文件复制成功，MD5验证通过")
        else:
            print(f"✗ 数据库文件复制失败，MD5验证不通过")
            return False
        
        # 3. 检查网络共享文件夹是否存在
        print(f"正在检查网络共享文件夹: {network_dst_path}")
        if not os.path.exists(network_dst_path):
            print(f"✗ 网络共享文件夹不存在或无法访问: {network_dst_path}")
            # 尝试创建网络共享文件夹
            try:
                os.makedirs(network_dst_path, exist_ok=True)
                print(f"✓ 已尝试创建网络共享文件夹")
            except Exception as e:
                print(f"✗ 无法创建网络共享文件夹: {e}")
                return False
        else:
            print(f"✓ 网络共享文件夹存在")
        
        # 4. 直接复制源文件夹到网络共享位置，并使用新名称
        print(f"正在复制文件夹到网络共享位置...")
        print(f"源文件夹: {src_folder_path}")
        print(f"目标文件夹: {final_dst_path}")
        
        if os.path.exists(final_dst_path):
            # 如果目标文件夹已存在，先删除
            print(f"目标文件夹已存在，正在删除...")
            try:
                shutil.rmtree(final_dst_path)
                print(f"✓ 已删除目标文件夹")
            except Exception as e:
                print(f"✗ 删除目标文件夹失败: {e}")
                return False
        
        print(f"开始执行 copytree 操作...")
        try:
            # 自定义复制函数，添加进度显示
            def copy_with_progress(src, dst):
                # 获取当前复制的文件名
                filename = os.path.basename(src)
                print(f"  正在复制: {filename}")
                shutil.copy2(src, dst)
            
            # 统计总文件数，用于进度计算
            total_files = 0
            for root, dirs, files in os.walk(src_folder_path):
                total_files += len(files)
            
            current_file = 0
            # 自定义copytree函数，添加进度显示
            def copytree_with_progress(src, dst, symlinks=False, ignore=None, copy_function=copy_with_progress, ignore_dangling_symlinks=False):
                nonlocal current_file
                # 创建目标目录
                os.makedirs(dst, exist_ok=True)
                
                # 获取源目录下的文件和子目录
                items = os.listdir(src)
                if ignore is not None:
                    ignored_items = ignore(src, items)
                else:
                    ignored_items = set()
                
                for item in items:
                    if item in ignored_items:
                        continue
                    
                    s = os.path.join(src, item)
                    d = os.path.join(dst, item)
                    
                    if os.path.isdir(s):
                        # 递归复制子目录
                        copytree_with_progress(s, d, symlinks, ignore, copy_function, ignore_dangling_symlinks)
                    else:
                        # 复制文件
                        current_file += 1
                        print(f"  [{current_file}/{total_files}] 正在复制: {os.path.relpath(s, src_folder_path)}")
                        copy_function(s, d)
            
            # 执行带进度的复制
            copytree_with_progress(src_folder_path, final_dst_path, copy_function=shutil.copy2)
            print(f"✓ 文件夹复制成功: {final_dst_path}")
        except KeyboardInterrupt:
            print(f"\n✗ 复制操作被用户中断")
            return False
        except Exception as e:
            print(f"✗ 文件夹复制失败: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # 5. 验证网络共享文件夹中的数据库文件完整性
        network_db_path = os.path.join(final_dst_path, "db_storage", "contact", "contact.db")
        print(f"正在验证网络共享中的数据库文件: {network_db_path}")
        
        if os.path.exists(network_db_path):
            if verify_file_integrity(src_db_path, network_db_path):
                print(f"✓ 网络共享文件夹中的数据库文件MD5验证通过")
            else:
                print(f"✗ 网络共享文件夹中的数据库文件MD5验证不通过")
                return False
        else:
            print(f"✗ 网络共享文件夹中的数据库文件不存在")
            return False
        
        # 6. 验证文件夹结构完整性
        src_file_count = sum([len(files) for r, d, files in os.walk(src_folder_path)])
        dst_file_count = sum([len(files) for r, d, files in os.walk(final_dst_path)])
        
        if src_file_count == dst_file_count:
            print(f"✓ 文件夹结构完整，源文件数: {src_file_count}，目标文件数: {dst_file_count}")
        else:
            print(f"✗ 文件夹结构不完整，源文件数: {src_file_count}，目标文件数: {dst_file_count}")
            return False
        
        print("\n所有操作完成！")
        print(f"1. 数据库文件已复制到桌面: {dst_db_path}")
        print(f"2. 文件夹已复制到网络共享位置: {final_dst_path}")
        print(f"3. 所有文件完整性验证通过")
        print(f"4. 文件夹结构完整")
        
        return True
        
    except Exception as e:
        print(f"执行过程中发生错误: {e}")
        return False

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import threading

class ChatLogApp:
    def __init__(self, root):
        """
        初始化GUI应用程序
        :param root: Tkinter根窗口对象
        """
        # 设置窗口基本属性
        self.root = root
        self.root.title("多功能集合工具")
        self.root.geometry("800x600")
        self.root.minsize(600, 400)  # 设置最小窗口尺寸
        
        # 配置主窗口的grid权重，使其能够响应大小变化
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        
        # 加载配置文件，用于记住用户上次的选择
        self.config_file = "config.json"
        self.config = self.load_config()
        
        # 创建主框架容器，用于组织所有UI元素
        self.main_frame = ttk.Frame(root, padding="10")
        self.main_frame.grid(row=0, column=0, sticky="nsew")
        
        # 配置主框架的grid权重
        self.main_frame.grid_rowconfigure(2, weight=0)  # 进度条区域不分配额外权重
        self.main_frame.grid_rowconfigure(3, weight=1)  # 为PanedWindow区域分配权重
        self.main_frame.grid_columnconfigure(0, weight=1)
        
        # 创建应用程序标题
        self.title_label = ttk.Label(self.main_frame, text="多功能集合工具", font=("Arial", 16, "bold"))
        self.title_label.grid(row=0, column=0, pady=10, sticky="ew")
        self.main_frame.grid_columnconfigure(0, weight=1)
        
        # 创建功能按钮框架，包含所有主要功能按钮
        self.function_frame = ttk.LabelFrame(self.main_frame, text="功能选项", padding="10")
        self.function_frame.grid(row=1, column=0, sticky="ew", pady=10)
        self.function_frame.grid_columnconfigure(6, weight=1)  # 为退出按钮前的空间分配权重
        
        # 解密数据库按钮 - 用于解密加密的数据库文件
        self.decrypt_button = ttk.Button(self.function_frame, text="解密数据库", command=self.start_decrypt_db)
        self.decrypt_button.grid(row=0, column=0, padx=2, sticky="ew")
        self.function_frame.grid_columnconfigure(0, weight=1)
        
        # 复制文件按钮 - 用于复制数据库文件到指定位置
        self.copy_button = ttk.Button(self.function_frame, text="复制文件", command=self.start_copy_files)
        self.copy_button.grid(row=0, column=1, padx=2, sticky="ew")
        self.function_frame.grid_columnconfigure(1, weight=1)
        
        # 查询好友按钮 - 通过备注查询联系人列表中的好友
        self.query_friend_button = ttk.Button(self.function_frame, text="查询好友", command=self.start_query_friend)
        self.query_friend_button.grid(row=0, column=2, padx=2, sticky="ew")
        self.function_frame.grid_columnconfigure(2, weight=1)
        
        # 查询微信号按钮 - 通过备注查询微信号，显示alias或username
        self.query_wxid_button = ttk.Button(self.function_frame, text="查询微信号", command=self.start_query_wxid)
        self.query_wxid_button.grid(row=0, column=3, padx=2, sticky="ew")
        self.function_frame.grid_columnconfigure(3, weight=1)
        
        # 查询孵化人按钮 - 查询孵化人群成员
        self.query_incubator_button = ttk.Button(self.function_frame, text="查询孵化人", command=self.start_query_incubator)
        self.query_incubator_button.grid(row=0, column=4, padx=2, sticky="ew")
        self.function_frame.grid_columnconfigure(4, weight=1)
        
        # 显示孵化人路径按钮 - 用于显示/隐藏孵化人路径选择
        self.incubator_paths_button = ttk.Button(self.function_frame, text="孵化人路径", command=self.show_incubator_paths)
        self.incubator_paths_button.grid(row=0, column=5, padx=2, sticky="ew")
        self.function_frame.grid_columnconfigure(5, weight=1)

        # 查询上级按钮 - 基于最新宝妈结构图查询上级
        self.query_superior_button = ttk.Button(
            self.function_frame,
            text="查询上级",
            command=self.start_query_superior_from_latest_structure
        )
        self.query_superior_button.grid(row=0, column=6, padx=2, sticky="ew")
        self.function_frame.grid_columnconfigure(6, weight=1)
        
        # 退出按钮 - 关闭应用程序
        self.exit_button = ttk.Button(self.function_frame, text="退出", command=root.quit)
        self.exit_button.grid(row=0, column=8, padx=2, sticky="e")
        
        # 创建进度条框架，显示操作进度
        self.progress_frame = ttk.LabelFrame(self.main_frame, text="进度", padding="10")
        self.progress_frame.grid(row=2, column=0, sticky="ew", pady=10)
        self.progress_frame.grid_columnconfigure(0, weight=1)
        
        # 进度条变量和控件，用于显示当前操作的进度
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.progress_frame, variable=self.progress_var, mode='determinate')
        self.progress_bar.grid(row=0, column=0, sticky="ew", pady=5)
        
        # 进度标签，显示当前操作状态的文字描述
        self.progress_label = ttk.Label(self.progress_frame, text="准备就绪")
        self.progress_label.grid(row=1, column=0, pady=5, sticky="ew")
        
        # 创建PanedWindow用于分隔日志和结果区域，允许用户调整大小
        self.paned_window = ttk.PanedWindow(self.main_frame, orient=tk.VERTICAL)
        self.paned_window.grid(row=3, column=0, sticky="nsew", pady=10)
        
        # 创建日志显示框架，用于显示操作日志
        self.log_frame = ttk.LabelFrame(self.paned_window, text="日志", padding="10")
        
        # 日志文本区域，用于显示详细的操作日志信息
        self.log_text = scrolledtext.ScrolledText(self.log_frame, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)  # 初始设置为只读状态
        
        # 创建查询结果框架，用于显示查询结果
        self.result_frame = ttk.LabelFrame(self.paned_window, text="查询结果", padding="10")
        
        # 创建结果文本区域和保存按钮的框架
        self.result_content_frame = ttk.Frame(self.result_frame)
        self.result_content_frame.grid(row=0, column=0, sticky="nsew")
        self.result_frame.grid_rowconfigure(0, weight=1)
        self.result_frame.grid_columnconfigure(0, weight=1)
        
        # 创建结果文本区域和保存按钮的框架
        self.result_text_frame = ttk.Frame(self.result_content_frame)
        self.result_text_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.result_content_frame.grid_rowconfigure(0, weight=1)
        self.result_content_frame.grid_columnconfigure(0, weight=1)
        
        # 查询结果文本区域，用于显示查询结果
        self.result_text = scrolledtext.ScrolledText(self.result_text_frame, wrap=tk.WORD)
        self.result_text.pack(fill=tk.BOTH, expand=True)
        
        # 保存结果按钮，用于将查询结果保存到文件
        self.save_result_button = ttk.Button(self.result_content_frame, text="保存结果", command=self.save_result_to_file)
        self.save_result_button.grid(row=0, column=1, sticky="ne")
        
        self.result_text.config(state=tk.DISABLED)  # 初始设置为只读状态
        
        # 将日志和结果框架添加到PanedWindow中
        self.paned_window.add(self.log_frame, weight=2)  # 日志区域初始权重
        self.paned_window.add(self.result_frame, weight=3)  # 结果区域初始权重
        
        # 配置窗口大小变化事件监听器
        # self.root.bind('<Configure>', self.on_window_resize)
        
        # 创建孵化人路径选择框架（默认隐藏）
        self.incubator_path_frame = ttk.LabelFrame(self.main_frame, text="孵化人路径选择", padding="10")
        
        # 配置孵化人路径框架的grid权重
        self.incubator_path_frame.grid_columnconfigure(0, weight=1)
        
        # 创建输入路径组
        self.input_paths_frame = ttk.LabelFrame(self.incubator_path_frame, text="输入路径", padding="5")
        self.input_paths_frame.grid(row=0, column=0, sticky="ew", pady=5)
        self.input_paths_frame.grid_columnconfigure(0, weight=1)
        
        # 数据库路径选择区域
        self.db_path_frame = ttk.Frame(self.input_paths_frame)
        self.db_path_frame.grid(row=0, column=0, sticky="ew", pady=2)
        self.input_paths_frame.grid_columnconfigure(0, weight=1)
        self.db_label = ttk.Label(self.db_path_frame, text="数据库路径:")
        self.db_label.pack(side=tk.LEFT, padx=(0, 5))
        self.db_path_var = tk.StringVar()
        self.db_path_entry = ttk.Entry(self.db_path_frame, textvariable=self.db_path_var)
        self.db_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.db_path_indicator = ttk.Label(self.db_path_frame, text="●", foreground="red")
        self.db_path_indicator.pack(side=tk.LEFT, padx=(0, 5))
        create_tooltip(self.db_path_indicator, "路径无效或不存在")
        self.db_browse_button = ttk.Button(self.db_path_frame, text="浏览", command=self.browse_db_path)
        self.db_browse_button.pack(side=tk.LEFT)
        
        # 输入文件1路径选择区域（_脚本输入_1.txt）
        self.input1_path_frame = ttk.Frame(self.input_paths_frame)
        self.input1_path_frame.grid(row=1, column=0, sticky="ew", pady=2)
        self.input1_label = ttk.Label(self.input1_path_frame, text="输入文件1:")
        self.input1_label.pack(side=tk.LEFT, padx=(0, 5))
        self.input1_path_var = tk.StringVar()
        self.input1_path_entry = ttk.Entry(self.input1_path_frame, textvariable=self.input1_path_var)
        self.input1_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.input1_path_indicator = ttk.Label(self.input1_path_frame, text="●", foreground="red")
        self.input1_path_indicator.pack(side=tk.LEFT, padx=(0, 5))
        create_tooltip(self.input1_path_indicator, "路径无效或不存在")
        self.input1_browse_button = ttk.Button(self.input1_path_frame, text="浏览", command=self.browse_input1_path)
        self.input1_browse_button.pack(side=tk.LEFT)
        
        # 输入文件2路径选择区域（_脚本输入_2.txt）
        self.input2_path_frame = ttk.Frame(self.input_paths_frame)
        self.input2_path_frame.grid(row=2, column=0, sticky="ew", pady=2)
        self.input2_label = ttk.Label(self.input2_path_frame, text="输入文件2:")
        self.input2_label.pack(side=tk.LEFT, padx=(0, 5))
        self.input2_path_var = tk.StringVar()
        self.input2_path_entry = ttk.Entry(self.input2_path_frame, textvariable=self.input2_path_var)
        self.input2_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.input2_path_indicator = ttk.Label(self.input2_path_frame, text="●", foreground="red")
        self.input2_path_indicator.pack(side=tk.LEFT, padx=(0, 5))
        create_tooltip(self.input2_path_indicator, "路径无效或不存在")
        self.input2_browse_button = ttk.Button(self.input2_path_frame, text="浏览", command=self.browse_input2_path)
        self.input2_browse_button.pack(side=tk.LEFT)
        
        # 创建输出路径组
        self.output_paths_frame = ttk.LabelFrame(self.incubator_path_frame, text="输出路径", padding="5")
        self.output_paths_frame.grid(row=1, column=0, sticky="ew", pady=5)
        self.output_paths_frame.grid_columnconfigure(0, weight=1)
        
        # 输出文件路径选择区域（_输出结果_1.txt）
        self.output_path_frame = ttk.Frame(self.output_paths_frame)
        self.output_path_frame.grid(row=0, column=0, sticky="ew", pady=2)
        self.output_paths_frame.grid_columnconfigure(0, weight=1)
        self.output_label = ttk.Label(self.output_path_frame, text="输出文件:")
        self.output_label.pack(side=tk.LEFT, padx=(0, 5))
        self.output_path_var = tk.StringVar()
        self.output_path_entry = ttk.Entry(self.output_path_frame, textvariable=self.output_path_var)
        self.output_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.output_path_indicator = ttk.Label(self.output_path_frame, text="●", foreground="red")
        self.output_path_indicator.pack(side=tk.LEFT, padx=(0, 5))
        create_tooltip(self.output_path_indicator, "路径无效或目录不存在")
        self.output_browse_button = ttk.Button(self.output_path_frame, text="浏览", command=self.browse_output_path)
        self.output_browse_button.pack(side=tk.LEFT)
        
        # 初始化路径变量，从配置中加载上次的值（如果存在）
        self.db_path_var.set(self.config.get("incubator_db_path", r'C:\Users\LENOVO\Desktop\contact.db'))
        self.input1_path_var.set(self.config.get("incubator_input1_path", r'C:\Users\LENOVO\Desktop\_脚本输入_1.txt'))
        self.input2_path_var.set(self.config.get("incubator_input2_path", r'C:\Users\LENOVO\Desktop\_脚本输入_2.txt'))
        self.output_path_var.set(self.config.get("incubator_output_path", r'C:\Users\LENOVO\Desktop\_输出结果_1.txt'))
        
        # 添加事件监听器以实现路径输入实时验证和自动保存
        self.db_path_var.trace_add("write", self.on_incubator_path_change)
        self.input1_path_var.trace_add("write", self.on_incubator_path_change)
        self.input2_path_var.trace_add("write", self.on_incubator_path_change)
        self.output_path_var.trace_add("write", self.on_incubator_path_change)
        
        # 初始更新路径指示器
        self.update_path_indicators()
        
        # 初始化应用程序状态变量
        self.decrypted_db_path = None  # 存储解密后的数据库路径
        self.copied_files_path = None  # 存储复制的文件路径
    
    def load_config(self):
        """
        从配置文件加载用户设置
        :return: 配置字典，包含用户上次的输入输出路径等信息
        """
        # 定义默认配置，确保即使配置文件不存在或损坏也有默认值
        default_config = {
            "last_input_file": "",      # 上次选择的输入文件路径
            "last_output_dir": "",      # 上次选择的输出目录路径
            "window_size": "800x600",   # 窗口大小
            "incubator_db_path": r'C:\Users\LENOVO\Desktop\contact.db',      # 孵化人数据库路径
            "incubator_input1_path": r'C:\Users\LENOVO\Desktop\_脚本输入_1.txt',  # 孵化人输入文件1路径
            "incubator_input2_path": r'C:\Users\LENOVO\Desktop\_脚本输入_2.txt',  # 孵化人输入文件2路径
            "incubator_output_path": r'C:\Users\LENOVO\Desktop\_输出结果_1.txt'    # 孵化人输出文件路径
        }
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 合并默认配置，确保新添加的配置项存在
                    for key, value in default_config.items():
                        if key not in config:
                            config[key] = value
                    return config
            except Exception as e:
                print(f"读取配置文件失败: {e}")
                return default_config
        else:
            return default_config
    
    def save_config(self):
        """
        保存当前配置到配置文件
        将用户选择的路径等信息保存，以便下次启动时记住
        """
        try:
            # 确保配置目录存在
            config_dir = os.path.dirname(self.config_file)
            if config_dir and not os.path.exists(config_dir):
                os.makedirs(config_dir)
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置文件失败: {e}")
        
    def log(self, message):
        """
        向日志区域添加信息
        :param message: 要添加的日志消息
        """
        # 启用文本框编辑状态
        self.log_text.config(state=tk.NORMAL)
        # 在末尾插入消息并换行
        self.log_text.insert(tk.END, message + "\n")
        # 滚动到最新消息
        self.log_text.see(tk.END)
        # 设置为只读状态
        self.log_text.config(state=tk.DISABLED)
        # 更新GUI以立即显示新消息
        self.root.update_idletasks()
    
    def update_progress(self, value, message):
        """
        更新进度条和进度标签
        :param value: 进度值 (0-100)
        :param message: 进度描述消息
        """
        # 更新进度条的值
        self.progress_var.set(value)
        # 更新进度标签的文本
        self.progress_label.config(text=message)
        # 立即更新GUI
        self.root.update_idletasks()
    
    def enable_step2(self):
        """启用步骤2按钮 - 已废弃，保留兼容性"""
        self.step2_button.config(state=tk.NORMAL)
        self.log("✓ 步骤1完成，可以执行步骤2")
    
    def enable_step3(self):
        """启用步骤3按钮 - 已废弃，保留兼容性"""
        self.step3_button.config(state=tk.NORMAL)
        self.log("✓ 步骤2完成，可以执行步骤3")
    
    # ==================== 模块1: 解密数据库 ====================
    
    def start_decrypt_db(self):
        """
        启动解密数据库功能
        在新线程中执行实际的解密操作，避免阻塞GUI界面
        """
        self.log("开始执行解密数据库")
        # 禁用按钮，防止重复点击
        self.decrypt_button.config(state=tk.DISABLED)
        self.update_progress(0, "正在启动 chatlog.exe...")
        
        # 在新线程中执行，避免阻塞GUI
        thread = threading.Thread(target=self.run_decrypt_db)
        thread.daemon = True
        thread.start()
    
    def run_decrypt_db(self):
        """
        执行解密数据库的实际操作
        调用外部程序chatlog.exe来解密数据库文件
        """
        try:
            # 执行解密自动化流程
            run_chatlog_automation()
            # 记录解密后的数据库路径，供后续功能使用
            self.decrypted_db_path = r'C:\Users\LENOVO\Desktop\contact.db'  # 假设解密后数据库在桌面
            self.update_progress(100, "解密数据库完成")
            self.log("✓ 解密数据库完成")
            # 重新启用按钮
            self.decrypt_button.config(state=tk.NORMAL)
        except Exception as e:
            self.log(f"✗ 解密数据库失败: {e}")
            self.update_progress(0, "解密数据库失败")
            # 出错时也要重新启用按钮
            self.decrypt_button.config(state=tk.NORMAL)
    
    # ==================== 模块2: 复制文件 ====================
    
    def start_copy_files(self):
        """
        启动复制文件功能
        在新线程中执行实际的复制操作，避免阻塞GUI界面
        """
        self.log("开始执行复制文件")
        # 禁用按钮，防止重复点击
        self.copy_button.config(state=tk.DISABLED)
        self.update_progress(0, "正在准备复制文件...")
        
        # 在新线程中执行，避免阻塞GUI
        thread = threading.Thread(target=self.run_copy_files)
        thread.daemon = True
        thread.start()
    
    def run_copy_files(self):
        """
        执行复制文件的实际操作
        复制数据库文件到用户指定的目标目录
        """
        try:
            # 重写print函数，将输出重定向到日志
            original_print = print
            def custom_print(*args, **kwargs):
                message = " ".join(map(str, args))
                self.log(message)
                original_print(*args, **kwargs)
            
            # 替换print函数
            import builtins
            builtins.print = custom_print
            
            self.update_progress(10, "正在检查源文件和文件夹...")
            result = main()
            
            # 恢复原print函数
            builtins.print = original_print
            
            if result:
                # 记住输出目录
                output_dir = r"X:\【技术】-专属共享文件夹\chatlog\内部专用号\chatlog"
                self.config["last_output_dir"] = output_dir
                self.save_config()
                
                self.copied_files_path = output_dir
                self.update_progress(100, "复制文件完成")
                self.log("✓ 复制文件完成")
                # 重新启用按钮
                self.copy_button.config(state=tk.NORMAL)
            else:
                self.log("✗ 复制文件失败")
                self.update_progress(0, "复制文件失败")
                # 出错时也要重新启用按钮
                self.copy_button.config(state=tk.NORMAL)
        except Exception as e:
            self.log(f"✗ 复制文件失败: {e}")
            self.update_progress(0, "复制失败")
            # 出错时也要重新启用按钮
            self.copy_button.config(state=tk.NORMAL)
    
    # ==================== 模块3: 查询好友 ====================
    
    def start_query_friend(self):
        """开始执行查询好友"""
        self.log("开始执行查询好友")
        self.query_friend_button.config(state=tk.DISABLED)
        self.update_progress(0, "正在连接数据库...")
        
        # 在新线程中执行，避免阻塞GUI
        thread = threading.Thread(target=self.run_query_friend)
        thread.daemon = True
        thread.start()
    
    def run_query_friend(self):
        """
        执行查询好友的实际操作
        按备注查询联系人，优先显示alias字段，如果alias为空则显示备注
        """
        try:
            # 选择文本文件，使用上次记住的路径
            initial_dir = os.path.dirname(self.config["last_input_file"]) if self.config["last_input_file"] else os.path.expanduser("~")
            txt_path = filedialog.askopenfilename(
                title="选择文本文件",
                filetypes=[("文本文件", "*.txt"), ("所有 files", "*.*")],
                initialdir=initial_dir
            )
            
            # 检查是否选择了文件
            if not txt_path:
                self.log("✗ 未选择文本文件，查询取消")
                self.update_progress(0, "查询取消")
                # 重新启用按钮
                self.query_friend_button.config(state=tk.NORMAL)
                return
            
            # 记住选择的文件路径，以便下次启动时使用
            self.config["last_input_file"] = txt_path
            self.save_config()
            
            self.log(f"选择的文本文件: {txt_path}")
            self.log("查询类型: 按备注查询（优先显示alias）")
            self.update_progress(30, "正在连接数据库...")
            
            # 连接数据库
            conn = sqlite3.connect(r'C:\Users\LENOVO\Desktop\contact.db')
            cursor = conn.cursor()
            
            self.update_progress(50, "正在查询数据...")
            
            # 查询备注和alias字段，优先使用alias，如果alias为空则使用备注
            # 使用CASE语句优先显示alias，如果alias为空则显示备注
            create_table_sql = '''
            SELECT remark, CASE 
                WHEN alias IS NOT NULL AND alias != '' THEN alias
                ELSE remark
            END AS display_name
            FROM contact 
            WHERE remark like '¿¿¿%';
            '''
            
            # 执行查询并处理可能的异常
            try:
                results = cursor.execute(create_table_sql).fetchall()
                # 创建一个映射，键为remark，值为显示名称（alias或remark）
                remark_to_display = {item[0]: item[1] for item in results}
                # 创建显示名称列表用于查询匹配
                display_names = list(remark_to_display.keys())
            except sqlite3.Error as e:
                self.log(f"数据库查询错误: {e}")
                conn.close()
                self.query_friend_button.config(state=tk.NORMAL)
                return
            
            self.update_progress(70, "正在比较数据...")
            
            # 清空之前的结果显示
            self.result_text.config(state=tk.NORMAL)
            self.result_text.delete(1.0, tk.END)
            
            # 从文本文件中读取所有查询项
            query_items = []
            with open(txt_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()  # 去除首尾空白字符
                    if line:  # 如果行不为空
                        query_items.append(line)
            
            # 统计查询结果：找到的项目和未找到的项目
            found_items = []
            not_found_items = []
            
            # 遍历查询项，检查是否在数据库结果中存在
            for item in query_items:
                if item in display_names:
                    found_items.append(item)
                else:
                    not_found_items.append(item)
            
            # 显示查询结果摘要
            self.result_text.insert(tk.END, f"查询完成！共查询 {len(query_items)} 个项目\n")
            self.result_text.insert(tk.END, f"找到: {len(found_items)} 个\n", "summary")
            self.result_text.insert(tk.END, f"未找到: {len(not_found_items)} 个\n\n", "summary")
            
            # 显示找到的项目
            if found_items:
                self.result_text.insert(tk.END, "【找到的项目】\n", "header")
                for item in found_items:
                    # 找到对应的remark来显示详细信息
                    found_remark = None
                    for remark, display_name in remark_to_display.items():
                        if display_name == item:
                            found_remark = remark
                            break
                    result_line = f"✓ '{item}' 在列表中\n"
                    self.result_text.insert(tk.END, result_line, "in_list")
            
            # 显示未找到的项目
            if not_found_items:
                self.result_text.insert(tk.END, "\n【未找到的项目】\n", "header")
                for item in not_found_items:
                    result_line = f"✗ '{item}' 不在列表中\n"
                    self.result_text.insert(tk.END, result_line, "not_in_list")
            
            # 添加标签样式
            self.result_text.tag_config("in_list", foreground="green")
            self.result_text.tag_config("not_in_list", foreground="red")
            self.result_text.tag_config("summary", foreground="blue", font=("Arial", 10, "bold"))
            self.result_text.tag_config("header", foreground="purple", font=("Arial", 10, "bold"))
            
            self.result_text.config(state=tk.DISABLED)
            
            conn.close()
            
            self.update_progress(100, "查询好友完成")
            self.log("✓ 查询好友完成，结果已显示")
            self.query_friend_button.config(state=tk.NORMAL)
        except Exception as e:
            self.log(f"✗ 查询好友失败: {e}")
            self.update_progress(0, "查询好友失败")
            self.query_friend_button.config(state=tk.NORMAL)
    
    # ==================== 模块4: 查询微信号 ====================
    
    def start_query_wxid(self):
        """
        启动查询微信号功能
        在新线程中执行实际的查询操作，避免阻塞GUI界面
        """
        self.log("开始执行查询微信号")
        # 禁用按钮，防止重复点击
        self.query_wxid_button.config(state=tk.DISABLED)
        self.update_progress(0, "正在连接数据库...")
        
        # 在新线程中执行，避免阻塞GUI
        thread = threading.Thread(target=self.run_query_wxid)
        thread.daemon = True
        thread.start()
    
    def run_query_wxid(self):
        """
        执行查询微信号的实际操作
        通过remark字段查询联系人，但显示结果为alias或username
        """
        try:
            # 选择文本文件，使用上次记住的路径
            initial_dir = os.path.dirname(self.config["last_input_file"]) if self.config["last_input_file"] else os.path.expanduser("~")
            txt_path = filedialog.askopenfilename(
                title="选择文本文件",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
                initialdir=initial_dir
            )
            
            # 检查是否选择了文件
            if not txt_path:
                self.log("✗ 未选择文本文件，查询取消")
                self.update_progress(0, "查询取消")
                # 重新启用按钮
                self.query_wxid_button.config(state=tk.NORMAL)
                return
            
            # 记住选择的文件路径，以便下次启动时使用
            self.config["last_input_file"] = txt_path
            self.save_config()
            
            self.log(f"选择的文本文件: {txt_path}")
            self.log("查询类型: 按备注查询微信号（显示alias或username）")
            self.update_progress(30, "正在连接数据库...")
            
            # 连接数据库
            conn = sqlite3.connect(r'C:\Users\LENOVO\Desktop\contact.db')
            cursor = conn.cursor()
            
            self.update_progress(50, "正在查询数据...")
            
            # 查询remark、username和alias字段，显示结果为alias或username
            # 使用CASE语句优先显示alias，如果alias为空则显示username
            create_table_sql = '''
            SELECT remark,  CASE 
                WHEN alias IS NOT NULL AND alias != '' THEN alias
                ELSE username
            END AS display_name
            FROM contact 
            WHERE remark like '¿¿¿%';
            '''
            
            # 执行查询并处理可能的异常
            try:
                results = cursor.execute(create_table_sql).fetchall()
                # 创建一个映射，键为remark，值为显示名称（alias或username）
                remark_to_display = {item[0].split('-')[0]: item[1] for item in results}
                # 创建显示名称列表用于查询匹配
                display_names = list(remark_to_display.keys())
            except sqlite3.Error as e:
                self.log(f"数据库查询错误: {e}")
                conn.close()
                self.query_wxid_button.config(state=tk.NORMAL)
                return
            
            self.update_progress(70, "正在比较数据...")
            
            # 清空之前的结果显示
            self.result_text.config(state=tk.NORMAL)
            self.result_text.delete(1.0, tk.END)
            
            # 从文本文件中读取所有查询项
            query_items = []
            with open(txt_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()  # 去除首尾空白字符
                    if line:  # 如果行不为空
                        query_items.append(line.split('-')[0])
            
            # 统计查询结果：找到的项目和未找到的项目
            found_items = []
            not_found_items = []
            all_items = []
            # 遍历查询项，检查是否在数据库结果中存在
            for item in query_items:
                if item in display_names:
                    found_items.append(item)
                else:
                    not_found_items.append(item)
                all_items.append(item)
            
            # 显示查询结果摘要
            self.result_text.insert(tk.END, f"查询完成！共查询 {len(query_items)} 个项目\n")
            self.result_text.insert(tk.END, f"找到: {len(found_items)} 个\n", "summary")
            self.result_text.insert(tk.END, f"未找到: {len(not_found_items)} 个\n\n", "summary")
            
            # # 显示找到的项目
            # if found_items:
            #     self.result_text.insert(tk.END, "【找到的项目】\n", "header")
            #     for item in found_items:
            #         # 找到对应的remark和username来显示详细信息
            #         found_remark = None
            #         found_username = None
            #         for remark, display_name in remark_to_display.items():
            #             if display_name == item:
            #                 # 遍历所有结果找到匹配的remark和username
            #                 for result in results:
            #                     if result[1] == item:  # result[1] is display_name
            #                         found_remark = result[0]  # result[0] is remark
            #                         found_username = result[2]  # result[2] is username
            #                         break
            #                 break
            #         if found_remark and found_username:
            #             result_line = f"✓ '{item}' (原始备注: {found_remark}, 用户名: {found_username}) 在列表中\n"
            #         else:
            #             result_line = f"✓ '{item}' {remark_to_display[item]} \n"
            #         self.result_text.insert(tk.END, result_line, "in_list")
            
            # # 显示未找到的项目
            # if not_found_items:
            #     self.result_text.insert(tk.END, "\n【未找到的项目】\n", "header")
            #     for item in not_found_items:
            #         result_line = f"✗ '{item}' 不在列表中\n"
            #         self.result_text.insert(tk.END, result_line, "not_in_list")
            # 显示所有的项目
            if all_items:
                self.result_text.insert(tk.END, "【所有的项目】\n", "header")
                for item in all_items:
                    if item in found_items:
                        # 项目找到了，显示对应的alias或username
                        result_line = f"{remark_to_display[item]} \n"
                        self.result_text.insert(tk.END, result_line, "in_list")
                    else:
                        # 项目没找到，显示警告符号
                        result_line = f" \n"
                        self.result_text.insert(tk.END, result_line, "not_in_list")
            # 添加标签样式
            self.result_text.tag_config("in_list", foreground="green")
            self.result_text.tag_config("not_in_list", foreground="red")
            self.result_text.tag_config("summary", foreground="blue", font=("Arial", 10, "bold"))
            self.result_text.tag_config("header", foreground="purple", font=("Arial", 10, "bold"))
            
            self.result_text.config(state=tk.DISABLED)
            
            conn.close()
            
            self.update_progress(100, "查询微信号完成")
            self.log("✓ 查询微信号完成，结果已显示")
            self.query_wxid_button.config(state=tk.NORMAL)
        except Exception as e:
            self.log(f"✗ 查询微信号失败: {e}")
            self.update_progress(0, "查询微信号失败")
            self.query_wxid_button.config(state=tk.NORMAL)
    # ==================== 模块5: 查询孵化人 ====================
    def start_query_incubator(self):
        """
        启动查询孵化人功能
        这个函数会启动一个新的线程来执行查询孵化人操作，避免GUI冻结
        """
        # 禁用查询孵化人按钮，防止重复点击
        self.query_incubator_button.config(state=tk.DISABLED)
        # 重置进度条
        self.update_progress(0, "正在启动查询孵化人...")
        # 记录开始时间
        start_time = time.time()
        self.log("开始查询孵化人...")
        
        # 在新线程中执行查询孵化人操作
        thread = threading.Thread(target=self.run_query_incubator, args=(start_time,))
        thread.daemon = True
        thread.start()

    def run_query_incubator(self, start_time):
        """
        执行查询孵化人的实际操作
        通过数据库查询孵化人成员，处理内部通讯录和学员信息
        """
        try:
            # 首先验证所有路径是否有效
            db_path = self.db_path_var.get()
            input1_path = self.input1_path_var.get()
            input2_path = self.input2_path_var.get()
            output_path = self.output_path_var.get()
            
            # 检查所有必需的路径是否存在
            missing_paths = []
            if not db_path or not os.path.exists(db_path):
                missing_paths.append("数据库路径")
            if not input1_path or not os.path.exists(input1_path):
                missing_paths.append("输入文件1路径")
            if not input2_path or not os.path.exists(input2_path):
                missing_paths.append("输入文件2路径")
            if not output_path:
                missing_paths.append("输出文件路径")
            
            if missing_paths:
                self.log(f"✗ 缺少必要的路径: {', '.join(missing_paths)}")
                messagebox.showerror("错误", f"缺少必要的路径:\n{chr(10).join(missing_paths)}")
                self.query_incubator_button.config(state=tk.NORMAL)
                return
            
            # 导入需要的库
            import sqlite3
            
            self.update_progress(10, "正在连接数据库...")
            self.log(f"连接到联系人数据库: {db_path}")
            
            # 连接数据库
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            self.update_progress(20, "正在查询孵化人群成员...")
            
            # 定义孵化人列表
            name_list = ['倪佳毅','程中宥','杨昕林']
            all_chengyuan = []
            nijiayi_chengyuan = []
            chengzhongyou_chengyuan = []
            yingxinlin_chengyuan = []
            
            for name in name_list:
                create_table_sql = f'''
                SELECT 
                    DISTINCT remark,
                    CASE 
                        WHEN INSTR(remark, '-') > 0
                        THEN SUBSTR(remark, 1, INSTR(remark, '-') - 1)
                        ELSE remark
                    END AS number
                FROM contact WHERE username in 
                    (SELECT username FROM name2id WHERE rowid IN 
                        (SELECT member_id FROM chatroom_member WHERE room_id in 
                            (SELECT room_id_ FROM chat_room_info_detail WHERE username_ in 
                                (SELECT username FROM contact WHERE remark like "{name} 孵化群%")
                            )
                        )			
                    )  ORDER BY number ASC;
                '''
                
                results = cursor.execute(create_table_sql).fetchall()
                members = [i[1] for i in results if i[1] is not None and i[1][:3] == '¿¿¿']
                
                if name == "倪佳毅":    
                    nijiayi_chengyuan = members
                elif name == "程中宥":
                    chengzhongyou_chengyuan = members
                elif name == "杨昕林":
                    yingxinlin_chengyuan = members
                
                all_chengyuan = all_chengyuan + [i[1] for i in cursor.execute(create_table_sql).fetchall() if i[1] is not None and i[1][:3] == '¿¿¿']
                all_chengyuan = list(set(all_chengyuan))
            
            self.update_progress(40, "正在读取内部通讯录...")
            
            # 读取内部通讯录文件
            try:
                xueyuan2dailin = {}
                with open(input2_path, 'r', encoding='utf-8') as f:
                    f_lines = f.readlines()
                    for i in f_lines:
                        i = i.split('\t')
                        if len(i) > 3:  # 确保有足够字段
                            xueyuan2dailin[i[0].split('-')[0]] = i[3].strip()  # 使用strip()去除换行符
            except FileNotFoundError:
                self.log(f"警告: 未找到内部通讯录文件 {input2_path}")
                xueyuan2dailin = {}
            
            self.update_progress(60, "正在读取学员信息...")
            
            # 读取学员信息文件
            try:
                l1 = []
                l2 = []
                with open(input1_path, 'r', encoding='utf-8') as f:
                    f_lines = f.readlines()
                    for j in f_lines:
                        parts = j.split("\t")
                        if len(parts) > 25:  # 确保有足够字段
                            l1.append(parts[7])
                            l2.append(parts[25].strip())  # 使用strip()去除换行符
                stu2fuhua = dict(zip(l1, l2))  # 使用dict()确保zip对象可以多次迭代
            except FileNotFoundError:
                self.log(f"警告: 未找到学员信息文件 {input1_path}")
                stu2fuhua = {}
            
            self.update_progress(80, "正在处理查询结果...")
            
            # 清空之前的结果显示
            self.result_text.config(state=tk.NORMAL)
            self.result_text.delete(1.0, tk.END)
            
            # 输出结果到文件
            with open(output_path, 'w', encoding='utf-8') as f:
                pass
            weizhaodao = 0
            for i, j in stu2fuhua.items():  # 使用.items()迭代字典
                if j == "⚠️" or j == "❌" or j is not None:
                    if i in all_chengyuan:
                        # 在官方孵化群中
                        guanfangfuhuaren = []
                        if i in nijiayi_chengyuan:
                            guanfangfuhuaren.append("¿¿¿000032-倪佳毅")
                        elif i in chengzhongyou_chengyuan:
                            guanfangfuhuaren.append("¿¿¿000067-程中宥")
                        elif i in yingxinlin_chengyuan:
                            guanfangfuhuaren.append("¿¿¿000115-杨昕林")
                        if len(guanfangfuhuaren) == 1:
                            guanfangfuhuaren = ' '.join(guanfangfuhuaren)
                        else:
                            guanfangfuhuaren = '⚠️'
                        with open(output_path, 'a', encoding='utf-8') as f:
                            f.write(f"{guanfangfuhuaren}\n")
                    else:
                        # 不在官方孵化群中
                        try:
                            dailc = xueyuan2dailin[i]
                        except KeyError:
                            dailc = "⚠️"
                            weizhaodao += 1
                        with open(output_path, 'a', encoding='utf-8') as f:
                            f.write(f"{dailc}\n")
                else:
                    with open(output_path, 'a', encoding='utf-8') as f:
                        f.write(f"{j}\n")
            
            # 显示处理结果
            self.result_text.insert(tk.END, f"查询孵化人完成！\n")
            self.result_text.insert(tk.END, f"倪佳毅孵化群成员数: {len(nijiayi_chengyuan)}\n")
            self.result_text.insert(tk.END, f"程中宥孵化群成员数: {len(chengzhongyou_chengyuan)}\n")
            self.result_text.insert(tk.END, f"杨昕林孵化群成员数: {len(yingxinlin_chengyuan)}\n")
            self.result_text.insert(tk.END, f"处理学员数: {len(l1)}\n")
            self.result_text.insert(tk.END, f"未找到对应孵化群成员数: {weizhaodao}\n")
            self.result_text.insert(tk.END, f"结果已保存到: {output_path}\n")
            
            # 添加标签样式
            self.result_text.tag_config("summary", foreground="blue", font=("Arial", 10, "bold"))
            
            self.result_text.config(state=tk.DISABLED)
            
            conn.close()
            
            self.update_progress(100, "查询孵化人完成")
            self.log(f"✓ 查询孵化人完成，耗时 {time.time() - start_time:.2f} 秒")
            self.query_incubator_button.config(state=tk.NORMAL)
        except Exception as e:
            self.log(f"✗ 查询孵化人失败: {e}")
            self.update_progress(0, "查询孵化人失败")
            self.query_incubator_button.config(state=tk.NORMAL)

    # ==================== 模块6: 从最新结构图查询上级 ====================
    def start_query_superior_from_latest_structure(self):
        """
        启动“从最新宝妈结构图查询上级”功能
        在新线程中执行，避免阻塞GUI
        """
        self.query_superior_button.config(state=tk.DISABLED)
        self.update_progress(0, "正在准备查询上级...")
        self.log("开始执行：从最新宝妈结构图查询上级")

        thread = threading.Thread(target=self.run_query_superior_from_latest_structure)
        thread.daemon = True
        thread.start()

    def _extract_number_and_name(self, text):
        """
        从一行文本中提取“编号”和“名字”
        支持格式：
        1) 编号-名字
        2) 编号<空格>名字
        """
        raw = text.strip()
        if not raw:
            return "", ""

        if "-" in raw:
            # 关键逻辑：仅按第一个“-”拆分，避免尾部统计字段影响解析
            number, rest = raw.split("-", 1)
            name = rest.split("-", 1)[0].strip()
            return number.strip(), name

        parts = raw.split(None, 1)
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
        return raw, ""

    def _pick_latest_structure_md(self, structure_dir):
        """
        在目录中选择最新的“宝妈结构图YYYYMMDD.md”文件
        优先按文件名日期选择；同日期时按修改时间降序兜底
        """
        pattern = re.compile(r"^宝妈结构图(\d{8})\.md$")
        candidates = []

        for file_name in os.listdir(structure_dir):
            match = pattern.match(file_name)
            if not match:
                continue
            full_path = os.path.join(structure_dir, file_name)
            if os.path.isfile(full_path):
                date_key = match.group(1)
                mtime = os.path.getmtime(full_path)
                candidates.append((date_key, mtime, full_path))

        if not candidates:
            raise FileNotFoundError(f"目录中未找到符合命名规则的结构图文件: {structure_dir}")

        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return candidates[0][2]

    def _build_parent_mapping_from_md(self, md_path):
        """
        解析结构图Markdown，构建“编号 -> 节点列表（含上级）”映射
        节点层级通过前导空格缩进识别（每2个空格视为1层）
        """
        number_to_nodes = {}
        stack = []

        with open(md_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip().startswith("- "):
                    continue

                # 关键逻辑：根据缩进计算层级，并维护当前路径栈
                indent_count = len(line) - len(line.lstrip(" "))
                depth = indent_count // 2
                content = line.strip()[2:].strip()

                number, name = self._extract_number_and_name(content)
                if not number:
                    continue

                while len(stack) > depth:
                    stack.pop()

                parent_node = stack[-1] if stack else None
                node = {
                    "number": number,
                    "name": name,
                    "content": content,
                    "parent": parent_node["content"] if parent_node else "",
                    "line_no": line_no
                }

                if number not in number_to_nodes:
                    number_to_nodes[number] = []
                number_to_nodes[number].append(node)

                stack.append(node)

        return number_to_nodes

    def run_query_superior_from_latest_structure(self):
        """
        实际执行“查询上级”逻辑：
        1) 读取输入文件中的“编号+名字”
        2) 自动找到最新结构图
        3) 编号优先匹配，名字校验
        4) 冲突项提示人工确认
        """
        try:
            structure_dir = r"D:\桌面文件\宝妈结构图"

            self.update_progress(10, "请选择包含编号与名字的文本文件...")
            initial_dir = os.path.dirname(self.config["last_input_file"]) if self.config["last_input_file"] else os.path.expanduser("~")
            txt_path = filedialog.askopenfilename(
                title="选择包含“编号+名字”的文本文件",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
                initialdir=initial_dir
            )

            if not txt_path:
                self.log("✗ 未选择输入文件，查询取消")
                self.update_progress(0, "查询取消")
                self.query_superior_button.config(state=tk.NORMAL)
                return

            self.config["last_input_file"] = txt_path
            self.save_config()
            self.log(f"输入文件: {txt_path}")

            self.update_progress(25, "正在定位最新结构图文件...")
            latest_md_path = self._pick_latest_structure_md(structure_dir)
            self.log(f"使用结构图: {latest_md_path}")

            self.update_progress(45, "正在解析结构图层级关系...")
            number_to_nodes = self._build_parent_mapping_from_md(latest_md_path)

            self.update_progress(65, "正在读取查询项并匹配上级...")
            query_items = []
            with open(txt_path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    q_number, q_name = self._extract_number_and_name(raw_line)
                    query_items.append({
                        "raw": raw_line,
                        "number": q_number,
                        "name": q_name
                    })

            success_items = []
            conflict_items = []
            not_found_items = []

            for item in query_items:
                number = item["number"]
                input_name = item["name"]
                nodes = number_to_nodes.get(number, [])

                if not nodes:
                    not_found_items.append((item, "结构图中不存在该编号"))
                    continue

                if len(nodes) == 1:
                    node = nodes[0]
                    # 编号优先匹配，名字仅用于校验；若校验不一致则提示人工确认
                    if input_name and node["name"] and input_name != node["name"]:
                        conflict_items.append((item, [node], "编号匹配但名字不一致，请人工确认"))
                    else:
                        success_items.append((item, node))
                    continue

                # 同编号出现多次时，尝试用名字消歧
                if input_name:
                    matched = [n for n in nodes if n["name"] == input_name]
                    if len(matched) == 1:
                        success_items.append((item, matched[0]))
                    else:
                        conflict_items.append((item, nodes, "同编号对应多条记录，名字无法唯一确认"))
                else:
                    conflict_items.append((item, nodes, "同编号对应多条记录，缺少名字无法确认"))

            self.update_progress(85, "正在渲染结果...")
            self.result_text.config(state=tk.NORMAL)
            self.result_text.delete(1.0, tk.END)

            self.result_text.insert(tk.END, f"查询完成！共查询 {len(query_items)} 条\n", "summary")
            self.result_text.insert(tk.END, f"成功匹配: {len(success_items)} 条\n", "summary")
            self.result_text.insert(tk.END, f"冲突待确认: {len(conflict_items)} 条\n", "warning")
            self.result_text.insert(tk.END, f"未找到: {len(not_found_items)} 条\n\n", "not_in_list")

            if success_items:
                self.result_text.insert(tk.END, "【成功匹配】\n", "header")
                for item, node in success_items:
                    # 输出规范：子级与上级都统一为“编号-名字”，并用Tab分隔，便于直接保存后复用
                    child_number_name = f"{node['number']}-{node['name']}".strip("-")
                    if node["parent"]:
                        parent_number, parent_name = self._extract_number_and_name(node["parent"])
                        parent_number_name = f"{parent_number}-{parent_name}".strip("-")
                    else:
                        parent_number_name = "无上级（根节点）"
                    self.result_text.insert(
                        tk.END,
                        f"{child_number_name}\t{parent_number_name}\n",
                        "in_list"
                    )
                self.result_text.insert(tk.END, "\n")

            if conflict_items:
                self.result_text.insert(tk.END, "【冲突待人工确认】\n", "header")
                for item, candidate_nodes, reason in conflict_items:
                    self.result_text.insert(
                        tk.END,
                        f"⚠ 输入: {item['raw']} -> {reason}\n",
                        "warning"
                    )
                    for node in candidate_nodes:
                        parent = node["parent"] if node["parent"] else "无上级（根节点）"
                        self.result_text.insert(
                            tk.END,
                            f"    候选: {node['content']} | 上级: {parent}\n",
                            "warning"
                        )
                self.result_text.insert(tk.END, "\n")

            if not_found_items:
                self.result_text.insert(tk.END, "【未找到】\n", "header")
                for item, reason in not_found_items:
                    self.result_text.insert(
                        tk.END,
                        f"✗ 输入: {item['raw']} -> {reason}\n",
                        "not_in_list"
                    )

            self.result_text.tag_config("in_list", foreground="green")
            self.result_text.tag_config("not_in_list", foreground="red")
            self.result_text.tag_config("summary", foreground="blue", font=("Arial", 10, "bold"))
            self.result_text.tag_config("header", foreground="purple", font=("Arial", 10, "bold"))
            self.result_text.tag_config("warning", foreground="#B8860B")
            self.result_text.config(state=tk.DISABLED)

            self.update_progress(100, "查询上级完成")
            self.log("✓ 查询上级完成")
            self.query_superior_button.config(state=tk.NORMAL)
        except Exception as e:
            self.log(f"✗ 查询上级失败: {e}")
            self.update_progress(0, "查询上级失败")
            self.query_superior_button.config(state=tk.NORMAL)

    def save_result_to_file(self):
        """
        保存查询结果到文件
        """
        # 获取当前结果文本框的内容
        result_content = self.result_text.get("1.0", tk.END).strip()
        
        # 检查是否有内容可保存
        if not result_content:
            messagebox.showwarning("警告", "没有可保存的结果内容！")
            return
        
        # 打开文件保存对话框
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            title="保存查询结果"
        )
        
        # 如果用户取消了保存操作
        if not file_path:
            return
        
        try:
            # 将结果内容写入文件
            with open(file_path, 'w', encoding='utf-8') as file:
                file.write(result_content)
            
            # 显示成功消息
            messagebox.showinfo("成功", f"查询结果已成功保存到:\n{file_path}")
            
        except Exception as e:
            # 如果保存过程中出现错误，显示错误消息
            messagebox.showerror("错误", f"保存文件时发生错误:\n{str(e)}")

    # ==================== 孵化人路径选择功能 ====================
    def show_incubator_paths(self):
        """
        显示/隐藏孵化人路径选择区域
        """
        if self.incubator_path_frame.winfo_viewable():
            # 如果已经显示，则隐藏
            self.incubator_path_frame.grid_remove()
            self.log("已隐藏孵化人路径选择区域")
        else:
            # 如果未显示，则显示
            self.incubator_path_frame.grid(row=4, column=0, sticky="ew", pady=10)
            self.log("已显示孵化人路径选择区域")
            
    def browse_db_path(self):
        """
        浏览并选择数据库路径
        """
        db_path = filedialog.askopenfilename(
            title="选择数据库文件",
            filetypes=[("数据库文件", "*.db"), ("所有文件", "*.*")],
            initialdir=os.path.expanduser("~")
        )
        if db_path:
            self.db_path_var.set(db_path)
            self.log(f"已选择数据库路径: {db_path}")
            
    def browse_input1_path(self):
        """
        浏览并选择输入文件1路径
        """
        input1_path = filedialog.askopenfilename(
            title="选择输入文件1",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialdir=os.path.expanduser("~")
        )
        if input1_path:
            self.input1_path_var.set(input1_path)
            self.log(f"已选择输入文件1路径: {input1_path}")
            
    def browse_input2_path(self):
        """
        浏览并选择输入文件2路径
        """
        input2_path = filedialog.askopenfilename(
            title="选择输入文件2",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialdir=os.path.expanduser("~")
        )
        if input2_path:
            self.input2_path_var.set(input2_path)
            self.log(f"已选择输入文件2路径: {input2_path}")
            
    def browse_output_path(self):
        """
        浏览并选择输出文件路径
        """
        output_path = filedialog.asksaveasfilename(
            title="选择输出文件",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialdir=os.path.expanduser("~")
        )
        if output_path:
            self.output_path_var.set(output_path)
            self.log(f"已选择输出文件路径: {output_path}")
            
    def on_incubator_path_change(self, *args):
        """
        当孵化人路径发生变化时的回调函数
        """
        # 保存当前路径到配置
        self.config["incubator_db_path"] = self.db_path_var.get()
        self.config["incubator_input1_path"] = self.input1_path_var.get()
        self.config["incubator_input2_path"] = self.input2_path_var.get()
        self.config["incubator_output_path"] = self.output_path_var.get()
        
        # 更新路径验证指示器
        self.update_path_indicators()
        
        # 保存配置到文件
        self.save_config()
    
    def update_path_indicators(self):
        """
        更新路径验证指示器
        """
        # 检查数据库路径
        db_path = self.db_path_var.get()
        if db_path and os.path.exists(db_path):
            self.db_path_indicator.config(text="●", foreground="green")
            create_tooltip(self.db_path_indicator, "路径有效")
        else:
            self.db_path_indicator.config(text="●", foreground="red")
            create_tooltip(self.db_path_indicator, "路径无效或不存在")
        
        # 检查输入文件1路径
        input1_path = self.input1_path_var.get()
        if input1_path and os.path.exists(input1_path):
            self.input1_path_indicator.config(text="●", foreground="green")
            create_tooltip(self.input1_path_indicator, "路径有效")
        else:
            self.input1_path_indicator.config(text="●", foreground="red")
            create_tooltip(self.input1_path_indicator, "路径无效或不存在")
        
        # 检查输入文件2路径
        input2_path = self.input2_path_var.get()
        if input2_path and os.path.exists(input2_path):
            self.input2_path_indicator.config(text="●", foreground="green")
            create_tooltip(self.input2_path_indicator, "路径有效")
        else:
            self.input2_path_indicator.config(text="●", foreground="red")
            create_tooltip(self.input2_path_indicator, "路径无效或不存在")
        
        # 检查输出文件路径
        output_path = self.output_path_var.get()
        # For output file, we check if the directory exists
        output_dir = os.path.dirname(output_path) if output_path else ""
        if output_path and (os.path.exists(output_dir) if output_dir else True):
            self.output_path_indicator.config(text="●", foreground="green")
            create_tooltip(self.output_path_indicator, "路径有效")
        else:
            self.output_path_indicator.config(text="●", foreground="red")
            create_tooltip(self.output_path_indicator, "路径无效或目录不存在")

    def on_window_resize(self, event):
        """
        窗口大小变化时的处理函数
        仅处理根窗口的大小变化事件，避免处理其他组件的事件
        """
        # 检查事件是否来自根窗口，避免重复处理
        if event.widget != self.root:
            return
            
        # 获取当前窗口尺寸
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        
        # 可以根据窗口大小进行特定的布局调整
        # 目前主要依赖grid的权重系统自动调整，这里可以添加特殊处理
        self.log(f"窗口大小调整为: {width}x{height}")

if __name__ == "__main__":
    # 创建GUI应用
    root = tk.Tk()
    app = ChatLogApp(root)
    
    # 运行主循环
    root.mainloop()