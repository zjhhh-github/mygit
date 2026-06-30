#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""manjike-tools 公共工具库。

本模块被 users/upload-users.py、internal/upload_internal.py、
prospect/upload_prospect.py 共同 import，避免在各脚本中重复实现
相同的 HTTP、日志、配置合并等基础功能。

各脚本仍可独立运行（CLI 入口不变），本模块不改变任何对外行为。

公共内容：
  - DualLogger          屏幕 + 文件双写日志
  - http_request        统一 HTTP 请求（支持 JSON / multipart，支持重定向跟随）
  - encode_multipart    构造 multipart/form-data 请求体
  - login               登录后端并返回 JWT token
  - deep_merge          两层字典深合并（用于配置文件与默认值合并）
  - resolve_path        把相对路径锚定到指定目录（默认脚本所在目录）
  - normalize_host      规范化 base url（去尾斜杠，修正已知 http→https 域名）
  - ensure_utf8_stdio   Windows 控制台 UTF-8 兼容处理
"""

import json
import mimetypes
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


# ============================================================
# 日志：屏幕 + 文件双写
# ============================================================

class DualLogger:
    """轻量日志：每行同时写文件和 stdout。

    - 不依赖 logging 模块，避免多脚本 import 时 handler 互相干扰。
    - 文件追加写，方便横向对比多次执行结果。
    - Windows GBK 控制台下的 UnicodeEncodeError 会被 replace 兜底，不中断脚本。
    """

    def __init__(self, log_path: Path):
        self.log_path = log_path
        # 确保日志目录存在
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, line: str) -> None:
        """同时输出到控制台和日志文件。"""
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            # Windows GBK 控制台无法输出某些 Unicode 字符时，用 replace 兜底
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(line.encode(enc, errors="replace").decode(enc, errors="replace"), flush=True)
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # 日志写失败不应中断主流程

    def info(self, msg: str) -> None:
        self._write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [INFO] {msg}")

    def warn(self, msg: str) -> None:
        self._write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [WARN] {msg}")

    def error(self, msg: str) -> None:
        self._write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [ERROR] {msg}")


# ============================================================
# HTTP 工具：JSON + multipart/form-data，支持重定向跟随
# ============================================================

# 需要跟随重定向的 HTTP 状态码（Python 的 urlopen 不自动跟随 POST 重定向）
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


def encode_multipart(fields: Dict[str, Any]) -> Tuple[str, bytes]:
    """构造 multipart/form-data 请求体。

    fields 的值若是 Path 对象 → 视为文件上传；否则视为普通文本字段。
    返回 (boundary, body_bytes)。
    """
    boundary = "----ManjikeBoundary" + uuid.uuid4().hex
    crlf = b"\r\n"
    chunks: List[bytes] = []

    for name, value in fields.items():
        chunks.append(f"--{boundary}".encode("utf-8"))
        if isinstance(value, Path):
            # 文件字段：自动推断 MIME 类型
            filename = value.name
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            chunks.append(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode("utf-8")
            )
            chunks.append(f"Content-Type: {mime}".encode("utf-8"))
            chunks.append(b"")
            chunks.append(value.read_bytes())
        else:
            # 普通文本字段
            chunks.append(f'Content-Disposition: form-data; name="{name}"'.encode("utf-8"))
            chunks.append(b"")
            chunks.append(str(value).encode("utf-8"))
        chunks.append(b"")

    chunks.append(f"--{boundary}--".encode("utf-8"))
    chunks.append(b"")
    return boundary, crlf.join(chunks)


def http_request(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    multipart: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
    max_redirects: int = 5,
) -> Tuple[int, Dict[str, Any]]:
    """统一 HTTP 请求封装，返回 (status_code, response_json)。

    参数说明：
    - json_body：JSON 请求体（dict），与 multipart 互斥。
    - multipart：multipart/form-data 字段（dict），值为 Path 时作文件上传。
    - timeout：单次请求超时秒数。
    - max_redirects：最大重定向次数（POST 重定向场景）。

    错误处理：
    - 4xx / 5xx 不抛异常，返回 (status, response_body)，由调用方按 code 判断。
    - 网络错误返回 (0, {"code": -1, "message": "..."})。
    - 重定向次数超限返回 (-1, {"code": -1, "message": "重定向次数过多"})。
    """
    if json_body is not None and multipart is not None:
        raise ValueError("json_body 与 multipart 不能同时使用")

    headers = dict(headers or {})
    data: Optional[bytes] = None

    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json; charset=utf-8")

    if multipart is not None:
        boundary, data = encode_multipart(multipart)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

    current_url = url
    status = 0
    raw = b""

    for _ in range(max_redirects + 1):
        req = Request(current_url, data=data, headers=headers, method=method.upper())
        try:
            with urlopen(req, timeout=timeout) as resp:
                status = resp.status
                raw = resp.read()
                break  # 成功拿到响应，退出重定向循环
        except HTTPError as ex:
            status = ex.code
            raw = ex.read() if hasattr(ex, "read") else b""
            # 遇到重定向状态码时，跟随 Location 头继续请求
            if status in _REDIRECT_STATUS:
                location = ex.headers.get("Location") if ex.headers else None
                if location:
                    current_url = urljoin(current_url, location)
                    continue
            break  # 非重定向的 4xx/5xx，退出循环
        except URLError as ex:
            return 0, {"code": -1, "message": f"网络错误：{ex}", "data": None}
    else:
        return -1, {"code": -1, "message": "重定向次数过多", "data": None}

    text = raw.decode("utf-8", errors="replace") if raw else ""
    try:
        body = json.loads(text) if text else {}
    except json.JSONDecodeError:
        body = {"_raw": text}
    return status, body


# ============================================================
# 业务：登录
# ============================================================

def login(host: str, account: str, password: str, logger: DualLogger) -> str:
    """调用 /api/auth/login 登录并返回 JWT token。

    同时发送 code 和 username 字段，兼容新旧两种后端 LoginDTO。
    登录失败时直接 sys.exit(1)，不向上抛异常（符合脚本脚本惯例）。
    """
    logger.info(f"登录目标后端：{host}")
    status, body = http_request(
        url=f"{host}/api/auth/login",
        method="POST",
        json_body={"code": account, "username": account, "password": password},
    )
    if status != 200 or body.get("code") != 0:
        logger.error(f"登录失败：HTTP {status} / body={json.dumps(body, ensure_ascii=False)}")
        sys.exit(1)

    data = body.get("data") or {}
    # 兼容 token / access_token 两种字段名
    token = data.get("token") or data.get("access_token")
    if not token:
        logger.error(f"登录响应里没找到 token：{json.dumps(body, ensure_ascii=False)}")
        sys.exit(1)

    logger.info(f"登录成功，token={token[:12]}...（已截断）")
    return token


# ============================================================
# 配置工具：深合并、路径解析、Host 规范化
# ============================================================

def deep_merge(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """深合并两层字典：base 是默认值，override 是用户配置。

    规则：
    - 相同 key 的值都是 dict 时，递归合并（不整体覆盖）。
    - 带 _ 开头的 key（注释字段）在 override 里会被忽略。
    - override 里有而 base 没有的 key，直接加入结果。
    """
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
            continue  # 跳过注释字段
        if key not in result:
            result[key] = user_value
    return result


def resolve_path(value: str, base_dir: Path) -> Path:
    """把路径字符串解析为 Path：绝对路径原样返回；相对路径锚定到 base_dir。

    调用方通常传 Path(__file__).resolve().parent 作为 base_dir，
    保证无论从哪个 cwd 运行，相对路径都基于脚本目录解析。
    """
    p = Path(value)
    return p if p.is_absolute() else (base_dir / p).resolve()


def load_shared_config() -> Dict[str, Any]:
    """读取 manjike-tools/shared.config.json（公共配置）。

    文件不存在时返回空字典，不影响各脚本的独立运行。
    调用方用法示例：
        shared = load_shared_config()
        host = my_config.get("host") or shared.get("host") or "http://localhost:8080"
    """
    shared_path = Path(__file__).resolve().parent / "shared.config.json"
    if not shared_path.exists():
        return {}
    try:
        raw = json.loads(shared_path.read_text(encoding="utf-8"))
        # 过滤掉注释字段（以 _ 开头的 key）
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except (json.JSONDecodeError, OSError):
        return {}


def normalize_host(host: str) -> str:
    """规范化 base url：去尾斜杠；已知 dev 域名的 http 自动修正为 https。

    避免因为少写 https 导致服务器返回 308 重定向，增加一次无效请求。
    """
    h = (host or "").strip().rstrip("/")
    # dev 环境强制 https（后端配置了 HSTS / 强制跳转）
    if h.startswith("http://dev.manjikeabc.com"):
        return "https://dev.manjikeabc.com"
    return h


# ============================================================
# Windows 控制台 UTF-8 兼容
# ============================================================

def ensure_utf8_stdio() -> None:
    """尝试把 stdout / stderr 切换为 UTF-8 编码。

    Windows 默认控制台编码是 GBK / CP936，直接 print 中文会乱码或报错。
    Python 3.7+ 支持 reconfigure；旧版本或已是 UTF-8 时静默忽略。
    """
    if os.name != "nt":
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
