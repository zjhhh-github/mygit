"""读取售前通讯录 Excel 数据并转换为目标 JSON 结构。"""

import json
from pathlib import Path
import locale
from datetime import datetime, timedelta
import sqlite3
from typing import Set, List, Dict, Tuple, Optional

import pandas as pd


# 目标 Excel 文件路径：按你的要求读取该文件
EXCEL_PATH = Path(r"C:\Users\LENOVO\Desktop\售前通讯录.xlsx")
# 报名匹配数据库：从 contact 表的 remark 列读取可报名备注集合
CONTACT_DB_PATH = Path(r"C:\Users\LENOVO\Desktop\contact.db")
# 输出文件路径：不覆盖原始“数据结构图.json”，另存转换结果
OUTPUT_JSON_PATH = Path(r"D:\桌面文件\新建文件夹\数据结构处理\售前通讯录\数据结构图_转换结果.json")


def 读取售前通讯录数据(excel_path: Path) -> pd.DataFrame:
    """
    读取售前通讯录 Excel 数据。

    参数:
        excel_path: Excel 文件路径

    返回:
        读取后的 DataFrame
    """
    # 先做文件存在性校验，避免读取时报错信息不直观
    if not excel_path.exists():
        raise FileNotFoundError(f"未找到文件：{excel_path}")

    # 默认读取第一个工作表（sheet_name=0），保持行为简单稳定
    数据表 = pd.read_excel(excel_path, sheet_name=0)
    return 数据表


def 读取报名备注集合(db_path: Path) -> Set[str]:
    """
    从 SQLite 数据库读取 contact.remark，并构建匹配集合。

    规则：
    - 仅保留非空值
    - 统一去除前后空格
    - 默认大小写敏感（按你确认的“完全匹配”执行）
    """
    if not db_path.exists():
        raise FileNotFoundError(f"未找到数据库文件：{db_path}")

    备注集合: Set[str] = set()
    with sqlite3.connect(str(db_path)) as 连接:
        游标 = 连接.cursor()
        游标.execute("SELECT remark FROM contact")
        for (备注值,) in 游标.fetchall():
            if 备注值 is None:
                continue
            标准备注 = str(备注值).strip()
            if 标准备注:
                备注集合.add(标准备注)
    return 备注集合


def 提取首个非空值(行数据: pd.Series, 字段列表: List[str]) -> str:
    """
    按字段优先级提取首个非空值。

    说明：
    - 对 NaN、空字符串、仅空白字符串都视为“空”
    - 若全部为空，返回空字符串
    """
    for 字段名 in 字段列表:
        值 = 行数据.get(字段名)
        if pd.isna(值):
            continue
        文本值 = str(值).strip()
        if 文本值:
            return 文本值
    return ""


def 格式化绑定日期(原始值: object) -> str:
    """
    将对象(添加时间)转换为 YYYY/MM/DD hh:mm:ss。

    说明：
    - 可解析则输出如 2026/03/23 14:05:09
    - 不可解析或为空则输出空字符串
    """
    if pd.isna(原始值):
        return ""
    时间戳 = pd.to_datetime(原始值, errors="coerce")
    if pd.isna(时间戳):
        return ""
    # 统一输出“年/月/日 时:分:秒”，满足下游展示要求
    return 时间戳.strftime("%Y/%m/%d %H:%M:%S")


def 解析绑定周期天数(行数据: pd.Series) -> Optional[int]:
    """
    从行数据中解析绑定周期（单位：天）。

    说明：
    - 优先读取常见字段名：绑定周期 / 对象(绑定周期) / 来源(绑定周期)
    - 当源数据没有绑定周期时，按你的要求默认 90 天
    - 解析失败或为负数时同样回落到 90 天，保证流程稳定
    """
    候选字段 = ["绑定周期", "对象(绑定周期)", "来源(绑定周期)"]
    原始周期 = 提取首个非空值(行数据, 候选字段)
    if not 原始周期:
        return 90
    try:
        周期天数 = int(float(原始周期))
    except ValueError:
        return 90
    if 周期天数 < 0:
        return 90
    return 周期天数


def 解析绑定日期(绑定日期文本: str) -> Optional[datetime]:
    """
    解析绑定日期文本，兼容历史格式。

    支持：
    - YYYY/MM/DD hh:mm:ss
    - YYYYMMDD
    """
    if not 绑定日期文本:
        return None
    for 日期格式 in ("%Y/%m/%d %H:%M:%S", "%Y%m%d"):
        try:
            return datetime.strptime(绑定日期文本, 日期格式)
        except ValueError:
            continue
    return None


def 计算解绑日期(绑定日期: str, 绑定周期天数: int) -> str:
    """
    根据绑定日期和绑定周期计算解绑日期。

    规则：
    - 解绑日期 = 绑定日期 + 绑定周期(天)
    - 绑定日期无效时返回空字符串
    """
    绑定日期对象 = 解析绑定日期(绑定日期)
    if 绑定日期对象 is None:
        return ""
    解绑日期对象 = 绑定日期对象 + timedelta(days=绑定周期天数)
    return 解绑日期对象.strftime("%Y%m%d")


def 判定是否报名(行数据: pd.Series, 报名备注集合: Set[str]) -> str:
    """
    按单行规则判定是否报名。

    规则：
    - 对象(内部备注) 去前后空格后，存在于报名备注集合 => 已报名
    - 不存在或为空 => 未报名
    """
    内部备注 = 行数据.get("对象(内部备注)")
    if pd.isna(内部备注):
        return "未报名"
    标准备注 = str(内部备注).strip()
    if not 标准备注:
        return "未报名"
    return "已报名" if 标准备注 in 报名备注集合 else "未报名"


def 转换为目标结构(数据表: pd.DataFrame, 报名备注集合: Set[str]) -> List[dict]:
    """
    将原始表格转换为“意向学员 -> 来源列表”的目标结构。

    关键规则：
    - 意向学员微信号：对象(总微信号) -> 对象(微信号) -> 对象(微信ID)
    - 来源微信号：来源(总微信号) -> 来源(微信号) -> 来源(微信ID)
    - 绑定日期：对象(添加时间) 转 YYYY/MM/DD hh:mm:ss
    - 绑定周期：按字段读取，单位为天；缺失则留空
    - 解绑日期：绑定日期 + 绑定周期（天）
    - 绑定状态：同一学员多个来源在当前时间同时有效时，仅绑定日期最早的为“有绑定”
    - 是否报名：对象(内部备注) 在 contact.remark 集合中为“已报名”，否则“未报名”
    - 不丢弃空对象行：使用“未知学员_行号”作为保底键，避免不同空对象误合并
    """
    学员映射: Dict[str, dict] = {}

    for 行号, 行数据 in 数据表.iterrows():
        意向学员微信号 = 提取首个非空值(
            行数据,
            ["对象(总微信号)", "对象(微信号)", "对象(微信ID)"],
        )
        if not 意向学员微信号:
            # 用户要求不丢弃：用行号生成稳定保底键，避免把多个空对象合并成一人
            意向学员微信号 = f"未知学员_{行号 + 1}"

        来源微信号 = 提取首个非空值(
            行数据,
            ["来源(总微信号)", "来源(微信号)", "来源(微信ID)"],
        )
        绑定日期 = 格式化绑定日期(行数据.get("对象(添加时间)"))
        绑定周期天数 = 解析绑定周期天数(行数据)
        解绑日期 = 计算解绑日期(绑定日期, 绑定周期天数)
        是否报名 = 判定是否报名(行数据, 报名备注集合)

        来源项 = {
            "来源微信号": 来源微信号,
            "绑定日期": 绑定日期,
            "绑定周期": 绑定周期天数,
            "解绑日期": 解绑日期,
            "绑定状态": "无绑定",
        }

        if 意向学员微信号 not in 学员映射:
            学员映射[意向学员微信号] = {
                "意向学员微信号": 意向学员微信号,
                "是否报名": 是否报名,
                "来源": [],
            }
        else:
            # 按你确认的方案1：同一学员出现多行时，使用当前行判定值覆盖
            学员映射[意向学员微信号]["是否报名"] = 是否报名

        学员映射[意向学员微信号]["来源"].append(来源项)

    统一计算绑定状态(学员映射)
    return list(学员映射.values())


def 统一计算绑定状态(学员映射: Dict[str, dict]) -> None:
    """
    统一计算每个学员来源列表的绑定状态。

    规则：
    - 默认全部“无绑定”
    - 仅对“当前日期在 [绑定日期, 解绑日期] 内”的来源参与竞争
    - 若同时有效来源 >= 1，仅绑定日期最早的一条标记为“有绑定”
    - 绑定日期相同按来源在列表中的先后顺序保留第一条
    """
    当前时间 = datetime.now()

    for 学员数据 in 学员映射.values():
        来源列表 = 学员数据.get("来源", [])
        有效来源候选: List[Tuple[datetime, int]] = []

        for 索引, 来源项 in enumerate(来源列表):
            来源项["绑定状态"] = "无绑定"

            绑定日期对象 = 解析绑定日期(str(来源项.get("绑定日期", "")).strip())
            if 绑定日期对象 is None:
                continue

            解绑日期文本 = str(来源项.get("解绑日期", "")).strip()
            try:
                解绑日期对象 = datetime.strptime(解绑日期文本, "%Y%m%d")
            except ValueError:
                continue

            # 当前时间落在有效绑定窗口内，才进入“有绑定资格”集合
            if 绑定日期对象 <= 当前时间 <= 解绑日期对象:
                有效来源候选.append((绑定日期对象, 索引))

        if 有效来源候选:
            # 先按绑定日期升序，再按原始顺序升序（稳定且符合“最早优先”）
            有效来源候选.sort(key=lambda 项: (项[0], 项[1]))
            最早来源索引 = 有效来源候选[0][1]
            来源列表[最早来源索引]["绑定状态"] = "有绑定"


def 写入JSON文件(数据: List[dict], 输出路径: Path) -> None:
    """将转换结果写入 JSON 文件。"""
    输出路径.parent.mkdir(parents=True, exist_ok=True)
    with 输出路径.open("w", encoding="utf-8") as 文件:
        json.dump(数据, 文件, ensure_ascii=False, indent=2)


def 安全打印(内容: object) -> None:
    """
    兼容 Windows 控制台编码的打印函数。

    作用：
    - 避免控制台编码不支持某些字符时抛出 UnicodeEncodeError
    - 保证脚本在不同终端环境下都能稳定输出
    """
    文本 = str(内容)
    控制台编码 = locale.getpreferredencoding(False) or "utf-8"
    print(文本.encode(控制台编码, errors="replace").decode(控制台编码, errors="replace"))


def main() -> None:
    """脚本入口：读取 Excel、转换结构并导出 JSON。"""
    try:
        数据表 = 读取售前通讯录数据(EXCEL_PATH)
    except Exception as exc:  # 捕获异常并输出，便于快速定位问题
        print(f"读取失败：{exc}")
        return

    try:
        报名备注集合 = 读取报名备注集合(CONTACT_DB_PATH)
    except Exception as exc:  # 捕获异常并输出，便于快速定位问题
        print(f"读取数据库失败：{exc}")
        return

    转换结果 = 转换为目标结构(数据表, 报名备注集合)
    写入JSON文件(转换结果, OUTPUT_JSON_PATH)

    # 输出基础信息，便于你确认读取与转换是否正确
    安全打印("读取并转换成功")
    安全打印(f"文件路径：{EXCEL_PATH}")
    安全打印(f"数据库路径：{CONTACT_DB_PATH}")
    安全打印(f"报名备注数量：{len(报名备注集合)}")
    安全打印(f"数据行列：{数据表.shape[0]} 行, {数据表.shape[1]} 列")
    安全打印(f"转换后学员数量：{len(转换结果)}")
    安全打印(f"输出文件：{OUTPUT_JSON_PATH}")
    安全打印("转换结果前 2 条预览：")
    安全打印(json.dumps(转换结果[:2], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
