# -*- coding: utf-8 -*-
"""
从飞书多维表格按编号查找「收件人地址」。

数据源（默认）：
    https://ipcjg02m9k.feishu.cn/base/Zk05bwki2abD8XsBBOccaFsPn8e?table=tblfoJucuZkeL9L1&view=vewQDcuhBV

用法：
    python lookup_address.py
    python lookup_address.py --numbers 000487 005022
    python lookup_address.py --input numbers.txt --output result.csv
"""

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

# Windows 下控制台默认可能是 GBK，遇到特殊字符会报编码错误，这里统一切到 UTF-8 输出。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

# 复用项目内 address_parser 解析省市区
_DATA_PROCESS_ROOT = Path(__file__).resolve().parents[2] / "数据结构处理"
if str(_DATA_PROCESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_DATA_PROCESS_ROOT))

from address_parser.parser import AddressParser  # noqa: E402

_ADDRESS_PARSER = AddressParser(
    db_path=str(_DATA_PROCESS_ROOT / "address_parser" / "district_db.json"),
    abbrev_path=str(_DATA_PROCESS_ROOT / "简称映射.json"),
    alias_path=str(_DATA_PROCESS_ROOT / "address_parser" / "city_alias.json"),
    enable_pinyin=False,
    enable_corrector=True,
    enable_es=False,
    enable_llm=False,
)

CSV_FIELDS = ["编号", "状态", "匹配列", "省", "市", "区县", "原地址"]

# ── 飞书配置（编号来源表 + 地址查询表）──
APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a96f36ed1538dbcf")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB")

# 编号来源（你提供的新链接）
SOURCE_APP_TOKEN = "Zk05bwki2abD8XsBBOccaFsPn8e"
SOURCE_TABLE_ID = "tblfoJucuZkeL9L1"
SOURCE_VIEW_ID = "vewQDcuhBV"

# 地址查询来源（你提供的新链接）
# https://ipcjg02m9k.feishu.cn/base/Zk05bwki2abD8XsBBOccaFsPn8e?table=tblvDAfK3HBpkydQ&view=vewCJrHnyI
LOOKUP_APP_TOKEN = "Zk05bwki2abD8XsBBOccaFsPn8e"
LOOKUP_TABLE_ID = "tblvDAfK3HBpkydQ"
LOOKUP_VIEW_ID = "vewCJrHnyI"

FEISHU_HOST = "https://open.feishu.cn"
PAGE_SIZE = 500
REQUEST_TIMEOUT = 60
飞书请求禁用代理 = True

FIELD_NUMBER = "编号"
FIELD_NUMBER_PAST = "编号_往月"
FIELD_ADDRESS = "收件人地址"

# 默认要查的编号（6 位）
DEFAULT_NUMBERS = """
000487
005022
005738
004902
002250
000328
003512
001835
004381
005499
007702
003046
004387
001432
005938
004312
007847
001738
000751
002206
004385
004410
005086
005620
001771
002917
001405
000473
003938
006239
008504
000079
005730
000573
003318
003335
000226
007035
006187
003225
002711
001275
002227
005731
000917
006297
000616
001439
005484
008147
001689
006935
000852
001661
007133
007013
009206
001710
003220
005854
005526
002882
000654
001354
006012
003138
003918
007512
004351
005688
002404
006956
008654
002899
008803
009802
003624
000424
007152
006696
003098
000346
007606
009199
006051
010048
010093
005424
007920
""".strip().split()


def _build_session():
    session = requests.Session()
    if 飞书请求禁用代理:
        session.trust_env = False
        session.proxies.update({"http": None, "https": None})
    return session


_SESSION = _build_session()


def _request(method, url, **kwargs):
    if 飞书请求禁用代理 and "proxies" not in kwargs:
        kwargs["proxies"] = {"http": None, "https": None}
    resp = _SESSION.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
    return resp


def get_tenant_access_token():
    url = FEISHU_HOST + "/open-apis/auth/v3/tenant_access_token/internal"
    resp = _request("POST", url, json={"app_id": APP_ID, "app_secret": APP_SECRET})
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError("获取 token 失败：{}".format(data))
    return data["tenant_access_token"]


def extract_text(value, default=""):
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()
    if isinstance(value, list):
        if not value:
            return default
        return extract_text(value[0], default)
    if isinstance(value, dict):
        if "text" in value:
            return str(value.get("text") or "").strip()
        if "value" in value:
            return extract_text(value.get("value"), default)
    return str(value).strip()


def normalize_number(text):
    """把编号规范为 6 位数字字符串；无法识别时返回空串。"""
    text = extract_text(text)
    if not text:
        return ""
    if text.isdigit():
        return text.zfill(6)
    match = re.search(r"\d+", text)
    if match:
        return match.group().zfill(6)
    return ""


def prepare_address_text(address):
    # type: (str) -> str
    """把飞书收件人地址整理成便于解析的文本。"""
    text = (address or "").strip()
    if not text:
        return ""

    # 结构化地址：所在地区 + 详细地址
    region_match = re.search(r"所在地区[:：]\s*([^详细地址]+)", text)
    detail_match = re.search(r"详细地址[:：]\s*(.+)$", text)
    if region_match:
        region = region_match.group(1).strip()
        detail = detail_match.group(1).strip() if detail_match else ""
        return (region + detail).strip()

    return text


MUNICIPALITY_NAMES = ("北京市", "上海市", "天津市", "重庆市")


def _normalize_municipality_region(province, city, district):
    # type: (str, str, str) -> Tuple[str, str, str]
    """直辖市：省列写「直辖市」，市列保留具体城市名。"""
    if province in MUNICIPALITY_NAMES:
        return "直辖市", province, district
    if city in MUNICIPALITY_NAMES and (not province or province == city or province in MUNICIPALITY_NAMES):
        return "直辖市", city, district
    return province, city, district


def _parse_direct_municipality(text):
    # type: (str) -> Optional[Tuple[str, str, str]]
    """
    直辖市优先按「北京市/上海市/... + 区/县」解析。
    避免「朝阳区」被误识别为辽宁省朝阳市。
    省列返回「直辖市」，市列为具体城市名。
    """
    match = re.match(r"^(北京市|上海市|天津市|重庆市)(.*)$", text)
    if not match:
        return None

    city_name = match.group(1)
    rest = (match.group(2) or "").strip()
    district = ""

    if rest:
        dist_match = re.match(r"^(.+?(?:区|县|旗))", rest)
        if dist_match:
            district = dist_match.group(1).strip()

    return _normalize_municipality_region(city_name, city_name, district)


# 省级简称（无「省/自治区」后缀）→ 标准全称
PROVINCE_SHORT_TO_FULL = {
    "河北": "河北省",
    "山西": "山西省",
    "辽宁": "辽宁省",
    "吉林": "吉林省",
    "黑龙江": "黑龙江省",
    "江苏": "江苏省",
    "浙江": "浙江省",
    "安徽": "安徽省",
    "福建": "福建省",
    "江西": "江西省",
    "山东": "山东省",
    "河南": "河南省",
    "湖北": "湖北省",
    "湖南": "湖南省",
    "广东": "广东省",
    "海南": "海南省",
    "四川": "四川省",
    "贵州": "贵州省",
    "云南": "云南省",
    "陕西": "陕西省",
    "甘肃": "甘肃省",
    "青海": "青海省",
    "台湾": "台湾省",
    "内蒙古": "内蒙古自治区",
    "广西": "广西壮族自治区",
    "西藏": "西藏自治区",
    "宁夏": "宁夏回族自治区",
    "新疆": "新疆维吾尔自治区",
}


def _ensure_city_suffix(city_name):
    # type: (str) -> str
    if not city_name:
        return ""
    if city_name.endswith(("市", "自治州", "地区", "盟", "州")):
        return city_name
    return city_name + "市"


def _parse_city_district_from_rest(rest):
    # type: (str) -> Tuple[str, str]
    """从省名之后的片段解析市、区县（支持省略「市」）。"""
    if not rest:
        return "", ""

    std = re.match(
        r"^(?P<city>.+?(?:市|自治州|地区|盟))"
        r"(?:(?P<dist>.+?(?:区|县|旗)))?",
        rest,
    )
    if std and std.group("city"):
        city_name = std.group("city").strip()
        # 排除误把详情里的「超市」等当成地级「市」
        if len(city_name) <= 12 and not city_name.endswith(("超市", "市场", "商场")):
            return city_name, (std.group("dist") or "").strip()

    # 省略「市」：唐山乐亭县 -> 唐山市 / 乐亭县
    short_city = re.match(
        r"^(?P<city>.{2,8}?)(?P<dist>.+?(?:县|区|旗))",
        rest,
    )
    if short_city:
        city = _ensure_city_suffix(short_city.group("city").strip())
        return city, short_city.group("dist").strip()

    loose = re.match(r"^(?P<city>.+?(?:市|自治州|地区|盟))", rest)
    if loose:
        city_name = loose.group("city").strip()
        if len(city_name) <= 12 and not city_name.endswith(("超市", "市场", "商场")):
            return city_name, ""

    return "", ""


def _parse_loose_region(text):
    # type: (str) -> Optional[Tuple[str, str, str]]
    """
    解析省略「省/市」后缀的地址，如：
    河北唐山乐亭县... -> 河北省 / 唐山市 / 乐亭县
    """
    municipality = _parse_direct_municipality(text)
    if municipality:
        return municipality

    # 已有「省/自治区」后缀
    full = re.match(
        r"^(?P<prov>.+?(?:省|自治区|特别行政区))(?P<rest>.+)$",
        text,
    )
    if full and full.group("prov"):
        province = full.group("prov").strip()
        city, district = _parse_city_district_from_rest(full.group("rest").strip())
        return province, city, district

    # 省略省后缀：河北唐山乐亭县
    for short_name in sorted(PROVINCE_SHORT_TO_FULL.keys(), key=len, reverse=True):
        if not text.startswith(short_name):
            continue
        if text.startswith(short_name + "省") or text.startswith(short_name + "自治区"):
            continue

        province = PROVINCE_SHORT_TO_FULL[short_name]
        rest = text[len(short_name) :].strip()
        city, district = _parse_city_district_from_rest(rest)
        return province, city, district

    return None


def _parser_conflicts_with_text(text, province, city, district):
    # type: (str, str, str, str) -> bool
    """解析结果与地址明文冲突时，丢弃 parser 结果。"""
    municipality_prefixes = MUNICIPALITY_NAMES
    for prefix in municipality_prefixes:
        if not text.startswith(prefix):
            continue
        if (
            province
            and province != "直辖市"
            and prefix not in province
            and not province.startswith(prefix[:2])
        ):
            return True
        if city and city.endswith("朝阳市") and "朝阳区" in text:
            return True
        if district and district.endswith("朝阳市") and "朝阳区" in text:
            return True

    # 明文是河北，却被解析成重庆等
    if text.startswith(("河北", "河北省")):
        joined = (province or "") + (city or "") + (district or "")
        if joined and "河北" not in joined:
            return True
        if "重庆" in joined and "唐山" in text:
            return True

    if "唐山" in text and city and "唐山" not in city and "重庆" in (province or "") + (city or ""):
        return True

    return False


def _parse_region_regex_fallback(text):
    # type: (str) -> Tuple[str, str, str]
    """简单规则切分省/市/区县。"""
    municipality = _parse_direct_municipality(text)
    if municipality:
        return municipality

    province = city = district = ""
    remain = text

    prov_match = re.match(r"^(.+?(?:省|自治区|特别行政区))(.+)$", remain)
    if prov_match:
        province = prov_match.group(1).strip()
        remain = prov_match.group(2).strip()
    elif remain.startswith(MUNICIPALITY_NAMES):
        municipality = _parse_direct_municipality(remain)
        if municipality:
            return municipality

    city_match = re.match(r"^(.+?(?:市|州|盟|地区|自治州))(.+)$", remain)
    if city_match:
        city = city_match.group(1).strip()
        remain = city_match.group(2).strip()

    dist_match = re.match(r"^(.+?(?:区|县|旗))", remain)
    if dist_match:
        district = dist_match.group(1).strip()

    return province, city, district


def parse_region_columns(address):
    # type: (str) -> Tuple[str, str, str]
    """解析收件人地址，返回 (省, 市, 区县)。"""
    text = prepare_address_text(address)
    if not text:
        return "", "", ""

    # 1. 直辖市明文优先
    municipality = _parse_direct_municipality(text)
    if municipality:
        return municipality

    # 2. 省略「省/市」后缀的常见写法（如 河北唐山乐亭县）
    loose = _parse_loose_region(text)
    if loose:
        return _normalize_municipality_region(*loose)

    # 3. address_parser
    try:
        parsed = _ADDRESS_PARSER.parse(text)
        province = (parsed.get("province") or "").strip()
        city = (parsed.get("city") or "").strip()
        district = (parsed.get("district") or "").strip()
        if (province or city or district) and not _parser_conflicts_with_text(
            text, province, city, district
        ):
            return _normalize_municipality_region(province, city, district)
    except Exception:
        pass

    # 4. 规则兜底
    return _normalize_municipality_region(*_parse_region_regex_fallback(text))


def build_original_address_column(address):
    # type: (str) -> str
    """始终保留飞书原始收件人地址。"""
    return (address or "").strip()


def empty_result_row(num, status="", match_field=""):
    # type: (str, str, str) -> dict
    return {
        "编号": num,
        "状态": status,
        "匹配列": match_field,
        "省": "",
        "市": "",
        "区县": "",
        "原地址": "",
    }


def list_all_records(token, app_token, table_id, view_id):
    url = "{}/open-apis/bitable/v1/apps/{}/tables/{}/records".format(
        FEISHU_HOST, app_token, table_id
    )
    headers = {"Authorization": "Bearer {}".format(token)}
    all_items = []
    page_token = None
    page_index = 0

    while True:
        page_index += 1
        params = {"page_size": PAGE_SIZE, "view_id": view_id}
        if page_token:
            params["page_token"] = page_token

        resp = _request("GET", url, headers=headers, params=params)
        payload = resp.json()
        if payload.get("code") != 0:
            raise RuntimeError("拉取记录失败：{}".format(payload))

        data = payload.get("data") or {}
        items = data.get("items") or []
        all_items.extend(items)
        print("  拉取第 {} 页，累计 {} 条 ...".format(page_index, len(all_items)))

        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
        time.sleep(0.15)

    return all_items


def _extract_record_time(item):
    # type: (dict) -> int
    """取记录修改/创建时间，用于判断新旧。"""
    for key in ("last_modified_time", "created_time"):
        val = item.get(key)
        if val is None:
            continue
        try:
            return int(val)
        except (TypeError, ValueError):
            continue
    return 0


def _entry_priority(match_field, address, item, seq):
    # type: (str, str, dict, int) -> Tuple[int, int, int, int]
    """
    记录优先级（越大越应保留）：
    1. 编号列 > 编号_往月
    2. 有收件人地址 > 无地址
    3. 修改时间越新越好
    4. 同条件下后出现视为更新
    """
    has_content = 1 if (address or "").strip() else 0
    is_current = 1 if match_field == FIELD_NUMBER else 0
    return (is_current, has_content, _extract_record_time(item), seq)


def _should_keep_new(old_pri, old_addr, new_pri, new_addr):
    # type: (Tuple[int, int, int, int], str, Tuple[int, int, int, int], str) -> bool
    """同一编号重复时：保留更新的且有内容的记录。"""
    old_has = bool((old_addr or "").strip())
    new_has = bool((new_addr or "").strip())

    # 编号列优先于编号_往月；若新记录来自编号列且有内容，可覆盖往月记录
    if new_pri[0] != old_pri[0]:
        if new_pri[0] > old_pri[0]:
            return new_has or not old_has
        return False

    new_newer = new_pri[2:] > old_pri[2:]
    if not new_newer:
        # 不是更新记录：仅当旧记录无地址、新记录有地址时才替换
        return new_has and not old_has

    # 更新记录：有内容则保留新的；新记录无内容但旧记录有内容则保留旧的
    if new_has:
        return True
    return not old_has


def _upsert_index_entry(index, num, match_field, address, fields, item, seq):
    # type: (Dict[str, Tuple], str, str, str, dict, dict, int) -> None
    new_pri = _entry_priority(match_field, address, item, seq)
    if num not in index:
        index[num] = (match_field, address, fields, new_pri)
        return

    old_match, old_addr, old_fields, old_pri = index[num]
    if _should_keep_new(old_pri, old_addr, new_pri, address):
        index[num] = (match_field, address, fields, new_pri)


def build_number_index(records):
    # type: (List[dict]) -> Dict[str, Tuple[str, str, dict]]
    """
    编号 -> (匹配列名, 收件人地址, 原始 fields)

    同一编号重复时：保留较新且有内容的记录；编号列优先于编号_往月。
    """
    index = {}  # type: Dict[str, Tuple[str, str, dict]]

    for seq, item in enumerate(records):
        fields = item.get("fields") or {}
        address = extract_text(fields.get(FIELD_ADDRESS))

        current_num = normalize_number(fields.get(FIELD_NUMBER))
        if current_num:
            _upsert_index_entry(
                index, current_num, FIELD_NUMBER, address, fields, item, seq
            )

        past_num = normalize_number(fields.get(FIELD_NUMBER_PAST))
        if past_num:
            _upsert_index_entry(
                index, past_num, FIELD_NUMBER_PAST, address, fields, item, seq
            )

    # 去掉内部优先级字段，对外仍返回三元组
    return {
        num: (match_field, address, fields)
        for num, (match_field, address, fields, _pri) in index.items()
    }


def parse_numbers_from_args(numbers, input_path):
    # type: (List[str], str) -> List[str]
    result = []  # type: List[str]
    seen = set()  # type: Set[str]

    if input_path:
        text = Path(input_path).read_text(encoding="utf-8")
        for line in text.splitlines():
            num = normalize_number(line)
            if num and num not in seen:
                seen.add(num)
                result.append(num)

    for raw in numbers:
        num = normalize_number(raw)
        if num and num not in seen:
            seen.add(num)
            result.append(num)

    return result


def collect_numbers_from_records(records, number_field=FIELD_NUMBER):
    # type: (List[dict], str) -> List[str]
    """
    从飞书记录中按指定编号列提取待查编号（去重且保留原顺序）。

    默认只取「编号」列，满足“从编号列获取编号”场景。
    """
    result = []  # type: List[str]
    seen = set()  # type: Set[str]

    for item in records:
        fields = item.get("fields") or {}
        num = normalize_number(fields.get(number_field))
        if not num or num in seen:
            continue
        seen.add(num)
        result.append(num)
    return result


def lookup_addresses(target_numbers, index):
    # type: (List[str], Dict[str, Tuple[str, str, dict]]) -> List[dict]
    rows = []
    for num in target_numbers:
        if num in index:
            match_field, address, _fields = index[num]
            province, city, district = parse_region_columns(address)
            row = empty_result_row(num, "已找到", match_field)
            row["省"] = province
            row["市"] = city
            row["区县"] = district
            row["原地址"] = build_original_address_column(address)
            rows.append(row)
        else:
            rows.append(empty_result_row(num, "未找到"))
    return rows


def save_csv(rows, output_path):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print("已保存：{}".format(path))


def print_table(rows):
    # type: (List[dict]) -> None
    found = sum(1 for r in rows if r["状态"] == "已找到")
    missing = len(rows) - found
    print()
    print("=== 查询结果：找到 {} / {}，未找到 {} ===".format(found, len(rows), missing))
    print()
    # 结果很多时仅预览前 300 条，避免终端输出过大且减少编码异常风险。
    preview_limit = 300
    for idx, row in enumerate(rows):
        if idx >= preview_limit:
            break
        if row["状态"] == "已找到":
            raw_address = (row["原地址"] or "").replace("\xa0", " ").strip()
            print(
                "{} [{}] {} / {} / {} | {}".format(
                    row["编号"],
                    row["匹配列"],
                    row["省"] or "(省空)",
                    row["市"] or "(市空)",
                    row["区县"] or "(区县空)",
                    raw_address or "(原地址空)",
                )
            )
        else:
            print("{} [未找到]".format(row["编号"]))
    if len(rows) > preview_limit:
        print("... 已省略后续 {} 条，请查看 CSV 全量结果。".format(len(rows) - preview_limit))


def main():
    parser = argparse.ArgumentParser(description="按编号查飞书表收件人地址")
    parser.add_argument("--numbers", nargs="*", default=[], help="要查的编号，可多个")
    parser.add_argument("--input", default="", help="从文本文件读取编号，一行一个")
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parent / "收件人地址查询结果.csv"),
        help="结果 CSV 路径",
    )
    args = parser.parse_args()

    print(
        "开始拉取编号来源表 {} / 视图 {} ...".format(
            SOURCE_TABLE_ID, SOURCE_VIEW_ID
        )
    )

    token = get_tenant_access_token()
    source_records = list_all_records(
        token, SOURCE_APP_TOKEN, SOURCE_TABLE_ID, SOURCE_VIEW_ID
    )
    print("编号来源记录总数：{}".format(len(source_records)))

    print(
        "开始拉取地址查询表 {} / 视图 {} ...".format(
            LOOKUP_TABLE_ID, LOOKUP_VIEW_ID
        )
    )
    lookup_records = list_all_records(
        token, LOOKUP_APP_TOKEN, LOOKUP_TABLE_ID, LOOKUP_VIEW_ID
    )
    print("地址查询记录总数：{}".format(len(lookup_records)))

    # 优先使用命令行显式传入编号；未传时，自动从当前视图「编号」列收集全部编号。
    target_numbers = parse_numbers_from_args(args.numbers, args.input)
    if not target_numbers:
        target_numbers = collect_numbers_from_records(source_records, FIELD_NUMBER)
        print("未传 --numbers/--input，已从「{}」列自动提取编号。".format(FIELD_NUMBER))
    print("待查编号数量：{}".format(len(target_numbers)))

    index = build_number_index(lookup_records)
    print("编号索引数量：{}（含编号 + 编号_往月）".format(len(index)))

    rows = lookup_addresses(target_numbers, index)
    print_table(rows)
    save_csv(rows, args.output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("[错误] {}".format(exc))
        raise SystemExit(1)
