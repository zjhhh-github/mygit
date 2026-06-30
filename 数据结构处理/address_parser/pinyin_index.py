"""
拼音索引模块：将行政区名称转为拼音，支持拼音输入的地址识别。

功能：
  - 构建 拼音全拼 → 地名 的索引
  - 支持不带声调、首字母缩写两种匹配模式
  - 提供 query(pinyin_text) 接口，返回候选地名列表

依赖：
  pip install pypinyin
"""

import re
from typing import Optional, List, Dict

try:
    from pypinyin import lazy_pinyin, Style
    _PYPINYIN_AVAILABLE = True
except ImportError:
    _PYPINYIN_AVAILABLE = False


class PinyinIndex:
    """
    拼音索引：构建地名拼音 → 地名 的映射，支持拼音匹配。
    """

    def __init__(self, db: List[dict]):
        """
        :param db: district_db.json 加载后的列表，每条含 name / level 等字段
        """
        if not _PYPINYIN_AVAILABLE:
            raise ImportError(
                "pypinyin 未安装，请执行: pip install pypinyin"
            )

        # 全拼索引：{ "beijing": [{"name": "北京市", ...}, ...] }
        self._full_index: Dict[str, List[dict]] = {}
        # 首字母索引：{ "bj": [{"name": "北京市", ...}, ...] }
        self._initial_index: Dict[str, List[dict]] = {}

        self._build(db)

    # ── 构建索引 ──────────────────────────────────────────

    def _build(self, db: List[dict]) -> None:
        """遍历数据库，为每条地名建立全拼和首字母索引。"""
        for item in db:
            name = item.get("name", "")
            if not name:
                continue

            full_py  = self._to_full_pinyin(name)
            init_py  = self._to_initial_pinyin(name)

            # 全拼
            if full_py:
                self._full_index.setdefault(full_py, []).append(item)

            # 首字母（长度过短容易误匹配，只索引 ≥2 个字的地名）
            if init_py and len(name) >= 2:
                self._initial_index.setdefault(init_py, []).append(item)

    @staticmethod
    def _to_full_pinyin(text: str) -> str:
        """将中文转为无声调全拼拼接字符串，如 '北京' → 'beijing'。"""
        try:
            parts = lazy_pinyin(text, style=Style.NORMAL)
            return "".join(parts).lower()
        except Exception:
            return ""

    @staticmethod
    def _to_initial_pinyin(text: str) -> str:
        """将中文转为首字母串，如 '北京市' → 'bjs'。"""
        try:
            parts = lazy_pinyin(text, style=Style.FIRST_LETTER)
            return "".join(parts).lower()
        except Exception:
            return ""

    # ── 查询接口 ──────────────────────────────────────────

    def query(self, text: str, mode: str = "auto") -> List[dict]:
        """
        根据拼音文本查找匹配的地名列表。

        :param text:  输入文本，如 'beijing'、'bj'、'chengdu shi' 等
        :param mode:  匹配模式
                      'full'    - 仅全拼匹配
                      'initial' - 仅首字母匹配
                      'auto'    - 自动：先全拼，无结果再首字母
        :return: 匹配到的地名记录列表（可能多条）
        """
        # 清洗输入：去除空格和非字母字符，转小写
        clean = re.sub(r"[^a-zA-Z]", "", text).lower()
        if not clean:
            return []

        results: List[dict] = []

        if mode in ("full", "auto"):
            results = self._full_index.get(clean, [])

        if not results and mode in ("initial", "auto"):
            results = self._initial_index.get(clean, [])

        return results

    def query_from_text(self, text: str) -> List[dict]:
        """
        从混合文本中提取连续英文字母段，逐段尝试拼音匹配。
        适用于 "beijing shi chaoyang qu" 这类带空格的输入。

        :param text: 原始文本
        :return:     所有命中的地名记录列表（去重）
        """
        # 提取所有连续字母片段
        segments = re.findall(r"[a-zA-Z]+", text.lower())
        seen: set = set()
        results: List[dict] = []

        for seg in segments:
            for item in self.query(seg, mode="auto"):
                key = item["name"]
                if key not in seen:
                    seen.add(key)
                    results.append(item)

        return results

    def text_to_pinyin(self, text: str) -> str:
        """
        将任意中文文本转为全拼字符串（供外部调用）。
        如 '北京朝阳区' → 'beijingchaoyangqu'
        """
        return self._to_full_pinyin(text)

    def is_pinyin_input(self, text: str) -> bool:
        """
        判断输入是否为纯拼音（无中文字符）。
        用于 parser.py 决定是否启用拼音匹配分支。
        """
        # 只含字母、数字、空格、常见标点则视为拼音输入
        return bool(re.fullmatch(r"[a-zA-Z0-9\s\-,.']+", text.strip()))
