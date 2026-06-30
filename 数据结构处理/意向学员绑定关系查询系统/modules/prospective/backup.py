# -*- coding: utf-8 -*-
"""
意向学员系统 —— 数据库备份模块
================================

本模块包含两类备份函数：

    1. backup_database(source_path, target_path)
       通用文件级备份：把任意 SQLite 数据库文件安全地复制到目标位置，
       自动创建目标目录、保留元数据、对常见异常做静默处理。

    2. backup_to_network(source_path, network_base)
       NAS 备份：在 backup_database 基础上，按 "原文件名_YYYYMMDD_HHMMSS.ext"
       生成带时间戳的目标文件名，写入 NAS 根目录。

设计要点：
    - 函数签名显式接受路径参数，不再依赖模块级全局变量；
      历史调用方（db_viewer.DatabaseViewer）通过自身模块顶部的常量做转发，
      可继续支持「外部覆写模块全局变量」这种依赖注入方式。
    - 任何异常都不会抛到调用方（保留原 db_viewer.py 的契约），失败仅返回 False。
"""

import os
import shutil
from datetime import datetime


def backup_database(source_path, target_path):
    """
    备份数据库文件（安全复制，保留元数据）

    Args:
        source_path: 源数据库文件路径
        target_path: 目标备份文件路径

    Returns:
        bool: 备份是否成功

    安全性:
        - 验证源文件存在且为文件
        - 自动创建目标目录
        - 使用 shutil.copy2 保留文件元数据
        - 静默处理异常，不影响主流程

    注意:
        - 如果目标文件已存在，会被覆盖
        - 备份失败不会抛出异常
    """
    try:
        # 验证源文件存在且不为空
        if not source_path or not os.path.exists(source_path):
            return False

        # 验证源文件是文件而非目录
        if not os.path.isfile(source_path):
            return False

        # 验证源文件大小（避免备份空文件或损坏文件）
        if os.path.getsize(source_path) == 0:
            return False

        # 创建目标文件夹（如果不存在）
        target_dir = os.path.dirname(target_path)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)

        # 复制文件（覆盖已存在的文件，保留元数据）
        shutil.copy2(source_path, target_path)

        # 验证备份文件是否成功创建
        if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
            return True
        return False

    except PermissionError as e:
        # 权限不足，记录到日志方便排错（不抛出，保持原有调用契约）
        print("[备份日志] backup_database 权限不足: {} -> {}: {}".format(source_path, target_path, e))
        return False
    except shutil.Error as e:
        # 文件复制错误
        print("[备份日志] backup_database 复制错误: {} -> {}: {}".format(source_path, target_path, e))
        return False
    except OSError as e:
        # 文件系统错误（含网络盘断开）
        print("[备份日志] backup_database 文件系统错误: {} -> {}: {}".format(source_path, target_path, e))
        return False
    except Exception as e:
        # 其他未预期的错误
        print("[备份日志] backup_database 未知异常: {}: {}".format(type(e).__name__, e))
        return False


def backup_to_network(source_path, network_base):
    """
    备份数据库到网络路径（带时间标签）

    Args:
        source_path: 源数据库文件路径
        network_base: NAS 根目录（例如 r"X:\\backup\\SuperXDatabase_Prospective"）

    Returns:
        bool: 备份是否成功

    文件命名规则:
        原文件: db_wxid_3iidhz1xmnta22.db3
        备份文件: db_wxid_3iidhz1xmnta22_20260125_143025.db3
        时间格式: YYYYMMDD_HHMMSS

    注意:
        - 网络路径不可访问时静默失败
        - 备份失败不影响主流程
        - 调用方通常用异步线程执行，不阻塞 UI
    """
    try:
        # 检查源文件是否存在且为文件
        if not source_path or not os.path.exists(source_path):
            return False
        if not os.path.isfile(source_path):
            return False

        # 检查网络备份路径是否可访问且为目录
        if not network_base:
            return False
        if not os.path.exists(network_base):
            return False
        if not os.path.isdir(network_base):
            return False

        # 生成带时间标签的备份文件名
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = os.path.basename(source_path)
        name, ext = os.path.splitext(filename)
        backup_filename = "{}_{}{}".format(name, timestamp, ext)

        # 构建完整的目标路径
        target_path = os.path.join(network_base, backup_filename)

        # 执行备份（复用通用备份函数）
        success = backup_database(source_path, target_path)

        if success:
            print("[备份日志] 网络备份成功: {}".format(backup_filename))
        else:
            print("[备份日志] 网络备份失败")

        return success

    except PermissionError as e:
        print("[备份日志] 网络备份权限不足: {}".format(e))
        return False
    except OSError as e:
        print("[备份日志] 网络备份文件系统错误: {}".format(e))
        return False
    except Exception as e:
        print("[备份日志] 网络备份未知异常: {}: {}".format(type(e).__name__, e))
        return False
