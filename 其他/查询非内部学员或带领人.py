import re

import pandas as pd
from pathlib import Path


def 解析工作表名称(输入文件路径: Path, 期望名称: str, 回退索引: int):
    """
    优先使用期望名称；若不存在则回退到固定索引，提升脚本兼容性。
    """
    excel_file = pd.ExcelFile(输入文件路径)
    if 期望名称 in excel_file.sheet_names:
        return 期望名称
    if 回退索引 < len(excel_file.sheet_names):
        return 回退索引
    raise ValueError(
        f"无法找到工作表：期望名称={期望名称}，且回退索引={回退索引} 超出范围。"
    )


def 标准化姓名序列(series: pd.Series) -> pd.Series:
    """
    对姓名做基础清洗，避免因空格或空值导致误判。
    """
    cleaned = series.fillna("").astype(str).str.strip()
    return cleaned[cleaned != ""]


def 提取姓名并标准化(series: pd.Series) -> pd.Series:
    """
    处理"编号+姓名"文本，提取所有真实姓名用于匹配。
    支持一个单元格含多个姓名（空格分隔），例如：
      ¿¿¿003891-韩嘉 韩彧     -> [韩嘉, 韩彧]
      ¿¿¿000030-韩鹤天 韩鹤鸣 -> [韩鹤天, 韩鹤鸣]
    """
    cleaned = series.fillna("").astype(str).str.strip()

    # 用 findall 提取每个单元格中所有中文姓名段（2~8字，含少数民族中点·）
    all_names = []
    for text in cleaned:
        names = re.findall(r"[\u4e00-\u9fff\u00b7\u30fb·]{2,8}", text)
        all_names.extend(names)

    result = pd.Series(all_names).str.strip() if all_names else pd.Series(dtype=str)
    return result[result != ""]


def 查询非内部学员或带领人(
    输入文件路径: Path,
    sheet1名称: str = "sheet1",
    sheet2名称: str = "sheet2",
) -> Path:
    """
    业务规则：
    - sheet1 的 A 列是待验证姓名
    - sheet2 的 A 列是专属带领人，B 列是内部学员
    - 找出 sheet1 A 列中既不在 sheet2 A 列、也不在 sheet2 B 列的整行数据
    """
    # 解析工作表：优先按名称，失败时回退到第1/第2个表
    sheet1选择器 = 解析工作表名称(输入文件路径, sheet1名称, 0)
    sheet2选择器 = 解析工作表名称(输入文件路径, sheet2名称, 1)

    # 读取两个工作表；header=None 按纯数据表处理，避免首行被误当表头
    sheet1_df = pd.read_excel(输入文件路径, sheet_name=sheet1选择器, header=None)
    sheet2_df = pd.read_excel(输入文件路径, sheet_name=sheet2选择器, header=None)

    # 基础列数校验，提前暴露数据结构问题
    if sheet1_df.shape[1] < 1:
        raise ValueError("sheet1 至少需要 1 列（A列姓名）。")
    if sheet2_df.shape[1] < 2:
        raise ValueError("sheet2 至少需要 2 列（A列专属带领人，B列内部学员）。")

    # 取出排除名单（sheet2 A列 + B列），按"编号+姓名"格式提取姓名后再匹配
    专属带领人 = 提取姓名并标准化(sheet2_df.iloc[:, 0])
    内部学员 = 提取姓名并标准化(sheet2_df.iloc[:, 1])
    排除姓名集合 = set(专属带领人.tolist()) | set(内部学员.tolist())

    # 使用 sheet1 原始行序构建掩码，确保输出"整行信息"
    原始姓名列 = sheet1_df.iloc[:, 0].fillna("").astype(str).str.strip()
    非内部且非带领人掩码 = (原始姓名列 != "") & (~原始姓名列.isin(排除姓名集合))
    非内部且非带领人_df = sheet1_df[非内部且非带领人掩码].copy()
    非内部且非带领人_df[sheet1_df.shape[1]] = "非内部学员且非带领人"

    # 新增业务结果：内部学员且非带领人（在B列且不在A列）
    内部且非带领人集合 = set(内部学员.tolist()) - set(专属带领人.tolist())
    内部且非带领人掩码 = (原始姓名列 != "") & (原始姓名列.isin(内部且非带领人集合))
    内部且非带领人_df = sheet1_df[内部且非带领人掩码].copy()
    内部且非带领人_df[sheet1_df.shape[1]] = "是内部学员但是非带领人"

    # 合并两类数据到同一个 sheet，并在最后一列标注分类
    合并结果_df = pd.concat([内部且非带领人_df, 非内部且非带领人_df], ignore_index=True)
    输出文件路径 = 输入文件路径.with_name(f"{输入文件路径.stem}_分类结果.xlsx")
    合并结果_df.to_excel(输出文件路径, index=False, header=False)

    # 控制台打印统计信息，便于快速核对
    print(f"输入文件：{输入文件路径}")
    print(f"输出文件：{输出文件路径}")
    print(f"sheet1总行数：{len(sheet1_df)}")
    print(f"是内部学员但是非带领人命中行数：{len(内部且非带领人_df)}")
    print(f"非内部学员且非带领人命中行数：{len(非内部且非带领人_df)}")
    print(f"合并总行数：{len(合并结果_df)}")
    print("结果预览（前10行）：")
    if 合并结果_df.empty:
        print("无符合条件的数据。")
    else:
        print(合并结果_df.head(10).to_string(index=False, header=False))

    return 输出文件路径


if __name__ == "__main__":
    # 按你的需求固定默认输入路径，双击或命令行执行即可
    默认输入文件 = Path(r"C:\Users\LENOVO\Downloads\活动vrua7报名记录.xls")

    if not 默认输入文件.exists():
        raise FileNotFoundError(f"未找到文件：{默认输入文件}")

    查询非内部学员或带领人(默认输入文件)
