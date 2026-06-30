"""
LLM 智能兜底模块：当 Trie + ES 均无法解析地址时，调用大模型进行结构化提取。

支持的接口：
  - OpenAI / 兼容接口（DeepSeek、Moonshot、通义千问等）
  - 本地 Ollama

配置方式（二选一）：
  1. 环境变量（推荐）：
       export LLM_API_KEY="sk-xxx"
       export LLM_BASE_URL="https://api.deepseek.com/v1"   # 可选，默认 OpenAI
       export LLM_MODEL="deepseek-chat"                    # 可选
  2. 实例化时传参：
       LLMBackend(api_key="sk-xxx", base_url="...", model="...")

依赖：
  pip install openai
"""

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── 默认配置 ──────────────────────────────────────────────
DEFAULT_MODEL    = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

# System prompt：要求 LLM 只输出 JSON，不输出多余内容
SYSTEM_PROMPT = """你是一个中国地址结构化解析助手。
用户会给你一段地址文本，你需要从中提取出：
- province：省份（如"广东省"、"北京市"）
- city：城市（如"深圳市"、"广州市"）
- district：区/县（如"南山区"、"天河区"）
- detail_address：去掉省市区后的详细地址

输出规则：
1. 只输出 JSON，不输出任何解释或多余文字
2. 无法识别的字段填 null
3. 省市区名称需补全行政后缀（如"广东"→"广东省"，"深圳"→"深圳市"）

示例输入：魔都浦东新区张江高科技园区
示例输出：{"province":"上海市","city":"上海市","district":"浦东新区","detail_address":"张江高科技园区"}
"""


class LLMBackend:
    """
    LLM 地址解析兜底后端。
    未安装 openai 包或未配置 API Key 时，调用会抛出明确异常，
    不影响 AddressParser 的正常运行（由 parser.py 负责 try/except）。
    """

    def __init__(
        self,
        api_key:  Optional[str] = None,
        base_url: Optional[str] = None,
        model:    Optional[str] = None,
        timeout:  int = 15,
    ):
        """
        :param api_key:  API Key（优先于环境变量）
        :param base_url: 接口地址（默认 OpenAI 官方，可换成 DeepSeek 等兼容地址）
        :param model:    模型名称
        :param timeout:  请求超时秒数
        """
        try:
            from openai import OpenAI
            self._OpenAI = OpenAI
        except ImportError:
            raise ImportError(
                "openai 未安装，请执行: pip install openai"
            )

        # 优先使用传参，其次读取环境变量
        self._api_key  = api_key  or os.getenv("LLM_API_KEY",  "")
        self._base_url = base_url or os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)
        self._model    = model    or os.getenv("LLM_MODEL",    DEFAULT_MODEL)
        self._timeout  = timeout

        if not self._api_key:
            raise ValueError(
                "LLM API Key 未配置。\n"
                "请设置环境变量 LLM_API_KEY，或在实例化时传入 api_key 参数。\n"
                "示例：LLMBackend(api_key='sk-xxx', base_url='https://api.deepseek.com/v1', model='deepseek-chat')"
            )

        # 初始化 OpenAI 客户端
        self._client = self._OpenAI(
            api_key  = self._api_key,
            base_url = self._base_url,
            timeout  = self._timeout,
        )
        logger.info("LLM 后端初始化成功: model=%s, base_url=%s", self._model, self._base_url)

    # ── 核心接口 ──────────────────────────────────────────

    def parse(self, text: str) -> dict:
        """
        调用 LLM 解析地址，返回结构化字典。

        :param text: 原始地址文本
        :return:     {province, city, district, detail_address}，解析失败时各字段为 None
        """
        logger.info("LLM 兜底解析: %r", text)

        try:
            response = self._client.chat.completions.create(
                model    = self._model,
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": text},
                ],
                temperature       = 0,       # 零温度，保证输出稳定
                max_tokens        = 200,
                response_format   = {"type": "json_object"},  # 强制 JSON 输出（部分模型支持）
            )
            raw = response.choices[0].message.content or ""
            return self._parse_response(raw)

        except Exception as e:
            logger.error("LLM 调用失败: %s", e)
            return self._empty_result()

    # ── 私有辅助 ──────────────────────────────────────────

    def _parse_response(self, raw: str) -> dict:
        """
        从 LLM 返回文本中提取 JSON。
        兼容两种情况：纯 JSON 输出、JSON 嵌在 markdown 代码块中。
        """
        # 尝试提取 ```json ... ``` 代码块
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        json_str = match.group(1) if match else raw.strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("LLM 返回内容无法解析为 JSON: %r", raw[:200])
            return self._empty_result()

        # 标准化字段名（兼容 LLM 可能返回不同字段名）
        return {
            "province":       data.get("province")       or data.get("省份") or None,
            "city":           data.get("city")           or data.get("城市") or None,
            "district":       data.get("district")       or data.get("区县") or None,
            "detail_address": data.get("detail_address") or data.get("详细地址") or None,
        }

    @staticmethod
    def _empty_result() -> dict:
        """返回空结果，各字段均为 None。"""
        return {
            "province":       None,
            "city":           None,
            "district":       None,
            "detail_address": None,
        }

    def is_available(self) -> bool:
        """检查 LLM 后端是否可用（API Key 已配置且包已安装）。"""
        return bool(self._api_key)
