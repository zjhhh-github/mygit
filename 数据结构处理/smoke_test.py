"""
冒烟测试：验证解析 + 结构校验 + district 补全功能
"""
import sys, logging
sys.path.insert(0, r"D:\桌面文件\新建文件夹\数据结构处理")
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")

from address_parser.parser import AddressParser

DB_PATH     = r"D:\桌面文件\新建文件夹\数据结构处理\address_parser\district_db.json"
ABBREV_PATH = r"D:\桌面文件\新建文件夹\数据结构处理\简称映射.json"
ALIAS_PATH  = r"D:\桌面文件\新建文件夹\数据结构处理\address_parser\city_alias.json"

parser = AddressParser(
    db_path=DB_PATH,
    abbrev_path=ABBREV_PATH,
    alias_path=ALIAS_PATH,
    enable_pinyin=False,
    enable_corrector=True,
    enable_es=False,
    enable_llm=False,
)

cases = [
    # (输入, 期望说明)
    ("张三 13800138000 内蒙古呼和浩特市农大东区",
     "核心用例：district 应补全为赛罕区（农大东区在赛罕区）或保持 None"),

    ("张三 13800138000 内蒙古呼和浩特市赛罕区农大东区",
     "district 已在地址中，应直接解析为赛罕区"),

    ("广东省深圳市南山区科技园南路",
     "正常三级完整地址"),

    ("魔都浦东新区张江高科技园区",
     "俗称+区县，province/city 应为上海市"),

    ("北京市朝阳区三里屯路19号",
     "直辖市，city 应等于 province"),

    ("郑洲金水区农业路",
     "错别字纠错：郑洲→郑州市，district=金水区"),
]

print("=" * 60)
for addr, desc in cases:
    result = parser.parse(addr)
    print(f"[{desc}]")
    print(f"  输入: {addr}")
    print(f"  province : {result['province']}")
    print(f"  city     : {result['city']}")
    print(f"  district : {result['district']}")
    print(f"  detail   : {result['detail_address']}")
    print(f"  name     : {result['name']}  phone: {result['phone']}")
    print()
