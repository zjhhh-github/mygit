import json
import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path


json_path = Path(r'D:\桌面文件\新建文件夹\数据结构处理\售前通讯录\数据结构图_转换结果.json')

with json_path.open('r', encoding='utf-8') as f:
    data = json.load(f)

# 兼容两种数据结构：如果是单个对象(dict)则转成列表统一处理
if isinstance(data, dict):
    records = [data]
elif isinstance(data, list):
    records = data
else:
    raise TypeError(f"不支持的数据结构类型：{type(data)}")


def 解析绑定日期(text):
    """
    解析绑定日期，兼容常见格式：
    - YYYYMMDD
    - YYYYMMDDHHMMSS
    - YYYY-MM-DD / YYYY-MM-DD HH:MM:SS
    解析失败返回 None。
    """
    value = str(text).strip()
    if not value:
        return None

    formats = [
        "%Y%m%d%H%M%S",
        "%Y%m%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def 格式化绑定日期为8位(text):
    """
    将绑定日期统一格式化为 YYYYMMDD。
    - 可解析日期：统一转为 8 位日期字符串
    - 不可解析日期：保持原值，避免误改脏数据
    """
    dt = 解析绑定日期(text)
    if dt is None:
        return str(text).strip()
    return dt.strftime("%Y%m%d")


def 获取可写输出路径(path):
    """
    若目标文件被占用，则自动追加时间戳后缀，避免 PermissionError。
    """
    try:
        with path.open("a", encoding="utf-8"):
            pass
        return path
    except PermissionError:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return path.with_name(f"{path.stem}_{ts}{path.suffix}")

# 结果容器：
# 1) pair_rows: 保存“意向学员微信号-来源微信号”明细（一行一个来源）
# 2) source_to_intent_set: 用于统计每个来源微信号关联了多少个意向学员（去重后）
pair_rows = []
source_to_intent_set = defaultdict(set)
source_counter = Counter()
intent_counter = Counter()
# 三字段判重计数器：意向学员微信号 + 是否报名 + 绑定日期
triple_counter = Counter()

for item in records:
    if not isinstance(item, dict):
        continue

    # 当前记录的意向学员微信号
    intent_wx = str(item.get("意向学员微信号", "")).strip()
    if not intent_wx:
        continue
    intent_counter[intent_wx] += 1

    # 当前记录的来源列表，预期是 list[dict]
    sources = item.get("来源", [])
    if not isinstance(sources, list):
        continue

    for source_item in sources:
        if not isinstance(source_item, dict):
            continue

        source_wx = str(source_item.get("来源微信号", "")).strip()
        signup_status = str(item.get("是否报名", "")).strip()
        bind_date = str(source_item.get("绑定日期", "")).strip()

        # 无论来源微信号是否为空，都参与“三字段判重”
        triple_key = (intent_wx, signup_status, bind_date)
        triple_counter[triple_key] += 1

        if not source_wx:
            # 空来源微信号不参与重复统计
            continue

        pair_rows.append(
            {
                "意向学员微信号": intent_wx,
                "来源微信号": source_wx,
                "是否报名": signup_status,
                "绑定日期": bind_date,
                "解绑日期": str(source_item.get("解绑日期", "")).strip(),
                "绑定状态": str(source_item.get("绑定状态", "")).strip(),
            }
        )
        source_counter[source_wx] += 1
        source_to_intent_set[source_wx].add(intent_wx)

# 来源微信号重复：出现次数 > 1
duplicate_source_rows = []
for source_wx, count in source_counter.items():
    if count > 1:
        intents = sorted(source_to_intent_set[source_wx])
        duplicate_source_rows.append(
            {
                "来源微信号": source_wx,
                "出现次数": count,
                "关联意向学员数量": len(intents),
                "关联意向学员微信号列表": " | ".join(intents),
            }
        )

# 按出现次数降序，便于优先处理问题数据
duplicate_source_rows.sort(key=lambda x: x["出现次数"], reverse=True)

# 三字段重复：同一个“意向学员微信号+是否报名+绑定日期”出现次数 > 1
duplicate_triple_rows = []
for (intent_wx, signup_status, bind_date), count in triple_counter.items():
    if count > 1:
        duplicate_triple_rows.append(
            {
                "意向学员微信号": intent_wx,
                "是否报名": signup_status,
                "绑定日期": bind_date,
                "出现次数": count,
            }
        )
duplicate_triple_rows.sort(key=lambda x: x["出现次数"], reverse=True)

# 清洗逻辑：
# 条件为“同一个已报名意向学员微信号”下，若两条来源记录时间相差约 8 小时
# （容差 ±1 分钟），删除“较晚”的那条来源记录。
source_refs_by_intent = defaultdict(list)
for rec_idx, item in enumerate(records):
    if not isinstance(item, dict):
        continue
    if str(item.get("是否报名", "")).strip() != "已报名":
        continue

    intent_wx = str(item.get("意向学员微信号", "")).strip()
    if not intent_wx:
        continue

    sources = item.get("来源", [])
    if not isinstance(sources, list):
        continue

    for src_idx, source_item in enumerate(sources):
        if not isinstance(source_item, dict):
            continue
        source_wx = str(source_item.get("来源微信号", "")).strip()
        bind_raw = str(source_item.get("绑定日期", "")).strip()
        bind_dt = 解析绑定日期(bind_raw)
        if bind_dt is None:
            continue

        source_refs_by_intent[intent_wx].append(
            {
                "rec_idx": rec_idx,
                "src_idx": src_idx,
                "source_wx": source_wx,
                "bind_raw": bind_raw,
                "bind_dt": bind_dt,
            }
        )

to_remove_keys = set()
removed_rows = []
target_delta = timedelta(hours=8)
tolerance = timedelta(minutes=1)
min_delta = target_delta - tolerance
max_delta = target_delta + tolerance

for intent_wx, refs in source_refs_by_intent.items():
    # 至少有两条来源记录，才有比较意义
    if len(refs) < 2:
        continue

    refs_sorted = sorted(refs, key=lambda x: x["bind_dt"])

    # 若 later - earlier 落在 [7h59m, 8h1m]，删除 later
    for i in range(len(refs_sorted)):
        later = refs_sorted[i]
        for j in range(i):
            earlier = refs_sorted[j]
            delta = later["bind_dt"] - earlier["bind_dt"]
            if min_delta <= delta <= max_delta:
                key = (later["rec_idx"], later["src_idx"])
                if key not in to_remove_keys:
                    to_remove_keys.add(key)
                    removed_rows.append(
                        {
                            "意向学员微信号": intent_wx,
                            "删除来源微信号": later["source_wx"],
                            "删除绑定日期": later["bind_raw"],
                            "参考来源微信号": earlier["source_wx"],
                            "参考绑定日期": earlier["bind_raw"],
                            "规则": "同意向学员+已报名+晚8小时(±1分钟)删除",
                        }
                    )
                break

# 实际删除来源记录（按原索引过滤）
for rec_idx, item in enumerate(records):
    if not isinstance(item, dict):
        continue
    sources = item.get("来源", [])
    if not isinstance(sources, list):
        continue
    kept = []
    for src_idx, source_item in enumerate(sources):
        if (rec_idx, src_idx) not in to_remove_keys:
            kept.append(source_item)
    item["来源"] = kept

# 将“去重后结果”中的绑定日期统一为 YYYYMMDD
normalized_bind_date_count = 0
for item in records:
    if not isinstance(item, dict):
        continue
    sources = item.get("来源", [])
    if not isinstance(sources, list):
        continue
    for source_item in sources:
        if not isinstance(source_item, dict):
            continue
        old_bind_date = str(source_item.get("绑定日期", "")).strip()
        new_bind_date = 格式化绑定日期为8位(old_bind_date)
        if new_bind_date != old_bind_date:
            normalized_bind_date_count += 1
        source_item["绑定日期"] = new_bind_date

# 输出文件路径
pair_csv = json_path.with_name("意向学员_来源微信号明细.csv")
duplicate_csv = json_path.with_name("来源微信号重复统计.csv")
duplicate_triple_csv = json_path.with_name("三字段重复统计.csv")
removed_csv = json_path.with_name("删除_晚8小时_明细.csv")
cleaned_json = json_path.with_name("数据结构图_转换结果_清洗后.json")

pair_csv = 获取可写输出路径(pair_csv)
duplicate_csv = 获取可写输出路径(duplicate_csv)
duplicate_triple_csv = 获取可写输出路径(duplicate_triple_csv)
removed_csv = 获取可写输出路径(removed_csv)
cleaned_json = 获取可写输出路径(cleaned_json)

# 导出明细 CSV
with pair_csv.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=["意向学员微信号", "来源微信号", "是否报名", "绑定日期", "解绑日期", "绑定状态"],
    )
    writer.writeheader()
    writer.writerows(pair_rows)

# 导出重复来源统计 CSV
with duplicate_csv.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=["来源微信号", "出现次数", "关联意向学员数量", "关联意向学员微信号列表"],
    )
    writer.writeheader()
    writer.writerows(duplicate_source_rows)

# 导出三字段重复统计 CSV
with duplicate_triple_csv.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=["意向学员微信号", "是否报名", "绑定日期", "出现次数"],
    )
    writer.writeheader()
    writer.writerows(duplicate_triple_rows)

# 导出“晚8小时删除”明细
with removed_csv.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "意向学员微信号",
            "删除来源微信号",
            "删除绑定日期",
            "参考来源微信号",
            "参考绑定日期",
            "规则",
        ],
    )
    writer.writeheader()
    writer.writerows(removed_rows)

# 导出清洗后的 JSON（不覆盖原始输入）
with cleaned_json.open("w", encoding="utf-8") as f:
    json.dump(records, f, ensure_ascii=False, indent=2)

print(f"总记录数: {len(records)}")
print(f"有效意向学员微信号数: {sum(intent_counter.values())}")
print(f"去重后意向学员微信号数: {len(intent_counter)}")
print(f"有效来源关系条数: {len(pair_rows)}")
print(f"来源微信号去重数: {len(source_counter)}")
print(f"重复来源微信号种类数: {len(duplicate_source_rows)}")
print(f"三字段重复种类数: {len(duplicate_triple_rows)}")
print(f"按“晚8小时”规则删除条数: {len(removed_rows)}")
print(f"绑定日期格式化条数(YYYYMMDD): {normalized_bind_date_count}")
print("-" * 40)
print(f"明细输出: {pair_csv}")
print(f"重复统计输出: {duplicate_csv}")
print(f"三字段重复输出: {duplicate_triple_csv}")
print(f"删除明细输出: {removed_csv}")
print(f"清洗后JSON输出: {cleaned_json}")

if not duplicate_source_rows:
    print("未发现来源微信号重复数据。")
else:
    print("来源微信号重复示例（前10条）：")
    for row in duplicate_source_rows[:10]:
        print(f"{row['来源微信号']} -> 出现 {row['出现次数']} 次，关联 {row['关联意向学员数量']} 个意向学员")

print("-" * 40)
if not duplicate_triple_rows:
    print("未发现三字段重复数据。")
else:
    print("三字段重复示例（前10条）：")
    for row in duplicate_triple_rows[:10]:
        print(
            f"{row['意向学员微信号']} | {row['是否报名']} | {row['绑定日期']} "
            f"-> 出现 {row['出现次数']} 次"
        )