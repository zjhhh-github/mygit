#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量下载飞书多维表格数据到本地 CSV 文件。

运行方式（在 feishu_download/ 目录下）：
    python download_feishu_csv.py

功能概要：
1. 读取 config.py 中的 DOWNLOAD_TABLES 列表，按 table_id 并发下载（ThreadPoolExecutor）。
2. 并发单位为「表」：每张表内部仍按 page_token 顺序分页（page_size=500）。
3. 自动获取 tenant_access_token，将 record.fields 转为 CSV（utf-8-sig）。
4. 单表失败不影响其他表；网络异常与限流（1254290）在线程内自动重试。
"""

from __future__ import annotations

import csv
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config

# 脚本所在目录：输出路径、导入 config 均锚定于此，不受运行时 cwd 影响
SCRIPT_DIR = Path(__file__).resolve().parent

# 飞书开放平台基础地址
FEISHU_HOST = "https://open.feishu.cn"
URL_TENANT_TOKEN = "/open-apis/auth/v3/tenant_access_token/internal"
URL_LIST_RECORDS_TPL = "/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"

# 限流错误码：TooManyRequest，按需求依次等待 2 / 5 / 10 秒后重试
FEISHU_RATE_LIMIT_CODE = 1254290
RATE_LIMIT_WAIT_SECONDS = [2, 5, 10]

# 普通网络 / HTTP 异常最多重试次数
MAX_RETRY = 3

# 控制台输出锁：多线程日志不交错
_print_lock = threading.Lock()

# 日志函数类型：worker_id + 消息行列表
LogFn = Callable[[int, List[str]], None]


@dataclass
class DownloadResult:
    """单表下载结果，供主线程汇总。"""

    table_id: str
    table_name: str
    worker_id: int
    success: bool
    field_count: int = 0
    row_count: int = 0
    error: Optional[str] = None


def _ensure_utf8_stdio() -> None:
    """Windows 控制台尽量以 UTF-8 输出中文日志。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def thread_log(worker_id: int, *lines: str) -> None:
    """线程安全日志：首行带 [线程N] 前缀，后续行正常缩进输出。"""
    with _print_lock:
        for index, line in enumerate(lines):
            if index == 0:
                print(f"[线程{worker_id}]", flush=True)
            print(line, flush=True)


def http_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """发送 JSON HTTP 请求并返回解析后的 dict（不含重试逻辑）。"""
    data_bytes: Optional[bytes] = None
    req_headers: Dict[str, str] = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    if body is not None:
        data_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json; charset=utf-8")

    req = Request(url=url, data=data_bytes, headers=req_headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8")
        except Exception:
            pass
        raise RuntimeError(
            f"HTTP {exc.code} 调用失败：{url}\n响应体：{err_body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"网络错误：{url}\n原因：{exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"响应不是合法 JSON：{raw[:500]}") from exc


def request_with_retry(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    worker_id: Optional[int] = None,
) -> Dict[str, Any]:
    """带重试的 HTTP 请求：网络异常最多重试 MAX_RETRY 次；限流 1254290 按 2/5/10 秒等待。"""
    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = http_json(method, url, headers=headers, body=body)
            code = resp.get("code")
            if code == FEISHU_RATE_LIMIT_CODE:
                wait_idx = min(attempt - 1, len(RATE_LIMIT_WAIT_SECONDS) - 1)
                wait_sec = RATE_LIMIT_WAIT_SECONDS[wait_idx]
                if worker_id is not None:
                    thread_log(
                        worker_id,
                        f"  [限流] 飞书返回 {FEISHU_RATE_LIMIT_CODE} TooManyRequest，"
                        f"第 {attempt} 次重试，等待 {wait_sec} 秒 ...",
                    )
                time.sleep(wait_sec)
                continue
            if code != 0:
                raise RuntimeError(
                    f"飞书接口业务失败：code={code}, msg={resp.get('msg')}, raw={resp}"
                )
            return resp
        except RuntimeError as exc:
            last_error = exc
            msg = str(exc)
            is_rate_limit = str(FEISHU_RATE_LIMIT_CODE) in msg
            if attempt < MAX_RETRY and not is_rate_limit:
                if worker_id is not None:
                    thread_log(worker_id, f"  [重试] 第 {attempt} 次失败：{exc}，即将重试 ...")
                time.sleep(1.0 * attempt)
                continue
            if attempt >= MAX_RETRY:
                break

    raise RuntimeError(f"请求失败（已重试 {MAX_RETRY} 次）：{last_error}") from last_error


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    """获取 tenant_access_token（应用维度访问令牌）。"""
    url = FEISHU_HOST + URL_TENANT_TOKEN
    resp = request_with_retry(
        "POST",
        url,
        body={"app_id": app_id, "app_secret": app_secret},
    )
    token = resp.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"响应缺少 tenant_access_token：{resp}")
    return str(token)


def flatten_field_value(value: Any) -> str:
    """将飞书 fields 中的复杂结构转为 CSV 可写入的字符串。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if not value:
            return ""
        if all(isinstance(item, dict) and "text" in item for item in value):
            return "".join(str(item.get("text") or "") for item in value)
        if all(isinstance(item, (str, int, float, bool)) or item is None for item in value):
            return ",".join("" if item is None else str(item) for item in value)
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        for key in ("text", "name", "link"):
            if key in value and value[key] is not None:
                return str(value[key])
        if "value" in value:
            return flatten_field_value(value["value"])
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def records_to_rows(
    records: List[Dict[str, Any]],
    fields: Optional[List[str]] = None,
) -> Tuple[List[str], List[Dict[str, str]], List[str]]:
    """把飞书记录列表转为 CSV 列名 + 行数据 + 不存在的配置字段列表。"""
    if fields is not None:
        columns = list(fields)
        all_feishu_keys: set[str] = set()
        for rec in records:
            all_feishu_keys.update((rec.get("fields") or {}).keys())
        missing_fields = [name for name in columns if name not in all_feishu_keys]

        rows: List[Dict[str, str]] = []
        for rec in records:
            raw_fields = rec.get("fields") or {}
            row = {col: flatten_field_value(raw_fields.get(col)) for col in columns}
            rows.append(row)
        return columns, rows, missing_fields

    if not records:
        return [], [], []

    columns: List[str] = []
    seen: set[str] = set()

    first_fields = records[0].get("fields") or {}
    for key in first_fields.keys():
        columns.append(key)
        seen.add(key)

    for rec in records[1:]:
        rec_fields = rec.get("fields") or {}
        for key in rec_fields.keys():
            if key not in seen:
                columns.append(key)
                seen.add(key)

    rows = []
    for rec in records:
        rec_fields = rec.get("fields") or {}
        row = {col: flatten_field_value(rec_fields.get(col)) for col in columns}
        rows.append(row)

    return columns, rows, []


def format_download_fields(fields: Optional[List[str]], columns: List[str]) -> List[str]:
    """格式化「下载字段」日志行。"""
    lines = ["下载字段:"]
    if fields is not None:
        lines.extend(fields)
        return lines
    if not columns:
        lines.append("(全部字段，暂无数据)")
        return lines
    lines.extend(columns)
    return lines


def fetch_all_records(
    token: str,
    app_token: str,
    table_id: str,
    page_size: int,
    worker_id: int,
) -> List[Dict[str, Any]]:
    """分页拉取一张数据表的全部记录（表内顺序分页，不并发）。"""
    base_url = FEISHU_HOST + URL_LIST_RECORDS_TPL.format(
        app_token=app_token,
        table_id=table_id,
    )
    headers = {"Authorization": f"Bearer {token}"}

    all_records: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    page_index = 0

    while True:
        page_index += 1
        params: Dict[str, Any] = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        url = base_url + "?" + urlencode(params)

        resp = request_with_retry("GET", url, headers=headers, worker_id=worker_id)
        data = resp.get("data") or {}
        items = data.get("items") or []
        all_records.extend(items)

        thread_log(worker_id, f"  第{page_index}页 {len(items)}条")

        has_more = bool(data.get("has_more"))
        page_token = data.get("page_token")
        if not has_more or not page_token:
            break

    return all_records


def save_csv(
    output_path: Path,
    columns: List[str],
    rows: List[Dict[str, str]],
) -> None:
    """写入 CSV 文件，编码 utf-8-sig 以便 Excel 正确显示中文。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        if rows:
            writer.writerows(rows)


def download_one_table(
    token: str,
    table_id: str,
    table_name: str,
    app_token: str,
    output_dir: Path,
    page_size: int,
    worker_id: int,
    fields: Optional[List[str]] = None,
) -> DownloadResult:
    """下载单张表并保存 CSV（在线程池中执行，异常不向外抛出）。"""
    try:
        thread_log(worker_id, f"开始下载 {table_name}")

        if fields is not None:
            thread_log(worker_id, *format_download_fields(fields, fields))

        records = fetch_all_records(
            token, app_token, table_id, page_size, worker_id
        )
        columns, rows, missing_fields = records_to_rows(records, fields)

        if fields is None:
            thread_log(worker_id, *format_download_fields(None, columns))

        if missing_fields:
            missing_lines = ["字段不存在:"] + missing_fields
            thread_log(worker_id, *missing_lines)

        csv_name = f"{table_name}.csv"
        output_path = output_dir / csv_name
        save_csv(output_path, columns, rows)

        thread_log(
            worker_id,
            f"完成: {csv_name}",
            f"字段数:{len(columns)}",
            f"数据量:{len(rows)}条",
        )
        return DownloadResult(
            table_id=table_id,
            table_name=table_name,
            worker_id=worker_id,
            success=True,
            field_count=len(columns),
            row_count=len(rows),
        )
    except Exception as exc:
        err_msg = str(exc)
        thread_log(worker_id, f"[失败] {table_id} ({table_name})：{err_msg}")
        return DownloadResult(
            table_id=table_id,
            table_name=table_name,
            worker_id=worker_id,
            success=False,
            error=err_msg,
        )


def download_table_task(
    worker_id: int,
    table_id: str,
    token: str,
    output_dir: Path,
) -> DownloadResult:
    """线程池任务包装：根据 table_id 查配置并下载，保证异常不中断线程池。"""
    mapping = config.TABLE_MAPPING.get(table_id)
    if not mapping:
        msg = f"TABLE_MAPPING 中未配置 table_id「{table_id}」"
        thread_log(worker_id, f"[跳过] {msg}")
        return DownloadResult(
            table_id=table_id,
            table_name="未配置",
            worker_id=worker_id,
            success=False,
            error=msg,
        )

    return download_one_table(
        token=token,
        table_id=table_id,
        table_name=mapping["table_name"],
        app_token=mapping["app_token"],
        output_dir=output_dir,
        page_size=config.PAGE_SIZE,
        worker_id=worker_id,
        fields=mapping.get("fields"),
    )


def main() -> int:
    """主入口：并发下载 DOWNLOAD_TABLES 中的全部表格。"""
    _ensure_utf8_stdio()

    output_dir = SCRIPT_DIR / config.OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    pending_labels = []
    for tid in config.DOWNLOAD_TABLES:
        name = config.TABLE_MAPPING.get(tid, {}).get("table_name", "未配置")
        pending_labels.append(f"{tid} ({name})")

    print("=" * 60)
    print("飞书多维表格批量下载 → CSV（多表并发）")
    print(f"输出目录：{output_dir}")
    print(f"待下载表：{', '.join(pending_labels)}")
    print(f"并发线程数：{config.MAX_WORKERS}")
    print("=" * 60)

    print("[认证] 获取 tenant_access_token ...")
    token = get_tenant_access_token(config.APP_ID, config.APP_SECRET)
    print(f"[认证] 成功，token=***{token[-6:]}")

    start_time = time.time()
    results: List[DownloadResult] = []

    # 每个 table_id 提交为一个任务；worker_id 与 DOWNLOAD_TABLES 序号对应（1 起）
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
        future_map = {
            executor.submit(
                download_table_task,
                worker_id=idx + 1,
                table_id=table_id,
                token=token,
                output_dir=output_dir,
            ): table_id
            for idx, table_id in enumerate(config.DOWNLOAD_TABLES)
        }

        for future in as_completed(future_map):
            table_id = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:
                # 兜底：理论上 worker 已捕获异常，此处防止线程池意外中断
                results.append(
                    DownloadResult(
                        table_id=table_id,
                        table_name="未知",
                        worker_id=0,
                        success=False,
                        error=str(exc),
                    )
                )

    elapsed = time.time() - start_time
    success_count = sum(1 for r in results if r.success)
    fail_count = len(results) - success_count

    # 按 DOWNLOAD_TABLES 原始顺序排列汇总明细
    result_by_id = {r.table_id: r for r in results}
    detail_lines: List[str] = []
    for table_id in config.DOWNLOAD_TABLES:
        result = result_by_id.get(table_id)
        if not result:
            continue
        if result.success:
            detail_lines.append(
                f"  ✓ {result.table_name}.csv"
                f"（字段数:{result.field_count}，数据量:{result.row_count}条）"
            )
        else:
            detail_lines.append(
                f"  ✗ {result.table_id} ({result.table_name})：{result.error}"
            )

    print("=" * 60)
    print("下载完成:")
    print(f"成功:\n{success_count}个")
    print(f"失败:\n{fail_count}个")
    print(f"总耗时:\n{elapsed:.1f}秒")
    if detail_lines:
        print("明细:")
        for line in detail_lines:
            print(line)
    print("=" * 60)

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(130)
    except Exception as exc:
        print(f"[致命错误] {exc}")
        sys.exit(1)
