"""
宝妈工作群「接龙」消息解析脚本
- 按时间升序排列所有接龙消息（不去重，保留每一次发送）
- 对比当前消息与上一条消息的差集，精确找出本次新增的内容
- 输出：发送时间、发送人备注/昵称、本次新增行
"""

import sqlite3
import hashlib
import glob
import os
import sys
import datetime
import re
import zstandard as zstd

# -------- 常量 --------
CONTACT_DB      = r"C:\Users\LENOVO\Desktop\contact_内部专用.db"
MESSAGE_PATTERN = r"C:\Users\LENOVO\Desktop\message*.db"
OUTPUT_PATH     = r"C:\Users\LENOVO\Desktop\output_接龙解析2.txt"
GROUP_NICK      = "宝妈工作群"


# -------- 工具函数 --------

def decode_content(raw):
    """将 message_content 原始数据解压/解码为 UTF-8 字符串"""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        if raw[:4] == b'\x28\xb5\x2f\xfd':
            try:
                dctx = zstd.ZstdDecompressor()
                raw = dctx.decompress(raw)
            except Exception as e:
                return f"[zstd解压失败: {e}]"
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return raw.hex()
    return str(raw)


def ts_to_str(ts):
    """Unix 时间戳 → 可读字符串"""
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def is_jielong(text):
    """判断是否是接龙消息"""
    body = strip_sender_prefix(text)
    return body.lstrip().startswith("#接龙")


def strip_sender_prefix(text):
    """去掉 'wxid_xxx:\n' 格式的发言人前缀，返回正文"""
    text = text.strip()
    if "\n" in text:
        first_line, rest = text.split("\n", 1)
        first_line = first_line.strip()
        if first_line.endswith(":") and " " not in first_line:
            return rest.strip()
    return text


def get_jielong_lines(text):
    """
    提取接龙正文里的所有有效行（去掉标题行和空行）。
    返回行列表，每行是一个接龙条目，如 "1. 张三 13800000000"。
    """
    body = strip_sender_prefix(text)
    lines = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        # 跳过标题行（#接龙 + 说明文字）
        if line.startswith("#接龙"):
            continue
        # 跳过纯说明文字（不以数字序号开头的行）
        if not re.match(r'^\d+', line):
            continue
        lines.append(line)
    return lines


def strip_no(line):
    """去掉行首序号（如 '12. ' '12、' '12 '），只保留内容部分"""
    return re.sub(r'^\d+[.\s、]\s*', '', line.strip())


def diff_lines(prev_lines, curr_lines):
    """
    计算 curr_lines 相比 prev_lines 新增了哪些行。
    比较时忽略序号，只看内容是否出现过。
    """
    # 建立上一条的"内容集合"（去掉序号后的文本）
    prev_content_set = {strip_no(l) for l in prev_lines}

    added = []
    for l in curr_lines:
        content = strip_no(l)
        if content and content not in prev_content_set:
            added.append(l)  # 输出时仍带序号，方便阅读

    return added


# -------- 第一步：查 contact 获取 username --------
print(f"正在连接联系人数据库...")
try:
    conn = sqlite3.connect(CONTACT_DB)
    conn.text_factory = bytes
    cur = conn.cursor()
    cur.execute("SELECT username, nick_name FROM contact")
    row = None
    for uname_b, nick_b in cur.fetchall():
        nick_str = nick_b.decode("utf-8", errors="replace").strip() if isinstance(nick_b, bytes) else (nick_b or "")
        if nick_str == GROUP_NICK:
            row = uname_b
            break
    conn.close()
except Exception as e:
    print(f"[错误] {e}")
    sys.exit(1)

if not row:
    print(f"[错误] 未找到 nick_name='{GROUP_NICK}'")
    sys.exit(1)

username = row.decode("utf-8") if isinstance(row, bytes) else row
md5_value = hashlib.md5(username.encode("utf-8")).hexdigest()
target_table = f"Msg_{md5_value}"
print(f"群 username：{username}")
print(f"目标表：{target_table}")

# -------- 第二步：加载联系人映射 --------
print("\n正在加载联系人映射...")
local_id_map = {}
wxid_map     = {}

try:
    conn = sqlite3.connect(CONTACT_DB)
    conn.text_factory = bytes
    cur = conn.cursor()
    cur.execute("SELECT id, username, nick_name, remark FROM contact")

    for row in cur.fetchall():
        lid, uname_b, nick_b, remark_b = row

        def to_str(val):
            if val is None: return ""
            if isinstance(val, (bytes, bytearray)):
                return val.decode("utf-8", errors="replace").strip()
            return str(val).strip()

        uname  = to_str(uname_b)
        nick   = to_str(nick_b)
        remark = to_str(remark_b)

        if remark: display = remark
        elif nick: display = nick
        else:      display = uname

        local_id_map[lid] = {"remark": remark, "nick": nick, "wxid": uname}
        if uname:
            wxid_map[uname] = {"remark": remark, "nick": nick}

    conn.close()
    print(f"  已加载 {len(wxid_map)} 条联系人")
except Exception as e:
    print(f"  [警告] 加载联系人失败：{e}")


def clean_remark(remark):
    """去掉微信 remark 里的内部编号前缀（¿¿¿NNNNNN-）"""
    if not remark:
        return ""
    remark = remark.strip()
    if remark.startswith("\u00bf") and "-" in remark:
        after = remark.split("-", 1)[-1].strip()
        if after:
            return after
    return remark


def get_contact_info(sender_id, wxid_in_msg):
    """
    返回 (remark显示名, nick_name, wxid)。
    优先用消息体里的 wxid_in_msg 字符串匹配（更准确），
    其次再用 real_sender_id 数字匹配（可能因换号导致错位）。
    """
    info = None

    # 优先：消息体第一行的 wxid 字符串
    if wxid_in_msg and wxid_in_msg in wxid_map:
        info = wxid_map[wxid_in_msg]

    # 备用：real_sender_id 数字 → local_id_map
    if info is None and sender_id is not None:
        try:
            info = local_id_map.get(int(sender_id))
        except (ValueError, TypeError):
            pass

    # 都没查到：返回原始 wxid
    if info is None:
        return ("", "", wxid_in_msg or str(sender_id))

    remark = clean_remark(info["remark"])
    nick   = info["nick"]
    # wxid 优先用消息体里的（比 contact 存的更准确）
    wxid   = wxid_in_msg or info.get("wxid", "")
    display_remark = remark if remark else nick
    return (display_remark, nick, wxid)


# -------- 第三步：读取所有接龙消息，按时间升序 --------
print(f"\n开始扫描消息数据库...")
jielong_list = []

for db_path in sorted(glob.glob(MESSAGE_PATTERN)):
    db_name = os.path.basename(db_path)
    try:
        conn = sqlite3.connect(db_path)
        cur  = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (target_table,))
        if not cur.fetchone():
            print(f"  {db_name}: 无目标表，跳过")
            conn.close()
            continue

        cur.execute(
            f"SELECT create_time, real_sender_id, message_content "
            f"FROM [{target_table}] WHERE local_type = 1"
        )
        rows = cur.fetchall()
        conn.close()

        count = 0
        for create_time, sender_id, raw in rows:
            text = decode_content(raw)
            if not is_jielong(text):
                continue

            lines_all = text.strip().splitlines()
            first_line = lines_all[0].strip().rstrip(":") if lines_all else ""
            # 判断第一行是否是发送人 ID：
            # 1. 以 wxid_ / gh_ 开头的标准格式
            # 2. 或者：纯字母数字下划线、不含空格、长度 <= 40，且能在联系人表里找到
            def is_sender_id(s):
                if not s:
                    return False
                if s.startswith("wxid_") or s.startswith("gh_"):
                    return True
                # 非标准格式（如 lty398）：在联系人表里能查到才认可
                if re.match(r'^[A-Za-z0-9_\-\.]{3,40}$', s) and s in wxid_map:
                    return True
                return False
            wxid_in_msg = first_line if is_sender_id(first_line) else ""

            jielong_lines = get_jielong_lines(text)

            jielong_list.append({
                "create_time":  create_time,
                "sender_id":    sender_id,
                "wxid_in_msg":  wxid_in_msg,
                "lines":        jielong_lines,  # 该消息的全部接龙行
            })
            count += 1

        print(f"  {db_name}: 找到接龙消息 {count} 条")
    except Exception as e:
        print(f"  {db_name}: 出错 {e}")

# -------- 第四步：按时间升序排列 --------
jielong_list.sort(key=lambda x: x["create_time"])
print(f"\n共 {len(jielong_list)} 条接龙消息，按时间升序排列")

# -------- 第五步：对比差集，找出每条新增了什么 --------
SEP = "\t"

with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    f.write(f"群名：{GROUP_NICK}\n")
    f.write(f"接龙消息总数：{len(jielong_list)} 条（含重发）\n")
    f.write(f"排列：按发送时间升序\n")
    f.write("\n")

    # 表头
    f.write(SEP.join(["序号", "发送时间", "备注名(remark)", "昵称(nick_name)", "wxid", "本次新增内容"]) + "\n")
    f.write(SEP.join(["----", "-------------------", "--------------", "--------------", "------------------------------", "----------"]) + "\n")

    prev_lines = []       # 上一条消息的接龙行列表
    seen_content = set()  # 全局已出现过的新增内容（去序号后）

    for idx, item in enumerate(jielong_list, 1):
        time_str = ts_to_str(item["create_time"])
        remark, nick, wxid = get_contact_info(item["sender_id"], item["wxid_in_msg"])

        curr_lines = item["lines"]

        # 计算与上一条的差集
        if idx == 1:
            added = curr_lines
        else:
            added = diff_lines(prev_lines, curr_lines)

        # 过滤掉已在前面某条记录中出现过的内容
        fresh = []
        for l in added:
            c = strip_no(l)
            if c and c not in seen_content:
                fresh.append(l)
                seen_content.add(c)  # 记录到全局集合

        added_str = " | ".join(fresh) if fresh else "[无新增]"

        f.write(SEP.join([str(idx), time_str, remark, nick, wxid, added_str]) + "\n")

        prev_lines = curr_lines  # 更新上一条

print(f"\n[完成] TXT 已保存到：{OUTPUT_PATH}")
if jielong_list:
    print(f"最早：{ts_to_str(jielong_list[0]['create_time'])}")
    print(f"最新：{ts_to_str(jielong_list[-1]['create_time'])}")
