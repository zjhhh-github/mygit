"""
对比两份数据，将 JSON 中所有学员（含未报名）的推荐人（绑定日期最大的来源微信号）
修正为 TXT 中的来源微信号，并保存为新 JSON 文件。

规则：
- JSON 有且 TXT 有 → 用 TXT 推荐人全量覆盖 JSON（无论报名状态）
  - 有来源记录 → 直接修改来源微信号
  - 无来源记录且 TXT 推荐人不为空 → 新增来源条目
- JSON 有、TXT 没有 → 不处理
- JSON 没有、TXT 有 → 新增学员到 JSON

输出：
- C:/Users/LENOVO/Desktop/_推荐人差异对比.txt  （差异明细）
- C:/Users/LENOVO/Downloads/意向学员数据_已修正.json （修改后的 JSON）
"""

import json
import csv
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────────────────────
JSON_PATH     = Path(r'C:\Users\LENOVO\Downloads\意向学员数据_2026-05-04.json')
TXT_PATH      = Path(r'C:\Users\LENOVO\Desktop\_脚本输出_1.txt')
OUT_DIFF_PATH = Path(r'C:\Users\LENOVO\Desktop\_推荐人差异对比.txt')
OUT_JSON_PATH = Path(r'C:\Users\LENOVO\Downloads\意向学员数据_已修正.json')


def s(v) -> str:
    """统一转字符串，None 转空串"""
    return str(v) if v is not None else ''


def pick_source(sources: list) -> dict | None:
    """
    来源筛选逻辑（与 意向通讯录导入.py 保持一致）：
    1. 多条时过滤掉来源微信号为空的记录
    2. 有绑定 → 取绑定日期最大的
    3. 无绑定 → 取绑定日期最大的
    4. 无来源 → 返回 None

    返回值是列表中对象的直接引用，修改返回值即修改原数据。
    """
    if not sources:
        return None

    if len(sources) > 1:
        filtered = [x for x in sources if s(x.get('来源微信号')) != '']
        if filtered:
            sources = filtered

    if len(sources) > 1:
        bound = [x for x in sources if s(x.get('绑定状态')) == '有绑定']
        if bound:
            return max(bound, key=lambda x: s(x.get('绑定日期')))
        else:
            return max(sources, key=lambda x: s(x.get('绑定日期')))

    return sources[0] if sources else None


# ── 读取 JSON，全部读入，已报名的同时保存来源对象引用 ──────────────────
print(f'读取 JSON：{JSON_PATH}')
with JSON_PATH.open('r', encoding='utf-8') as f:
    json_data = json.load(f)

# map_a_rec:  全部学员微信号 → 推荐人微信号（字符串，用于比较）
# map_a_src:  全部学员微信号 → 被选中的来源对象引用（有来源时用于直接修改推荐人）
# map_a_item: 全部学员微信号 → 学员 JSON 对象引用（无来源时用于新增来源条目）
# set_a_all:  全部学员微信号（用于判断 TXT 独有）
map_a_rec:  dict[str, str]  = {}
map_a_src:  dict[str, dict] = {}
map_a_item: dict[str, dict] = {}
set_a_all:  set[str]        = set()

for item in json_data:
    wx = s(item.get('意向学员微信号'))
    if not wx:
        continue
    set_a_all.add(wx)
    map_a_item[wx] = item          # 保存学员对象引用，无来源时用于追加来源条目
    src = pick_source(item.get('来源') or [])
    map_a_rec[wx] = s(src.get('来源微信号')) if src else ''
    if src is not None:
        map_a_src[wx] = src        # 有来源时保存来源对象引用，用于直接修改来源微信号

print(f'  JSON 共 {len(set_a_all)} 条')


# ── 读取 TXT（不过滤报名状态，由 JSON 统一判断）────────────────────────
# 同一学员可能有多行（每行 = 一条绑定/解绑记录），需要按学员累积成列表，
# 而不是覆盖，否则只会保留最后一行。
print(f'读取 TXT：{TXT_PATH}')
# rows_b: wx → 该学员在 TXT 中的全部行（每行已 strip）
# map_b:  wx → "代表推荐人"，按 pick_source 规则从多行中挑选，仅用于差异比较
rows_b: dict[str, list[dict]] = {}
map_b:  dict[str, str]        = {}
txt_row_count = 0
with TXT_PATH.open('r', encoding='utf-8') as f:
    reader = csv.DictReader(f, delimiter='\t')
    for row in reader:
        wx = row.get('意向学员总微信号', '').strip()
        if not wx:
            continue
        clean_row = {k: (v or '').strip() for k, v in row.items()}
        rows_b.setdefault(wx, []).append(clean_row)
        txt_row_count += 1

# 用与 JSON 端一致的 pick_source 规则，从 TXT 多行中挑出代表推荐人，
# 仅用于和 JSON 端推荐人做差异比较；JSON 重建仍使用全部行。
def _txt_row_to_source(r: dict) -> dict:
    """把 TXT 一行转成与 JSON 来源结构一致的对象"""
    return {
        '来源微信号': r.get('推荐人总微信号', ''),
        '绑定状态':   r.get('绑定状态', ''),
        '绑定日期':   r.get('绑定日期', ''),
        '解绑日期':   r.get('解绑日期', ''),
    }

for wx, lst in rows_b.items():
    src_objs = [_txt_row_to_source(r) for r in lst]
    picked = pick_source(src_objs)
    map_b[wx] = s(picked.get('来源微信号')) if picked else ''

print(f'  TXT 共 {txt_row_count} 行，去重后学员 {len(rows_b)} 个')


# ── 对比，并用 TXT 全量重建 JSON 中的来源数组 ──────────────────────────
# 重建规则：
#   - 凡是出现在 TXT 中的学员，其 JSON 的 "来源" 数组将整体替换为
#     TXT 中该学员的全部行（每行一条 来源 entry）。
#   - TXT 中推荐人为空的行，不进入 来源（与原脚本行为一致）。
#   - JSON 有、TXT 没有的学员，保持原样不动。
results = []
rebuild_count = 0   # 两边都有、来源被重建的学员数
add_count     = 0   # TXT 独有、新增到 JSON 的学员数

def _build_sources_from_txt(lst: list[dict]) -> list[dict]:
    """把 TXT 中同一学员的多行，按顺序构造为 来源 数组"""
    sources = []
    for r in lst:
        rec = r.get('推荐人总微信号', '')
        if not rec:
            continue
        sources.append({
            '来源微信号': rec,
            '绑定状态':   r.get('绑定状态', ''),
            '绑定日期':   r.get('绑定日期', ''),
            '解绑日期':   r.get('解绑日期', ''),
        })
    return sources

# 1. TXT 有、JSON 完全没有 → 构造新学员对象追加到 json_data
only_in_txt = set(rows_b.keys()) - set_a_all
for wx in sorted(only_in_txt):
    lst   = rows_b[wx]
    rec_b = map_b[wx]
    results.append((wx, '', rec_b, 'TXT独有-已新增'))

    # 报名状态优先取第一行（同一学员各行通常一致）
    new_item = {
        '意向学员微信号': wx,
        '是否报名':       lst[0].get('报名状态', ''),
        '来源':           _build_sources_from_txt(lst),
    }
    json_data.append(new_item)
    add_count += 1

# 2. 两边都有 → 用 TXT 全部行整体重建 来源 数组（覆盖 JSON 旧来源）
common_wx = set(map_a_rec.keys()) & set(map_b.keys())
for wx in sorted(common_wx):
    rec_a = map_a_rec[wx]
    rec_b = map_b[wx]
    item  = map_a_item[wx]
    item['来源'] = _build_sources_from_txt(rows_b[wx])
    rebuild_count += 1
    if rec_a != rec_b:
        results.append((wx, rec_a, rec_b, '推荐人已覆盖'))

print(f'TXT独有-已新增到JSON：{add_count} 条')
print(f'两边都有-已用 TXT 重建 来源：{rebuild_count} 条')
print(f'其中推荐人有变化：{len(results) - add_count} 条')


# ── 写入差异明细 ──────────────────────────────────────────────────────────
with OUT_DIFF_PATH.open('w', encoding='utf-8') as f:
    f.write('\t'.join(['意向学员微信号', 'JSON原推荐人', 'TXT推荐人', '类型']) + '\n')
    for wx, rec_a, rec_b, tag in results:
        f.write(f'{wx}\t{rec_a}\t{rec_b}\t{tag}\n')

print(f'差异明细已输出到：{OUT_DIFF_PATH}')


# ── 保存修改后的 JSON ─────────────────────────────────────────────────────
with OUT_JSON_PATH.open('w', encoding='utf-8') as f:
    json.dump(json_data, f, ensure_ascii=False, indent=2)

print(f'修正后 JSON 已保存到：{OUT_JSON_PATH}')
