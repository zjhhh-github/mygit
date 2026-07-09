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


def 打印日志(*args, **kwargs):
    """
    统一的日志打印函数：在原有打印内容前加上“当前时间”，方便排查每条日志
    具体发生在什么时间点。用法和内置 print 完全一样，只是自动多了时间前缀。

    项目里其他文件只需要在导入时写：
        from utils import 打印日志 as print
    之后该文件里所有 print(...) 调用都会自动带上时间，不需要逐行修改。
    """
    时间字符串 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{时间字符串}]", *args, **kwargs)


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
        打印日志("=" * 80)
        打印日志(f"开始处理第 {序号} 个文件")
        打印日志("源文件：", 源文件路径)
        打印日志("目标文件：", 目标文件路径)

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

        打印日志("拷贝完成")
        打印日志("【源文件信息】大小：", 源文件信息["size"], "修改时间：", 格式化时间戳(源文件信息["mtime"]))
        打印日志("【目标文件信息】大小：", 目标文件信息["size"], "修改时间：", 格式化时间戳(目标文件信息["mtime"]))


def _查找最新备份数据库(备份根目录: str, 目录前缀: str) -> str:
    """
    在备份根目录下查找形如 "{目录前缀}yyyymmdd" 的备份目录：
    - 用正则匹配目录名末尾的 8 位日期
    - 按 yyyymmdd 字符串排序，取日期最大（最新）的一个
    - 返回该目录下 db_storage\\contact\\contact.db 的完整路径
    - 找不到任何合法备份目录或数据库文件，返回空字符串
    """
    if not 备份根目录 or not os.path.isdir(备份根目录):
        return ""

    目录日期匹配 = re.compile(r"^" + re.escape(目录前缀) + r"(\d{8})$")
    候选列表: List[tuple] = []
    for 子目录名 in os.listdir(备份根目录):
        子目录路径 = os.path.join(备份根目录, 子目录名)
        if not os.path.isdir(子目录路径):
            continue
        匹配结果 = 目录日期匹配.match(子目录名)
        if not 匹配结果:
            continue
        候选列表.append((匹配结果.group(1), 子目录路径))

    if not 候选列表:
        return ""

    候选列表.sort(key=lambda item: item[0])
    _最新日期, 最新目录路径 = 候选列表[-1]

    候选数据库路径 = os.path.join(最新目录路径, "db_storage", "contact", "contact.db")
    if os.path.isfile(候选数据库路径):
        return 候选数据库路径
    return ""


def 准备数据库(任务列表: list):
    """
    程序启动时的“数据库准备步骤”，在连接 SQLite 之前调用。

    每个任务按优先级依次尝试：
    1) 实时来源路径存在 -> 直接复制为最新数据
    2) 实时来源不存在 -> 去备份根目录找“目录前缀 + yyyymmdd”里日期最新的备份
    3) 备份也找不到 -> 若桌面目标文件已存在，则沿用现有文件（不报错）
    4) 桌面文件也不存在 -> 只打印提示，不中断程序（后续 SQLite 连接逻辑不变，
       该逻辑本身会在真正查询时按原有方式报错）

    任务字典字段：
    - 目标文件路径：最终提供给业务逻辑读取的 contact.db 路径
    - 实时来源路径：映射盘在线时的原始 contact.db
    - 备份根目录：存放按日期命名备份目录的根目录
    - 备份目录前缀：备份目录名中日期前面的固定前缀
    """
    for 任务 in 任务列表:
        目标文件路径 = 任务["目标文件路径"]
        实时来源路径 = 任务.get("实时来源路径", "")
        备份根目录 = 任务.get("备份根目录", "")
        备份目录前缀 = 任务.get("备份目录前缀", "")

        打印日志("[数据库准备]")

        if 实时来源路径 and os.path.isfile(实时来源路径):
            os.makedirs(os.path.dirname(目标文件路径), exist_ok=True)
            shutil.copy2(实时来源路径, 目标文件路径)
            打印日志("使用实时数据库：")
            打印日志(实时来源路径)
            continue

        打印日志("实时数据库不存在")
        备份数据库路径 = _查找最新备份数据库(备份根目录, 备份目录前缀)
        if 备份数据库路径:
            os.makedirs(os.path.dirname(目标文件路径), exist_ok=True)
            shutil.copy2(备份数据库路径, 目标文件路径)
            打印日志("使用最新备份：")
            打印日志(备份数据库路径)
            continue

        if os.path.isfile(目标文件路径):
            打印日志("没有找到新的数据库，使用桌面已有文件。")
            continue

        打印日志("数据库不存在：", 目标文件路径)


def 备份_json(backup_dir: str, prefix: str, data: Any):
    if not backup_dir or not os.path.isdir(backup_dir):
        return None
    os.makedirs(backup_dir, exist_ok=True)
    时间结尾 = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(backup_dir, f"{prefix}{时间结尾}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path
