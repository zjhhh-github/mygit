"""
错别字纠错模块：基于编辑距离 + 常见地名错别字词典，对地址中的错别字进行容错匹配。

策略（优先级由高到低）：
  1. 常见错别字词典直接替换（零开销，高精度）
  2. 编辑距离相似度匹配（容错，有一定开销）
     - 仅对长度 ≥ 2 的词做模糊匹配
     - 默认最大编辑距离为 1（可调）

依赖：
  - 无强制外部依赖（编辑距离为内置实现）
  - 可选：pip install rapidfuzz  （速度提升约 10x）
"""

import re
from typing import Optional, Dict, List, Tuple

# ── 尝试导入 rapidfuzz，不可用时降级为内置实现 ────────────
try:
    from rapidfuzz.distance import Levenshtein  # pyright: ignore[reportMissingImports]
    _USE_RAPIDFUZZ = True
except ImportError:
    _USE_RAPIDFUZZ = False


# ── 常见地名错别字词典 ──────────────────────────────────────
# 格式：{ 错误写法: 正确写法 }
# 正确写法须与 district_db.json 中 name 字段一致
TYPO_DICT: Dict[str, str] = {
    # 省级
    "内蒙古": "内蒙古自治区",
    "新疆": "新疆维吾尔自治区",
    "西藏": "西藏自治区",
    "广西": "广西壮族自治区",
    "宁夏": "宁夏回族自治区",

    # 常见错别字
    "浙江省": "浙江省",
    "哈滨": "哈尔滨市",
    "乌鲁木其": "乌鲁木齐市",
    "乌鲁木奇": "乌鲁木齐市",
    "呼和浩特": "呼和浩特市",
    "石家装": "石家庄市",
    "石家桩": "石家庄市",
    "石家庒": "石家庄市",
    "郑洲市": "郑州市",
    "郑洲": "郑州市",
    "兰洲": "兰州市",
    "惠州市": "惠州市",
    "深圳市": "深圳市",
    "广洲": "广州市",
    "广洲市": "广州市",
    "上海市": "上海市",
    "成都市": "成都市",
    "成都市": "成都市",
    "武汉市": "武汉市",
    "长沙市": "长沙市",
    "长沙": "长沙市",
    "西安市": "西安市",
    "南京市": "南京市",
    "杭洲": "杭州市",
    "杭洲市": "杭州市",
    "苏洲": "苏州市",
    "苏洲市": "苏州市",
    "厦门市": "厦门市",
    "福洲": "福州市",
    "贵洲": "贵州省",
    "贵洲省": "贵州省",
    "宁波市": "宁波市",
    "温洲": "温州市",
    "温洲市": "温州市",
    "绍兴市": "绍兴市",
    "合肥市": "合肥市",
    "南昌市": "南昌市",
    "昆明市": "昆明市",
    "太原市": "太原市",
    "沈阳市": "沈阳市",
    "大连市": "大连市",
    "长春市": "长春市",
    "哈尔滨市": "哈尔滨市",
    "海口市": "海口市",
    "银川市": "银川市",
    "西宁市": "西宁市",
    "拉萨市": "拉萨市",
    "南宁市": "南宁市",
    "桂林市": "桂林市",
    "重庆市": "重庆市",
    "天津市": "天津市",
    "北京市": "北京市",
    "班头说":"包头市",
    # 区/县级常见错别字
    "朝阳区": "朝阳区",
    "海甸区": "海淀区",
    "海甸": "海淀区",
    "丰泰区": "丰台区",
    "浦东新区": "浦东新区",
    "浦东区": "浦东新区",
    "天河区": "天河区",
    "白云区": "白云区",
    "越秀区": "越秀区",
    "南山区": "南山区",
    "福田区": "福田区",
    "龙岗区": "龙岗区",
    "宝安区": "宝安区",
}


def _edit_distance(s1: str, s2: str) -> int:
    """
    计算两个字符串的 Levenshtein 编辑距离（内置实现）。
    若已安装 rapidfuzz 则自动使用更快的实现。
    """
    if _USE_RAPIDFUZZ:
        return Levenshtein.distance(s1, s2)

    # 内置动态规划实现
    m, n = len(s1), len(s2)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if s1[i - 1] == s2[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = temp
    return dp[n]


class Corrector:
    """
    地名错别字纠错器。
    先查词典，再做编辑距离模糊匹配。
    """

    def __init__(
        self,
        db: List[dict],
        max_distance: int = 1,
        extra_typo_dict: Optional[Dict[str, str]] = None,
    ):
        """
        :param db:              district_db.json 列表
        :param max_distance:    允许的最大编辑距离（建议 1，过大会误匹配）
        :param extra_typo_dict: 额外的错别字词典，会与内置词典合并
        """
        self.max_distance = max_distance

        # 合并错别字词典
        self._typo_dict: Dict[str, str] = {**TYPO_DICT}
        if extra_typo_dict:
            self._typo_dict.update(extra_typo_dict)

        # 构建地名集合，用于编辑距离匹配
        # 只取省/市/区三级，街道级太多且不易出现错别字场景
        self._name_list: List[str] = [
            item["name"]
            for item in db
            if item.get("level") in ("province", "city", "district")
        ]

        # 按长度分组，匹配时只比对长度相近的候选，提升速度
        self._name_by_len: Dict[int, List[str]] = {}
        for name in self._name_list:
            ln = len(name)
            self._name_by_len.setdefault(ln, []).append(name)

    def correct_word(self, word: str) -> str:
        """
        对单个词语进行纠错，返回纠正后的词（若无匹配则返回原词）。

        :param word: 疑似包含错别字的地名词
        :return:     纠正后的词
        """
        # 1. 词典直接命中
        if word in self._typo_dict:
            return self._typo_dict[word]

        # 2. 词太短（1字）不做模糊匹配，误匹配率过高
        if len(word) <= 1:
            return word

        # 3. 编辑距离模糊匹配
        best_match: Optional[str] = None
        best_dist = self.max_distance + 1

        # 只对长度相近（±1）的候选做比较，减少计算量
        for cand_len in range(len(word) - 1, len(word) + 2):
            for candidate in self._name_by_len.get(cand_len, []):
                dist = _edit_distance(word, candidate)
                if dist < best_dist:
                    best_dist = dist
                    best_match = candidate

        return best_match if best_match and best_dist <= self.max_distance else word

    def correct_text(self, text: str) -> Tuple[str, List[Tuple[str, str]]]:
        """
        对整段地址文本进行纠错。

        策略：先对词典中的所有错误词做全文替换，再对剩余切词做编辑距离纠错。
        返回：(纠正后文本, [(原词, 纠正词), ...] 纠正记录)

        :param text: 原始地址文本
        :return:     (corrected_text, corrections)
        """
        sorted_typos = sorted(
            self._typo_dict.items(), key=lambda x: len(x[0]), reverse=True
        )
        corrections: List[Tuple[str, str]] = []
        for wrong, right in sorted_typos:
            if wrong in text and wrong != right:
                text = text.replace(wrong, right)
                corrections.append((wrong, right))

        return text, corrections

    def is_likely_typo(self, word: str, threshold: int = 1) -> bool:
        """
        判断一个词是否可能是错别字（在数据库中找不到精确匹配，但编辑距离很近）。

        :param word:      待判断的词
        :param threshold: 编辑距离阈值
        :return:          True 表示疑似错别字
        """
        if word in self._name_list:
            return False
        corrected = self.correct_word(word)
        return corrected != word
