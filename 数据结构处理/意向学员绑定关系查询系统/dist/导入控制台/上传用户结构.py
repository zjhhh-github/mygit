# -*- coding: utf-8 -*-
"""
飞书多维表格 → 上传用户结构.json → 秒哒（Supabase）import-users
==============================================================

流程：
    1. 飞书多维表格分页拉取 → 字段抽取 → 转换为上传结构
    2. 写入本地 JSON 文件（保留 4 字段：账号 / 密码 / 微信号 / 角色）
    3. 登录秒哒（Supabase）拿 access_token
    4. 调用 /functions/v1/import-users，按 200/批 上传
       上传载荷只含 3 字段（账号 / 密码 / 角色），符合接口约定
    5. 汇总每批 total / created / skipped / errors

数据源（飞书多维表格）：
    https://ipcjg02m9k.feishu.cn/base/Zk05bwki2abD8XsBBOccaFsPn8e
        ?table=tblKa8wryhV4d7F4&view=vew7GtEotv

字段映射：
    账号    ← 编号
    密码    ← 意向通讯录密码
    微信号  ← 合伙宝妈微信号
    角色    ← 固定 "普通用户"

依赖：
    pip install requests

运行（PowerShell，从脚本所在目录）：
    cd D:\\桌面文件\\新建文件夹\\数据结构处理\\意向学员绑定关系查询系统

    # 全流程：拉取 → 写 JSON → 上传秒哒（默认）
    python .\\上传用户结构.py

    # 只生成 JSON，不上传
    python .\\上传用户结构.py --no-upload

    # 只用本地已有 JSON 上传，不重新拉飞书
    python .\\上传用户结构.py --upload-only

    # 拉取 + 写 JSON + 干跑上传（仅打印将要上传的条数，不真发请求）
    python .\\上传用户结构.py --dry-run
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests


# ────────────────────────── 控制台 UTF-8 修复 ──────────────────────────
# Windows 默认 GBK 控制台遇到中文 / emoji 会抛 UnicodeEncodeError。
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ────────────────────────── 配置 ──────────────────────────
# 飞书应用凭证：环境变量优先，缺省回退到本仓库其他脚本同款默认值
_DEFAULT_APP_ID     = "cli_a96f36ed1538dbcf"
_DEFAULT_APP_SECRET = "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB"
APP_ID     = os.environ.get("FEISHU_APP_ID", "").strip()     or _DEFAULT_APP_ID
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "").strip() or _DEFAULT_APP_SECRET

# 多维表格定位（直接来自数据源 URL）
APP_TOKEN = "Zk05bwki2abD8XsBBOccaFsPn8e"
TABLE_ID  = "tblKa8wryhV4d7F4"
VIEW_ID   = "vew7GtEotv"            # 仅读取该视图；置空字符串 "" 读整张表

# 字段名（飞书多维表格中实际显示的字段名）
FIELD_ID       = "编号"             # → 账号
FIELD_PASSWORD = "意向通讯录密码"   # → 密码
FIELD_WECHAT   = "合伙宝妈总微信号"   # → 微信号（飞书实际字段名带"总"字）

# 输出文件
OUTPUT_JSON_PATH = Path(
    r"D:\桌面文件\新建文件夹\数据结构处理\意向学员绑定关系查询系统\上传用户结构.json"
)
# 本地密码缓存（账号 -> 最近一次成功上传的密码）。
# 用于“密码相同则不更新，密码不同才更新”。
PASSWORD_CACHE_PATH = Path(
    r"D:\桌面文件\新建文件夹\数据结构处理\意向学员绑定关系查询系统\upload_password_cache.json"
)

# 角色固定值
DEFAULT_ROLE = "普通用户"

# 网络
FEISHU_HOST     = "https://open.feishu.cn"
# 默认超时：飞书等轻量请求够用
# 元组 (连接超时秒, 读取超时秒)；秒哒 import-users 单批耗时较长，改用 UPLOAD_TIMEOUT
REQUEST_TIMEOUT = (10, 30)
# 上传秒哒 import-users 时使用：连接 10 秒、读取 180 秒
# 200 条/批的处理时间 + 网关排队，30 秒经常不够
UPLOAD_TIMEOUT  = (10, 180)
RETRY_TIMES     = 3
PAGE_SIZE       = 100


# ────────────────────────── 秒哒（Supabase）配置 ──────────────────────────
# 与本仓库 增量导入.py 同款配置；环境变量优先
SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    "https://backend.appmiaoda.com/projects/supabase293970823448936448",
).rstrip("/")
SUPABASE_ANON_KEY = os.environ.get(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoyMDg5NTE1MzA2LCJpc3MiOiJzdXBhYmFzZSIsInJvbGUiOiJhbm9uIiwic3ViIjoiYW5vbiJ9.Z19rhe7D6v4pXthoontMmG_C1U3yW6DTTSyFOKYvs54",
)
SUPABASE_EMAIL    = os.environ.get("SUPABASE_EMAIL",    "15648230994@miaoda.com")
SUPABASE_PASSWORD = os.environ.get("SUPABASE_PASSWORD", "028056hQ@")

# 上传接口：PostgREST RPC
# - 新建用户：create_user_by_admin（upsert 的新建分支会把 role 写成非法值「业务员」）
# - 已存在用户：upsert_user_by_admin（更新密码 / 微信号 / 角色）
RPC_CREATE_FUNCTION = "create_user_by_admin"
RPC_UPSERT_FUNCTION = "upsert_user_by_admin"
# 兼容旧代码引用
RPC_FUNCTION = RPC_UPSERT_FUNCTION

# 秒哒 profiles.role 合法枚举（来自 PostgREST OpenAPI：public.user_role）
VALID_ROLES = frozenset({"普通用户", "管理员", "只读管理员"})

# RPC 单条调用，无 batch 概念；为保留打印进度的颗粒度，每 N 条打印一次
UPLOAD_PROGRESS_STEP = 50

# 本地结构 → RPC 参数名 的映射
# 注意：RPC 形参必须使用英文 p_xxx，不能传中文键
LOCAL_TO_RPC_KEYS = {
    "账号":   "p_username",
    "密码":   "p_password",
    "角色":   "p_role",
    "微信号": "p_wechat_id",
}


# ────────────────────────── 通用：带重试的请求 ──────────────────────────
def _request(method: str, url: str, **kwargs) -> requests.Response:
    """5xx / 429 / 网络异常自动重试，最多 RETRY_TIMES 次。
    支持调用方通过 kwargs 传入 timeout 覆盖默认值（如上传场景使用 UPLOAD_TIMEOUT）。"""
    timeout = kwargs.pop("timeout", REQUEST_TIMEOUT)
    last_exc: Optional[Exception] = None
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code >= 500 or resp.status_code == 429:
                last_exc = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                if attempt < RETRY_TIMES:
                    print(f"  [重试 {attempt}/{RETRY_TIMES - 1}] {method} {url} → {resp.status_code}")
                    time.sleep(min(2 ** attempt, 5))
                    continue
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt < RETRY_TIMES:
                print(f"  [重试 {attempt}/{RETRY_TIMES - 1}] {method} {url} → {e}")
                time.sleep(min(2 ** attempt, 5))
                continue
    raise RuntimeError(f"请求最终失败：{method} {url}，原因：{last_exc}")


# ────────────────────────── 字段值抽取 ──────────────────────────
def _extract_text(value: Any) -> str:
    """
    把飞书字段值统一抽成字符串：
        - 纯字符串 → strip
        - 数字 → int 化（去 .0）后 str
        - 富文本数组 [{"type":"text","text":"..."}] → 拼接 text
        - dict（如超链接）→ 取 text
        - None → ""
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
        parts: List[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")).strip())
            elif item is not None:
                parts.append(str(item).strip())
        return "".join(parts).strip()
    if isinstance(value, dict):
        return str(value.get("text", "")).strip()
    return str(value).strip()


# ────────────────────────── 1. 鉴权 ──────────────────────────
def 获取_tenant_access_token() -> str:
    """获取 tenant_access_token（internal 应用方式）"""
    url = f"{FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal"
    print("[鉴权] 获取 tenant_access_token ...")
    resp = _request("POST", url, json={"app_id": APP_ID, "app_secret": APP_SECRET})
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 token 失败：{data}")
    print(f"[鉴权] 成功，有效期 {data.get('expire')} 秒")
    return data["tenant_access_token"]


# ────────────────────────── 2. 拉取记录（分页 + 视图过滤） ──────────────
def 拉取所有记录(token: str) -> List[Dict[str, Any]]:
    """
    分页拉取多维表格全部记录（限定 VIEW_ID 视图）。
    返回每条形如：{"record_id": "...", "fields": {...}}
    """
    url = (
        f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{APP_TOKEN}"
        f"/tables/{TABLE_ID}/records"
    )
    headers = {"Authorization": f"Bearer {token}"}

    所有记录: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    页码 = 0

    while True:
        页码 += 1
        params: Dict[str, Any] = {"page_size": PAGE_SIZE}
        if VIEW_ID:
            params["view_id"] = VIEW_ID
        if page_token:
            params["page_token"] = page_token

        resp = _request("GET", url, headers=headers, params=params)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"拉取记录失败：{data}")

        items = data["data"].get("items") or []
        所有记录.extend(items)
        print(f"[拉取] 第 {页码} 页，本页 {len(items)} 条，累计 {len(所有记录)} 条")

        if not data["data"].get("has_more"):
            break
        page_token = data["data"].get("page_token")
        if not page_token:
            break

    return 所有记录


# ────────────────────────── 3. 转换为上传结构 ──────────────────────────
def 转换为上传结构(records: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    映射规则：
        账号    ← 编号
        密码    ← 意向通讯录密码
        微信号  ← 合伙宝妈微信号
        角色    ← "普通用户"

    业务规则：
        - 账号为空 → 跳过
        - 账号重复 → 仅保留首次出现
        - 密码 / 微信号缺失 → 仍输出，对应字段为 ""
    """
    输出数据: List[Dict[str, str]] = []
    已出现账号: Set[str] = set()
    跳过_无账号 = 0
    跳过_重复 = 0

    for rec in records:
        fields = rec.get("fields", {}) or {}
        账号   = _extract_text(fields.get(FIELD_ID))
        密码   = _extract_text(fields.get(FIELD_PASSWORD))
        微信号 = _extract_text(fields.get(FIELD_WECHAT))

        if not 账号:
            跳过_无账号 += 1
            continue
        if 账号 in 已出现账号:
            跳过_重复 += 1
            continue
        已出现账号.add(账号)

        输出数据.append(
            {
                "账号":   账号,
                "密码":   密码,
                "微信号": 微信号,
                "角色":   DEFAULT_ROLE,
            }
        )

    print(f"[转换] 入库 {len(输出数据)} 条 / 跳过-无账号 {跳过_无账号} 条 / 跳过-重复 {跳过_重复} 条")
    return 输出数据


# ────────────────────────── 4. 写入 JSON ──────────────────────────
def 写入_json(数据: List[Dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(数据, f, ensure_ascii=False, indent=2)


# ────────────────────────── 5. 秒哒登录 ──────────────────────────
def 秒哒登录() -> str:
    """通过邮箱+密码登录 Supabase，返回 access_token。"""
    url = f"{SUPABASE_URL}/auth/v1/token?grant_type=password"
    headers = {
        "apikey":       SUPABASE_ANON_KEY,
        "Content-Type": "application/json",
    }
    payload = {"email": SUPABASE_EMAIL, "password": SUPABASE_PASSWORD}

    print(f"[秒哒] 登录 {SUPABASE_EMAIL} ...")
    resp = _request("POST", url, headers=headers, json=payload)
    if resp.status_code != 200:
        raise RuntimeError(
            f"秒哒登录失败 status={resp.status_code} body={resp.text[:300]}"
        )
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"秒哒登录响应缺少 access_token：{data}")
    print(f"[秒哒] 登录成功，token 长度 {len(token)}")
    return token


# ────────────────────────── 6. 秒哒上传（逐条 RPC） ──────────────────────────
def _normalize_role(角色: str) -> str:
    """
    把本地「角色」规范成秒哒 user_role 枚举允许的值。
    当前库只接受：普通用户 / 管理员 / 只读管理员；其它值（含历史「业务员」）统一回落为普通用户。
    """
    角色 = (角色 or "").strip()
    if 角色 in VALID_ROLES:
        return 角色
    return DEFAULT_ROLE


def _抽取上传载荷(条目: Dict[str, str]) -> Dict[str, str]:
    """
    把本地中文键转换成 RPC 形参（p_username / p_password / p_role / p_wechat_id）。
    必须使用英文形参名，传中文键 PostgREST 会报 "could not find function ... in schema"。
    密码以明文发送，由数据库 RPC 内部自己处理，避免依赖失效的 gen_salt。
    """
    payload: Dict[str, str] = {}
    for 本地键, rpc键 in LOCAL_TO_RPC_KEYS.items():
        payload[rpc键] = 条目.get(本地键, "") or ""
    payload["p_role"] = _normalize_role(payload.get("p_role", ""))
    return payload


def _rpc_error_message(resp: requests.Response) -> str:
    """从 PostgREST 错误响应里提取可读 message。"""
    try:
        err_body = resp.json()
        return str(
            err_body.get("message") or err_body.get("error") or resp.text[:200]
        )
    except Exception:
        return resp.text[:200]


def _load_password_cache(path: Path) -> Dict[str, str]:
    """读取本地密码缓存。"""
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if str(k).strip()}
    except Exception:
        return {}


def _save_password_cache(path: Path, cache: Dict[str, str]) -> None:
    """保存本地密码缓存。"""
    with path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _upsert_create_role_bug(err_msg: str) -> bool:
    """
    upsert_user_by_admin 在「用户尚不存在」时会触发 enum user_role 报错（内部写死业务员）。
    识别该特征后应改用 create_user_by_admin。
    """
    return "enum user_role" in err_msg


def _调用用户_rpc(
    token: str,
    rpc_name: str,
    载荷: Dict[str, str],
) -> requests.Response:
    """调用指定 RPC（create / upsert），统一 headers 与超时。"""
    url = f"{SUPABASE_URL}/rest/v1/rpc/{rpc_name}"
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey":        SUPABASE_ANON_KEY,
        "Content-Type":  "application/json; charset=utf-8",
    }
    body = json.dumps(载荷, ensure_ascii=False).encode("utf-8")
    return _request(
        "POST", url,
        headers=headers,
        data=body,
        timeout=UPLOAD_TIMEOUT,
    )


def 秒哒上传用户(
    用户列表: List[Dict[str, str]],
    token: str,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    通过 PostgREST RPC 逐条上传用户。

    设计要点：
        - 已存在用户：POST upsert_user_by_admin（更新）
        - 新建用户：upsert 会误写非法角色「业务员」，故改走 create_user_by_admin
        - 形参必须是英文 p_username / p_password / p_role / p_wechat_id
        - 密码明文发送，数据库 RPC 内部自行处理
        - 单条失败不阻断后续；汇总里记录失败明细

    返回汇总：
        {
            "total":   int,        # 实际尝试上传的条数
            "created": int,        # RPC 视为新建/更新成功的条数
            "skipped": int,        # 数据库返回"已存在跳过"的条数（若 RPC 支持）
            "errors":  [ {...}, ... ],
            "batches": int,        # 这里等同于成功调用次数；保留字段方便兼容外层调用
        }

    dry_run=True 时只打印将要上传的条数，不真发请求。
    """
    汇总: Dict[str, Any] = {
        "total":   0,
        "created": 0,
        "skipped": 0,
        "password_unchanged": 0,
        "errors":  [],
        "batches": 0,
    }
    密码缓存 = _load_password_cache(PASSWORD_CACHE_PATH)

    总数 = len(用户列表)
    if 总数 == 0:
        print("[上传] 用户列表为空，跳过上传")
        return 汇总

    print(
        f"[上传] 共 {总数} 条，逐条 RPC "
        f"（已存在→{RPC_UPSERT_FUNCTION} / 新建→{RPC_CREATE_FUNCTION}）"
    )

    if dry_run:
        # 干跑：仅打印第 1 条 payload 形态，便于排查字段名
        if 用户列表:
            示例载荷 = _抽取上传载荷(用户列表[0])
            print(f"  [干跑] 示例 payload = {示例载荷}")
        print(f"  [干跑] 将上传 {总数} 条（未发请求）")
        汇总["total"] = 总数
        return 汇总

    for idx, 条目 in enumerate(用户列表, start=1):
        原始载荷 = _抽取上传载荷(条目)
        载荷 = dict(原始载荷)
        账号 = 条目.get("账号", "") or 原始载荷.get("p_username", "")
        本次密码 = 原始载荷.get("p_password", "") or ""

        # 本地缓存命中且密码未变化：不传 p_password，避免无效更新密码
        if 账号 and 本次密码 and 密码缓存.get(账号) == 本次密码:
            载荷.pop("p_password", None)
            汇总["password_unchanged"] += 1

        try:
            resp = _调用用户_rpc(token, RPC_UPSERT_FUNCTION, 载荷)
        except Exception as e:
            汇总["errors"].append({"账号": 账号, "reason": f"网络异常: {e}"})
            汇总["total"] += 1
            continue

        汇总["total"] += 1

        # upsert 对「尚不存在」的用户会报 enum user_role / 业务员，改走 create
        if resp.status_code not in (200, 201, 204):
            err_msg = _rpc_error_message(resp)
            if _upsert_create_role_bug(err_msg):
                try:
                    # 新建时必须传完整载荷（含 p_password）
                    resp = _调用用户_rpc(token, RPC_CREATE_FUNCTION, 原始载荷)
                except Exception as e:
                    汇总["errors"].append({
                        "账号": 账号,
                        "reason": f"新建回退网络异常: {e}",
                    })
                    continue

        if resp.status_code not in (200, 201, 204):
            err_msg = _rpc_error_message(resp)
            汇总["errors"].append({
                "账号": 账号,
                "reason": f"HTTP {resp.status_code}: {err_msg}",
            })
        else:
            # PostgREST RPC 成功返回：
            #   - 函数有返回值 → JSON（可能是单值、对象或对象数组）
            #   - 函数 returns void → 空字符串 / 204
            # 这里我们不依赖具体结构，统一计入 created
            汇总["created"] += 1
            # 如果 RPC 显式回传了 {"skipped": true} 或 {"action": "skipped"}，识别一下
            try:
                if resp.text.strip():
                    data = resp.json()
                    候选 = data[0] if isinstance(data, list) and data else data
                    if isinstance(候选, dict):
                        if 候选.get("skipped") is True or 候选.get("action") == "skipped":
                            汇总["created"] -= 1
                            汇总["skipped"] += 1
            except Exception:
                pass
            # 成功后更新本地缓存（只记录非空密码）
            if 账号 and 本次密码:
                密码缓存[账号] = 本次密码

        # 进度打印
        if idx == 总数 or idx % UPLOAD_PROGRESS_STEP == 0:
            print(
                f"  [进度] {idx}/{总数} "
                f"created={汇总['created']} skipped={汇总['skipped']} "
                f"errors={len(汇总['errors'])}"
            )

    汇总["batches"] = 汇总["total"]  # 兼容外层"批次"概念
    _save_password_cache(PASSWORD_CACHE_PATH, 密码缓存)
    return 汇总


# ────────────────────────── 7. 主流程 ──────────────────────────
def _解析参数() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="飞书 → 上传用户结构.json → 秒哒 import-users",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="只生成 JSON，不上传到秒哒",
    )
    parser.add_argument(
        "--upload-only",
        action="store_true",
        help="跳过飞书拉取，直接读取已有的 上传用户结构.json 上传",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="上传环节只打印将要上传的批次，不真发请求",
    )
    return parser.parse_args()


def _读取本地_json(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"未找到本地 JSON：{path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"本地 JSON 顶层不是数组：{path}")
    return data


def main() -> None:
    args = _解析参数()

    print("=" * 60)
    print("飞书多维表格 → 上传用户结构.json → 秒哒 import-users")
    print(f"  飞书 app_token = {APP_TOKEN}")
    print(f"  飞书 table_id  = {TABLE_ID}")
    print(f"  飞书 view_id   = {VIEW_ID or '(整张表)'}")
    print(f"  字段映射       : 账号←{FIELD_ID} / 密码←{FIELD_PASSWORD} / 微信号←{FIELD_WECHAT}")
    print(f"  本地 JSON      = {OUTPUT_JSON_PATH}")
    print(f"  秒哒 URL       = {SUPABASE_URL}")
    print(f"  秒哒账号       = {SUPABASE_EMAIL}")
    print(f"  模式           : {'仅上传' if args.upload_only else '拉取+生成'}"
          f"{' (不上传)' if args.no_upload else ''}"
          f"{' [干跑]' if args.dry_run else ''}")
    print("=" * 60)

    # ── 1. 取数据：飞书拉取 OR 本地 JSON ───────────────────────
    if args.upload_only:
        print(f"[阶段1] 从本地 JSON 读取：{OUTPUT_JSON_PATH}")
        结果 = _读取本地_json(OUTPUT_JSON_PATH)
        print(f"[阶段1] 已读取 {len(结果)} 条")
    else:
        print("[阶段1] 从飞书多维表格拉取")
        token   = 获取_tenant_access_token()
        records = 拉取所有记录(token)
        结果    = 转换为上传结构(records)
        写入_json(结果, OUTPUT_JSON_PATH)
        print(f"[阶段1] 已写入本地 JSON：{OUTPUT_JSON_PATH}（{len(结果)} 条）")

    # ── 2. 上传秒哒 ────────────────────────────────────────────
    if args.no_upload:
        print()
        print("[阶段2] 按 --no-upload 跳过上传")
        print("=" * 60)
        print(f"[完成] 用户数：{len(结果)}（仅生成 JSON）")
        return

    print()
    print("[阶段2] 上传到秒哒 import-users")
    秒哒_token = 秒哒登录()
    汇总 = 秒哒上传用户(结果, 秒哒_token, dry_run=args.dry_run)

    # ── 3. 汇总 ────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("【上传汇总】")
    print(f"  本地待上传      : {len(结果)}")
    print(f"  接口 total      : {汇总['total']}")
    print(f"  接口 created    : {汇总['created']}")
    print(f"  接口 skipped    : {汇总['skipped']}")
    print(f"  密码未变不更新  : {汇总.get('password_unchanged', 0)}")
    print(f"  接口 errors     : {len(汇总['errors'])}")
    print(f"  请求批次        : {汇总['batches']}")
    if 汇总["errors"]:
        print("  失败明细（前 20 条）：")
        for err in 汇总["errors"][:20]:
            print(f"    - {err}")
    print("=" * 60)
    print(f"[完成] 本地 JSON：{OUTPUT_JSON_PATH}")

    # 有错误时退出码 = 1，方便外层串接 / 计划任务感知
    if 汇总["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
