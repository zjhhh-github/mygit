# -*- coding: utf-8 -*-
import json
import os
import re
import shutil
import time
from datetime import datetime
from typing import Any, Iterable, List


def 是合法编号(编号: Any) -> bool:
    """
    判断传入内容是否为合法编号。
    合法编号规则：去掉头尾空格后，必须是连续 6 位数字。
    """
    if 编号 is None:
        return False
    return re.fullmatch(r"\d{6}", str(编号).strip()) is not None


def 清洗编号(编号: Any) -> str:
    return "" if 编号 is None else str(编号).strip()


def 分批(original_list: list, max_length: int = 1000) -> List[list]:
    if max_length <= 0:
        max_length = 1000
    return [original_list[i:i + max_length] for i in range(0, len(original_list), max_length)]


def 去重列表(items: Iterable[Any]) -> list:
    result = []
    seen = set()
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) if isinstance(item, (dict, list)) else item
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def 格式化时间戳(时间戳):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(时间戳))


def 获取文件信息(文件路径: str):
    stat = os.stat(文件路径)
    return {"size": stat.st_size, "mtime": stat.st_mtime, "ctime": stat.st_ctime}


def 等待源文件稳定(文件路径: str, 检查间隔秒: int = 2, 最大重试次数: int = 5):
    for _ in range(最大重试次数):
        第一次 = 获取文件信息(文件路径)
        time.sleep(检查间隔秒)
        第二次 = 获取文件信息(文件路径)
        if 第一次["size"] == 第二次["size"] and 第一次["mtime"] == 第二次["mtime"]:
            return 第二次
    raise RuntimeError("源文件长时间不稳定，可能正在被写入，无法安全复制：" + 文件路径)


def 复制文件任务(复制任务列表: list):
    for 序号, 任务 in enumerate(复制任务列表, start=1):
        源文件路径 = 任务["源文件路径"]
        目标文件路径 = 任务["目标文件路径"]
        print("=" * 80)
        print(f"开始处理第 {序号} 个文件")
        print("源文件：", 源文件路径)
        print("目标文件：", 目标文件路径)

        if not os.path.exists(源文件路径):
            raise FileNotFoundError("源文件不存在：" + 源文件路径)

        os.makedirs(os.path.dirname(目标文件路径), exist_ok=True)
        源文件信息 = 等待源文件稳定(源文件路径)
        shutil.copyfile(源文件路径, 目标文件路径)

        if not os.path.exists(目标文件路径):
            raise RuntimeError("拷贝失败，目标文件不存在：" + 目标文件路径)

        目标文件信息 = 获取文件信息(目标文件路径)
        if 源文件信息["size"] != 目标文件信息["size"]:
            raise RuntimeError(
                "拷贝后文件大小不一致："
                + f"源文件={源文件信息['size']}字节，目标文件={目标文件信息['size']}字节"
            )

        print("拷贝完成")
        print("【源文件信息】大小：", 源文件信息["size"], "修改时间：", 格式化时间戳(源文件信息["mtime"]))
        print("【目标文件信息】大小：", 目标文件信息["size"], "修改时间：", 格式化时间戳(目标文件信息["mtime"]))


def 备份_json(backup_dir: str, prefix: str, data: Any):
    if not backup_dir or not os.path.isdir(backup_dir):
        return None
    os.makedirs(backup_dir, exist_ok=True)
    时间结尾 = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(backup_dir, f"{prefix}{时间结尾}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path
