import os
import shutil
import datetime
import hashlib
import time

def calculate_md5(file_path):
    """
    计算文件的MD5哈希值，用于验证文件完整性
    """
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
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

def copy_directory(src_dir, dst_dir):
    """
    复制目录，确保完整性
    """
    try:
        # 创建目标目录
        os.makedirs(dst_dir, exist_ok=True)
        
        # 复制目录内容
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        return True
    except Exception as e:
        print(f"复制目录失败: {e}")
        return False

def main():
    # 源文件和文件夹路径
    src_db_path = r"C:\Users\LENOVO\Documents\chatlog\wxid_42272spv9uq522_6ded\db_storage\contact\contact.db"
    src_folder_path = r"C:\Users\LENOVO\Documents\chatlog\wxid_42272spv9uq522_6ded"
    
    # 桌面路径
    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    dst_db_path = os.path.join(desktop_path, "contact.db")
    
    # 获取当前日期，格式化为YYYYMMDD
    current_date = datetime.datetime.now().strftime("%Y%m%d")
    
    # 网络共享文件夹路径
    network_dst_path = r"X:\【技术】-专属共享文件夹\chatlog\内部专用号\chatlog"
    
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
            shutil.copytree(src_folder_path, final_dst_path, copy_function=shutil.copy2)
            print(f"✓ 文件夹复制成功: {final_dst_path}")
        except Exception as e:
            print(f"✗ 文件夹复制失败: {e}")
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

if __name__ == "__main__":
    main()