import subprocess
import time
import sys
import ctypes
from ctypes import wintypes

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
    """
    def enum_windows_proc(hwnd, lparam):
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
            if title.lower() in buff.value.lower():
                # 将找到的句柄存储在 lparam 指向的变量中
                ctypes.pointer(lparam)[0] = hwnd
                return False  # 停止枚举
        return True  # 继续枚举

    hwnd = wintypes.HWND(0)
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.LPARAM))(enum_windows_proc)
    ctypes.windll.user32.EnumWindows(proc, ctypes.byref(hwnd))
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
        return False
    
    # 创建一个回调函数来枚举对话框中的控件
    def enum_child_proc(child_hwnd, lparam):
        # 获取控件类名
        class_name = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(child_hwnd, class_name, 256)
        
        # 检查是否是按钮控件
        if "button" in class_name.value.lower():
            # 获取按钮文本
            button_text = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(child_hwnd, button_text, 256)
            
            # 检查按钮文本是否包含"OK"或类似确认按钮的文本
            ok_texts = ["确定", "OK", "ok", "Ok", "是", "Yes", "yes", "Yes"]
            for text in ok_texts:
                if text in button_text.value:
                    return child_hwnd  # 返回找到的OK按钮句柄
        
        return None

    # 定义回调函数类型
    enum_child_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.LPARAM))
    
    # 用于存储找到的按钮句柄
    ok_button_hwnd = ctypes.c_void_p(0)
    
    # 枚举对话框中的子窗口
    def enum_child_wrapper(child_hwnd, lparam):
        nonlocal ok_button_hwnd
        button_hwnd = enum_child_proc(child_hwnd, lparam)
        if button_hwnd:
            ok_button_hwnd.value = button_hwnd
            return False  # 找到按钮，停止枚举
        return True  # 继续枚举

    proc = enum_child_proc_type(enum_child_wrapper)
    ctypes.windll.user32.EnumChildWindows(dialog_hwnd, proc, 0)
    
    return ok_button_hwnd.value if ok_button_hwnd.value else None

def click_ok_button(dialog_hwnd):
    """
    在对话框中查找并点击"OK"按钮
    """
    if not dialog_hwnd:
        return False
    
    # 首先尝试查找实际的OK按钮
    ok_button_hwnd = find_ok_button_in_dialog(dialog_hwnd)
    
    if ok_button_hwnd:
        print("找到OK按钮，正在点击...")
        # 发送点击消息到按钮
        ctypes.windll.user32.SendMessageW(ok_button_hwnd, 0x00F5, 0, 0)  # BM_CLICK
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
    """
    exe_path = r"C:\Users\LENOVO\Desktop\chatlog.exe"
    
    VK_DOWN = 0x28
    VK_RETURN = 0x0D
    
    try:
        print("正在启动 chatlog.exe...")
        # 使用 CREATE_NEW_CONSOLE 标志启动独立窗口
        process = subprocess.Popen(exe_path, creationflags=subprocess.CREATE_NEW_CONSOLE)
        
        print("等待程序启动...")
        time.sleep(3)
        
        print("正在选择第二个功能选项...")
        send_key(VK_DOWN)
        time.sleep(0.5)
        
        print("发送回车键确认选择...")
        send_key(VK_RETURN)
        
        print("等待功能执行完成，持续监控弹窗...")
        # 持续循环检查是否有弹窗出现，直到找到弹窗
        check_interval = 0.5
        
        while True:
            time.sleep(check_interval)
            
            dialog_hwnd = find_dialog_window()
            if dialog_hwnd:
                print("检测到弹窗，正在查找OK按钮...")
                # 等待确保弹窗完全加载
                time.sleep(1)
                
                # 再次确认弹窗仍然存在
                if ctypes.windll.user32.IsWindow(dialog_hwnd):
                    print("正在点击OK按钮...")
                    click_ok_button(dialog_hwnd)
                    print("点击OK完成，正在退出程序...")
                    subprocess.run(['taskkill', '/f', '/im', 'chatlog.exe'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    print("自动化脚本因弹窗而退出！")
                    return  # 正常退出函数
                else:
                    print("弹窗已消失，继续等待...")
        
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

if __name__ == "__main__":
    run_chatlog_automation()
