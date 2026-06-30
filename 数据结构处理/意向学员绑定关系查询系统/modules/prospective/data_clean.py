# -*- coding: utf-8 -*-
"""
意向学员数据清洗 9 步流水线
============================

本模块完整移植自 db_viewer.py 顶部的「运行时清洗流水线」段落，行为完全等价。

设计要点：
    - 输入 / 输出统一为 list[ {'values': tuple|list[12], 'tag': str, ...} ]
    - 仅依据「12 列业务数据」做判定与过滤；tag、行颜色、txt_file_*/warning_*/
      deleted_* 等附加标记一律保留在被保留的那条记录上。
    - Step3 会把第 5 列（添加时间）的 values 同步改写为标准化结果
      YYYY/MM/DD HH:MM:SS（status=ok/empty 时改写；unknown 保留原值）。
    - 不在运行时落地任何中间 txt 文件。

公开 API：
    - clean_newlines(text): 字符串清洗辅助
    - run_clean_pipeline(items): 9 步流水线串联入口
"""

import re as _clean_re
from collections import OrderedDict as _CleanOrderedDict
from datetime import datetime


# ==================== 字符串辅助 ====================

def clean_newlines(text):
    """
    去除文本中的换行符，用空格替换。

    - 将 \\r\\n、\\n、\\r 替换为空格
    - 保留多余空格（不做 trim 处理）
    - 主要用于清理昵称字段中的换行符

    示例：
        "张三\\n李四"   -> "张三 李四"
        "王五\\r\\n赵六" -> "王五 赵六"
    """
    if not text:
        return ""
    return text.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ')


# ==================== 列索引（与 config.COL_* 同义，独立命名避免循环依赖） ====================

_CL_OBJ_WXID = 1
_CL_OBJ_TOTAL = 3
_CL_OBJ_TIME = 4
_CL_SRC_WXID = 8
_CL_COL_COUNT = 12

# Step3 时间标准化目标格式与兼容输入
_CL_TIME_STD_FMT = "%Y/%m/%d %H:%M:%S"
_CL_DATETIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
]
_CL_DATE_ONLY_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
]
_CL_RE_YEAR_MONTH_CN = _clean_re.compile(r"^\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*$")
_CL_RE_YEAR_MONTH_DAY_CN = _clean_re.compile(r"^\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?\s*$")

# Step4~Step7 用于解析「已标准化或常见格式」时间
_CL_PARSE_FORMATS_AFTER_STD = [
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y/%m/%d",
    "%Y-%m-%d",
]

# Step7 「约 8 小时」窗口（秒）：8h=28800，±10min
_CL_ABOUT_8H_LOWER_SEC = 28200
_CL_ABOUT_8H_UPPER_SEC = 29400
# Step8 用：小时间差阈值（<= 60s 保留最早），中等时间差上界（< 600s 保留最晚）
_CL_SMALL_DELTA_KEEP_EARLIEST_SEC = 60
_CL_MEDIUM_DELTA_UPPER_SEC = 600


# ==================== 通用工具函数 ====================

def _cl_get(values, idx):
    """安全取列；越界返回空字符串。values 可为 tuple 或 list。"""
    try:
        v = values[idx]
    except (IndexError, TypeError):
        return ""
    return v if isinstance(v, str) else ("" if v is None else str(v))


def _cl_pad_values(values):
    """将 values 标准化为长度 12 的 list（保留原内容，不做 strip）。"""
    out = list(values) if values is not None else []
    if len(out) < _CL_COL_COUNT:
        out = out + [""] * (_CL_COL_COUNT - len(out))
    elif len(out) > _CL_COL_COUNT:
        out = out[:_CL_COL_COUNT]
    return out


def _cl_parse_time(s):
    """Step4~Step7 用：解析时间字符串。失败 / 空返回 None。"""
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    for fmt in _CL_PARSE_FORMATS_AFTER_STD:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _cl_normalize_time_step3(raw):
    """Step3 时间标准化：返回 (normalized_str, status)，status ∈ {'ok','empty','unknown'}。"""
    if raw is None:
        return "", "empty"
    s = raw.strip()
    if s == "":
        return "", "empty"

    for fmt in _CL_DATETIME_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime(_CL_TIME_STD_FMT), "ok"
        except ValueError:
            pass

    for fmt in _CL_DATE_ONLY_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y/%m/%d 00:00:00"), "ok"
        except ValueError:
            pass

    m = _CL_RE_YEAR_MONTH_DAY_CN.match(s)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return dt.strftime("%Y/%m/%d 00:00:00"), "ok"
        except ValueError:
            pass

    m = _CL_RE_YEAR_MONTH_CN.match(s)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), 1)
            return dt.strftime("%Y/%m/%d 00:00:00"), "ok"
        except ValueError:
            pass

    return s, "unknown"


def _cl_is_risky_time(s):
    """Step4：危险时间 = 能解析 + day==1 + HH:MM:SS==00:00:00。"""
    dt = _cl_parse_time(s)
    if dt is None:
        return False
    return dt.day == 1 and dt.hour == 0 and dt.minute == 0 and dt.second == 0


def _cl_is_about_8h(delta_seconds):
    return _CL_ABOUT_8H_LOWER_SEC <= delta_seconds <= _CL_ABOUT_8H_UPPER_SEC


def _cl_group_by_obj_wxid(items):
    """按「意向学员(微信ID)」分组，保留出现顺序。"""
    groups = _CleanOrderedDict()
    for orig_idx, it in enumerate(items):
        key = _cl_get(it['values'], _CL_OBJ_WXID).strip()
        groups.setdefault(key, []).append((orig_idx, it))
    return groups


# ==================== Step1~Step9 ====================

# ---------- Step1：删除「来源(微信ID)」为空的行 ----------
def _clean_step1_remove_empty_source_wxid(items):
    kept = []
    for it in items:
        if _cl_get(it['values'], _CL_SRC_WXID).strip() == "":
            continue
        kept.append(it)
    return kept


# ---------- Step2：按 (意向学员微信ID, 添加时间, 来源微信ID) 三列原值去重 ----------
def _clean_step2_dedup_by_wxid_time_source(items):
    seen = set()
    kept = []
    for it in items:
        v = it['values']
        key = (_cl_get(v, _CL_OBJ_WXID), _cl_get(v, _CL_OBJ_TIME), _cl_get(v, _CL_SRC_WXID))
        if key in seen:
            continue
        seen.add(key)
        kept.append(it)
    return kept


# ---------- Step3：第 2 列空回退第 4 列；第 5 列时间标准化后参与 key；同步改写第 5 列 ----------
def _clean_step3_dedup_by_normalized_time_and_fallback_id(items):
    seen = set()
    kept = []
    for it in items:
        values = _cl_pad_values(it['values'])

        obj_wxid = values[_CL_OBJ_WXID].strip() if isinstance(values[_CL_OBJ_WXID], str) else str(values[_CL_OBJ_WXID]).strip()
        obj_total = values[_CL_OBJ_TOTAL].strip() if isinstance(values[_CL_OBJ_TOTAL], str) else str(values[_CL_OBJ_TOTAL]).strip()
        obj_time_raw = values[_CL_OBJ_TIME] if isinstance(values[_CL_OBJ_TIME], str) else ("" if values[_CL_OBJ_TIME] is None else str(values[_CL_OBJ_TIME]))
        source_wxid = values[_CL_SRC_WXID].strip() if isinstance(values[_CL_SRC_WXID], str) else str(values[_CL_SRC_WXID]).strip()

        dedup_obj_id = obj_wxid if obj_wxid else obj_total
        normalized_time, status = _cl_normalize_time_step3(obj_time_raw)

        key = (dedup_obj_id, normalized_time, source_wxid)
        if key in seen:
            continue
        seen.add(key)

        # 仅 ok / empty 才改写第 5 列；unknown 保留原值（与 clean_all_in_one 一致）
        if status in ("ok", "empty"):
            values[_CL_OBJ_TIME] = normalized_time

        # 写回 item.values，保留 tag 等其它字段
        new_item = dict(it)
        new_item['values'] = tuple(values)
        kept.append(new_item)
    return kept


# ---------- Step4：危险时间整组跳过；其余按来源保留时间最晚 ----------
def _clean_step4_skip_risky_day1_keep_latest_per_source(items):
    groups = _cl_group_by_obj_wxid(items)
    kept = {}

    for _obj_wxid, entries in groups.items():
        has_risky = any(_cl_is_risky_time(_cl_get(it['values'], _CL_OBJ_TIME)) for _, it in entries)
        if has_risky:
            for orig_idx, it in entries:
                kept[orig_idx] = it
            continue

        # 子分组：来源(微信ID) -> [best_orig_idx, best_item, best_dt]
        sub = {}
        for orig_idx, it in entries:
            src = _cl_get(it['values'], _CL_SRC_WXID).strip()
            cur_dt = _cl_parse_time(_cl_get(it['values'], _CL_OBJ_TIME))
            if src not in sub:
                sub[src] = [orig_idx, it, cur_dt]
                continue
            best = sub[src]
            best_dt = best[2]
            should_replace = False
            if cur_dt is not None and best_dt is None:
                should_replace = True
            elif cur_dt is not None and best_dt is not None and cur_dt > best_dt:
                should_replace = True
            if should_replace:
                sub[src] = [orig_idx, it, cur_dt]

        for _src, info in sub.items():
            kept[info[0]] = info[1]

    return [kept[i] for i in sorted(kept.keys())]


# ---------- Step5：恰好 2 条 + 来源相同 -> 保留更晚一条 ----------
def _clean_step5_two_rows_same_source_keep_latest(items):
    groups = _cl_group_by_obj_wxid(items)
    kept = {}

    for _obj_wxid, entries in groups.items():
        if len(entries) == 2:
            (i1, it1), (i2, it2) = entries[0], entries[1]
            src1 = _cl_get(it1['values'], _CL_SRC_WXID).strip()
            src2 = _cl_get(it2['values'], _CL_SRC_WXID).strip()
            if src1 == src2:
                t1 = _cl_parse_time(_cl_get(it1['values'], _CL_OBJ_TIME))
                t2 = _cl_parse_time(_cl_get(it2['values'], _CL_OBJ_TIME))
                if t1 is not None and t2 is not None:
                    kept[i2 if t2 > t1 else i1] = it2 if t2 > t1 else it1
                elif t1 is not None and t2 is None:
                    kept[i1] = it1
                elif t1 is None and t2 is not None:
                    kept[i2] = it2
                else:
                    kept[i1] = it1
                continue
        for orig_idx, it in entries:
            kept[orig_idx] = it

    return [kept[i] for i in sorted(kept.keys())]


def _cl_pick_latest(entries):
    """从 [(orig_idx, item), ...] 中按时间挑最新一条；规则与 step6 一致。"""
    best_idx, best_it = entries[0]
    best_dt = _cl_parse_time(_cl_get(best_it['values'], _CL_OBJ_TIME))
    for orig_idx, it in entries[1:]:
        cur_dt = _cl_parse_time(_cl_get(it['values'], _CL_OBJ_TIME))
        should_replace = False
        if cur_dt is not None and best_dt is None:
            should_replace = True
        elif cur_dt is not None and best_dt is not None and cur_dt > best_dt:
            should_replace = True
        if should_replace:
            best_idx, best_it, best_dt = orig_idx, it, cur_dt
    return best_idx, best_it


# ---------- Step6：条数 > 1 + 来源全一致 -> 保留时间最新一条 ----------
def _clean_step6_multi_rows_same_source_keep_latest(items):
    groups = _cl_group_by_obj_wxid(items)
    kept = {}

    for _obj_wxid, entries in groups.items():
        if len(entries) > 1:
            sources = {_cl_get(it['values'], _CL_SRC_WXID).strip() for _, it in entries}
            if len(sources) == 1:
                best_idx, best_it = _cl_pick_latest(entries)
                kept[best_idx] = best_it
                continue
        for orig_idx, it in entries:
            kept[orig_idx] = it

    return [kept[i] for i in sorted(kept.keys())]


def _cl_pick_earliest_latest(sub_entries):
    """子组内按有效时间挑 earliest / latest。返回 (earliest_entry, latest_entry)，
    entry = (orig_idx, item, dt or None)。全部无效则两者皆为首条。"""
    valid = []
    for orig_idx, it in sub_entries:
        dt = _cl_parse_time(_cl_get(it['values'], _CL_OBJ_TIME))
        if dt is not None:
            valid.append((orig_idx, it, dt))
    if not valid:
        first_idx, first_it = sub_entries[0]
        e = (first_idx, first_it, None)
        return e, e
    earliest = valid[0]
    latest = valid[0]
    for entry in valid[1:]:
        if entry[2] < earliest[2]:
            earliest = entry
        if entry[2] > latest[2]:
            latest = entry
    return earliest, latest


# ---------- Step7（正确版）：跨来源 8 小时污染判定 ----------
def _clean_step7_cross_source_8h_contamination_check(items):
    groups = _cl_group_by_obj_wxid(items)
    kept = {}

    for _obj_wxid, entries in groups.items():
        unique_sources = {_cl_get(it['values'], _CL_SRC_WXID).strip() for _, it in entries}
        if len(unique_sources) <= 1:
            for orig_idx, it in entries:
                kept[orig_idx] = it
            continue

        # 多来源：按 来源(微信ID) 拆子组（保留出现顺序）
        sub_map = _CleanOrderedDict()
        for orig_idx, it in entries:
            src = _cl_get(it['values'], _CL_SRC_WXID).strip()
            sub_map.setdefault(src, []).append((orig_idx, it))

        # 先求每个来源子组 earliest / latest
        sub_summary = _CleanOrderedDict()
        for src, sub_entries in sub_map.items():
            e_entry, l_entry = _cl_pick_earliest_latest(sub_entries)
            sub_summary[src] = {
                "items": sub_entries,
                "earliest": e_entry,
                "latest": l_entry,
            }

        for src, info in sub_summary.items():
            sub_entries = info["items"]

            if len(sub_entries) <= 1:
                orig_idx, it = sub_entries[0]
                kept[orig_idx] = it
                continue

            latest_dt = info["latest"][2]
            if latest_dt is None:
                first_idx, first_it = sub_entries[0]
                kept[first_idx] = first_it
                continue

            contaminated = False
            for other_src, other_info in sub_summary.items():
                if other_src == src:
                    continue
                other_dt = other_info["latest"][2]
                if other_dt is None:
                    continue
                delta = abs((latest_dt - other_dt).total_seconds())
                if _cl_is_about_8h(delta):
                    contaminated = True
                    break

            if contaminated:
                e_idx, e_it, _ = info["earliest"]
                kept[e_idx] = e_it
            else:
                l_idx, l_it, _ = info["latest"]
                kept[l_idx] = l_it

    return [kept[i] for i in sorted(kept.keys())]


# ---------- Step8：同一意向学员恰好 2 条 -> 按添加时间差最终裁决 ----------
def _clean_step8_two_rows_final_time_delta_decision(items):
    """
    针对 step1~step7 跑完后的结果：
      - 仅处理「同一个 意向学员(微信ID) 恰好剩 2 条」的组
      - 两条时间均有效，按以下顺序裁决（取 delta = abs(t1 - t2) 秒）：
          * delta <= 60                          → 保留较早一条
          * 60 < delta < 600                     → 保留较晚一条
          * 28200 <= delta <= 29400 (≈8h ±10min) → 保留较早一条
          * 其他                                  → 保留较晚一条
      - 仅一条时间有效                            → 保留有效那条
      - 两条均无效                                → 保留第一条（按出现顺序）
      - 两条时间完全相同 (delta == 0)             → 落入「<=60」分支，等价保留第一条
    其他组（1 条 / >=3 条）原样保留。
    """
    groups = _cl_group_by_obj_wxid(items)
    kept = {}

    for _obj_wxid, entries in groups.items():
        if len(entries) != 2:
            for orig_idx, it in entries:
                kept[orig_idx] = it
            continue

        (i1, it1), (i2, it2) = entries[0], entries[1]
        t1 = _cl_parse_time(_cl_get(it1['values'], _CL_OBJ_TIME))
        t2 = _cl_parse_time(_cl_get(it2['values'], _CL_OBJ_TIME))

        if t1 is not None and t2 is not None:
            delta = abs((t2 - t1).total_seconds())
            # 预先确定较早 / 较晚条目（时间相同时较早=第一条、较晚=第二条，等价保留第一条由各分支决定）
            if t1 <= t2:
                earliest_idx, earliest_it = i1, it1
                latest_idx, latest_it = i2, it2
            else:
                earliest_idx, earliest_it = i2, it2
                latest_idx, latest_it = i1, it1

            if delta <= _CL_SMALL_DELTA_KEEP_EARLIEST_SEC:
                # <=60s：保留较早（delta==0 时较早即第一条）
                if delta == 0:
                    kept[i1] = it1
                else:
                    kept[earliest_idx] = earliest_it
            elif delta < _CL_MEDIUM_DELTA_UPPER_SEC:
                # 60s < delta < 600s：保留较晚
                kept[latest_idx] = latest_it
            elif _cl_is_about_8h(delta):
                # ≈8h：保留较早
                kept[earliest_idx] = earliest_it
            else:
                # 其他：保留较晚
                kept[latest_idx] = latest_it
        elif t1 is not None and t2 is None:
            kept[i1] = it1
        elif t1 is None and t2 is not None:
            kept[i2] = it2
        else:
            kept[i1] = it1

    return [kept[i] for i in sorted(kept.keys())]


# Step9 用：固定过滤的来源(微信ID)黑名单
_CL_STEP9_BLOCKED_SRC_WXIDS = frozenset({"wxid_6492294921712"})


# ---------- Step9：按固定来源(微信ID)黑名单直接过滤 ----------
def _clean_step9_filter_specific_source_wxid(items):
    """
    在 step1~step8 跑完后，按 来源(微信ID)（索引 8）黑名单直接剔除。
    其余字段不参与判断，不影响其它来源。
    """
    kept = []
    for it in items:
        src = _cl_get(it['values'], _CL_SRC_WXID).strip()
        if src in _CL_STEP9_BLOCKED_SRC_WXIDS:
            continue
        kept.append(it)
    return kept


# ==================== 流水线串联入口 ====================

def run_clean_pipeline(items):
    """
    依次执行 9 步清洗（在原 8 步基础上追加 Step9）。
    输入 / 输出：list[ {'values': tuple|list[12], 'tag': str} ]

    控制台会按步骤打印 before / after / removed，便于排查。
    """
    steps = [
        ("STEP1 删除来源微信ID为空", _clean_step1_remove_empty_source_wxid),
        ("STEP2 三列原值去重", _clean_step2_dedup_by_wxid_time_source),
        ("STEP3 时间标准化+回退ID去重", _clean_step3_dedup_by_normalized_time_and_fallback_id),
        ("STEP4 危险时间整组跳过+按来源最晚", _clean_step4_skip_risky_day1_keep_latest_per_source),
        ("STEP5 两条同来源保留最新", _clean_step5_two_rows_same_source_keep_latest),
        ("STEP6 多条同来源保留最新", _clean_step6_multi_rows_same_source_keep_latest),
        ("STEP7 跨来源8小时污染判定", _clean_step7_cross_source_8h_contamination_check),
        ("STEP8 两条时间差最终裁决", _clean_step8_two_rows_final_time_delta_decision),
        ("STEP9 过滤指定来源微信ID", _clean_step9_filter_specific_source_wxid),
    ]
    rows = items
    print("[运行时清洗] 开始执行 9 步清洗流水线 ...")
    for name, fn in steps:
        before = len(rows)
        rows = fn(rows)
        after = len(rows)
        print("[清洗 {}] before={} after={} removed={}".format(name, before, after, before - after))
    return rows
