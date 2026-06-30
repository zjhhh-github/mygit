#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 manjike 后端流式导出全部意向学员 JSON。

【接口】
    GET /api/prospect/prospective-students/export
    权限：ADMIN 或 READONLY_ADMIN

【输出格式】
    与后台「意向学员管理 → 导出全部」一致，中文 key + 来源数组，
    可直接作为 upload_prospect.py 的 source_file（STUDENTS_IMPORT）。

【配置】同目录 export_prospect.config.json（可与 upload_prospect.config.json 共用服务端账号）

CLI：
    python export_prospect_students.py
    python export_prospect_students.py --host https://dev.manjikeabc.com --out 意向学员数据.json
    python export_prospect_students.py --no-validate
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "export_prospect.config.json"

_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})

EXPORT_PATH = "/api/prospect/prospective-students/export"

DEFAULT_CONFIG: Dict[str, Any] = {
    "服务端": {
        "host": "http://localhost:8080",
        "account": "admin",
        "password": "admin123",
    },
    "导出": {
        "output_dir": ".",
        "json_filename": "意向学员数据.json",
        "save_timestamped_copy": True,
        "timestamped_filename_template": "logs/意向学员数据导出_{timestamp}.json",
        "validate_json": True,
        "timeout_seconds": 600,
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


def deep_merge(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not override:
        return dict(base)
    result: Dict[str, Any] = {}
    for key, base_value in base.items():
        if key in override and not (isinstance(key, str) and key.startswith("_")):
            user_value = override[key]
            if isinstance(base_value, dict) and isinstance(user_value, dict):
                result[key] = deep_merge(base_value, user_value)
            else:
                result[key] = user_value
        else:
            result[key] = base_value if not isinstance(base_value, dict) else dict(base_value)
    for key, user_value in override.items():
        if isinstance(key, str) and key.startswith("_"):
            continue
        if key not in result:
            result[key] = user_value
    return result


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return dict(DEFAULT_CONFIG)
    user_cfg = json.loads(config_path.read_text(encoding="utf-8"))
    return deep_merge(DEFAULT_CONFIG, user_cfg)


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
    main_path: Path,
    export_cfg: Dict[str, Any],
    logger: DualLogger,
) -> Optional[Path]:
    """主文件写入成功后，再写一份带时间戳的快照（硬链接优先，跨盘则 copy2）。"""
    if not export_cfg.get("save_timestamped_copy", True):
        return None
    if not main_path.is_file():
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    template = export_cfg.get(
        "timestamped_filename_template",
        "logs/意向学员数据导出_{timestamp}.json",
    )
    out_dir = resolve_output_dir(str(export_cfg.get("output_dir", ".")))
    snapshot_path = out_dir / template.format(timestamp=timestamp)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if snapshot_path.exists():
            snapshot_path.unlink()
        os.link(main_path, snapshot_path)
    except (OSError, NotImplementedError):
        shutil.copy2(main_path, snapshot_path)

    logger.info(f"已写入时间戳快照：{snapshot_path}（{snapshot_path.stat().st_size} B）")
    return snapshot_path


class DualLogger:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, line: str) -> None:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(line.encode(enc, errors="replace").decode(enc, errors="replace"), flush=True)
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def info(self, msg: str) -> None:
        self._write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [INFO] {msg}")

    def error(self, msg: str) -> None:
        self._write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [ERROR] {msg}")


def http_json_request(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
) -> Tuple[int, Dict[str, Any]]:
    headers = dict(headers or {})
    data: Optional[bytes] = None
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json; charset=utf-8")

    current_url = url
    for _ in range(6):
        req = Request(current_url, data=data, headers=headers, method=method.upper())
        try:
            with urlopen(req, timeout=timeout) as resp:
                status = resp.status
                raw = resp.read()
            break
        except HTTPError as ex:
            status = ex.code
            raw = ex.read() if hasattr(ex, "read") else b""
            if status in _REDIRECT_STATUS:
                location = ex.headers.get("Location") if ex.headers else None
                if location:
                    current_url = urljoin(current_url, location)
                    continue
            text = raw.decode("utf-8", errors="replace") if raw else ""
            try:
                body = json.loads(text) if text else {}
            except json.JSONDecodeError:
                body = {"_raw": text}
            return status, body
        except URLError as ex:
            return 0, {"code": -1, "message": str(ex), "data": None}
    else:
        return 0, {"code": -1, "message": "重定向次数过多", "data": None}

    text = raw.decode("utf-8", errors="replace") if raw else ""
    try:
        body = json.loads(text) if text else {}
    except json.JSONDecodeError:
        body = {"_raw": text}
    return status, body


def login(host: str, account: str, password: str, logger: DualLogger) -> str:
    logger.info(f"登录：{host}")
    status, body = http_json_request(
        url=f"{host}/api/auth/login",
        method="POST",
        json_body={"code": account, "username": account, "password": password},
    )
    if status != 200 or body.get("code") != 0:
        logger.error(f"登录失败：HTTP {status} / {json.dumps(body, ensure_ascii=False)}")
        raise SystemExit(1)
    token = (body.get("data") or {}).get("token") or (body.get("data") or {}).get("access_token")
    if not token:
        logger.error(f"响应无 token：{json.dumps(body, ensure_ascii=False)}")
        raise SystemExit(1)
    logger.info(f"登录成功，token={token[:12]}...")
    return token


def parse_content_disposition(header: Optional[str], fallback: str) -> str:
    if not header:
        return fallback
    star = re.search(r"filename\*=UTF-8''([^;\s]+)", header, re.I)
    if star:
        from urllib.parse import unquote
        return unquote(star.group(1).strip())
    plain = re.search(r'filename="([^"]+)"', header, re.I)
    if plain:
        return plain.group(1)
    return fallback


def try_parse_api_error(raw: bytes) -> Optional[str]:
    if not raw or len(raw) > 256 * 1024:
        return None
    text = raw.decode("utf-8", errors="replace").strip()
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(obj.get("code"), int) and obj.get("code") != 0:
        return obj.get("message") or json.dumps(obj, ensure_ascii=False)
    return None


def download_export(
    host: str,
    token: str,
    output_file: Path,
    timeout: int,
    logger: DualLogger,
) -> Tuple[int, str]:
    """流式下载导出 JSON，返回 (字节数, 实际文件名)。"""
    url = f"{host}{EXPORT_PATH}"
    logger.info(f"请求导出：{url}")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {token}"}
    current_url = url
    resp = None

    for _ in range(6):
        req = Request(current_url, headers=headers, method="GET")
        try:
            resp = urlopen(req, timeout=timeout)
            break
        except HTTPError as ex:
            if ex.code in _REDIRECT_STATUS:
                location = ex.headers.get("Location") if ex.headers else None
                if location:
                    current_url = urljoin(current_url, location)
                    continue
            raw = ex.read() if hasattr(ex, "read") else b""
            err = try_parse_api_error(raw)
            logger.error(f"导出失败：HTTP {ex.code} / {err or raw[:500]!r}")
            raise SystemExit(1)
        except URLError as ex:
            logger.error(f"网络错误：{ex}")
            raise SystemExit(1)
    else:
        logger.error("重定向次数过多")
        raise SystemExit(1)

    assert resp is not None
    try:
        if resp.status != 200:
            raw = resp.read()
            err = try_parse_api_error(raw)
            logger.error(f"导出失败：HTTP {resp.status} / {err or raw[:500]!r}")
            raise SystemExit(1)

        disposition = resp.headers.get("Content-Disposition")
        server_name = parse_content_disposition(disposition, output_file.name)
        if server_name != output_file.name:
            logger.info(f"服务端建议文件名：{server_name}（仍写入配置主文件 {output_file.name}）")

        chunk_size = 1024 * 1024
        total = 0
        first_chunk = b""
        with output_file.open("wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                if not first_chunk:
                    first_chunk = chunk[:4096]
                f.write(chunk)
                total += len(chunk)

        err = try_parse_api_error(first_chunk)
        if err:
            try:
                output_file.unlink()
            except OSError:
                pass
            logger.error(f"导出返回业务错误：{err}")
            raise SystemExit(1)

        logger.info(f"已写入主文件：{output_file}（{total / 1024 / 1024:.2f} MB）")
        return total, output_file.name
    finally:
        resp.close()


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
) -> int:
    _ensure_utf8_stdio()
    config = load_config(config_path)
    server = dict(config["服务端"])
    server["host"] = normalize_host(host_override or server.get("host", ""))
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

    logger.info("=" * 60)
    logger.info("意向学员导出开始")
    logger.info(f"host={server['host']}, account={server['account']}")
    logger.info(f"output={output_file}, validate={validate}, timeout={timeout}s")
    logger.info("=" * 60)

    token = login(server["host"], server["account"], server["password"], logger)
    download_export(server["host"], token, output_file, timeout, logger)
    save_timestamped_snapshot(output_file, export_cfg, logger)

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
    return parser.parse_args()


def _cli_main() -> int:
    args = _parse_args()
    return run_pipeline(
        config_path=Path(args.config) if args.config else None,
        host_override=args.host,
        out_override=args.out,
        validate_override=False if args.no_validate else None,
    )


if __name__ == "__main__":
    try:
        sys.exit(_cli_main())
    except KeyboardInterrupt:
        print("\n[CANCELLED]")
        sys.exit(130)
