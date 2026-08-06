#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""manjike-tools 公共工具库。

【模块功能说明】
为 manjike-tools 下各脚本提供统一的 HTTP 请求、登录鉴权、配置解析与日志能力，
避免每个脚本重复实现 Prospect / 主系统登录和 curl 文件上传逻辑。

本模块被 users/upload-users.py、internal/upload_internal.py、
prospect/upload_prospect.py、prospect_client.py 共同 import。

公共内容：
  - DualLogger          屏幕 + 文件双写日志
  - http_request        统一 HTTP 请求（JSON / multipart）
  - http_get / http_post  带 token 的便捷封装
  - http_upload_multipart curl -F 文件上传（兼容 Spring Boot）
  - login               主系统登录 /api/auth/login
  - login_prospect      Prospect 登录，返回 {token, userInfo}
  - load_root_config    读取 config.json 统一配置
  - resolve_prospect_config / resolve_commission_config
"""

import json
import mimetypes
import os
import shutil
import subprocess
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

    注意：Spring / Tomcat 解析 multipart 时，文本字段必须排在文件字段之前，
    否则 @RequestParam("type") 等可能读不到值（表现为“type 只能是 ...”参数校验失败）。
    """
    boundary = "----ManjikeBoundary" + uuid.uuid4().hex
    crlf = b"\r\n"
    chunks: List[bytes] = []

    # 先写普通字段，再写文件字段，避免 Spring 漏读 type / mode
    text_items = [(name, value) for name, value in fields.items() if not isinstance(value, Path)]
    file_items = [(name, value) for name, value in fields.items() if isinstance(value, Path)]
    ordered_items = text_items + file_items

    for name, value in ordered_items:
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
            # 普通文本字段：显式声明 text/plain，提升 Spring multipart 解析兼容性
            chunks.append(f'Content-Disposition: form-data; name="{name}"'.encode("utf-8"))
            chunks.append(b"Content-Type: text/plain; charset=UTF-8")
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


def http_get(
    url: str,
    token: Optional[str] = None,
    timeout: int = 60,
) -> Tuple[int, Dict[str, Any]]:
    """GET 请求；token 非空时自动加 Authorization。"""
    headers = {}
    if token:
        headers["Authorization"] = "Bearer {}".format(token)
    return http_request(url=url, method="GET", headers=headers, timeout=timeout)


def http_post(
    url: str,
    token: Optional[str] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
) -> Tuple[int, Dict[str, Any]]:
    """POST JSON 请求；token 非空时自动加 Authorization。"""
    headers = {}
    if token:
        headers["Authorization"] = "Bearer {}".format(token)
    return http_request(
        url=url,
        method="POST",
        headers=headers,
        json_body=json_body,
        timeout=timeout,
    )


def log_business_error(logger: DualLogger, body: Dict[str, Any], prefix: str = "") -> None:
    """统一输出业务失败日志（HTTP 200 但 code != 0 的情况）。"""
    code = body.get("code")
    message = body.get("message") or body.get("_raw") or json.dumps(body, ensure_ascii=False)
    label = (prefix + "：") if prefix else ""
    logger.error("{}业务失败 code={} message={}".format(label, code, message))


def _guess_upload_mime(file_path: Path) -> str:
    """按扩展名推断上传文件的 MIME 类型。"""
    suffix = file_path.suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if suffix == ".xls":
        return "application/vnd.ms-excel"
    return mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"


def http_upload_multipart(
    url: str,
    token: str,
    fields: Dict[str, str],
    file_path: Path,
    file_field: str = "file",
    timeout: int = 600,
) -> Tuple[int, Dict[str, Any]]:
    """使用 curl 上传 multipart/form-data，兼容 Spring Boot 导入接口。

    背景：标准库 urllib 手工拼 multipart 时，Tomcat/Spring 偶发读不到 type/mode 文本字段；
    curl -F 与浏览器 FormData 行为一致，Prospect 文件导入接口优先走此函数。
    """
    curl_bin = shutil.which("curl")
    if curl_bin is None:
        return 0, {
            "code": -1,
            "message": "未找到 curl 可执行文件，无法上传 multipart 文件",
            "data": None,
        }
    if not file_path.is_file():
        return 0, {"code": -1, "message": f"待上传文件不存在：{file_path}", "data": None}

    # curl 在 Windows 下对 @路径 更稳的是正斜杠
    curl_file_path = str(file_path.resolve()).replace("\\", "/")
    mime = _guess_upload_mime(file_path)
    cmd: List[str] = [
        curl_bin,
        "-sS",
        "-X",
        "POST",
        url,
        "-H",
        f"Authorization: Bearer {token}",
        "-w",
        "\n__HTTP_CODE__:%{http_code}",
        "--max-time",
        str(max(1, int(timeout))),
    ]
    for key, value in fields.items():
        cmd.extend(["-F", f"{key}={value}"])
    cmd.extend(["-F", f"{file_field}=@{curl_file_path};type={mime}"])

    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=False,
        )
    except OSError as ex:
        return 0, {"code": -1, "message": f"调用 curl 失败：{ex}", "data": None}

    raw_output = completed.stdout or ""
    stderr_text = (completed.stderr or "").strip()
    if "__HTTP_CODE__:" in raw_output:
        body_text, _, status_text = raw_output.rpartition("\n__HTTP_CODE__:")
        try:
            status = int(status_text.strip())
        except ValueError:
            status = 0
    else:
        body_text = raw_output
        status = 0

    body_text = body_text.strip()
    try:
        body = json.loads(body_text) if body_text else {}
    except json.JSONDecodeError:
        body = {"_raw": body_text}
        if stderr_text:
            body["_stderr"] = stderr_text

    if status == 0 and completed.returncode != 0:
        message = stderr_text or body_text or f"curl 退出码 {completed.returncode}"
        return 0, {"code": -1, "message": message, "data": None}

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


def login_prospect(host: str, account: str, password: str, logger: DualLogger) -> Dict[str, Any]:
    """调用 Prospect 独立后端 /api/prospect/auth/login，返回 {token, userInfo}。

    注意：主系统 http://localhost:8080 已下线 /api/prospect/**，不可用于本函数。
    """
    logger.info("登录 Prospect 独立后端：{}".format(host))
    status, body = http_request(
        url="{}/api/prospect/auth/login".format(host),
        method="POST",
        json_body={"username": account, "password": password},
    )
    if status != 200 or body.get("code") != 0:
        log_business_error(logger, body, prefix="Prospect 登录失败")
        logger.error(
            "请确认 host 指向 Prospect（http://localhost 或 http://localhost:18081），"
            "并使用 prospect_user 账号。"
        )
        sys.exit(1)

    data = body.get("data") or {}
    token = data.get("token") or data.get("access_token")
    if not token:
        logger.error("Prospect 登录响应无 token：{}".format(json.dumps(body, ensure_ascii=False)))
        sys.exit(1)

    user_info = {
        "userId": data.get("userId"),
        "username": data.get("username"),
        "roleCode": data.get("roleCode"),
        "displayName": data.get("displayName"),
    }
    logger.info("Prospect 登录成功 user={} token={}...".format(
        user_info.get("username") or account, token[:12]
    ))
    return {"token": token, "userInfo": user_info}


# ============================================================
# 配置：config.json 统一读取
# ============================================================

TOOLS_ROOT = Path(__file__).resolve().parent
DEFAULT_PROSPECT_HOST = "http://localhost"
DEFAULT_COMMISSION_HOST = "http://localhost:8080"


def load_root_config() -> Dict[str, Any]:
    """读取 manjike-tools/config.json；不存在则回退 shared.config.json。"""
    for name in ("config.json", "shared.config.json"):
        path = TOOLS_ROOT / name
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return {k: v for k, v in raw.items() if not (isinstance(k, str) and k.startswith("_"))}
        except (json.JSONDecodeError, OSError):
            continue
    return {}


# 配置文件里常见的占位符；遇到这些值视为「未填写」，继续回退到上级 config.json
_CONFIG_PLACEHOLDER_MARKERS = (
    "你的真实密码",
    "请填写",
    "changeme",
    "your_password",
    "your password",
)


def is_effective_config_value(value: Any) -> bool:
    """判断配置项是否为有效值（非空、非占位符）。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    if not text:
        return False
    lowered = text.lower()
    for marker in _CONFIG_PLACEHOLDER_MARKERS:
        if marker.lower() in lowered:
            return False
    return True


def _pick_script_value(script_server: Dict[str, Any], key: str) -> str:
    """从脚本局部配置取值；占位符视为未填写。"""
    val = script_server.get(key)
    return str(val).strip() if is_effective_config_value(val) else ""


def resolve_prospect_config(script_server: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """解析 Prospect 连接配置。

    优先级：环境变量 PROSPECT_HOST / PROSPECT_ACCOUNT / PROSPECT_PASSWORD
            > 脚本「服务端」配置
            > config.json prospect 段
            > 旧 shared.config.json prospect_host
            > 默认 http://localhost
    """
    root = load_root_config()
    prospect_cfg = dict(root.get("prospect") or {})
    script_server = script_server or {}

    host = (
        os.environ.get("PROSPECT_HOST")
        or _pick_script_value(script_server, "host")
        or prospect_cfg.get("host")
        or root.get("prospect_host")
        or DEFAULT_PROSPECT_HOST
    )
    account = (
        os.environ.get("PROSPECT_ACCOUNT")
        or _pick_script_value(script_server, "account")
        or prospect_cfg.get("account")
        or ""
    )
    password = (
        os.environ.get("PROSPECT_PASSWORD")
        or _pick_script_value(script_server, "password")
        or prospect_cfg.get("password")
        or ""
    )
    return {
        "host": normalize_host(host),
        "account": str(account).strip(),
        "password": str(password).strip(),
    }


def resolve_commission_config(script_server: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """解析主系统（佣金/用户）连接配置。默认 http://localhost:8080。"""
    root = load_root_config()
    commission_cfg = dict(root.get("commission") or {})
    script_server = script_server or {}

    def _pick_script(key: str) -> str:
        return _pick_script_value(script_server, key)

    host = (
        os.environ.get("COMMISSION_HOST")
        or os.environ.get("MANJIKE_HOST")
        or _pick_script("host")
        or commission_cfg.get("host")
        or root.get("host")
        or DEFAULT_COMMISSION_HOST
    )
    account = (
        os.environ.get("COMMISSION_ACCOUNT")
        or _pick_script("account")
        or commission_cfg.get("account")
        or ""
    )
    password = (
        os.environ.get("COMMISSION_PASSWORD")
        or _pick_script("password")
        or commission_cfg.get("password")
        or ""
    )
    return {
        "host": normalize_host(host),
        "account": str(account).strip(),
        "password": str(password).strip(),
    }


def warn_if_main_backend_for_prospect(host: str, logger: Optional[DualLogger] = None) -> None:
    """启动检查：Prospect 脚本若指向 8080，给出明确警告。"""
    h = normalize_host(host or "")
    if ":8080" in h and "18081" not in h:
        msg = (
            "当前 host={} 指向主系统 backend，不是 Prospect 独立服务。"
            "请改为 http://localhost（Docker）或 http://localhost:18081（直连后端）。"
        ).format(h)
        if logger:
            logger.warn(msg)
        else:
            print("[WARN] {}".format(msg), file=sys.stderr)


def load_shared_config() -> Dict[str, Any]:
    """兼容旧代码：读取 config.json / shared.config.json。"""
    return load_root_config()

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
