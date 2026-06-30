# -*- coding: utf-8 -*-
"""飞书多维表格客户端：替换影刀 xbot_extensions.activity_feishu_bitable。"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import time
from typing import Any, Dict, List, Optional

import requests

from utils import 分批


class FeishuError(RuntimeError):
    pass


class FeishuBitableClient:
    def __init__(self, app_id: str, app_secret: str, app_token: str, batch_size: int = 1000):
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.batch_size = batch_size
        self.base_url = "https://open.feishu.cn/open-apis"
        self._tenant_access_token: Optional[str] = None
        self._token_expire_at = 0.0
        # 飞书开放平台偶发返回“数据未就绪”，并发读取时更常见。
        # 这里统一做轻量重试，减少一次性失败概率。
        self.max_retry = 5
        self.retry_sleep_seconds = 0.8

    def _request_records_page(
        self,
        table_id: str,
        view_id: str = "",
        page_size: int = 500,
        page_token: str | None = None,
        field_names: List[str] | None = None,
    ) -> dict:
        """
        请求单页 records，返回 data 节点。

        说明：
        - page_size 最大 500；
        - field_names 传入后，仅拉取业务需要字段以减少网络负载。
        """
        params: Dict[str, Any] = {"page_size": min(max(int(page_size), 1), 500)}
        if page_token:
            params["page_token"] = page_token
        if view_id:
            params["view_id"] = view_id
        if field_names:
            # 飞书接口要求 field_names 为 JSON 数组字符串。
            params["field_names"] = "[" + ",".join([f"\"{name}\"" for name in field_names]) + "]"

        path = f"/bitable/v1/apps/{self.app_token}/tables/{table_id}/records"
        data = self._request("GET", path, params=params)
        return data.get("data", {})

    @staticmethod
    def _日志表标识(table_id: str, table_name: str = "") -> str:
        """日志中优先显示业务表名，没有表名时回退显示 table_id。"""
        return (table_name or "").strip() or table_id

    def _裁剪记录字段(self, items: List[dict], field_names: List[str] | None = None) -> List[dict]:
        """
        仅保留业务需要字段（双保险）：
        - 服务端通过 field_names 尽量只返回必要字段；
        - 客户端再次裁剪，确保缓存落地数据不会混入无关字段。
        """
        if not field_names:
            return items
        需要字段 = set(field_names)
        裁剪后: List[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields", {})
            if not isinstance(fields, dict):
                fields = {}
            新字段 = {k: v for k, v in fields.items() if k in 需要字段}
            新记录 = dict(item)
            新记录["fields"] = 新字段
            裁剪后.append(新记录)
        return 裁剪后

    def _request(self, method: str, path: str, *, json: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        url = self.base_url + path
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if not path.endswith("/auth/v3/tenant_access_token/internal"):
            headers["Authorization"] = "Bearer " + self.get_token()

        last_error: Exception | None = None
        for attempt in range(1, self.max_retry + 1):
            try:
                resp = requests.request(method, url, headers=headers, json=json, params=params, timeout=30)
                data = resp.json()
            except Exception as exc:
                # 网络抖动或偶发非 JSON 响应时，按重试策略处理；超限后再抛出。
                last_error = exc
                if attempt < self.max_retry:
                    time.sleep(self.retry_sleep_seconds * attempt)
                    continue
                raise FeishuError(f"飞书接口非 JSON 响应：{exc}") from exc

            code = data.get("code", 0)
            is_retryable = (
                code in {1254607}  # Data not ready, please try again later
                or "rate limit" in str(data.get("msg", "")).lower()
                or "too many requests" in str(data.get("msg", "")).lower()
                or "限流" in str(data.get("msg", ""))
                or resp.status_code in {429, 500, 502, 503, 504}
            )
            if resp.status_code >= 400 or code != 0:
                if is_retryable and attempt < self.max_retry:
                    print(
                        "飞书接口可重试错误，准备重试："
                        f" attempt={attempt}/{self.max_retry} HTTP={resp.status_code} code={code} msg={data.get('msg')}"
                    )
                    # 线性退避，减少瞬时并发冲击。
                    time.sleep(self.retry_sleep_seconds * attempt)
                    continue
                raise FeishuError(
                    f"飞书接口错误：HTTP {resp.status_code} code={code} msg={data.get('msg')} data={data}"
                )
            return data

        # 正常不会走到这里，兜底抛出最后一次错误。
        if last_error:
            raise FeishuError(f"飞书接口请求失败：{last_error}") from last_error
        raise FeishuError("飞书接口请求失败：未知错误")

    def get_token(self) -> str:
        now = time.time()
        if self._tenant_access_token and now < self._token_expire_at - 60:
            return self._tenant_access_token

        data = self._request(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        token = data.get("tenant_access_token")
        if not token:
            raise FeishuError("获取 tenant_access_token 失败")
        self._tenant_access_token = token
        self._token_expire_at = now + int(data.get("expire", 7200))
        return token

    def _fetch_bitable_records_serial(
        self,
        table_id: str,
        table_name: str = "",
        view_id: str = "",
        field_names: List[str] | None = None,
        page_size: int = 500,
    ) -> dict:
        """串行分页读取，作为并发失败时的兜底方案。"""
        开始时间 = time.time()
        表标识 = self._日志表标识(table_id, table_name)
        records_info: Dict[str, Any] = {"items": [], "total": 0}
        page_token: str | None = None
        页数 = 0
        while True:
            body = self._request_records_page(
                table_id=table_id,
                view_id=view_id,
                page_size=page_size,
                page_token=page_token,
                field_names=field_names,
            )
            页数 += 1
            当前页items = self._裁剪记录字段(body.get("items", []), field_names)
            records_info["items"].extend(当前页items)
            records_info["total"] = int(body.get("total", records_info["total"]))
            总条数 = int(records_info["total"] or 0)
            已下载条数 = len(records_info["items"])
            if 总条数 > 0:
                预计总页 = (总条数 + page_size - 1) // page_size
                百分比 = min(100.0, 已下载条数 * 100.0 / 总条数)
                print(
                    "飞书串行分页进度："
                    f" table={表标识} page={页数}/{预计总页} records={已下载条数}/{总条数} ({百分比:.1f}%)"
                )
            else:
                print("飞书串行分页进度：" f" table={表标识} page={页数} records={已下载条数}")
            if not body.get("has_more"):
                break
            page_token = body.get("page_token")

        总数 = len(records_info["items"])
        耗时 = time.time() - 开始时间
        print(
            "飞书串行分页读取完成："
            f" table={表标识} total={总数} pages={页数} workers=1 elapsed={耗时:.2f}s"
        )
        return records_info

    def list_records(
        self,
        table_id: str,
        table_name: str = "",
        view_id: str = "",
        view_type: str = "ID",
        field_names: List[str] | None = None,
        page_size: int = 500,
        max_workers: int = 8,
        max_retries: int = 3,
    ) -> List[dict]:
        """读取视图记录（内部统一走并发分页方法，失败自动回退串行）。"""
        records_info = fetch_bitable_records_parallel(
            bitable_instance=self,
            table_id=table_id,
            table_name=table_name,
            view_id=view_id,
            field_names=field_names,
            page_size=page_size,
            max_workers=max_workers,
            max_retries=max_retries,
        )
        return records_info.get("items", [])

    def delete_all_records(self, table_id: str, view_id: str = "", view_type: str = "ID") -> int:
        records = self.list_records(table_id=table_id, view_id=view_id, view_type=view_type)
        record_ids = [r.get("record_id") for r in records if r.get("record_id")]
        deleted = 0
        for ids in 分批(record_ids, self.batch_size):
            if not ids:
                continue
            path = f"/bitable/v1/apps/{self.app_token}/tables/{table_id}/records/batch_delete"
            self._request("POST", path, json={"records": ids})
            deleted += len(ids)
        return deleted

    def add_records(self, table_id: str, records: List[Dict[str, Any]]) -> int:
        added = 0
        for chunk in 分批(records, self.batch_size):
            if not chunk:
                continue
            path = f"/bitable/v1/apps/{self.app_token}/tables/{table_id}/records/batch_create"
            payload = {"records": [{"fields": item} for item in chunk]}
            self._request("POST", path, json=payload)
            added += len(chunk)
        return added

    def add_record(self, table_id: str, fields: Dict[str, Any]) -> str:
        path = f"/bitable/v1/apps/{self.app_token}/tables/{table_id}/records"
        data = self._request("POST", path, json={"fields": fields})
        return data.get("data", {}).get("record", {}).get("record_id", "")


def 字段文本(fields: dict, name: str, default: str = "") -> str:
    """
    兼容飞书多维表格字段返回格式，尽量取到文本。
    支持影刀中出现的：
    - fields['编号'][0]['text']
    - fields['推荐人编号']['value'][0]['text']
    - 普通字符串 / 数字
    """
    if not isinstance(fields, dict):
        return default
    value = fields.get(name, default)
    return 提取文本(value, default)


def 提取文本(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value).strip()
    if isinstance(value, list):
        if not value:
            return default
        return 提取文本(value[0], default)
    if isinstance(value, dict):
        if "text" in value:
            return str(value.get("text") or "").strip()
        if "value" in value:
            return 提取文本(value.get("value"), default)
        if "link" in value and isinstance(value.get("link"), str):
            return value.get("link", "").strip()
    return str(value).strip()


def fetch_bitable_records_parallel(
    bitable_instance,
    table_id,
    table_name="",
    view_id="",
    field_names=None,
    page_size=500,
    max_workers=8,
    max_retries=3,
):
    """
    多线程分页读取飞书多维表格记录。
    返回 records_info，格式保持为：
    {
        "items": [...],   # 记录列表（与原 list records items 一致）
        "total": 12345,   # 总记录数
    }
    """
    开始时间 = time.time()
    page_size = min(max(int(page_size), 1), 500)
    workers = max(1, int(max_workers))
    field_names = field_names or None
    表标识 = bitable_instance._日志表标识(table_id, table_name)

    def _打印分页进度(当前页: int, 已下载条数: int, 总条数: int):
        """
        打印分页下载进度，便于观察大表下载过程。
        - 总条数未知或为 0 时，仅展示已下载条数。
        - 总条数已知时，展示预计总页数与百分比。
        """
        if 总条数 > 0:
            预计总页 = (总条数 + page_size - 1) // page_size
            百分比 = min(100.0, 已下载条数 * 100.0 / 总条数)
            print(
                "飞书分页下载进度："
                f" table={表标识} page={当前页}/{预计总页} records={已下载条数}/{总条数} ({百分比:.1f}%)"
            )
        else:
            print(
                "飞书分页下载进度："
                f" table={表标识} page={当前页} records={已下载条数}"
            )

    # 第 1 页先取，拿到 total / page_token / has_more
    first_page = bitable_instance._request_records_page(
        table_id=table_id,
        view_id=view_id,
        page_size=page_size,
        page_token=None,
        field_names=field_names,
    )
    items: List[dict] = bitable_instance._裁剪记录字段(list(first_page.get("items", [])), field_names)
    total = int(first_page.get("total", len(items)))
    has_more = bool(first_page.get("has_more"))
    first_next_page_token = first_page.get("page_token")
    _打印分页进度(当前页=1, 已下载条数=len(items), 总条数=total)

    if not has_more or not first_next_page_token:
        耗时 = time.time() - 开始时间
        print(
            "飞书分页读取完成（单页快速路径）："
            f" table={表标识} total={len(items)} pages=1 workers=1 elapsed={耗时:.2f}s"
        )
        return {"items": items, "total": total}

    # 说明：
    # 飞书 page_token 为链式分页 token，后页 token 依赖前页响应。
    # 这里使用线程池驱动分页任务并保留回退机制；若并发流程异常，自动降级到串行。
    page_count = 1
    pending_tokens: List[str] = [str(first_next_page_token)]
    in_flight = {}
    retries_map: Dict[str, int] = {}

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            while pending_tokens or in_flight:
                while pending_tokens and len(in_flight) < workers:
                    token = pending_tokens.pop(0)
                    future = executor.submit(
                        bitable_instance._request_records_page,
                        table_id,
                        view_id,
                        page_size,
                        token,
                        field_names,
                    )
                    in_flight[future] = token

                done, _not_done = wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)
                for future in done:
                    token = in_flight.pop(future)
                    try:
                        body = future.result()
                    except Exception:
                        used = retries_map.get(token, 0) + 1
                        retries_map[token] = used
                        if used <= max(1, int(max_retries)):
                            sleep_s = 0.8 * used
                            print(
                                "飞书分页任务失败，准备重试："
                                f" table={表标识} token={token} retry={used}/{max_retries} sleep={sleep_s:.1f}s"
                            )
                            time.sleep(sleep_s)
                            pending_tokens.append(token)
                            continue
                        raise

                    page_count += 1
                    当前页items = bitable_instance._裁剪记录字段(body.get("items", []), field_names)
                    items.extend(当前页items)
                    _打印分页进度(当前页=page_count, 已下载条数=len(items), 总条数=total)
                    if body.get("has_more") and body.get("page_token"):
                        pending_tokens.append(str(body.get("page_token")))
    except Exception as exc:
        print(f"飞书并发分页读取失败，自动回退串行分页：table={表标识} err={exc}")
        return bitable_instance._fetch_bitable_records_serial(
            table_id=table_id,
            table_name=table_name,
            view_id=view_id,
            field_names=field_names,
            page_size=page_size,
        )

    耗时 = time.time() - 开始时间
    print(
        "飞书并发分页读取完成："
        f" table={表标识} total={len(items)} pages={page_count} workers={workers} elapsed={耗时:.2f}s"
    )
    return {"items": items, "total": total}
