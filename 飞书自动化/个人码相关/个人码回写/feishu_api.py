# -*- coding: utf-8 -*-
"""
飞书 API 封装
==============================================

提供：
    - get_tenant_access_token()             获取租户 token
    - list_all_records(token)               分页拉取多维表格全部记录
    - upload_attachment(token, file_path)   上传图片到 bitable 附件，返回 file_token
    - update_record(token, record_id, file_token)  写入"个人码3"字段

所有 HTTP 请求带 RETRY_TIMES 次重试。
"""

import os
import time
from typing import Any

import requests
import config
import logger
import encoding_utils


# ────────────────────────── 通用：带重试的请求 ──────────────────────────
def _request(method: str, url: str, **kwargs) -> requests.Response:
    """
    统一封装：5xx / 429 / 网络异常会自动重试，最多 config.RETRY_TIMES 次。
    其它 4xx 直接返回，由调用方根据业务字段判断。
    """
    last_exc: Exception | None = None
    for attempt in range(1, config.RETRY_TIMES + 1):
        try:
            resp = requests.request(
                method, url, timeout=config.REQUEST_TIMEOUT, **kwargs
            )
            if resp.status_code >= 500 or resp.status_code == 429:
                last_exc = RuntimeError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
                if attempt < config.RETRY_TIMES:
                    logger.warn(
                        f"接口暂时失败，正在重试 ({attempt}/{config.RETRY_TIMES - 1})："
                        f"{method} {url} → {resp.status_code}"
                    )
                    time.sleep(min(2 ** attempt, 5))
                    continue
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt < config.RETRY_TIMES:
                logger.warn(
                    f"网络异常重试 ({attempt}/{config.RETRY_TIMES - 1})："
                    f"{method} {url} → {e}"
                )
                time.sleep(min(2 ** attempt, 5))
                continue
    raise RuntimeError(f"请求最终失败：{method} {url}，原因：{last_exc}")


# ────────────────────────── 字段值兼容工具 ──────────────────────────
def _extract_text(value: Any) -> str:
    """
    把飞书字段值统一转为字符串，兼容三种返回形态：
        1. 纯字符串："001"
        2. 富文本数组：[{"type":"text","text":"001"}, ...]
        3. 数字 / None
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")).strip())
            elif item is not None:
                parts.append(str(item).strip())
        return "".join(parts).strip()
    if isinstance(value, dict):
        return str(value.get("text", "")).strip()
    return str(value).strip()


# ────────────────────────── 鉴权 ──────────────────────────
def get_tenant_access_token() -> str:
    """获取 tenant_access_token（internal 应用方式）"""
    url = f"{config.FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal"
    body = {"app_id": config.APP_ID, "app_secret": config.APP_SECRET}
    resp = _request("POST", url, json=body)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 token 失败：{data}")
    logger.info(f"已获取 tenant_access_token，有效期 {data.get('expire')} 秒")
    return data["tenant_access_token"]


# ────────────────────────── 拉取记录（分页） ──────────────────────────
def list_all_records(token: str) -> list[dict]:
    """
    分页拉取多维表格全部记录（page_size = config.PAGE_SIZE）。

    返回的每条记录形如：
        {
            "record_id": "rec_xxx",
            "fields": {"编号": "001", "其他字段": ...},
        }
    """
    url = (
        f"{config.FEISHU_HOST}/open-apis/bitable/v1/apps/{config.APP_TOKEN}"
        f"/tables/{config.TABLE_ID}/records"
    )
    headers = {"Authorization": f"Bearer {token}"}

    all_records: list[dict] = []
    page_token: str | None = None
    page_index = 0

    while True:
        page_index += 1
        params: dict[str, Any] = {"page_size": config.PAGE_SIZE}
        if config.VIEW_ID:
            params["view_id"] = config.VIEW_ID
        if page_token:
            params["page_token"] = page_token

        resp = _request("GET", url, headers=headers, params=params)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"拉取记录失败：{data}")

        items = data["data"].get("items") or []
        all_records.extend(items)
        logger.info(
            f"拉取第 {page_index} 页，本页 {len(items)} 条，累计 {len(all_records)} 条"
        )

        if not data["data"].get("has_more"):
            break
        page_token = data["data"].get("page_token")
        if not page_token:
            break

    return all_records


def get_record_field_text(record: dict, field_name: str) -> str:
    """从记录中安全抽取指定字段的字符串值（兼容多种结构）"""
    fields = record.get("fields", {}) or {}
    return _extract_text(fields.get(field_name))


# ────────────────────────── 上传附件 ──────────────────────────
def upload_attachment(token: str, file_path: str) -> str:
    """
    上传图片到 bitable 附件，返回可直接写入附件字段的 file_token。

    使用 /open-apis/drive/v1/medias/upload_all
    parent_type = bitable_image：表示这是某个 bitable app 的附件图片
    parent_node = APP_TOKEN：定位到具体的 bitable app
    """
    url = f"{config.FEISHU_HOST}/open-apis/drive/v1/medias/upload_all"
    headers = {"Authorization": f"Bearer {token}"}

    file_size = os.path.getsize(file_path)
    file_name = os.path.basename(file_path)

    # 关键防 latin-1 崩溃：
    # multipart 的 Content-Disposition 头里的 filename 会被 urllib3
    # 用 latin-1 编码，含中文 / ¿ 会直接抛 UnicodeEncodeError。
    # 这里给 multipart 用一个纯 ASCII 兜底名，飞书展示用的文件名
    # 由表单字段 file_name 单独传（飞书后端读这一项）。
    multipart_name = encoding_utils.ascii_safe_name(file_name, default="upload.bin")
    # 飞书表单 file_name 也做一次 clean_filename 兜底，避免控制字符
    display_name = encoding_utils.clean_filename(file_name) or multipart_name

    # multipart 表单：file 字段是二进制；其它字段是普通文本
    with open(file_path, "rb") as f:
        files = {
            "file": (multipart_name, f, "application/octet-stream"),
        }
        data = {
            "file_name":   display_name,
            "parent_type": "bitable_image",
            "parent_node": config.APP_TOKEN,
            "size":        str(file_size),
        }
        resp = _request("POST", url, headers=headers, files=files, data=data)

    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"上传附件失败：{payload}")
    file_token = payload["data"]["file_token"]
    return file_token


# ────────────────────────── 更新记录 ──────────────────────────
def update_record(token: str, record_id: str, file_token: str) -> dict:
    """把 file_token 写入指定记录的 FIELD_IMAGE（附件字段）"""
    url = (
        f"{config.FEISHU_HOST}/open-apis/bitable/v1/apps/{config.APP_TOKEN}"
        f"/tables/{config.TABLE_ID}/records/{record_id}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }
    body = {
        "fields": {
            config.FIELD_IMAGE: [{"file_token": file_token}],
        }
    }
    resp = _request("PUT", url, headers=headers, json=body)
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"更新记录失败：{payload}")
    return payload
