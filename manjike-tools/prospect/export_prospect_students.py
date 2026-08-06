#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Prospect 独立后端导出全部意向学员 JSON。

【脚本功能说明】
分页拉取 Prospect 后端全部意向学员及推荐来源，组装为 STUDENTS_IMPORT 标准 JSON
主文件，并可选生成 logs/ 下带时间戳的 CSV 备份，供 upload_prospect.py 或飞书同步使用。

【数据来源】（后端暂无流式 /export 接口，脚本分页拉取后本地组装）
    GET /api/prospect/prospective-students?pageNum=&pageSize=
    GET /api/prospect/admin/students/recommendations/batch?studentIds=1,2,3
        （批量推荐记录接口，本次导出性能优化新增，一次请求查完一整页学员的推荐记录，
        替代之前"每个学员单独调一次 .../students/{id}/recommendations"的写法）
    权限：prospect:view

【输出格式】
    - 主文件：与意向通讯录.json 一致（中文 key +「推荐人」数组），
      可直接作为 upload_prospect.py 的 source_file。
    - 时间戳备份：logs/ 下扁平化 CSV（一行 = 学员 + 一条来源），便于 Excel 归档。

【配置】同目录 export_prospect.config.json（可与 upload_prospect.config.json 共用服务端账号）

CLI：
    python export_prospect_students.py
    python export_prospect_students.py --resume          # 从断点继续（502 中断后）
    python export_prospect_students.py --host http://localhost:18081  # 绕过 nginx
    python export_prospect_students.py --no-validate
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
_PARENT_DIR = SCRIPT_DIR.parent
if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))

from common import (  # noqa: E402
    DualLogger,
    deep_merge,
    http_get,
    login_prospect,
    resolve_prospect_config,
    warn_if_main_backend_for_prospect,
)
from prospect_csv_backup import write_student_csv_backup  # noqa: E402

DEFAULT_CONFIG_PATH = SCRIPT_DIR / "export_prospect.config.json"

# 分页列表接口（Prospect 独立后端当前无 /export，脚本自己分页拉取后在本地组装）
STUDENTS_LIST_PATH = "/api/prospect/prospective-students"
# 单学员推荐记录接口（已废弃于本导出脚本，仅保留常量供其它脚本/排查参考；
# 本脚本改用下面的批量接口，见 RECOMMENDATIONS_BATCH_PATH）
RECOMMENDATIONS_PATH = "/api/prospect/admin/students/{student_id}/recommendations"
# 批量推荐记录接口（本次导出性能优化新增）：一次请求传入一页学员的 ID（逗号分隔），
# 一次性查完这一页所有学员的推荐记录，把请求数量从"学员总数"降到"页数"这个量级。
RECOMMENDATIONS_BATCH_PATH = "/api/prospect/admin/students/recommendations/batch"

# 可重试的 HTTP 状态（nginx 502、后端短暂不可用、网络抖动）
_RETRYABLE_HTTP_STATUS = frozenset({0, 429, 500, 502, 503, 504})

DEFAULT_CONFIG: Dict[str, Any] = {
    "服务端": {
        "host": "",
        "account": "",
        "password": "",
    },
    "导出": {
        "output_dir": ".",
        "json_filename": "意向学员数据.json",
        "save_timestamped_copy": True,
        "timestamped_filename_template": "logs/意向学员数据导出_{timestamp}.csv",
        "validate_json": True,
        "timeout_seconds": 600,
        "page_size": 200,
        # 每一页发起"批量查询推荐记录"请求前的间隔（毫秒），减轻 nginx / 后端压力，
        # 0 表示不等待（本次导出性能优化调整：原来是"每个学员查推荐记录前都等一次"，
        # 现在改成批量查询后，是"每一页只等一次"，粒度从"每个学员"变成"每一页"）
        "request_delay_ms": 20,
        # 502/503 等临时错误时的重试次数与退避基数（秒）
        "retry_max": 8,
        "retry_backoff_seconds": 3,
        # 每拉取多少条学员写一次断点文件，502 中断后可 --resume 续传
        "checkpoint_interval": 500,
        "checkpoint_file": "logs/export_prospect.checkpoint.json",
    },
    "日志": {
        "log_file": "logs/export_prospect.log",
    },
}


def normalize_host(host: str) -> str:
    h = (host or "").strip().rstrip("/")
    if h.startswith("http://dev.manjikeabc.com"):
        return "https://dev.manjikeabc.com"
    return h


def _ensure_utf8_stdio() -> None:
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def deep_merge_local(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """本脚本 load_config 用；common.deep_merge 语义相同。"""
    return deep_merge(base, override)


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return dict(DEFAULT_CONFIG)
    user_cfg = json.loads(config_path.read_text(encoding="utf-8"))
    return deep_merge_local(DEFAULT_CONFIG, user_cfg)


def resolve_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (SCRIPT_DIR / p).resolve()


def resolve_output_dir(output_dir_value: str) -> Path:
    """与 xlsx_to_prospect_json.py 一致：相对路径以脚本目录为基准。"""
    p = Path(output_dir_value)
    if p.is_absolute():
        return p
    return (SCRIPT_DIR / p).resolve()


def resolve_main_output_path(export_cfg: Dict[str, Any], out_override: Optional[str] = None) -> Path:
    """解析主输出文件路径；兼容旧配置项 output_file（支持 {date}）。"""
    if out_override:
        name = out_override
        if "{date}" in name:
            name = name.replace("{date}", datetime.now().strftime("%Y-%m-%d"))
        p = Path(name)
        return p if p.is_absolute() else resolve_output_dir(".") / p

    if export_cfg.get("json_filename"):
        out_dir = resolve_output_dir(str(export_cfg.get("output_dir", ".")))
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / str(export_cfg["json_filename"])

    legacy = str(export_cfg.get("output_file", "意向学员数据.json"))
    if "{date}" in legacy:
        legacy = legacy.replace("{date}", datetime.now().strftime("%Y-%m-%d"))
    p = Path(legacy)
    return p if p.is_absolute() else resolve_path(legacy)


def save_timestamped_snapshot(
    rows: List[Dict[str, Any]],
    export_cfg: Dict[str, Any],
    logger: DualLogger,
) -> Optional[Path]:
    """主 JSON 写入成功后，再写一份带时间戳的 CSV 备份（扁平化学员 + 来源）。"""
    if not export_cfg.get("save_timestamped_copy", True):
        return None
    if not rows:
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    template = export_cfg.get(
        "timestamped_filename_template",
        "logs/意向学员数据导出_{timestamp}.csv",
    )
    out_dir = resolve_output_dir(str(export_cfg.get("output_dir", ".")))
    snapshot_path = out_dir / template.format(timestamp=timestamp)
    size = write_student_csv_backup(snapshot_path, rows)
    logger.info("已写入时间戳 CSV 备份：{}（{:.2f} MB）".format(
        snapshot_path, size / 1024 / 1024,
    ))
    return snapshot_path


def login_export(host: str, account: str, password: str, logger: DualLogger) -> str:
    """Prospect 登录，返回 token。"""
    warn_if_main_backend_for_prospect(host, logger)
    session = login_prospect(host, account, password, logger)
    return session["token"]


def _http_get_with_retry(
    url: str,
    token: str,
    timeout: int,
    retry_max: int,
    retry_backoff_seconds: float,
    logger: DualLogger,
    action: str,
) -> Tuple[int, Dict[str, Any]]:
    """带退避重试的 GET；502/503/504 等临时错误自动重试。"""
    last_status = 0
    last_body: Dict[str, Any] = {}
    for attempt in range(retry_max + 1):
        status, body = http_get(url, token=token, timeout=timeout)
        if status == 200 and body.get("code") == 0:
            return status, body
        last_status = status
        last_body = body if isinstance(body, dict) else {"_raw": body}
        if status not in _RETRYABLE_HTTP_STATUS or attempt >= retry_max:
            break
        wait_seconds = retry_backoff_seconds * (2 ** attempt)
        logger.warn("{} 临时失败 HTTP {}，{}s 后第 {}/{} 次重试".format(
            action, status, wait_seconds, attempt + 1, retry_max,
        ))
        time.sleep(wait_seconds)
    return last_status, last_body


def _api_data(status: int, body: Dict[str, Any], action: str) -> Any:
    """校验统一响应结构并返回 data 字段。"""
    if status != 200:
        raise RuntimeError("{} 失败：HTTP {} / {}".format(action, status, body))
    code = body.get("code")
    if code != 0:
        raise RuntimeError("{} 失败：code={} message={}".format(
            action, code, body.get("message") or body,
        ))
    return body.get("data")


def _text_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _map_enrolled_status(raw: Any) -> str:
    """后端枚举 → 导入 JSON 用的中文报名状态。"""
    text = _text_or_empty(raw).upper()
    if text in ("ENROLLED", "已报名"):
        return "已报名"
    if text in ("NOT_ENROLLED", "未报名"):
        return "未报名"
    return _text_or_empty(raw)


def _map_bind_status(raw: Any) -> str:
    """后端 bindStatus → 导入 JSON 用的中文绑定状态。"""
    text = _text_or_empty(raw).upper()
    if text in ("BOUND", "有绑定", "已绑定"):
        return "有绑定"
    if text in ("UNBOUND", "无绑定", "未绑定"):
        return "无绑定"
    return _text_or_empty(raw)


def _recommendation_to_recommender(rec: Dict[str, Any]) -> Dict[str, str]:
    """单条推荐记录 → 意向通讯录.json「推荐人」数组元素。"""
    item: Dict[str, str] = {
        "推荐人微信号": _text_or_empty(rec.get("recommenderWechat")),
        "推荐人微信原始ID": _text_or_empty(rec.get("recommenderOriginalId")),
        "绑定日期": _text_or_empty(rec.get("bindDate")),
    }
    unbind = _text_or_empty(rec.get("unbindDate"))
    if unbind:
        item["解绑日期"] = unbind
    bind_status = _map_bind_status(rec.get("bindStatus"))
    if bind_status:
        item["绑定状态"] = bind_status
    return item


def _student_to_export_row(
    student: Dict[str, Any],
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """学员 + 推荐记录 → 一条意向通讯录.json 对象（「推荐人」嵌套格式）。"""
    row: Dict[str, Any] = {
        "意向学员微信号": _text_or_empty(student.get("studentWechat")),
        "意向学员微信原始ID": _text_or_empty(student.get("studentOriginalId")),
        "是否报名": _map_enrolled_status(student.get("enrolledStatus")),
        "推荐人": [_recommendation_to_recommender(rec) for rec in sources],
    }
    return row


def fetch_student_page(
    host: str,
    token: str,
    page_num: int,
    page_size: int,
    timeout: int,
    retry_max: int,
    retry_backoff_seconds: float,
    logger: DualLogger,
) -> Tuple[List[Dict[str, Any]], int]:
    """拉取一页意向学员，返回 (list, total)。"""
    url = "{}{}?pageNum={}&pageSize={}".format(
        host, STUDENTS_LIST_PATH, page_num, page_size,
    )
    status, body = _http_get_with_retry(
        url, token, timeout, retry_max, retry_backoff_seconds, logger,
        "分页查询意向学员 pageNum={}".format(page_num),
    )
    data = _api_data(status, body, "分页查询意向学员")
    if not isinstance(data, dict):
        raise RuntimeError("分页查询返回 data 不是对象：{}".format(type(data).__name__))
    items = data.get("list") or []
    total = int(data.get("total") or 0)
    if not isinstance(items, list):
        raise RuntimeError("分页查询 list 不是数组")
    return items, total


def fetch_student_recommendations_batch(
    host: str,
    token: str,
    student_ids: List[int],
    timeout: int,
    retry_max: int,
    retry_backoff_seconds: float,
    request_delay_ms: int,
    logger: DualLogger,
) -> Dict[int, List[Dict[str, Any]]]:
    """批量拉取一批学员的全部推荐记录（本次导出性能优化新增，含重试）。

    一次 HTTP 请求查完 student_ids 里全部学员的推荐记录，返回
    {学员ID: [推荐记录, ...]} 的字典；某个学员没有任何推荐记录时，
    不会出现在返回结果的 key 里，调用方需要自行按"取不到就当作空列表"处理。
    """
    if not student_ids:
        return {}
    if request_delay_ms > 0:
        time.sleep(request_delay_ms / 1000.0)
    ids_csv = ",".join(str(sid) for sid in student_ids)
    url = "{}{}?studentIds={}".format(host, RECOMMENDATIONS_BATCH_PATH, ids_csv)
    status, body = _http_get_with_retry(
        url, token, timeout, retry_max, retry_backoff_seconds, logger,
        "批量查询推荐记录，共 {} 个学员".format(len(student_ids)),
    )
    data = _api_data(status, body, "批量查询推荐记录（共 {} 个学员）".format(len(student_ids)))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RuntimeError("批量推荐记录 data 不是对象：{}".format(type(data).__name__))
    result: Dict[int, List[Dict[str, Any]]] = {}
    for key, value in data.items():
        if not isinstance(value, list):
            raise RuntimeError("批量推荐记录 data[{}] 不是数组".format(key))
        result[int(key)] = value
    return result


def _resolve_checkpoint_path(export_cfg: Dict[str, Any]) -> Path:
    return resolve_path(str(export_cfg.get("checkpoint_file", "logs/export_prospect.checkpoint.json")))


def _load_checkpoint(
    checkpoint_path: Path,
    host: str,
    page_size: int,
    logger: DualLogger,
) -> Optional[Dict[str, Any]]:
    """读取断点；host / page_size 不一致则放弃续传。"""
    if not checkpoint_path.is_file():
        return None
    try:
        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as ex:
        logger.warn("断点文件损坏，将从头导出：{}".format(ex))
        return None
    if state.get("host") != host or int(state.get("page_size") or 0) != page_size:
        logger.warn("断点 host/page_size 与当前不一致，将从头导出")
        return None
    rows = state.get("rows") or []
    if not isinstance(rows, list):
        return None
    logger.info("续传断点：已完成 {} 条，从 pageNum={} offset={} 继续".format(
        len(rows), state.get("page_num"), state.get("page_offset"),
    ))
    return state


def _save_checkpoint(
    checkpoint_path: Path,
    host: str,
    page_size: int,
    total: int,
    page_num: int,
    page_offset: int,
    rows: List[Dict[str, Any]],
    logger: DualLogger,
) -> None:
    """写入断点，502 中断后可 --resume。"""
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "host": host,
        "page_size": page_size,
        "total": total,
        "page_num": page_num,
        "page_offset": page_offset,
        "rows": rows,
    }
    temp_path = checkpoint_path.with_suffix(checkpoint_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(checkpoint_path)
    logger.info("已保存断点：{} 条 -> {}".format(len(rows), checkpoint_path))


def _clear_checkpoint(checkpoint_path: Path, logger: DualLogger) -> None:
    if checkpoint_path.is_file():
        try:
            checkpoint_path.unlink()
            logger.info("已清除断点文件：{}".format(checkpoint_path))
        except OSError:
            pass


def build_export_payload(
    host: str,
    token: str,
    page_size: int,
    timeout: int,
    retry_max: int,
    retry_backoff_seconds: float,
    request_delay_ms: int,
    checkpoint_interval: int,
    checkpoint_path: Path,
    resume: bool,
    logger: DualLogger,
) -> List[Dict[str, Any]]:
    """分页拉取全部学员，并为每条学员补齐推荐记录后组装导出 JSON。"""
    state = _load_checkpoint(checkpoint_path, host, page_size, logger) if resume else None
    all_rows: List[Dict[str, Any]] = list(state.get("rows") or []) if state else []
    page_num = int(state.get("page_num") or 1) if state else 1
    page_offset = int(state.get("page_offset") or 0) if state else 0
    total = int(state.get("total") or 0) if state else None
    # 上次写断点文件时已经拉到的条数，用来控制"每 checkpoint_interval 条写一次"，
    # 不是每处理完一页就写一次——改成批量查询推荐记录之后单页处理速度快了很多，
    # 每页都写断点文件既没必要，还在 Windows 上实测会偶发触发文件重命名时的
    # PermissionError（临时文件刚写完、还没被系统释放，就立刻发起下一次改名）。
    last_checkpoint_count = len(all_rows)

    if resume and not state:
        logger.info("无可用断点，从头开始导出")

    try:
        while True:
            items, page_total = fetch_student_page(
                host, token, page_num, page_size, timeout,
                retry_max, retry_backoff_seconds, logger,
            )
            if total is None:
                total = page_total
                logger.info("意向学员总数：{}，每页 {} 条".format(total, page_size))
            if not items:
                break

            start_index = page_offset

            # 本次导出性能优化：先把这一页（从断点续传位置开始）所有学员的 ID 收集起来，
            # 一次批量请求查完这一页全部学员的推荐记录，而不是像之前那样对每个学员单独
            # 发一次请求——几万个学员就是几万次独立 HTTP 请求，是导出慢的主要原因。
            pending_student_ids: List[int] = []
            for idx, student in enumerate(items):
                if idx < start_index:
                    continue
                student_id = student.get("id")
                if student_id is None:
                    logger.warn("跳过缺少 id 的学员记录：{}".format(student))
                    continue
                pending_student_ids.append(int(student_id))

            recs_by_student_id: Dict[int, List[Dict[str, Any]]] = {}
            if pending_student_ids:
                recs_by_student_id = fetch_student_recommendations_batch(
                    host, token, pending_student_ids, timeout,
                    retry_max, retry_backoff_seconds, request_delay_ms, logger,
                )

            for idx, student in enumerate(items):
                if idx < start_index:
                    continue
                student_id = student.get("id")
                if student_id is None:
                    page_offset = idx + 1
                    continue
                recs = recs_by_student_id.get(int(student_id), [])
                all_rows.append(_student_to_export_row(student, recs))
                page_offset = idx + 1

            fetched = len(all_rows)
            logger.info("已拉取 {}/{} 条学员".format(fetched, total))

            is_last_page = fetched >= total or len(items) < page_size
            # 按"累计新增条数达到 checkpoint_interval"来决定要不要写断点文件，
            # 不是每处理完一页就写一次（页粒度太小、写盘太频繁，见上面变量注释）；
            # 最后一页额外强制写一次，保证结束时断点文件反映的是完整的已完成状态。
            if checkpoint_interval > 0 and (fetched - last_checkpoint_count >= checkpoint_interval or is_last_page):
                _save_checkpoint(
                    checkpoint_path, host, page_size, total or 0,
                    page_num, page_offset, all_rows, logger,
                )
                last_checkpoint_count = fetched

            if is_last_page:
                break
            page_num += 1
            page_offset = 0
    except RuntimeError:
        if all_rows and checkpoint_interval >= 0:
            _save_checkpoint(
                checkpoint_path, host, page_size, total or len(all_rows),
                page_num, page_offset, all_rows, logger,
            )
            logger.info("导出中断，已保存断点 {} 条，可执行：python export_prospect_students.py --resume".format(
                len(all_rows),
            ))
        raise

    return all_rows


def write_export_file(output_file: Path, rows: List[Dict[str, Any]], logger: DualLogger) -> int:
    """写入 JSON 主文件，返回字节数。"""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(rows, ensure_ascii=False, indent=2)
    output_file.write_text(text + "\n", encoding="utf-8")
    size = output_file.stat().st_size
    logger.info("已写入主文件：{}（{:.2f} MB）".format(output_file, size / 1024 / 1024))
    return size


def validate_export_file(path: Path, logger: DualLogger) -> int:
    logger.info("校验 JSON 结构（大文件可能需数十秒）...")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"顶层应为数组，实际为 {type(data).__name__}")
    logger.info(f"校验通过：共 {len(data)} 条意向学员")
    if data:
        sample = data[0]
        keys = list(sample.keys()) if isinstance(sample, dict) else []
        logger.info(f"首条字段示例：{keys}")
    return len(data)


def run_pipeline(
    config_path: Optional[Path] = None,
    host_override: Optional[str] = None,
    out_override: Optional[str] = None,
    validate_override: Optional[bool] = None,
    resume: bool = False,
) -> int:
    _ensure_utf8_stdio()
    config = load_config(config_path)
    server = resolve_prospect_config(config.get("服务端"))
    if host_override:
        server["host"] = normalize_host(host_override)
    export_cfg = config["导出"]
    log_cfg = config["日志"]

    log_path = resolve_path(log_cfg.get("log_file", "logs/export_prospect.log"))
    logger = DualLogger(log_path)

    output_file = resolve_main_output_path(export_cfg, out_override)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    validate = (
        validate_override
        if validate_override is not None
        else bool(export_cfg.get("validate_json", True))
    )
    timeout = int(export_cfg.get("timeout_seconds", 600))
    page_size = int(export_cfg.get("page_size", 200) or 200)
    if page_size < 1:
        page_size = 200
    retry_max = int(export_cfg.get("retry_max", 8) or 8)
    retry_backoff_seconds = float(export_cfg.get("retry_backoff_seconds", 3) or 3)
    request_delay_ms = int(export_cfg.get("request_delay_ms", 20) or 0)
    checkpoint_interval = int(export_cfg.get("checkpoint_interval", 500) or 0)
    checkpoint_path = _resolve_checkpoint_path(export_cfg)

    logger.info("=" * 60)
    logger.info("意向学员导出开始")
    logger.info(f"host={server['host']}, account={server['account']}")
    logger.info(f"output={output_file}, validate={validate}, timeout={timeout}s")
    if resume:
        logger.info("续传模式：--resume 已启用")
    logger.info("=" * 60)

    token = login_export(server["host"], server["account"], server["password"], logger)

    logger.info("拉取方式：分页列表 + 按页批量查询推荐记录（后端暂无 /export 流式接口，"
                "已改用批量推荐记录接口，不再逐个学员单独请求）")
    if server["host"].rstrip("/") in ("http://localhost", "http://127.0.0.1"):
        logger.info("提示：若仍频繁 502，可在 config.json 改用 host=http://localhost:18081 绕过 nginx")
    rows: List[Dict[str, Any]] = []
    try:
        rows = build_export_payload(
            server["host"], token, page_size, timeout,
            retry_max, retry_backoff_seconds, request_delay_ms,
            checkpoint_interval, checkpoint_path, resume, logger,
        )
    except RuntimeError as ex:
        logger.error(str(ex))
        return 2

    write_export_file(output_file, rows, logger)
    save_timestamped_snapshot(rows, export_cfg, logger)
    _clear_checkpoint(checkpoint_path, logger)

    if validate:
        try:
            validate_export_file(output_file, logger)
        except (json.JSONDecodeError, ValueError) as ex:
            logger.error(f"JSON 校验失败：{ex}")
            return 3

    logger.info("=== 导出完成 ===")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="manjike 意向学员流式导出")
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--out", type=str, default=None, help="输出文件路径，支持 {date}")
    parser.add_argument("--no-validate", action="store_true", help="下载后不解析 JSON")
    parser.add_argument("--resume", action="store_true", help="从断点文件继续导出（502 中断后使用）")
    return parser.parse_args()


def _cli_main() -> int:
    args = _parse_args()
    return run_pipeline(
        config_path=Path(args.config) if args.config else None,
        host_override=args.host,
        out_override=args.out,
        validate_override=False if args.no_validate else None,
        resume=bool(args.resume),
    )


if __name__ == "__main__":
    try:
        sys.exit(_cli_main())
    except KeyboardInterrupt:
        print("\n[CANCELLED]")
        sys.exit(130)
