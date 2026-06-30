import pandas as pd
from pathlib import Path

# 可选依赖：缺失时不影响主统计流程（仅跳过对应出图）
try:
    import matplotlib.pyplot as plt  # pyright: ignore[reportMissingImports]
except ImportError:
    plt = None

try:
    import plotly.graph_objects as go  # pyright: ignore[reportMissingImports]
    from plotly.subplots import make_subplots  # pyright: ignore[reportMissingImports]
except ImportError:
    go = None
    make_subplots = None


# 输入文件与工作表配置
INPUT_FILE = Path(r"C:\Users\LENOVO\Desktop\报名录入.xlsx")
SHEET_NAME = "报名录入"
REFUND_SHEET_NAME = "退费"

# 输出文件路径（与输入文件同目录）
OUTPUT_FILE = INPUT_FILE.with_name("报名录入_新报续费统计.xlsx")
# 输出图片路径（与输入文件同目录）
CHART_FILE = INPUT_FILE.with_name("报名录入_新报续费统计图.png")
# 输出交互式 HTML 路径（与输入文件同目录）
HTML_FILE = INPUT_FILE.with_name("报名录入_新报续费统计图.html")
# 输出“11月后新报率/退费率”交互式HTML路径
RATE_HTML_FILE = INPUT_FILE.with_name("报名录入_11月后新报退费率图.html")
# 输出“11月后新报率/退费率”结果路径
RATE_OUTPUT_FILE = INPUT_FILE.with_name("报名录入_11月后新报退费率统计.xlsx")


def parse_yyyymmdd(value):
    """
    将单元格值解析为日期。
    规则：
    1) 优先按 yyyymmdd 解析（例如 20260329）
    2) 若本身已是日期类型，也兼容解析
    3) 无法解析返回 NaT
    """
    # 空值直接返回 NaT，便于后续统一过滤
    if pd.isna(value):
        return pd.NaT

    # 先转字符串并去除空白，兼容数值与文本两种来源
    text = str(value).strip()
    if not text:
        return pd.NaT

    # 兼容 Excel 里可能出现的浮点文本，如 "20260329.0"
    if text.endswith(".0"):
        text = text[:-2]

    # 保守策略：只接受 8 位数字作为 yyyymmdd
    if text.isdigit() and len(text) == 8:
        return pd.to_datetime(text, format="%Y%m%d", errors="coerce")

    # 兜底兼容：如果单元格本身就是日期/时间格式，尽量解析
    return pd.to_datetime(value, errors="coerce")


def build_week_key(date_series):
    """
    生成自然周键值（周一到周日）。
    使用 W-SUN 频率，表示每周以周日结束，即周一起始。
    """
    # 转成周期后，再取每周起止日期，保证展示口径清晰
    period = date_series.dt.to_period("W-SUN")
    week_start = period.dt.start_time.dt.strftime("%Y-%m-%d")
    week_end = period.dt.end_time.dt.strftime("%Y-%m-%d")
    return week_start + "~" + week_end


def find_column_by_keywords(columns, keywords):
    """
    在列名中按关键字模糊匹配目标列。
    用于兼容表头存在空格或轻微命名差异的情况。
    """
    for col in columns:
        col_text = str(col).strip()
        if any(keyword in col_text for keyword in keywords):
            return col
    return None


def resolve_sheet_name(file_path, preferred_name, fallback_keywords):
    """
    解析工作表名称：
    1) 优先使用首选名称
    2) 不存在时按关键字在所有 sheet 名中模糊匹配
    """
    excel_file = pd.ExcelFile(file_path)
    all_sheet_names = excel_file.sheet_names
    if preferred_name in all_sheet_names:
        return preferred_name

    for sheet_name in all_sheet_names:
        if any(keyword in str(sheet_name) for keyword in fallback_keywords):
            return sheet_name

    raise ValueError(
        f"未找到退费sheet。当前可用sheet为：{all_sheet_names}；"
        f"请确认是否存在“{preferred_name}”或包含关键字 {fallback_keywords} 的工作表。"
    )


def find_sheet_and_column_by_keywords(file_path, keywords, header_row=1):
    """
    在所有 sheet 中查找包含指定关键字列的工作表与列名。
    返回 (sheet_name, column_name)。
    """
    excel_file = pd.ExcelFile(file_path)
    for sheet_name in excel_file.sheet_names:
        try:
            preview_df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row, nrows=0)
            target_col = find_column_by_keywords(preview_df.columns, keywords)
            if target_col is not None:
                return sheet_name, target_col
        except Exception:
            # 个别sheet可能存在格式异常，跳过继续尝试
            continue

    raise ValueError(
        f"未在任一sheet中找到包含关键字 {keywords} 的列，"
        "请检查表头行是否正确（当前按第2行为表头）。"
    )


def determine_november_cutoff(dates):
    """
    计算“11月份后”的起始时间：
    以数据最大日期为参考，取其最近的 11 月 1 日。
    例如最大日期是 2026-03，则起始日为 2025-11-01。
    """
    max_date = dates.max()
    if pd.isna(max_date):
        return pd.NaT

    target_year = max_date.year if max_date.month >= 11 else max_date.year - 1
    return pd.Timestamp(year=target_year, month=11, day=1)


def build_new_refund_rate_tables(signup_df, refund_df):
    """
    生成“11月后每周/每月 新报率与退费率”统计表。
    口径说明：
    1) 新报人次：报名录入中 G 列为“新”的记录数
    2) 退费人次：退费 sheet 中“申请时间”对应记录数
    3) 新报率 = 新报人次 / (新报人次 + 退费人次)
    4) 退费率 = 退费人次 / (新报人次 + 退费人次)
    """
    # 报名录入：只取新报数据
    signup_stat = signup_df.copy()
    signup_stat["新旧标记"] = signup_stat["新旧标记"].astype(str).str.strip()
    signup_stat = signup_stat[signup_stat["新旧标记"] == "新"].copy()
    signup_stat["报名日期"] = signup_stat["报名日期"].apply(parse_yyyymmdd)
    signup_stat = signup_stat.dropna(subset=["报名日期"]).copy()

    # 退费：申请时间作为退费时间
    refund_stat = refund_df.copy()
    refund_stat["退费时间"] = refund_stat["退费时间"].apply(parse_yyyymmdd)
    refund_stat = refund_stat.dropna(subset=["退费时间"]).copy()

    # 计算“11月份后”起始日，并统一过滤
    combined_dates = pd.concat(
        [signup_stat["报名日期"], refund_stat["退费时间"]], ignore_index=True
    )
    cutoff_date = determine_november_cutoff(combined_dates)
    if pd.isna(cutoff_date):
        raise ValueError("无法计算11月份后起始日期：新报与退费日期均为空。")

    signup_stat = signup_stat[signup_stat["报名日期"] >= cutoff_date].copy()
    refund_stat = refund_stat[refund_stat["退费时间"] >= cutoff_date].copy()

    # 构造周/月键
    signup_stat["周"] = build_week_key(signup_stat["报名日期"])
    signup_stat["月"] = signup_stat["报名日期"].dt.strftime("%Y-%m")
    refund_stat["周"] = build_week_key(refund_stat["退费时间"])
    refund_stat["月"] = refund_stat["退费时间"].dt.strftime("%Y-%m")

    # 每周新报/退费人次（先构造周期并集，避免空数据时列名丢失）
    weekly_new = signup_stat.groupby("周").size().rename("新报人次").reset_index()
    weekly_refund = refund_stat.groupby("周").size().rename("退费人次").reset_index()
    weekly_periods = sorted(set(signup_stat["周"]) | set(refund_stat["周"]))
    weekly_rate = pd.DataFrame({"周": weekly_periods})
    weekly_rate = weekly_rate.merge(weekly_new, on="周", how="left")
    weekly_rate = weekly_rate.merge(weekly_refund, on="周", how="left")
    weekly_rate[["新报人次", "退费人次"]] = (
        weekly_rate[["新报人次", "退费人次"]].fillna(0).astype(int)
    )
    weekly_rate["总事件人次"] = weekly_rate["新报人次"] + weekly_rate["退费人次"]
    weekly_rate["新报率"] = (
        weekly_rate["新报人次"] / weekly_rate["总事件人次"]
    ).where(weekly_rate["总事件人次"] > 0)
    weekly_rate["退费率"] = (
        weekly_rate["退费人次"] / weekly_rate["总事件人次"]
    ).where(weekly_rate["总事件人次"] > 0)

    # 每月新报/退费人次（先构造周期并集，避免空数据时列名丢失）
    monthly_new = signup_stat.groupby("月").size().rename("新报人次").reset_index()
    monthly_refund = refund_stat.groupby("月").size().rename("退费人次").reset_index()
    monthly_periods = sorted(set(signup_stat["月"]) | set(refund_stat["月"]))
    monthly_rate = pd.DataFrame({"月": monthly_periods})
    monthly_rate = monthly_rate.merge(monthly_new, on="月", how="left")
    monthly_rate = monthly_rate.merge(monthly_refund, on="月", how="left")
    monthly_rate[["新报人次", "退费人次"]] = (
        monthly_rate[["新报人次", "退费人次"]].fillna(0).astype(int)
    )
    monthly_rate["总事件人次"] = monthly_rate["新报人次"] + monthly_rate["退费人次"]
    monthly_rate["新报率"] = (
        monthly_rate["新报人次"] / monthly_rate["总事件人次"]
    ).where(monthly_rate["总事件人次"] > 0)
    monthly_rate["退费率"] = (
        monthly_rate["退费人次"] / monthly_rate["总事件人次"]
    ).where(monthly_rate["总事件人次"] > 0)

    # 增加百分比字符串列，便于业务查看
    weekly_rate["新报率(%)"] = (weekly_rate["新报率"] * 100).round(2).map(
        lambda x: f"{x:.2f}%" if pd.notna(x) else ""
    )
    weekly_rate["退费率(%)"] = (weekly_rate["退费率"] * 100).round(2).map(
        lambda x: f"{x:.2f}%" if pd.notna(x) else ""
    )
    monthly_rate["新报率(%)"] = (monthly_rate["新报率"] * 100).round(2).map(
        lambda x: f"{x:.2f}%" if pd.notna(x) else ""
    )
    monthly_rate["退费率(%)"] = (monthly_rate["退费率"] * 100).round(2).map(
        lambda x: f"{x:.2f}%" if pd.notna(x) else ""
    )

    return weekly_rate, monthly_rate, cutoff_date


def draw_chart(weekly_df, monthly_df):
    """
    绘制并保存统计图。
    图形说明：
    1) 上图：每周新报/续费折线 + 总人次柱状图
    2) 下图：每月新报/续费折线 + 总人次柱状图
    """
    # matplotlib 未安装时，返回 False 供主流程给出提示
    if plt is None:
        return False

    # 设置中文字体，避免中文标题乱码
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False

    # 创建两行子图，共享统一风格，便于对比周/月趋势
    fig, axes = plt.subplots(2, 1, figsize=(16, 10), dpi=120)

    # 统一将横轴日期格式化为 yyyymmdd（周使用周开始日期，月使用当月第一天）
    weekly_start_date = pd.to_datetime(
        weekly_df["周"].str.split("~").str[0], errors="coerce"
    )
    monthly_start_date = pd.to_datetime(
        monthly_df["月"] + "-01", errors="coerce"
    )
    weekly_axis_date = weekly_start_date.dt.strftime("%Y%m%d")
    monthly_axis_date = monthly_start_date.dt.strftime("%Y%m%d")
    # 解析失败时兜底为原值，避免极端脏数据导致空标签
    weekly_axis_date = weekly_axis_date.fillna(weekly_df["周"])
    monthly_axis_date = monthly_axis_date.fillna(monthly_df["月"])

    # 计算总人次同比增长率（周同比：向前 52 周；月同比：向前 12 个月）
    weekly_total_map = pd.Series(weekly_df["总人次"].values, index=weekly_start_date)
    monthly_total_map = pd.Series(monthly_df["总人次"].values, index=monthly_start_date)

    weekly_last_year = (weekly_start_date - pd.DateOffset(weeks=52)).map(weekly_total_map)
    monthly_last_year = (monthly_start_date - pd.DateOffset(years=1)).map(monthly_total_map)

    weekly_yoy = ((weekly_df["总人次"] - weekly_last_year) / weekly_last_year * 100).where(
        weekly_last_year > 0
    )
    monthly_yoy = ((monthly_df["总人次"] - monthly_last_year) / monthly_last_year * 100).where(
        monthly_last_year > 0
    )

    # ---------- 每周统计图 ----------
    # 先画总人次柱状图，再叠加新报/续费折线，避免柱子遮挡折线标记
    axes[0].bar(
        weekly_axis_date,
        weekly_df["总人次"],
        alpha=0.35,
        label="总人次",
        color="#7FB3D5",
    )
    axes[0].plot(
        weekly_axis_date,
        weekly_df["新报人次"],
        marker="o",
        linewidth=1.8,
        label="新报人次",
    )
    axes[0].plot(
        weekly_axis_date,
        weekly_df["续费人次"],
        marker="s",
        linewidth=1.8,
        label="续费人次",
    )
    axes[0].set_title("每周新报/续费（折线）+总人次（柱状）")
    axes[0].set_xlabel("周开始日期（yyyymmdd）")
    axes[0].set_ylabel("人次")
    axes[0].grid(alpha=0.3)
    # 右侧副轴显示同比增长率曲线，避免与人次量纲混用
    ax0_right = axes[0].twinx()
    ax0_right.plot(
        weekly_axis_date,
        weekly_yoy,
        marker="^",
        linestyle="--",
        linewidth=1.6,
        color="#C0392B",
        label="总人次同比增长率",
    )
    ax0_right.set_ylabel("同比增长率(%)")
    ax0_right.axhline(0, color="#C0392B", linewidth=0.8, alpha=0.25)
    # 合并主轴与副轴图例，统一放到右侧
    h0, l0 = axes[0].get_legend_handles_labels()
    h0r, l0r = ax0_right.get_legend_handles_labels()
    axes[0].legend(
        h0 + h0r,
        l0 + l0r,
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
        borderaxespad=0.0,
    )
    # 周粒度标签较长，旋转以避免重叠
    axes[0].tick_params(axis="x", rotation=45)

    # ---------- 每月统计图 ----------
    axes[1].bar(
        monthly_axis_date,
        monthly_df["总人次"],
        alpha=0.35,
        label="总人次",
        color="#A9DFBF",
    )
    axes[1].plot(
        monthly_axis_date,
        monthly_df["新报人次"],
        marker="o",
        linewidth=2.0,
        label="新报人次",
    )
    axes[1].plot(
        monthly_axis_date,
        monthly_df["续费人次"],
        marker="s",
        linewidth=2.0,
        label="续费人次",
    )
    axes[1].set_title("每月新报/续费（折线）+总人次（柱状）")
    axes[1].set_xlabel("月份（yyyymmdd，按当月第一天）")
    axes[1].set_ylabel("人次")
    axes[1].grid(alpha=0.3)
    # 右侧副轴显示同比增长率曲线
    ax1_right = axes[1].twinx()
    ax1_right.plot(
        monthly_axis_date,
        monthly_yoy,
        marker="^",
        linestyle="--",
        linewidth=1.6,
        color="#8E44AD",
        label="总人次同比增长率",
    )
    ax1_right.set_ylabel("同比增长率(%)")
    ax1_right.axhline(0, color="#8E44AD", linewidth=0.8, alpha=0.25)
    # 合并主轴与副轴图例，统一放到右侧
    h1, l1 = axes[1].get_legend_handles_labels()
    h1r, l1r = ax1_right.get_legend_handles_labels()
    axes[1].legend(
        h1 + h1r,
        l1 + l1r,
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
        borderaxespad=0.0,
    )
    axes[1].tick_params(axis="x", rotation=30)

    # 自动调整布局并保存图片
    # 右侧预留空间给图例，避免被裁剪
    fig.tight_layout(rect=(0, 0, 0.86, 1))
    fig.savefig(CHART_FILE, bbox_inches="tight")
    plt.close(fig)
    return True


def draw_interactive_html(weekly_df, monthly_df):
    """
    生成可交互式 HTML 图表。
    功能说明：
    1) 支持缩放、平移、悬浮提示、图例开关
    2) 上图展示每周数据，下图展示每月数据
    3) 每张图包含新报/续费折线和总人次柱状图
    """
    # plotly 未安装时，返回 False 供主流程给出提示
    if go is None or make_subplots is None:
        return False

    # 复制数据，避免修改主流程中的原始统计结果
    weekly_plot = weekly_df.copy()
    monthly_plot = monthly_df.copy()

    # 将周区间拆分出“周开始日期”，用于时间轴排序与交互缩放
    weekly_plot["周开始日期"] = pd.to_datetime(
        weekly_plot["周"].str.split("~").str[0], errors="coerce"
    )
    # 将月份转换为每月第一天，保证时间轴可连续缩放
    monthly_plot["月日期"] = pd.to_datetime(monthly_plot["月"] + "-01", errors="coerce")

    # 按时间排序，避免图线因顺序问题折返
    weekly_plot = weekly_plot.sort_values("周开始日期")
    monthly_plot = monthly_plot.sort_values("月日期")

    # 计算总人次同比增长率（周同比：向前 52 周；月同比：向前 12 个月）
    weekly_total_map = pd.Series(weekly_plot["总人次"].values, index=weekly_plot["周开始日期"])
    monthly_total_map = pd.Series(monthly_plot["总人次"].values, index=monthly_plot["月日期"])
    weekly_last_year = (weekly_plot["周开始日期"] - pd.DateOffset(weeks=52)).map(weekly_total_map)
    monthly_last_year = (monthly_plot["月日期"] - pd.DateOffset(years=1)).map(monthly_total_map)
    weekly_plot["总人次同比增长率"] = (
        (weekly_plot["总人次"] - weekly_last_year) / weekly_last_year * 100
    ).where(weekly_last_year > 0)
    monthly_plot["总人次同比增长率"] = (
        (monthly_plot["总人次"] - monthly_last_year) / monthly_last_year * 100
    ).where(monthly_last_year > 0)

    # 创建上下两个子图：每周趋势 + 每月趋势
    fig = make_subplots(
        rows=2,
        cols=1,
        specs=[[{"secondary_y": True}], [{"secondary_y": True}]],
        shared_xaxes=False,
        vertical_spacing=0.14,
        subplot_titles=("每周：新报/续费（折线）+总人次（柱状）", "每月：新报/续费（折线）+总人次（柱状）"),
    )

    # ---------- 上图：每周 ----------
    fig.add_trace(
        go.Bar(
            x=weekly_plot["周开始日期"],
            y=weekly_plot["总人次"],
            name="每周-总人次",
            customdata=weekly_plot["周"],
            opacity=0.35,
            hovertemplate="周区间: %{customdata}<br>总人次: %{y}<extra></extra>",
        ),
        row=1,
        col=1,
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=weekly_plot["周开始日期"],
            y=weekly_plot["新报人次"],
            mode="lines+markers",
            name="每周-新报人次",
            customdata=weekly_plot["周"],
            hovertemplate="周区间: %{customdata}<br>新报人次: %{y}<extra></extra>",
        ),
        row=1,
        col=1,
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=weekly_plot["周开始日期"],
            y=weekly_plot["续费人次"],
            mode="lines+markers",
            name="每周-续费人次",
            customdata=weekly_plot["周"],
            hovertemplate="周区间: %{customdata}<br>续费人次: %{y}<extra></extra>",
        ),
        row=1,
        col=1,
        secondary_y=False,
    )
    # 每周同比增长率曲线（右轴）
    fig.add_trace(
        go.Scatter(
            x=weekly_plot["周开始日期"],
            y=weekly_plot["总人次同比增长率"],
            mode="lines+markers",
            name="每周-总人次同比增长率",
            customdata=weekly_plot["周"],
            hovertemplate="周区间: %{customdata}<br>同比增长率: %{y:.2f}%<extra></extra>",
            line=dict(dash="dash"),
        ),
        row=1,
        col=1,
        secondary_y=True,
    )
    # ---------- 下图：每月 ----------
    fig.add_trace(
        go.Bar(
            x=monthly_plot["月日期"],
            y=monthly_plot["总人次"],
            name="每月-总人次",
            customdata=monthly_plot["月"],
            opacity=0.35,
            hovertemplate="月份: %{customdata}<br>总人次: %{y}<extra></extra>",
        ),
        row=2,
        col=1,
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=monthly_plot["月日期"],
            y=monthly_plot["新报人次"],
            mode="lines+markers",
            name="每月-新报人次",
            customdata=monthly_plot["月"],
            hovertemplate="月份: %{customdata}<br>新报人次: %{y}<extra></extra>",
        ),
        row=2,
        col=1,
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=monthly_plot["月日期"],
            y=monthly_plot["续费人次"],
            mode="lines+markers",
            name="每月-续费人次",
            customdata=monthly_plot["月"],
            hovertemplate="月份: %{customdata}<br>续费人次: %{y}<extra></extra>",
        ),
        row=2,
        col=1,
        secondary_y=False,
    )
    # 每月同比增长率曲线（右轴）
    fig.add_trace(
        go.Scatter(
            x=monthly_plot["月日期"],
            y=monthly_plot["总人次同比增长率"],
            mode="lines+markers",
            name="每月-总人次同比增长率",
            customdata=monthly_plot["月"],
            hovertemplate="月份: %{customdata}<br>同比增长率: %{y:.2f}%<extra></extra>",
            line=dict(dash="dash"),
        ),
        row=2,
        col=1,
        secondary_y=True,
    )
    # 全局样式与交互设置
    fig.update_layout(
        title="报名新报/续费趋势（交互式）",
        template="plotly_white",
        height=900,
        hovermode="x unified",
        # 交互图图例固定到右侧
        legend=dict(
            orientation="v",
            yanchor="top",
            y=1.0,
            xanchor="left",
            x=1.02,
        ),
        margin=dict(r=220),
    )

    # 统一定义底部滑条样式：进一步弱化迷你图视觉（更薄、更浅、更弱边框）
    slider_style = dict(
        visible=True,
        thickness=0.05,
        bgcolor="#FFFFFF",
        bordercolor="#EEF2F7",
        borderwidth=1,
    )

    # X 轴启用 rangeslider，方便拖拽查看局部区间
    fig.update_xaxes(
        title_text="周开始日期（yyyymmdd）",
        tickformat="%Y%m%d",
        row=1,
        col=1,
        rangeslider=slider_style,
    )
    fig.update_xaxes(
        title_text="月份（yyyymmdd，按当月第一天）",
        tickformat="%Y%m%d",
        row=2,
        col=1,
        rangeslider=slider_style,
    )
    fig.update_yaxes(title_text="人次", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="同比增长率(%)", row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="人次", row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text="同比增长率(%)", row=2, col=1, secondary_y=True)

    # 保存为自包含 HTML，双击即可离线打开
    fig.write_html(str(HTML_FILE), include_plotlyjs=True, full_html=True)
    return True


def draw_rate_interactive_html(weekly_rate_df, monthly_rate_df, cutoff_date):
    """
    生成“11月后每周/每月新报率与退费率”交互式 HTML 图表。
    图形说明：
    1) 上图：每周新报率/退费率
    2) 下图：每月新报率/退费率
    """
    # plotly 未安装时，返回 False 供主流程给出提示
    if go is None or make_subplots is None:
        return False

    weekly_plot = weekly_rate_df.copy()
    monthly_plot = monthly_rate_df.copy()

    # 统一横轴日期：周取周开始日期，月取当月第一天，并显示为 yyyymmdd
    weekly_plot["周开始日期"] = pd.to_datetime(
        weekly_plot["周"].astype(str).str.split("~").str[0], errors="coerce"
    )
    monthly_plot["月日期"] = pd.to_datetime(monthly_plot["月"] + "-01", errors="coerce")
    weekly_plot = weekly_plot.sort_values("周开始日期")
    monthly_plot = monthly_plot.sort_values("月日期")

    # 百分比轴使用 0~100 的展示刻度
    weekly_plot["新报率百分比"] = weekly_plot["新报率"] * 100
    weekly_plot["退费率百分比"] = weekly_plot["退费率"] * 100
    monthly_plot["新报率百分比"] = monthly_plot["新报率"] * 100
    monthly_plot["退费率百分比"] = monthly_plot["退费率"] * 100

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=False,
        vertical_spacing=0.14,
        subplot_titles=(
            "11月后每周新报率/退费率",
            "11月后每月新报率/退费率",
        ),
    )

    # 每周费率曲线
    fig.add_trace(
        go.Scatter(
            x=weekly_plot["周开始日期"],
            y=weekly_plot["新报率百分比"],
            mode="lines+markers",
            name="每周-新报率",
            customdata=weekly_plot["周"],
            hovertemplate="周区间: %{customdata}<br>新报率: %{y:.2f}%<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=weekly_plot["周开始日期"],
            y=weekly_plot["退费率百分比"],
            mode="lines+markers",
            name="每周-退费率",
            customdata=weekly_plot["周"],
            hovertemplate="周区间: %{customdata}<br>退费率: %{y:.2f}%<extra></extra>",
        ),
        row=1,
        col=1,
    )

    # 每月费率曲线
    fig.add_trace(
        go.Scatter(
            x=monthly_plot["月日期"],
            y=monthly_plot["新报率百分比"],
            mode="lines+markers",
            name="每月-新报率",
            customdata=monthly_plot["月"],
            hovertemplate="月份: %{customdata}<br>新报率: %{y:.2f}%<extra></extra>",
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=monthly_plot["月日期"],
            y=monthly_plot["退费率百分比"],
            mode="lines+markers",
            name="每月-退费率",
            customdata=monthly_plot["月"],
            hovertemplate="月份: %{customdata}<br>退费率: %{y:.2f}%<extra></extra>",
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        title=f"11月后新报率/退费率趋势（起始日：{cutoff_date.strftime('%Y-%m-%d')}）",
        template="plotly_white",
        height=900,
        hovermode="x unified",
        legend=dict(orientation="v", yanchor="top", y=1.0, xanchor="left", x=1.02),
        margin=dict(r=220),
    )

    # 滑条样式与主图统一
    slider_style = dict(
        visible=True,
        thickness=0.05,
        bgcolor="#FFFFFF",
        bordercolor="#EEF2F7",
        borderwidth=1,
    )
    fig.update_xaxes(
        title_text="周开始日期（yyyymmdd）",
        tickformat="%Y%m%d",
        row=1,
        col=1,
        rangeslider=slider_style,
    )
    fig.update_xaxes(
        title_text="月份（yyyymmdd，按当月第一天）",
        tickformat="%Y%m%d",
        row=2,
        col=1,
        rangeslider=slider_style,
    )
    fig.update_yaxes(title_text="比率(%)", ticksuffix="%", row=1, col=1)
    fig.update_yaxes(title_text="比率(%)", ticksuffix="%", row=2, col=1)

    fig.write_html(str(RATE_HTML_FILE), include_plotlyjs=True, full_html=True)
    return True


def main():
    # 1) 读取 Excel：header=1 表示第 2 行为表头（0 基索引）
    df = pd.read_excel(INPUT_FILE, sheet_name=SHEET_NAME, header=1)

    # 2) 按列位置取数据：B 列(索引1)为时间，G 列(索引6)为新旧标记
    #    这里用列位置而非列名，避免表头文案微调导致脚本失效
    if df.shape[1] < 7:
        raise ValueError("表格列数不足，无法读取 B 列和 G 列。")

    stat_df = pd.DataFrame(
        {
            "报名日期": df.iloc[:, 1],
            "新旧标记": df.iloc[:, 6],
        }
    )

    # 2.1) 读取“退费”sheet（数据从第3行开始 => 第2行为表头）
    refund_sheet_name, refund_time_col = find_sheet_and_column_by_keywords(
        INPUT_FILE, keywords=["申请时间"], header_row=1
    )
    refund_raw_df = pd.read_excel(INPUT_FILE, sheet_name=refund_sheet_name, header=1)
    refund_df = pd.DataFrame({"退费时间": refund_raw_df[refund_time_col]})
    print(f"已识别退费sheet：{refund_sheet_name}，退费时间列：{refund_time_col}")

    # 3) 清洗 G 列：只保留 新/旧，其他值视为无效并过滤
    stat_df["新旧标记"] = stat_df["新旧标记"].astype(str).str.strip()
    stat_df = stat_df[stat_df["新旧标记"].isin(["新", "旧"])].copy()

    # 4) 解析 B 列日期并过滤无效值
    stat_df["报名日期"] = stat_df["报名日期"].apply(parse_yyyymmdd)
    stat_df = stat_df.dropna(subset=["报名日期"]).copy()

    # 5) 生成统计维度字段
    stat_df["周"] = build_week_key(stat_df["报名日期"])
    stat_df["月"] = stat_df["报名日期"].dt.strftime("%Y-%m")

    # 6) 每周统计：新报/续费人次（计数）
    weekly = (
        stat_df.groupby(["周", "新旧标记"])
        .size()
        .unstack(fill_value=0)
        .rename(columns={"新": "新报人次", "旧": "续费人次"})
        .reset_index()
    )
    if "新报人次" not in weekly.columns:
        weekly["新报人次"] = 0
    if "续费人次" not in weekly.columns:
        weekly["续费人次"] = 0
    weekly["总人次"] = weekly["新报人次"] + weekly["续费人次"]
    weekly = weekly[["周", "新报人次", "续费人次", "总人次"]].sort_values("周")

    # 7) 每月统计：新报/续费人次（计数）
    monthly = (
        stat_df.groupby(["月", "新旧标记"])
        .size()
        .unstack(fill_value=0)
        .rename(columns={"新": "新报人次", "旧": "续费人次"})
        .reset_index()
    )
    if "新报人次" not in monthly.columns:
        monthly["新报人次"] = 0
    if "续费人次" not in monthly.columns:
        monthly["续费人次"] = 0
    monthly["总人次"] = monthly["新报人次"] + monthly["续费人次"]
    monthly = monthly[["月", "新报人次", "续费人次", "总人次"]].sort_values("月")

    # 7.1) 特殊情况统计：11月后每周/每月 退费率和新报率
    weekly_rate, monthly_rate, cutoff_date = build_new_refund_rate_tables(stat_df, refund_df)

    # 8) 导出结果到同一个 Excel（两个 sheet）
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        weekly.to_excel(writer, sheet_name="每周统计", index=False)
        monthly.to_excel(writer, sheet_name="每月统计", index=False)

    # 8.1) 导出“11月后新报率/退费率”到单独文件
    with pd.ExcelWriter(RATE_OUTPUT_FILE, engine="openpyxl") as writer:
        weekly_rate.to_excel(writer, sheet_name="11月后每周新报退费率", index=False)
        monthly_rate.to_excel(writer, sheet_name="11月后每月新报退费率", index=False)

    # 9) 控制台输出关键结果，方便快速核对
    print(f"统计完成，输出文件：{OUTPUT_FILE}")
    print("\n【每周统计】")
    print(weekly.to_string(index=False))
    print("\n【每月统计】")
    print(monthly.to_string(index=False))
    print(f"\n【11月后新报/退费率统计】起始日期：{cutoff_date.strftime('%Y-%m-%d')}")
    print(f"费率统计输出文件：{RATE_OUTPUT_FILE}")

    # 10) 生成统计图并保存
    png_ok = draw_chart(weekly, monthly)
    if png_ok:
        print(f"\n统计图已生成：{CHART_FILE}")
    else:
        print("\n未生成PNG统计图：当前环境缺少 matplotlib（可执行 pip install matplotlib）。")

    # 11) 生成可交互式 HTML 图表并保存
    html_ok = draw_interactive_html(weekly, monthly)
    if html_ok:
        print(f"交互式HTML已生成：{HTML_FILE}")
    else:
        print("未生成交互式HTML：当前环境缺少 plotly（可执行 pip install plotly）。")

    # 12) 生成“11月后新报率/退费率”交互式 HTML 图表并保存
    rate_html_ok = draw_rate_interactive_html(weekly_rate, monthly_rate, cutoff_date)
    if rate_html_ok:
        print(f"11月后费率交互式HTML已生成：{RATE_HTML_FILE}")
    else:
        print("未生成11月后费率HTML：当前环境缺少 plotly（可执行 pip install plotly）。")


if __name__ == "__main__":
    main()
