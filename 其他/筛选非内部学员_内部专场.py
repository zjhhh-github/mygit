import re
from pathlib import Path

import pandas as pd


def 解析工作表(输入文件: Path, 期望名称: str, 回退索引: int):
    """
    优先按名称读取工作表；若名称不存在，则回退到固定索引。
    """
    excel_file = pd.ExcelFile(输入文件)
    if 期望名称 in excel_file.sheet_names:
        return 期望名称
    if 回退索引 < len(excel_file.sheet_names):
        return 回退索引
    raise ValueError(f"未找到工作表：{期望名称}，且回退索引 {回退索引} 不可用。")


def 提取姓名集合(内部名单_df: pd.DataFrame) -> set:
    """
    从“编号+姓名”文本中提取所有中文姓名，支持一个单元格多个姓名。
    """
    姓名集合 = set()
    for 列索引 in range(内部名单_df.shape[1]):
        文本序列 = 内部名单_df.iloc[:, 列索引].fillna("").astype(str).str.strip()
        for 文本 in 文本序列:
            # 提取每个单元格中的全部中文姓名（2~8字，支持少数民族中点）
            匹配姓名 = re.findall(r"[\u4e00-\u9fff\u00b7\u30fb·]{2,8}", 文本)
            for 姓名 in 匹配姓名:
                if 姓名:
                    姓名集合.add(姓名)
    return 姓名集合


def 标注是否内部学员(输入文件: Path) -> tuple:
    """
    业务规则：
    - sheet1 的 A 列是要核验的姓名
    - sheet2 是内部学员“编号+姓名”名单
    - 在 sheet1 最后一列标注是否内部学员，并导出明细
    """
    sheet1选择器 = 解析工作表(输入文件, "sheet1", 0)
    sheet2选择器 = 解析工作表(输入文件, "sheet2", 1)

    sheet1_df = pd.read_excel(输入文件, sheet_name=sheet1选择器, header=None)
    sheet2_df = pd.read_excel(输入文件, sheet_name=sheet2选择器, header=None)

    if sheet1_df.shape[1] < 1:
        raise ValueError("sheet1 至少需要 1 列（A列姓名）。")

    内部姓名集合 = 提取姓名集合(sheet2_df)
    if not 内部姓名集合:
        raise ValueError("sheet2 未提取到任何姓名，请检查名单格式。")

    姓名列 = sheet1_df.iloc[:, 0].fillna("").astype(str).str.strip()
    结果_df = sheet1_df.copy()
    标注列索引 = sheet1_df.shape[1]

    # 对每一行姓名做精确匹配，避免模糊包含带来的误判
    结果_df[标注列索引] = 姓名列.apply(
        lambda x: "是内部学员" if (x != "" and x in 内部姓名集合) else "非内部学员"
    )

    非内部_df = 结果_df[结果_df[标注列索引] == "非内部学员"].copy()

    全量输出路径 = 输入文件.with_name(f"{输入文件.stem}_内部名单核验结果.xlsx")
    非内部输出路径 = 输入文件.with_name(f"{输入文件.stem}_非内部学员.xlsx")
    结果_df.to_excel(全量输出路径, index=False, header=False)
    非内部_df.to_excel(非内部输出路径, index=False, header=False)

    print(f"输入文件：{输入文件}")
    print(f"全量核验输出：{全量输出路径}")
    print(f"非内部学员输出：{非内部输出路径}")
    print(f"sheet1总行数：{len(sheet1_df)}")
    print(f"识别到的内部姓名数量：{len(内部姓名集合)}")
    print(f"非内部学员行数：{len(非内部_df)}")

    return 全量输出路径, 非内部输出路径


if __name__ == "__main__":
    输入路径 = Path(r"C:\Users\LENOVO\Downloads\内部专场.xls")
    if not 输入路径.exists():
        raise FileNotFoundError(f"未找到文件：{输入路径}")
    标注是否内部学员(输入路径)
