"""
Elasticsearch 地址搜索后端。

功能：
  - 将 district_db.json 数据批量索引到 ES
  - 提供 search(text) 接口，返回结构化的省/市/区候选
  - 作为 parser.py Trie 精确匹配的 fallback 层

依赖：
  pip install elasticsearch==8.*

ES 搭建（Docker 快速启动）：
  docker run -d --name es8 \
    -e "discovery.type=single-node" \
    -e "xpack.security.enabled=false" \
    -p 9200:9200 \
    docker.elastic.co/elasticsearch/elasticsearch:8.13.4

索引初始化（首次使用前执行一次）：
  from address_parser.es_backend import ESBackend
  backend = ESBackend()
  backend.build_index(db)   # db 为 district_db.json 加载后的列表
"""

import json
import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

# ES 索引名
INDEX_NAME = "address_districts"

# 索引 Mapping：对 name 字段开启中文分词（需 ik 插件）和关键字精确匹配
INDEX_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                # 若安装了 ik 插件则使用细粒度分词，否则用标准分词
                "default": {"type": "ik_max_word"} if False else {"type": "standard"}
            }
        },
    },
    "mappings": {
        "properties": {
            "name":     {"type": "text",    "analyzer": "ik_max_word", "fields": {"keyword": {"type": "keyword"}}},
            "level":    {"type": "keyword"},
            "province": {"type": "keyword"},
            "parent":   {"type": "keyword"},
            "code":     {"type": "keyword"},
        }
    },
}


class ESBackend:
    """
    Elasticsearch 地址搜索后端。
    所有 ES 相关依赖在 __init__ 中懒加载，
    未安装时不影响 AddressParser 的正常使用。
    """

    def __init__(
        self,
        hosts: Optional[List[str]] = None,
        index_name: str = INDEX_NAME,
    ):
        """
        :param hosts:      ES 节点地址列表，默认 ["http://localhost:9200"]
        :param index_name: ES 索引名
        """
        try:
            from elasticsearch import Elasticsearch, helpers
            self._helpers = helpers
        except ImportError:
            raise ImportError(
                "elasticsearch 未安装，请执行: pip install 'elasticsearch>=8,<9'"
            )

        self._hosts = hosts or ["http://localhost:9200"]
        self._index = index_name
        self._es = Elasticsearch(self._hosts)

        # 验证连接
        if not self._es.ping():
            raise ConnectionError(
                f"无法连接到 Elasticsearch: {self._hosts}\n"
                "请确认 ES 已启动，或参考文件头部的 Docker 启动命令。"
            )
        logger.info("ES 连接成功: %s", self._hosts)

    # ── 索引管理 ──────────────────────────────────────────

    def build_index(self, db: List[dict], batch_size: int = 500) -> None:
        """
        将行政区划数据批量写入 ES 索引（幂等操作，可重复执行）。

        :param db:         district_db.json 加载后的列表
        :param batch_size: 每批写入条数
        """
        # 删除旧索引（如存在）
        if self._es.indices.exists(index=self._index):
            self._es.indices.delete(index=self._index)
            logger.info("已删除旧索引: %s", self._index)

        # 创建索引
        self._es.indices.create(index=self._index, body=INDEX_MAPPING)
        logger.info("已创建索引: %s", self._index)

        # 构建批量写入动作
        def generate():
            for item in db:
                yield {
                    "_index": self._index,
                    "_source": {
                        "name":     item.get("name", ""),
                        "level":    item.get("level", ""),
                        "province": item.get("province", ""),
                        "parent":   item.get("parent", ""),
                        "code":     item.get("code", ""),
                    },
                }

        success, errors = self._helpers.bulk(
            self._es, generate(), chunk_size=batch_size, raise_on_error=False
        )
        logger.info("索引完成: 成功 %d 条, 失败 %d 条", success, len(errors))
        if errors:
            logger.warning("部分写入失败: %s", errors[:5])

    # ── 搜索接口 ──────────────────────────────────────────

    def search(
        self,
        text: str,
        levels: Optional[List[str]] = None,
        size: int = 5,
    ) -> List[dict]:
        """
        在 ES 中搜索地名。

        :param text:   查询文本（地址片段）
        :param levels: 限定级别，如 ["province", "city", "district"]，None 表示不限
        :param size:   最多返回条数
        :return:       匹配的地名记录列表，每条含 name/level/province/parent
        """
        # 构建查询：multi_match 对 name 字段做全文搜索
        query: dict = {
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query":  text,
                                "fields": ["name^3", "name.keyword"],
                                "type":   "best_fields",
                                "fuzziness": "AUTO",  # 自动容错（即 ES 内置模糊匹配）
                            }
                        }
                    ]
                }
            },
            "size": size,
        }

        # 若指定级别则加过滤条件
        if levels:
            query["query"]["bool"]["filter"] = [
                {"terms": {"level": levels}}
            ]

        resp = self._es.search(index=self._index, body=query)
        hits = resp.get("hits", {}).get("hits", [])

        return [hit["_source"] for hit in hits]

    def search_structured(self, text: str) -> Dict[str, Optional[str]]:
        """
        结构化搜索：分别对省/市/区三级做搜索，返回最佳候选。

        :param text: 地址文本
        :return:     { "province": ..., "city": ..., "district": ... }
                     未命中的字段为 None
        """
        result: Dict[str, Optional[str]] = {"province": None, "city": None, "district": None}

        for level in ("province", "city", "district"):
            hits = self.search(text, levels=[level], size=3)
            if hits:
                # 取分数最高的第一条
                result[level] = hits[0]["name"]

        return result

    def is_available(self) -> bool:
        """检查 ES 连接是否正常。"""
        try:
            return self._es.ping()
        except Exception:
            return False
