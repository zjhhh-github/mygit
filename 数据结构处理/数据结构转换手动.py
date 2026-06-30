# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import json
from pathlib import Path
import sqlite3
# from 地址解析器 import AddressParser
from address_parser.parser import AddressParser

def convert_value(val):
    if pd.isna(val):
        return ""
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return float(val)
    if isinstance(val, pd.Timestamp):
        return str(val)
    return val



# 定义获取SQLite连接的函数
def get_sqlite_connection(db_path):
    """
    获取SQLite数据库连接
    :param db_path: 数据库文件路径
    :return: sqlite3.Connection对象
    """
    try:
        conn = sqlite3.connect(db_path)
        # 设置行工厂，让查询结果可以通过列名访问（可选，提升易用性）
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"连接数据库失败: {e}")
        raise

def query_sqlite(conn, sql, params=None):
    """
    执行SQL查询并返回pandas DataFrame
    :param conn: 数据库连接对象
    :param sql: 查询SQL语句（含占位符?）
    :param params: 查询参数（元组/列表，可选）
    :return: pandas.DataFrame
    """
    try:
        # 使用pandas的read_sql_query，支持参数化查询
        if params is None:
            df = pd.read_sql_query(sql, conn)
        else:
            df = pd.read_sql_query(sql, conn, params=params)
        return df
    except sqlite3.Error as e:
        print(f"查询失败: {e}")
        raise

df_bmlr = pd.read_excel(r"C:\Users\LENOVO\AppData\Roaming\kingsoft\office6\templates\et\zh_CN\报名录入.xlsx", sheet_name='报名录入', header=1)
df_nbtx = pd.read_excel(r"C:\Users\LENOVO\AppData\Roaming\kingsoft\office6\templates\et\zh_CN\报名录入.xlsx", sheet_name='内部通讯录', header=1)
bm = pd.read_excel(r"C:\Users\LENOVO\AppData\Roaming\kingsoft\office6\templates\et\zh_CN\报名录入.xlsx", sheet_name='宝妈', header=1)
bm_list = bm['宝妈'].values.tolist()
bm2hfz = {}
for i in bm_list:
    bm2hfz[i] = bm.loc[bm['宝妈'] == i, '分账编号'].values[0]
xueyuan2qddl = {}


# print(bm_list)


base_path = Path(__file__).parent
address_map_path = base_path / '地址映射.json'
abbreviation_map_path = base_path / '简称映射.json'

# 加载小组结构图，构建 组长 → 公司名称 的快速查找字典
with open(base_path / '小组结构图.json', encoding='utf-8') as _f:
    _group_data = json.load(_f)
zuzhang2company = {g["组长"]: g["公司名称"] for g in _group_data}

parser = AddressParser(
    r"D:\桌面文件\新建文件夹\数据结构处理\address_parser\district_db.json",
    abbrev_path=r"D:\桌面文件\新建文件夹\数据结构处理\简称映射.json"
)

for i in df_nbtx.index:
    xueyuan2qddl[df_nbtx.iloc[i]["学员"]] = [df_nbtx.iloc[i]["渠道C"], df_nbtx.iloc[i]["带领C"]]


result = []
conn = get_sqlite_connection(r"C:\Users\LENOVO\Desktop\contact.db")
for i in df_bmlr.index:

    name_key = df_bmlr.iloc[i]["编号+孩子中文全名"]
    # 判断是否为合伙宝妈
    if name_key in bm_list:
        pt_bm = "❌"
        is_bm = "✅"
    else:
        pt_bm = "✅"
        is_bm = "❌"
    # 数据库查询微信原始ID,微信号,微信昵称,微信备注
    df = query_sqlite(conn, f"SELECT * FROM contact WHERE remark = ?", (name_key,))
    try:
        username = df["username"].values[0]
        alias = df["alias"].values[0]
        nick_name = df["nick_name"].values[0]
        remark = df["remark"].values[0]
    except:
        # print(name_key,"未添加好友")
        username = "未添加好友"
        alias = ""
        nick_name = ""
        remark = ""
    # 查询推荐
    tuijian = df_bmlr.iloc[i]["推荐"]
    if pd.isna(tuijian):
        tuijian = ""

    # 查询慧分账编号
    if name_key in bm2hfz:
        hfz = bm2hfz[name_key]
    else:
        hfz = "⚠️"


    addr = df_bmlr.iloc[i]["收件人地址"]
    

    # parsed_addr = parser.parse(addr)
    # province = parsed_addr["province"]
    # city = parsed_addr["city"]
    # district = parsed_addr["district"]
    # detail = parsed_addr["detail"]
    # print(parsed_addr)
    
    try:
        parsed_addr = parser.parse(addr)
    except:
        # print(name_key,"地址解析失败")
        parsed_addr = {
            "province": "",
            "city": "",
            "district": "",
            "detail_address": ""
        }
    province = parsed_addr["province"]
    city = parsed_addr["city"]
    district = parsed_addr["district"]
    detail = parsed_addr["detail_address"]
    if pd.isna(addr):
        addr = "原始地址为空"
    # print(parsed_addr)
    # print(addr)

    if name_key in xueyuan2qddl:
        qudao = xueyuan2qddl[name_key][0]
        dailin = xueyuan2qddl[name_key][1]
    else:
        qudao = ""
        dailin = ""

    # 查找组长对应的公司名称，未找到则标记为 ❌
    company_name = zuzhang2company.get(name_key, "❌")
    record = {
    "ID": convert_value(df_bmlr.iloc[i]["编号"]),
    "中文名": "",
    "英文名": "",
    "孩子中文全名": convert_value(df_bmlr.iloc[i]["孩子中文全名"]),
    "手机号": convert_value(df_bmlr.iloc[i]["收件人电话"]),
    "身份证号": "",
    "微信原始ID": username,
    "微信号": alias,
    "微信昵称": nick_name,
    "微信备注": remark,
    "地址": {
        "省": province,
        "市": city,
        "区/县": district,
        "具体": detail,
        "全部": addr
    },
    "公司信息": {
        "公司名称": company_name,
        "公司税号": "",
        "公司开户银行": "",
        "公司银行账号": ""
    },
    "编号信息": {
        "慧分账编号": hfz,
        "拉卡拉编号": ""
    },
    
    "普通宝妈": pt_bm,
    "合伙宝妈": is_bm,
    "老师": "",
    "教务": "",
    "场地": "",
    "推荐": tuijian.split("-")[0] if tuijian.split("-") else "",
    "渠道": str(qudao).split("-")[0] if qudao else "",
    "带领": str(dailin).split("-")[0] if dailin else "",
    "线下剩余课时": "",
    "线上剩余课时": "",
    "孩子信息": [
        {
            "孩子中文全名": convert_value(df_bmlr.iloc[i]["孩子中文全名"]),
            "孩子英文名": "",
            "孩子出生年月": "",
            "孩子性别": ""
        }
    ],
    "报名信息": [{
        "订单号": convert_value(df_bmlr.iloc[i]["交易订单编号"]),
        "项目": convert_value(df_bmlr.iloc[i]["报名项"]),
        "金额": convert_value(df_bmlr.iloc[i]["订单金额"]),
        "日期": str(df_bmlr.iloc[i]["交易时间"]),
        "聚水潭单号": "",
        "快递单号": convert_value(df_bmlr.iloc[i]["单号"]),
        "激活码": convert_value(df_bmlr.iloc[i]["激活码"]),
        "校区": convert_value(df_bmlr.iloc[i]["校区"]),
        "老师": convert_value(df_bmlr.iloc[i]["老师"]),
        "班级": ""
    }
    ],
    "班级信息": [{
        "星期": "",
        "上课时间": "",
        "下课时间": "",
        "学员": ["", "", ""]
    }
    ]
    }
    result.append(record)

def merge_by_id(records):
    """
    将 records 列表中 ID 相同的记录合并为一条。
    合并规则：
      - 基础字段（姓名、手机、地址、微信等）以第一次出现的值为准
      - 报名信息（列表）将所有同 ID 的条目依次追加，保留全部报名记录
    """
    # 有序字典：key=ID，value=合并后的记录（保持原始顺序）
    merged_map = {}
    merge_count = 0  # 记录被合并的条数

    for record in records:
        record_id = record.get("ID", None)

        if record_id not in merged_map:
            # 首次出现：直接存入
            merged_map[record_id] = record
        else:
            merge_count += 1
            # 已存在：只将本条的报名信息追加到已有记录中
            merged_map[record_id]["报名信息"].extend(record["报名信息"])

    if merge_count > 0:
        print(f"[合并] 共发现 {merge_count} 条重复ID记录，已将其报名信息合并至同一记录中。")

    return list(merged_map.values())


# 按 ID 合并，同一学员的多条报名信息汇总到一条记录的"报名信息"数组中
result = merge_by_id(result)

output_path = r'd:\桌面文件\新建文件夹\数据结构处理\转换结果_手动转换.json'
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)