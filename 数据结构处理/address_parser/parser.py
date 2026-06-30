"""
地址解析主模块。

解析流程（三级 fallback 链）：
  1. Trie 精确匹配（含简称、俗称、别名）
  2. 拼音匹配（纯拼音输入时启用）
  3. 错别字纠错后重新 Trie 匹配
  4. ES 模糊搜索（需安装 elasticsearch 并初始化索引）
  5. LLM 智能兜底（需配置 LLM_API_KEY 环境变量）

对外接口（与原版完全兼容）：
  parser.parse(text) → {name, phone, province, city, district, detail_address}
"""

import logging
import os
import re
import json
from typing import Optional

from address_parser.trie import Trie

logger = logging.getLogger(__name__)


class AddressParser:

    def __init__(
        self,
        db_path: str,
        abbrev_path: Optional[str] = None,
        alias_path:  Optional[str] = r"D:\桌面文件\新建文件夹\数据结构处理\address_parser\city_alias.json",
        enable_pinyin:    bool = True,
        enable_corrector: bool = True,
        enable_es:        bool = False,
        enable_llm:       bool = False,
        es_hosts:         Optional[list] = None,
        llm_api_key:      Optional[str] = None,
        llm_base_url:     Optional[str] = None,
        llm_model:        Optional[str] = None,
    ):
        """
        :param db_path:        district_db.json 路径（必填）
        :param abbrev_path:    简称映射 JSON 路径（可选）
        :param alias_path:     城市俗称/别名 JSON 路径（可选，对应 city_alias.json）
        :param enable_pinyin:  是否启用拼音匹配（需 pip install pypinyin）
        :param enable_corrector: 是否启用错别字纠错
        :param enable_es:      是否启用 ES 搜索后端（需 pip install elasticsearch）
        :param enable_llm:     是否启用 LLM 兜底（需配置 LLM_API_KEY）
        :param es_hosts:       ES 节点地址列表，默认 ["http://localhost:9200"]
        :param llm_api_key:    LLM API Key（也可通过环境变量 LLM_API_KEY 设置）
        :param llm_base_url:   LLM 接口地址（默认 OpenAI 官方）
        :param llm_model:      LLM 模型名称
        """
        with open(db_path, "r", encoding="utf8") as f:
            self.db = json.load(f)

        # 建立 name → db记录 的快速查找
        self._name_index = {item["name"]: item for item in self.db}

        # 建立父子关系索引，替代 complete_region 中的线性遍历
        # city_name → province_name
        self._city_to_province = {
            item["name"]: item["province"]
            for item in self.db
            if item.get("level") == "city"
        }
        # district_name → (parent_city_name, province_name)
        self._district_to_city = {
            item["name"]: (item.get("parent", ""), item.get("province", ""))
            for item in self.db
            if item.get("level") == "district"
        }
        # city_name → set(district_name)，用于快速验证区县归属
        # 直辖市的"市辖区"等虚拟中间层，同时也映射到 province 名下，
        # 使得省份校验步骤能正确识别直辖市下的区县
        _virtual = {"市辖区", "县", "省直辖县级行政区划", "自治区直辖县级行政区划"}
        self._city_districts: dict = {}
        for item in self.db:
            if item.get("level") == "district":
                parent = item.get("parent", "")
                self._city_districts.setdefault(parent, set()).add(item["name"])
                # 如果 parent 是虚拟中间层，同时挂到 province 名下
                if parent in _virtual:
                    prov = item.get("province", "")
                    if prov:
                        self._city_districts.setdefault(prov, set()).add(item["name"])

        self.trie = Trie()
        self._build_trie()

        # 可选：读取简称映射，把区县/城市简称也插入 Trie
        if abbrev_path:
            with open(abbrev_path, "r", encoding="utf8") as f:
                self._abbrev_map = json.load(f)
            self._build_abbrev_trie()
        else:
            self._abbrev_map = {}

        # 可选：读取城市俗称/别名，插入 Trie（如"魔都"→上海市）
        if alias_path:
            self._load_city_alias(alias_path)

        # 构建省份简称映射，用于 detail 清理
        self._province_aliases = self._build_province_aliases()

        # ── 可选功能模块（懒加载，失败时降级）────────────────

        # 拼音索引
        self._pinyin_index = None
        if enable_pinyin:
            try:
                from address_parser.pinyin_index import PinyinIndex
                self._pinyin_index = PinyinIndex(self.db)
                logger.info("拼音索引已加载")
            except ImportError as e:
                logger.warning("拼音模块不可用（%s），已跳过", e)

        # 错别字纠错器
        self._corrector = None
        if enable_corrector:
            try:
                from address_parser.corrector import Corrector
                self._corrector = Corrector(self.db)
                logger.info("错别字纠错器已加载")
            except Exception as e:
                logger.warning("纠错模块加载失败（%s），已跳过", e)

        # ES 后端
        self._es_backend = None
        if enable_es:
            try:
                from address_parser.es_backend import ESBackend
                self._es_backend = ESBackend(hosts=es_hosts)
                logger.info("ES 后端已连接")
            except Exception as e:
                logger.warning("ES 后端不可用（%s），已跳过", e)

        # LLM 后端
        self._llm_backend = None
        if enable_llm:
            try:
                from address_parser.llm_backend import LLMBackend
                self._llm_backend = LLMBackend(
                    api_key  = llm_api_key,
                    base_url = llm_base_url,
                    model    = llm_model,
                )
                logger.info("LLM 后端已初始化")
            except Exception as e:
                logger.warning("LLM 后端不可用（%s），已跳过", e)

    def _build_trie(self):
        for item in self.db:
            name = item["name"]
            self.trie.insert(name, item)

    def _load_city_alias(self, alias_path: str) -> None:
        """
        加载城市俗称/别名文件（city_alias.json），将所有俗称插入 Trie。
        格式：{ 俗称: 行政区全称 }，全称须与 district_db.json 中 name 一致。
        以 '_' 开头的 key 视为注释字段，跳过。
        """
        with open(alias_path, "r", encoding="utf8") as f:
            alias_map: dict = json.load(f)

        inserted = 0
        for alias, full_name in alias_map.items():
            # 跳过注释字段
            if alias.startswith("_"):
                continue
            item = self._name_index.get(full_name)
            if item:
                entry = dict(item)
                entry["_matched_key"] = alias
                self.trie.insert(alias, entry)
                inserted += 1
            else:
                logger.debug("城市别名 %r 对应的全称 %r 不在数据库中，已跳过", alias, full_name)

        logger.info("城市别名库已加载: %d 条", inserted)

    def _build_abbrev_trie(self):
        """
        遍历简称映射，将所有简称插入 Trie。
        省份简称（通常1-2字）因太短易误匹配，不插入 Trie；
        省份通过 complete_region 从市名反推，或通过全名直接匹配。
        城市和区县简称正常插入，但单字简称（长度=1）跳过，避免误匹配。

        兼容两种格式：
          格式A：{ 城市: [["别名1","别名2"], {区县字典}] }  ← 标准格式
          格式B：{ 城市: ["别名1","别名2"] }               ← 简化格式（无区县）
        """
        def insert_if_exists(abbrev, full_name):
            # 单字别名极易误匹配，跳过
            if len(abbrev) <= 1:
                return
            item = self._name_index.get(full_name)
            if item:
                entry = dict(item)
                entry["_matched_key"] = abbrev
                self.trie.insert(abbrev, entry)

        def parse_city_entry(city_full, city_data):
            """
            解析城市条目，返回 (别名列表, 区县字典或None)。
            兼容格式A和格式B。
            """
            if not city_data:
                return [], None
            # 判断 city_data[0] 是列表（格式A）还是字符串（格式B）
            if isinstance(city_data[0], list):
                # 格式A：[["别名1","别名2"], {区县字典}]
                abbrevs = city_data[0]
                dist_dict = city_data[1] if len(city_data) > 1 and isinstance(city_data[1], dict) else None
            elif isinstance(city_data[0], str):
                # 格式B：["别名1","别名2"]
                abbrevs = city_data
                dist_dict = None
            else:
                abbrevs = []
                dist_dict = None
            return abbrevs, dist_dict

        for section_key, section_val in self._abbrev_map.items():

            if section_key == "直辖市":
                for city_full, city_data in section_val.items():
                    if not isinstance(city_data, list) or len(city_data) < 1:
                        continue
                    abbrevs, dist_dict = parse_city_entry(city_full, city_data)
                    # 直辖市简称（如"京"、"沪"）保留，因为直辖市名本身就是省级
                    for abbrev in abbrevs:
                        insert_if_exists(abbrev, city_full)
                    if dist_dict:
                        for dist_full, dist_abbrevs in dist_dict.items():
                            for abbrev in dist_abbrevs:
                                insert_if_exists(abbrev, dist_full)
            else:
                if not isinstance(section_val, list) or len(section_val) < 1:
                    continue
                # ★ 省份简称不插入 Trie，避免单字/双字误匹配正文
                if len(section_val) > 1 and isinstance(section_val[1], dict):
                    for city_full, city_data in section_val[1].items():
                        if not isinstance(city_data, list) or len(city_data) < 1:
                            continue
                        abbrevs, dist_dict = parse_city_entry(city_full, city_data)
                        for abbrev in abbrevs:
                            insert_if_exists(abbrev, city_full)
                        if dist_dict:
                            for dist_full, dist_abbrevs in dist_dict.items():
                                for abbrev in dist_abbrevs:
                                    insert_if_exists(abbrev, dist_full)

    def _build_province_aliases(self):
        """
        为每个省份建立简称列表，例如：
          "内蒙古自治区" → ["内蒙古自治区", "内蒙古"]
          "广西壮族自治区" → ["广西壮族自治区", "广西"]
          "河北省" → ["河北省", "河北"]
        """
        aliases = {}
        for item in self.db:
            if item["level"] != "province":
                continue
            name = item["name"]
            variants = [name]
            # 去掉末尾常见后缀，保留核心名
            for suffix in ["族自治区", "自治区", "省"]:
                if name.endswith(suffix):
                    variants.append(name[: -len(suffix)])
                    break
            aliases[name] = variants
        return aliases

    def clean_text(self, text):

        text = text.replace(",", " ")
        text = text.replace("\n", " ")
        text = re.sub(r"\s+", " ", text)

        return text.strip()

    def extract_phone(self, text):

        phone_pattern = r"1[3-9]\d{9}"
        match = re.search(phone_pattern, text)

        if match:
            phone = match.group()
            text = text.replace(phone, "")
            return phone, text

        return None, text

    def extract_name(self, text):

        parts = text.split()

        if len(parts) > 0 and len(parts[0]) <= 3:
            name = parts[0]
            text = text.replace(name, "", 1)
            return name, text

        return None, text

    def match_regions(self, text):
        """
        扫描文本，按「首次出现位置」优先（而非名称长度）选取省/市/区匹配。
        同级别有多个候选时，取在原文中出现位置最靠前的那个；
        位置相同或均未直接出现时取名称更长的（更精确）。
        省份支持简称定位（如地址写"内蒙古"时能定位到"内蒙古自治区"）。
        """
        matches = self.trie.search(text)

        def find_pos(item):
            """返回该地名在 text 中最早的出现位置，未找到返回 len(text)"""
            name = item["name"]
            pos = text.find(name)
            if pos >= 0:
                return pos
            # 省份：尝试所有别名
            if item["level"] == "province":
                for alias in self._province_aliases.get(name, []):
                    p = text.find(alias)
                    if p >= 0:
                        return p
            return len(text)

        best = {}   # level -> (pos, name_len, item, matched_str)

        for m in matches:
            level = m["level"]
            # matched_str 是 Trie 中存的 key（即插入时用的字符串，可能是简称）
            matched_str = m.get("_matched_key", m["name"])
            pos = text.find(matched_str)
            if pos < 0:
                pos = len(text)
            # 省份补充：全名没出现时尝试别名
            if pos >= len(text) and level == "province":
                for alias in self._province_aliases.get(m["name"], []):
                    p = text.find(alias)
                    if p >= 0:
                        pos = p
                        matched_str = alias
                        break
            name_len = len(m["name"])

            if level not in best:
                best[level] = (pos, name_len, m, matched_str)
            else:
                cur_pos, cur_len, _, _ = best[level]
                if pos < cur_pos or (pos == cur_pos and name_len > cur_len):
                    best[level] = (pos, name_len, m, matched_str)

        # 省份：仅当真正出现在地址中（pos < len(text)）才采用，
        # 否则留 None 让 complete_region 从市/区反推
        def get_result(level):
            if level not in best:
                return None, None
            pos, _, item, matched_str = best[level]
            if level == "province" and pos >= len(text):
                return None, None
            return item["name"], matched_str

        province, province_str = get_result("province")
        city,     city_str     = get_result("city")
        district, district_str = get_result("district")

        # 把实际命中串挂到返回值上，供 extract_detail 使用
        self._last_matched = {
            "province": province_str,
            "city":     city_str,
            "district": district_str,
        }

        return province, city, district

    def complete_region(self, province, city, district):
        """
        反推缺失的上级行政区。
        使用预建索引，O(1) 查找，不再线性遍历整个数据库。
        """
        if district and not city:
            parent_city, parent_province = self._district_to_city.get(district, ("", ""))
            if parent_city:
                city = parent_city
            if parent_province and not province:
                province = parent_province

        if city and not province:
            province = self._city_to_province.get(city, province)

        return province, city, district

    # 无实意的中间层 city 名，直辖市结构中出现，需要特殊处理
    _VIRTUAL_CITIES = frozenset({
        "市辖区", "县", "省直辖县级行政区划", "自治区直辖县级行政区划"
    })

    def _pre_validate(
        self,
        province: Optional[str],
        city: Optional[str],
        district: Optional[str],
    ):
        """
        结构校验第一阶段（在 extract_detail 之前执行）：
          Step 0：处理直辖市"市辖区"等无实意中间层
          Step 1：校验 city 归属，province 不符时用数据库值覆盖
          Step 2：校验 district 归属，不属于 city 则清空

        不扫描 detail，不修改文本，只返回校验后的三元组。
        """
        # ── Step 0：处理直辖市"市辖区"等无实意中间层 ─────────
        if city and city in self._VIRTUAL_CITIES:
            city = province if province else None

        # ── Step 1：校验 city 归属，修正 province ──────────────
        if city:
            db_province = self._city_to_province.get(city)
            if db_province and db_province != province:
                logger.debug(
                    "province 校验：city=%r 应属于 %r，原值 %r 已修正",
                    city, db_province, province,
                )
                province = db_province

        # ── Step 2：校验 district 归属，不符则清空 ────────────
        if district and city:
            valid_districts = self._city_districts.get(city, set())
            if district not in valid_districts:
                logger.debug(
                    "district 校验：%r 不属于 city=%r，已清空",
                    district, city,
                )
                district = None

        return province, city, district

    def _post_validate(
        self,
        province: Optional[str],
        city: Optional[str],
        district: Optional[str],
        detail: str,
    ):
        """
        结构校验第二阶段（在 extract_detail 之后执行）：
          Step 3：district 为 None 时，对 detail_address 做 Trie 扫描，
                  取属于当前 city 的区县（长度≥3字）补全 district，
                  并从 detail 中去除命中的区县名。

        :return: (province, city, district, detail)
        """
        if district is None and city and detail:
            valid_districts = self._city_districts.get(city, set())
            if valid_districts:
                candidates = self.trie.search(detail)
                best_pos = len(detail)
                best_district = None

                for m in candidates:
                    if m.get("level") != "district":
                        continue
                    cand_name = m["name"]
                    # 名称过短（≤2字）容易误匹配非地名词，跳过
                    if len(cand_name) <= 2:
                        continue
                    # 必须属于当前 city
                    if cand_name not in valid_districts:
                        continue
                    pos = detail.find(cand_name)
                    if pos < 0:
                        continue
                    if pos < best_pos:
                        best_pos = pos
                        best_district = cand_name

                if best_district:
                    logger.debug(
                        "detail 扫描补全 district：%r（来源：%r）",
                        best_district, detail,
                    )
                    district = best_district
                    detail = detail.replace(best_district, "", 1).strip()

        return province, city, district, detail

    def _validate_and_complete(
        self,
        province: Optional[str],
        city: Optional[str],
        district: Optional[str],
        detail: str,
    ):
        """
        兼容旧调用：合并 _pre_validate + _post_validate。
        新流程中请直接使用 parse() 内的两阶段调用。
        """
        province, city, district = self._pre_validate(province, city, district)
        return self._post_validate(province, city, district, detail)



    def extract_detail(self, text, province, city, district):
        """
        从原始文本中删除省/市/区县部分，返回详细地址。
        使用 match_regions 记录的实际命中串（可能是简称）来删除，
        并自动吞掉紧跟其后的行政后缀（省/市/区/县/盟/州/旗等）。
        """
        matched = getattr(self, "_last_matched", {})

        ADMIN_SUFFIXES = ("省", "市", "区", "县", "旗", "盟", "州", "地区",
                          "自治区", "自治州", "自治县")

        def remove_region(t, matched_str, full_name, level=None):
            """
            在文本 t 中删除 matched_str（含其后可能紧跟的行政后缀）。
            若 matched_str 不在文本中，依次尝试：
              1. 省份：尝试所有别名（含简称，如"内蒙古"）
              2. 全名直接匹配
              3. 反查纠错词典：找到所有纠正后等于 full_name 的错误写法，
                 尝试在文本中删除（处理 complete_region 反推 city 但原文是错别字的情况）
            """
            if matched_str and matched_str in t:
                idx = t.find(matched_str)
                end = idx + len(matched_str)
                # 吞掉紧跟的行政后缀
                for suf in sorted(ADMIN_SUFFIXES, key=len, reverse=True):
                    if t[end:end + len(suf)] == suf:
                        end += len(suf)
                        break
                return t[:idx] + t[end:]
            # fallback 1：省份尝试所有已知别名（含简称，如"内蒙古"）
            if level == "province" and full_name:
                for alias in self._province_aliases.get(full_name, [full_name]):
                    if alias in t:
                        idx = t.find(alias)
                        end = idx + len(alias)
                        for suf in sorted(ADMIN_SUFFIXES, key=len, reverse=True):
                            if t[end:end + len(suf)] == suf:
                                end += len(suf)
                                break
                        return t[:idx] + t[end:]
            # fallback 2：全名直接匹配
            if full_name and full_name in t:
                return t.replace(full_name, "", 1)
            # fallback 3：反查纠错词典，找原文中可能存在的错误写法并删除
            # 用于处理 city 由 complete_region 反推、原文写的是错别字的情况
            # 例如 full_name="郑州市"，原文含"郑洲市"，直接查全名匹配不到
            if full_name and self._corrector:
                typo_dict = getattr(self._corrector, "_typo_dict", {})
                # 收集所有纠正后等于 full_name 的错误写法，按长度从长到短尝试
                candidates = sorted(
                    [wrong for wrong, right in typo_dict.items() if right == full_name],
                    key=len, reverse=True
                )
                for wrong in candidates:
                    if wrong in t:
                        idx = t.find(wrong)
                        end = idx + len(wrong)
                        # 吞掉紧跟的行政后缀（如"郑洲"后面的"市"）
                        for suf in sorted(ADMIN_SUFFIXES, key=len, reverse=True):
                            if t[end:end + len(suf)] == suf:
                                end += len(suf)
                                break
                        return t[:idx] + t[end:]
            return t

        # 省份
        text = remove_region(text, matched.get("province"), province, level="province")

        # 城市
        text = remove_region(text, matched.get("city"), city)

        # 区县
        text = remove_region(text, matched.get("district"), district)

        return text.strip()

    def parse(self, text: str) -> dict:
        """
        解析地址文本，返回结构化字典。

        三级 fallback 链：
          Level 1 — Trie 精确匹配（含简称/俗称）
          Level 2 — 错别字纠错后重试 Trie
          Level 3 — 拼音匹配（仅纯拼音输入）
          Level 4 — ES 模糊搜索（需 enable_es=True）
          Level 5 — LLM 智能兜底（需 enable_llm=True）

        返回字段：{name, phone, province, city, district, detail_address}
        """
        text = self.clean_text(text)

        phone, text = self.extract_phone(text)
        name,  text = self.extract_name(text)

        province = city = district = None
        detail = text

        # ── Level 1：Trie 精确匹配 ────────────────────────────
        province, city, district = self.match_regions(text)
        province, city, district = self.complete_region(province, city, district)

        # ── Level 2：错别字纠错后重试 ─────────────────────────
        # 触发条件：city 未命中（含省有市无、三者全无两种情况）
        # 省份已匹配但城市缺失时同样需要纠错，例如"内蒙古班头说"中
        # "内蒙古"由 Trie 命中省份，但"班头说"需纠错才能识别为"包头市"
        if not city and self._corrector:
            corrected_text, corrections = self._corrector.correct_text(text)
            if corrections:
                logger.debug("纠错：%s → %s，变更：%s", text, corrected_text, corrections)
                province, city, district = self.match_regions(corrected_text)
                province, city, district = self.complete_region(province, city, district)
                if any([province, city, district]):
                    # 用纠错后的文本提取 detail，同步更新 detail 以保持与 text 一致
                    text = corrected_text
                    detail = text

        # ── Level 3：拼音匹配（纯拼音输入） ──────────────────
        if not any([province, city, district]) and self._pinyin_index:
            if self._pinyin_index.is_pinyin_input(text):
                pinyin_hits = self._pinyin_index.query_from_text(text)
                if pinyin_hits:
                    # 按 province > city > district 优先取最高级别
                    for level in ("province", "city", "district"):
                        for hit in pinyin_hits:
                            if hit.get("level") == level:
                                if level == "province" and not province:
                                    province = hit["name"]
                                elif level == "city" and not city:
                                    city = hit["name"]
                                elif level == "district" and not district:
                                    district = hit["name"]
                    province, city, district = self.complete_region(province, city, district)
                    logger.debug("拼音匹配命中: province=%s city=%s district=%s", province, city, district)

        # ── Level 4：ES 模糊搜索 ──────────────────────────────
        if not any([province, city, district]) and self._es_backend:
            try:
                es_result = self._es_backend.search_structured(text)
                province = es_result.get("province") or province
                city     = es_result.get("city")     or city
                district = es_result.get("district") or district
                province, city, district = self.complete_region(province, city, district)
                if any([province, city, district]):
                    logger.debug("ES 匹配命中: %s", es_result)
            except Exception as e:
                logger.warning("ES 搜索失败: %s", e)

        # ── Level 5：LLM 智能兜底 ─────────────────────────────
        if not any([province, city, district]) and self._llm_backend:
            try:
                llm_result = self._llm_backend.parse(text)
                province = llm_result.get("province") or province
                city     = llm_result.get("city")     or city
                district = llm_result.get("district") or district
                # LLM 已返回 detail_address，直接使用
                if llm_result.get("detail_address"):
                    detail = llm_result["detail_address"]
                province, city, district = self.complete_region(province, city, district)
                logger.debug("LLM 兜底命中: %s", llm_result)
            except Exception as e:
                logger.warning("LLM 兜底失败: %s", e)

        # ── 结构校验第一阶段：仅校验/修正 province 和 district 归属 ──
        # 必须在 extract_detail 之前执行，防止错误的 district 被从文本中删除。
        district_before = district
        province, city, district = self._pre_validate(province, city, district)

        # 若 district 被校验清空，同步清除 _last_matched 中的记录，
        # 防止 extract_detail 仍使用旧命中串删除 detail 中的正常文字。
        if district_before is not None and district is None:
            if hasattr(self, "_last_matched"):
                self._last_matched["district"] = None

        # 提取详细地址（使用已校验的 province/city/district）
        if detail == text:
            detail = self.extract_detail(text, province, city, district)

        # ── 结构校验第二阶段：从 detail 扫描补全缺失的 district ─
        province, city, district, detail = self._post_validate(
            province, city, district, detail
        )

        return {
            "name":           name,
            "phone":          phone,
            "province":       province,
            "city":           city,
            "district":       district,
            "detail_address": detail,
        }

