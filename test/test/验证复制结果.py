import os
import hashlib
import datetime

def calculate_md5(file_path):
    """
    计算文件的MD5哈希值
    """
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def main():
    # 源文件路径
    src_db_path = r"C:\Users\LENOVO\Documents\chatlog\wxid_42272spv9uq522_6ded\db_storage\contact\contact.db"
    
    # 桌面路径
    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    dst_db_path = os.path.join(desktop_path, "contact.db")
    
    # 获取当前日期
    current_date = datetime.datetime.now().strftime("%Y%m%d")
    
    # 网络共享文件夹路径
    network_dst_path = r"X:\【技术】-专属共享文件夹\chatlog\内部专用号\chatlog"
    folder_name = "wxid_42272spv9uq522_6ded"
    new_folder_name = f"{folder_name}_{current_date}"
    final_dst_path = os.path.join(network_dst_path, new_folder_name)
    network_db_path = os.path.join(final_dst_path, "db_storage", "contact", "contact.db")
    
    print("开始验证文件复制结果...")
    
    # 1. 验证数据库文件是否复制到桌面
    print(f"\n1. 验证数据库文件是否复制到桌面:")
    if os.path.exists(dst_db_path):
        print(f"   ✓ 桌面数据库文件存在: {dst_db_path}")
        
        # 比较MD5值
        src_md5 = calculate_md5(src_db_path)
        dst_md5 = calculate_md5(dst_db_path)
        
        if src_md5 == dst_md5:
            print(f"   ✓ MD5验证通过: {src_md5}")
        else:
            print(f"   ✗ MD5验证失败")
            print(f"      源文件MD5: {src_md5}")
            print(f"      目标文件MD5: {dst_md5}")
    else:
        print(f"   ✗ 桌面数据库文件不存在")
    
    # 2. 验证文件夹是否复制到网络共享位置
    print(f"\n2. 验证文件夹是否复制到网络共享位置:")
    if os.path.exists(final_dst_path):
        print(f"   ✓ 网络共享文件夹存在: {final_dst_path}")
        
        # 检查文件夹中的文件数量
        file_count = sum([len(files) for r, d, files in os.walk(final_dst_path)])
        print(f"   ✓ 文件夹包含 {file_count} 个文件")
        
        # 3. 验证网络共享文件夹中的数据库文件
        print(f"\n3. 验证网络共享文件夹中的数据库文件:")
        if os.path.exists(network_db_path):
            print(f"   ✓ 网络共享数据库文件存在: {network_db_path}")
            
            # 比较MD5值
            src_md5 = calculate_md5(src_db_path)
            network_md5 = calculate_md5(network_db_path)
            
            if src_md5 == network_md5:
                print(f"   ✓ MD5验证通过: {src_md5}")
            else:
                print(f"   ✗ MD5验证失败")
                print(f"      源文件MD5: {src_md5}")
                print(f"      网络文件MD5: {network_md5}")
        else:
            print(f"   ✗ 网络共享数据库文件不存在")
    else:
        print(f"   ✗ 网络共享文件夹不存在")
    
    print(f"\n验证完成！")

if __name__ == "__main__":
    main()