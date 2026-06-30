# -*- coding: utf-8 -*-

import os
import time
import shutil


# ============================================================
# 一、配置需要复制的文件
# ============================================================

复制任务列表 = [
    {
        "源文件路径": r"Z:\Documents\chatlog\wxid_7u0rihcbbpbz12_ec5a\db_storage\contact\contact.db",
        "目标文件路径": r"C:\Users\LENOVO\Desktop\contact_内部专用2.db",
    },
    {
        "源文件路径": r"Z:\Documents\chatlog\wxid_42272spv9uq522_6ded\db_storage\contact\contact.db",
        "目标文件路径": r"C:\Users\LENOVO\Desktop\contact_内部专用.db",
    },
]


# ============================================================
# 二、工具函数
# ============================================================

def 格式化时间戳(时间戳):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(时间戳))


def 获取文件信息(文件路径):
    stat = os.stat(文件路径)
    return {
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "ctime": stat.st_ctime,
    }


def 等待源文件稳定(文件路径, 检查间隔秒=2, 最大重试次数=5):
    """
    连续两次检查文件大小和修改时间一致，认为文件已稳定
    """
    for _ in range(最大重试次数):
        第一次 = 获取文件信息(文件路径)

        time.sleep(检查间隔秒)

        第二次 = 获取文件信息(文件路径)

        if (
            第一次["size"] == 第二次["size"]
            and 第一次["mtime"] == 第二次["mtime"]
        ):
            return 第二次

    raise Exception(
        "源文件长时间不稳定，可能正在被写入，无法安全复制："
        + 文件路径
    )


# ============================================================
# 三、执行所有复制任务
# ============================================================

for 序号, 任务 in enumerate(复制任务列表, start=1):

    源文件路径 = 任务["源文件路径"]
    目标文件路径 = 任务["目标文件路径"]

    print("=" * 80)
    print(f"开始处理第 {序号} 个文件")
    print("源文件：", 源文件路径)
    print("目标文件：", 目标文件路径)

    # 检查源文件
    if not os.path.exists(源文件路径):
        raise FileNotFoundError("源文件不存在：" + 源文件路径)

    # 创建目标目录
    目标文件夹 = os.path.dirname(目标文件路径)

    if not os.path.exists(目标文件夹):
        os.makedirs(目标文件夹)

    # 等待文件稳定
    源文件信息 = 等待源文件稳定(源文件路径)

    # 执行复制
    shutil.copyfile(源文件路径, 目标文件路径)

    # 校验
    if not os.path.exists(目标文件路径):
        raise Exception("拷贝失败，目标文件不存在：" + 目标文件路径)

    目标文件信息 = 获取文件信息(目标文件路径)

    if 源文件信息["size"] != 目标文件信息["size"]:
        raise Exception(
            "拷贝后文件大小不一致："
            + f"源文件={源文件信息['size']}字节，"
            + f"目标文件={目标文件信息['size']}字节"
        )

    # 输出日志
    print("拷贝完成")

    print("【源文件信息】")
    print("大小：", 源文件信息["size"], "字节")
    print("修改时间：", 格式化时间戳(源文件信息["mtime"]))
    print("创建时间：", 格式化时间戳(源文件信息["ctime"]))

    print("【目标文件信息】")
    print("大小：", 目标文件信息["size"], "字节")
    print("修改时间：", 格式化时间戳(目标文件信息["mtime"]))
    print("创建时间：", 格式化时间戳(目标文件信息["ctime"]))

print("=" * 80)
print("全部文件复制完成")
