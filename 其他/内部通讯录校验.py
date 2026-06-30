
# -*- coding: utf-8 -*-
"""
内部通讯录数据校验脚本

规则（按最新需求）：
1. 读取 Excel：C:\\Users\\LENOVO\\Desktop\\内部通讯录.xlsx
2. 表头在第二行（header=1）
3. 关键列：推荐、渠道C、带领C
4. 判断逻辑：
   - 若某推荐人的推荐次数 <= 5：
     其对应行要求“渠道C和带领C都不是该推荐人”，否则该行不正常。
   - 若某推荐人的推荐次数 > 5：
     该推荐人必须至少有5行满足“渠道C和带领C都不是该推荐人”；
     若不足5行，则该推荐人的全部相关行判为不正常。
5. 输出：不正常数据的 Excel 行号 + 整行内容
"""

import pandas as pd
import sys


EXCEL_PATH = r"C:\Users\LENOVO\Desktop\内部通讯录.xlsx"
HEADER_ROW_INDEX = 1  # 表头在第二行


def is_empty_or_cross(value: object) -> bool:
    """判断值是否为空或无推荐标记（❌）。"""
    if pd.isna(value):
        return True
    text = str(value).strip()
    return text == "" or text == "❌"


def main() -> None:
    # 兼容 Windows 控制台编码，避免打印中文/特殊符号时报错
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # 读取数据：header=1 表示第二行为列名
    df = pd.read_excel(EXCEL_PATH, header=HEADER_ROW_INDEX)

    required_cols = ["推荐", "渠道C", "带领C"]
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        raise ValueError("缺少必要列: {}".format(",".join(missing_cols)))

    # 仅统计“有效推荐人”（排除空值和❌）
    valid_recommender_mask = ~df["推荐"].apply(is_empty_or_cross)
    recommender_counts = df.loc[valid_recommender_mask, "推荐"].astype(str).value_counts()

    abnormal_index_set = set()

    # 逐个推荐人执行规则判断
    for recommender, count in recommender_counts.items():
        person_rows = df.index[df["推荐"].astype(str) == recommender].tolist()

        # 判定该推荐人关联行中，是否“渠道C和带领C都不是推荐人”
        non_self_rows = []
        for idx in person_rows:
            channel_c = str(df.at[idx, "渠道C"]).strip()
            leader_c = str(df.at[idx, "带领C"]).strip()
            if channel_c != recommender and leader_c != recommender:
                non_self_rows.append(idx)

        if int(count) <= 5:
            # <=5：每一行都必须非本人，否则该行异常
            for idx in person_rows:
                if idx not in non_self_rows:
                    abnormal_index_set.add(idx)
        else:
            # >5：必须至少有5行非本人；若不足，则该推荐人的相关行全部异常
            if len(non_self_rows) < 5:
                abnormal_index_set.update(person_rows)

    abnormal_indices = sorted(abnormal_index_set)

    print("总行数:", len(df))
    print("有效推荐人数量:", len(recommender_counts))
    print("不正常行数:", len(abnormal_indices))
    print("=" * 80)

    if not abnormal_indices:
        print("未发现不正常数据。")
        return

    def print_chinese_line(text: str) -> None:
        """
        优先按原中文输出。
        若控制台编码不支持，则使用替换策略兜底（不做 Unicode 转义）。
        """
        try:
            print(text)
        except UnicodeEncodeError:
            # 兜底：仍输出中文可见文本，不使用 \uXXXX 转义
            safe_text = text.encode("gbk", errors="replace").decode("gbk", errors="replace")
            print(safe_text)

    # 打印“Excel行号 + 整行”
    # 说明：DataFrame索引0对应Excel第3行（第2行为表头）
    for idx in abnormal_indices:
        excel_row_num = idx + 3
        row_dict = df.loc[idx].to_dict()
        print_chinese_line("Excel行号: {}".format(excel_row_num))
        print_chinese_line("整行数据: {}".format(row_dict))
        print_chinese_line("-" * 80)


if __name__ == "__main__":
    main()
