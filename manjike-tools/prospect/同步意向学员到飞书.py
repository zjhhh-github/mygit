# -*- coding: utf-8 -*-
"""
全量覆盖同步：意向学员 JSON -> 飞书多维表格

执行方式：清空表内记录 + 重新全量导入
注意：只清空“记录内容”，不会删除数据表本身，也不会动表结构、字段、视图。

配置（同目录，勿改脚本内常量）：
  - sync_to_feishu.config.json：飞书凭证、数据源、同步参数、路径（示例见 sync_to_feishu.config.example.json）
  - field_mapping.json：导出字段 → 飞书列映射
  - export_prospect.config.json：导出 / manjike 账号（与 export_prospect_students.py 共用）

数据源 mode（见配置文件「数据源」）：
  - local_json：读取 export_prospect_students.py 生成的主文件（默认 意向学员数据导出.json）
  - manjike：直接调 GET /api/prospect/prospective-students/export（账号读 export_prospect.config.json）

推荐一键流程（同目录）：
  python export_prospect_students.py
  python 同步意向学员到飞书.py
  或：python 同步意向学员到飞书.py --export-first

执行流程：
1) 按配置拉取全量意向学员数据
2) 把每个学员的「来源」数组拆平为多条记录（一个来源对应一条记录）
3) 不再过滤来源/绑定日期为空的记录：
   - 学员无任何来源 → 仍生成 1 行，仅写入「意向学员微信号」，推荐人/绑定日期留空
   - 来源微信号 / 绑定日期为空 → 仍写入，对应字段留空（不写入该 key，飞书单元格视觉为空）
   - 仅当「意向学员微信号」为空时跳过
4) 查询表内全部 records，批量删除所有记录（仅删记录，保留表结构）
5) 把第 2 步处理后的数据分批重新导入

字段映射（导出 -> 飞书）：
  字段映射现已外置到脚本同目录下的 field_mapping.json，可随时编辑。
  - 文件不存在时首次运行会自动写出默认值（与历史版本完全一致）
  - 修改后下次运行立刻生效，无需重启服务
  - 支持任意"学员级 / 来源级"字段，详见 DEFAULT_FIELD_MAPPING 注释

安全机制：
- 默认 sync_to_feishu.config.json 中「同步」.dry_run=true，仅统计
- 真实执行：--execute 或改 dry_run 为 false；require_confirm=true 时终端输入 yes
- 限流自动指数退避重试
- 批大小默认 1000，失败时自动降级 1000 -> 500 -> 200
- 失败记录写入 logs/sync_feishu_failed.json（路径可在配置「路径」中修改）
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

# ─────────────────────────────────────────────────────────────────────────────
# 基础配置
# ─────────────────────────────────────────────────────────────────────────────

# 以下运行时变量由 sync_to_feishu.config.json 注入（见 apply_runtime_config）
DRY_RUN = True
REQUIRE_CONFIRM = True
APP_ID = ""
APP_SECRET = ""
APP_TOKEN = ""
TABLE_ID = ""

# ─────────────────────────────────────────────────────────────────────────────
# 字段映射（已外置）
# ─────────────────────────────────────────────────────────────────────────────
# 历史版本是把字段映射硬编码成 F_STUDENT / F_REFERRER / F_BIND_DATE / F_BIND_STATUS
# 四个常量。现在改为可配置：
#   - 默认值定义在 DEFAULT_FIELD_MAPPING（保持与历史版本完全一致的行为）
#   - 启动时从脚本同目录的 field_mapping.json 加载（不存在则自动写出默认值）
#   - 用户可直接编辑 JSON 文件来：
#       a) 更名飞书表字段
#       b) 增加 / 删除要导出的字段
#       c) 切换字段类型提示（目前仅 "date" 一种特殊处理）
#
# DEFAULT_FIELD_MAPPING 结构：
#   {
#     "student_id_field": "意向学员总微信号",   # 飞书侧学员唯一标识字段名（必填）
#     "fields": [
#       {"feishu": "<飞书字段名>", "source": "student.<导出顶层字段>"},
#       {"feishu": "<飞书字段名>", "source": "source.<导出来源字段>"},
#       {"feishu": "<飞书字段名>", "source": "source.<导出来源字段>", "type_hint": "date"},
#       ...
#     ]
#   }
# 其中 source 路径前缀语义：
#   - "student.X" 取自学员对象顶层（每条最终记录都会写入）
#   - "source.X"  取自学员的「来源」数组中的对象（每个来源生成一行）
# 说明：
#   - 所有空值（None / "" / 解析失败）统一不写入对应 key，飞书显示为空白单元格
#   - type_hint="date" 且飞书字段类型为 5（日期）时：YYYYMMDD 字符串会被转为毫秒时间戳
DEFAULT_FIELD_MAPPING = {
    "student_id_field": "意向学员总微信号",
    "fields": [
        {"feishu": "意向学员总微信号", "source": "student.意向学员微信号"},
        {"feishu": "推荐人总微信号",   "source": "source.来源微信号"},
        {"feishu": "绑定日期",        "source": "source.绑定日期", "type_hint": "date"},
        {"feishu": "绑定状态",        "source": "source.绑定状态"},
    ],
}

BATCH_SIZES: List[int] = [1000, 500, 200]
MAX_RETRY = 5
BASE_BACKOFF = 1.0
FEISHU_BASE = "https://open.feishu.cn/open-apis"

SCRIPT_DIR = Path(__file__).resolve().parent
EXPORT_CONFIG_FILE = SCRIPT_DIR / "export_prospect.config.json"
SYNC_CONFIG_FILE = SCRIPT_DIR / "sync_to_feishu.config.json"
SYNC_CONFIG_EXAMPLE_FILE = SCRIPT_DIR / "sync_to_feishu.config.example.json"
FIELD_MAPPING_FILE = SCRIPT_DIR / "field_mapping.json"
FAILED_FILE = SCRIPT_DIR / "logs" / "sync_feishu_failed.json"
SYNC_BACKUP_DIR = SCRIPT_DIR / "logs"
SYNC_BACKUP_FILENAME_PREFIX = "同步意向学员到飞书"

DEFAULT_SYNC_CONFIG: Dict[str, Any] = {
    "飞书": {
        "app_id": "cli_a96f36ed1538dbcf",
        "app_secret": "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB",
        "app_token": "Zk05bwki2abD8XsBBOccaFsPn8e",
        "table_id": "tblNIWZ1EsDyZ1ug",
        "api_base": "https://open.feishu.cn/open-apis",
    },
    "数据源": {
        "mode": "local_json",
        "local_json_file": "意向学员数据导出.json",
        "manjike_config_file": "export_prospect.config.json",
        "auto_export_before_sync": False,
        "manjike": {
            "timeout_seconds": 600,
        },
    },
    "同步": {
        "dry_run": True,
        "require_confirm": True,
        "batch_sizes": [1000, 500, 200],
        "max_retry": 5,
        "base_backoff_seconds": 1,
    },
    "路径": {
        "field_mapping_file": "field_mapping.json",
        "failed_file": "logs/sync_feishu_failed.json",
        "backup_dir": "logs",
        "backup_filename_prefix": "同步意向学员到飞书",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    """统一日志输出，带时间戳"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _ensure_utf8_stdio() -> None:
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _load_json_file(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_output_dir(output_dir_value: str) -> Path:
    p = Path(output_dir_value)
    return p if p.is_absolute() else (SCRIPT_DIR / p).resolve()


def _resolve_config_path(path_value: str) -> Path:
    p = Path(path_value)
    return p if p.is_absolute() else (SCRIPT_DIR / p).resolve()


def _deep_merge_config(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """合并配置节，跳过以 _ 开头的说明键；嵌套 dict 递归合并。"""
    out = dict(base)
    for key, value in incoming.items():
        if str(key).startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_config(dict(out[key]), value)
        else:
            out[key] = value
    return out


def _config_section(cfg: Dict[str, Any], name: str) -> Dict[str, Any]:
    section = cfg.get(name)
    return dict(section) if isinstance(section, dict) else {}


def apply_runtime_config(cfg: Dict[str, Any]) -> None:
    """将 sync_to_feishu.config.json 注入模块级运行时变量。"""
    global DRY_RUN, REQUIRE_CONFIRM
    global APP_ID, APP_SECRET, APP_TOKEN, TABLE_ID, FEISHU_BASE
    global BATCH_SIZES, MAX_RETRY, BASE_BACKOFF
    global FIELD_MAPPING_FILE, FAILED_FILE
    global SYNC_BACKUP_DIR, SYNC_BACKUP_FILENAME_PREFIX

    feishu = _config_section(cfg, "飞书")
    APP_ID = (os.environ.get("FEISHU_APP_ID") or feishu.get("app_id") or "").strip()
    APP_SECRET = (os.environ.get("FEISHU_APP_SECRET") or feishu.get("app_secret") or "").strip()
    APP_TOKEN = (os.environ.get("FEISHU_APP_TOKEN") or feishu.get("app_token") or "").strip()
    TABLE_ID = (os.environ.get("FEISHU_TABLE_ID") or feishu.get("table_id") or "").strip()
    FEISHU_BASE = (
        os.environ.get("FEISHU_API_BASE") or feishu.get("api_base") or FEISHU_BASE
    ).strip().rstrip("/")
    if not APP_ID or not APP_SECRET or not APP_TOKEN or not TABLE_ID:
        raise RuntimeError(
            "缺少飞书配置：请在 sync_to_feishu.config.json 的「飞书」中填写 "
            "app_id / app_secret / app_token / table_id"
        )

    sync_cfg = _config_section(cfg, "同步")
    DRY_RUN = bool(sync_cfg.get("dry_run", True))
    REQUIRE_CONFIRM = bool(sync_cfg.get("require_confirm", True))
    raw_batches = sync_cfg.get("batch_sizes") or [1000, 500, 200]
    BATCH_SIZES = [int(x) for x in raw_batches if int(x) > 0] or [1000, 500, 200]
    MAX_RETRY = int(sync_cfg.get("max_retry") or 5)
    BASE_BACKOFF = float(sync_cfg.get("base_backoff_seconds") or 1)

    paths = _config_section(cfg, "路径")
    FIELD_MAPPING_FILE = _resolve_config_path(
        str(paths.get("field_mapping_file") or "field_mapping.json")
    )
    FAILED_FILE = _resolve_config_path(
        str(paths.get("failed_file") or "logs/sync_feishu_failed.json")
    )
    SYNC_BACKUP_DIR = _resolve_config_path(str(paths.get("backup_dir") or "logs"))
    SYNC_BACKUP_FILENAME_PREFIX = str(
        paths.get("backup_filename_prefix") or "同步意向学员到飞书"
    )


def ensure_sync_config_file() -> None:
    """配置文件不存在时写出默认模板（与 DEFAULT_SYNC_CONFIG 一致）。"""
    if SYNC_CONFIG_FILE.exists():
        return
    payload = {
        "_说明": (
            "由脚本自动生成。可复制 sync_to_feishu.config.example.json 作为模板；"
            "字段映射见 field_mapping.json。"
        ),
        **DEFAULT_SYNC_CONFIG,
    }
    SYNC_CONFIG_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"已生成默认配置文件：{SYNC_CONFIG_FILE.name}")


def chunk_list(data: list, size: int = 1000) -> Iterable[list]:
    """通用切片函数：把 data 按 size 切成多批"""
    for i in range(0, len(data), size):
        yield data[i : i + size]


def s(v) -> str:
    """安全转字符串"""
    return str(v).strip() if v is not None else ""


def yyyymmdd_to_ms(date_str: str) -> Optional[int]:
    """
    把 YYYYMMDD 字符串转为毫秒时间戳（按东八区当日 00:00:00 计算）。
    解析失败返回 None。
    """
    date_str = s(date_str)
    if len(date_str) != 8 or not date_str.isdigit():
        return None
    try:
        tz = timezone(timedelta(hours=8))
        dt = datetime(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]), tzinfo=tz)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 一、拉取意向学员数据（多数据源）
# ─────────────────────────────────────────────────────────────────────────────

def load_sync_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """加载 sync_to_feishu.config.json；不存在则生成默认文件后加载。"""
    path = config_path or SYNC_CONFIG_FILE
    ensure_sync_config_file()
    merged = _deep_merge_config({}, DEFAULT_SYNC_CONFIG)
    if path.exists():
        try:
            data = _load_json_file(path)
            if not isinstance(data, dict):
                raise ValueError("根节点不是 JSON 对象")
            for section in ("飞书", "数据源", "同步", "路径"):
                if isinstance(data.get(section), dict):
                    merged[section] = _deep_merge_config(
                        dict(merged.get(section) or {}),
                        data[section],
                    )
            log(
                f"已加载配置：{path.name}（数据源 mode={merged['数据源'].get('mode')}）"
            )
        except Exception as e:
            log(f"读取 {path.name} 失败，使用内置默认：{e}")
    else:
        log(f"未找到 {path.name}，使用内置默认配置")
    return merged


def resolve_manjike_credentials(src_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """manjike 账号优先 sync 配置，缺项时从 export_prospect.config.json 的「服务端」补齐。"""
    mk = dict(src_cfg.get("manjike") or {})
    cfg_name = str(src_cfg.get("manjike_config_file") or "export_prospect.config.json")
    export_cfg_path = _resolve_data_path(cfg_name)
    if export_cfg_path.is_file():
        try:
            exp = _load_json_file(export_cfg_path)
            server = exp.get("服务端") or {}
            for key in ("host", "account", "password"):
                if not mk.get(key) and server.get(key):
                    mk[key] = server[key]
        except Exception as e:
            log(f"读取 {export_cfg_path.name} 补齐 manjike 账号失败：{e}")
    mk.setdefault("timeout_seconds", 600)
    return mk


def resolve_local_json_path(src_cfg: Dict[str, Any]) -> Path:
    """本地 JSON 路径：sync 配置 > export_prospect.config 的 json_filename。"""
    explicit = str(src_cfg.get("local_json_file") or "").strip()
    if explicit:
        return _resolve_data_path(explicit)

    export_cfg_path = EXPORT_CONFIG_FILE
    if export_cfg_path.is_file():
        try:
            exp = _load_json_file(export_cfg_path)
            upload = exp.get("导出") or {}
            out_dir = _resolve_output_dir(str(upload.get("output_dir", ".")))
            name = str(upload.get("json_filename", "意向学员数据导出.json"))
            return out_dir / name
        except Exception as e:
            log(f"从 {export_cfg_path.name} 解析导出路径失败：{e}")

    return SCRIPT_DIR / "意向学员数据导出.json"


def run_export_prospect_students() -> None:
    """调用同目录 export_prospect_students.py 生成最新 JSON。"""
    export_script = SCRIPT_DIR / "export_prospect_students.py"
    if not export_script.is_file():
        raise RuntimeError(f"未找到导出脚本：{export_script}")
    log("先执行 export_prospect_students.py 拉取最新意向学员 JSON ...")
    cmd = [sys.executable, str(export_script)]
    proc = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    if proc.returncode != 0:
        raise RuntimeError(f"export_prospect_students.py 退出码 {proc.returncode}")
    log("本地导出完成")


def _resolve_data_path(path_value: str) -> Path:
    p = Path(path_value)
    return p if p.is_absolute() else (SCRIPT_DIR / p).resolve()


def _normalize_manjike_host(host: str) -> str:
    h = (host or "").strip().rstrip("/")
    if h.startswith("http://dev.manjikeabc.com"):
        return "https://dev.manjikeabc.com"
    return h


def load_students_from_local_json(path: Path) -> list:
    """读取 manjike export_prospect_students.py 导出的 JSON 数组。"""
    if not path.is_file():
        raise RuntimeError(f"本地 JSON 不存在：{path}")
    log(f"从本地 JSON 加载：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError(f"JSON 顶层应为数组，实际为 {type(data).__name__}")
    log(f"本地 JSON 学员数：{len(data)}")
    return data


def load_students_from_manjike(manji_cfg: dict) -> list:
    """登录 manjike 并调用 GET /api/prospect/prospective-students/export。"""
    host = _normalize_manjike_host(str(manji_cfg.get("host", "")))
    account = str(manji_cfg.get("account", "")).strip()
    password = str(manji_cfg.get("password", "")).strip()
    timeout = int(manji_cfg.get("timeout_seconds", 600))
    if not host or not account or not password:
        raise RuntimeError("manjike 配置缺少 host / account / password")

    log(f"登录 manjike：{host}")
    login_url = f"{host}/api/auth/login"
    resp = requests.post(
        login_url,
        json={"code": account, "username": account, "password": password},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"manjike 登录失败：HTTP {resp.status_code} -> {resp.text[:300]}")
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"manjike 登录失败：{body}")
    token = (body.get("data") or {}).get("token") or (body.get("data") or {}).get("access_token")
    if not token:
        raise RuntimeError("manjike 登录响应无 token")

    export_url = f"{host}/api/prospect/prospective-students/export"
    log(f"调用 manjike 导出接口：{export_url}")
    resp = requests.get(
        export_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"manjike 导出失败：HTTP {resp.status_code} -> {resp.text[:300]}")
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError("manjike 导出响应不是合法 JSON")
    if not isinstance(data, list):
        raise RuntimeError(f"manjike 导出顶层应为数组，实际为 {type(data).__name__}")
    log(f"manjike 导出学员数：{len(data)}")
    return data


def fetch_students(config: Optional[Dict[str, Any]] = None, export_first: bool = False) -> List[Dict[str, Any]]:
    """按 sync_to_feishu.config.json 选择数据源。"""
    cfg = config or load_sync_config()
    src = cfg.get("数据源") or {}
    if export_first or bool(src.get("auto_export_before_sync")):
        run_export_prospect_students()

    mode = str(src.get("mode") or "local_json").strip().lower()

    if mode == "local_json":
        path = resolve_local_json_path(src)
        return load_students_from_local_json(path)
    if mode == "manjike":
        return load_students_from_manjike(resolve_manjike_credentials(src))
    raise RuntimeError(f"未知数据源 mode={mode!r}，可选：local_json / manjike")


# ─────────────────────────────────────────────────────────────────────────────
# 兼容导入控制台旧接口
# ─────────────────────────────────────────────────────────────────────────────
def export_students() -> List[Dict[str, Any]]:
    """
    兼容导入控制台调用方式：
      students = export_students()
      summary = sync_to_feishu(students)

    控制台不会传入 config，因此这里读取同目录 sync_to_feishu.config.json，
    并按其中「数据源」配置导出学员列表。
    """
    cfg = load_sync_config()
    src_cfg = cfg.get("数据源") or {}
    export_first = bool(src_cfg.get("auto_export_before_sync"))
    students = fetch_students(config=cfg, export_first=export_first)
    return students


# ─────────────────────────────────────────────────────────────────────────────
# 二、飞书 access_token & 通用调用封装
# ─────────────────────────────────────────────────────────────────────────────

def get_feishu_token() -> str:
    """获取飞书 tenant_access_token"""
    url = f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal"
    payload = {"app_id": APP_ID, "app_secret": APP_SECRET}
    resp = requests.post(url, json=payload, timeout=30)
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"获取飞书 token 失败：{body}")
    return body["tenant_access_token"]


def feishu_request(
    method: str,
    url: str,
    token: str,
    *,
    params: Optional[dict] = None,
    payload: Optional[dict] = None,
) -> dict:
    """
    飞书接口统一调用，带限流（429 / 99991400 系列）指数退避重试。
    返回解析后的 JSON body（飞书风格：{code, msg, data}）。
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    backoff = BASE_BACKOFF
    last_err = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = requests.request(
                method, url, headers=headers, params=params, json=payload, timeout=60
            )
            # 限流：HTTP 429 直接退避重试
            if resp.status_code == 429:
                last_err = f"HTTP 429 限流，第 {attempt} 次"
                log(f"  飞书限流，等待 {backoff}s 后重试 ...")
                time.sleep(backoff)
                backoff *= 2
                continue
            body = resp.json()
            code = body.get("code")
            # 业务限流码（飞书常见限流码）
            if code in (99991400, 99991401, 99991429, 1254607):
                last_err = f"业务限流 code={code}"
                log(f"  飞书业务限流 code={code}，等待 {backoff}s 后重试 ...")
                time.sleep(backoff)
                backoff *= 2
                continue
            return body
        except requests.RequestException as e:
            last_err = f"网络异常：{e}"
            log(f"  网络异常：{e}，等待 {backoff}s 后重试 ...")
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"飞书接口重试 {MAX_RETRY} 次仍失败：{last_err}")


# ─────────────────────────────────────────────────────────────────────────────
# 三、查询飞书表字段，确定「绑定日期」字段类型
# ─────────────────────────────────────────────────────────────────────────────

def get_field_types(token: str) -> Dict[str, int]:
    """
    返回字段名 -> 字段类型 的字典。
    飞书字段类型：1=多行文本，5=日期，... 详见官方文档。
    """
    url = f"{FEISHU_BASE}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/fields"
    page_token = None
    fields: Dict[str, int] = {}
    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        body = feishu_request("GET", url, token, params=params)
        if body.get("code") != 0:
            raise RuntimeError(f"读取字段列表失败：{body}")
        data = body.get("data") or {}
        for it in data.get("items") or []:
            fields[it.get("field_name")] = it.get("type")
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return fields


# ─────────────────────────────────────────────────────────────────────────────
# 四、拉取所有记录 + 批量删除
# ─────────────────────────────────────────────────────────────────────────────

def list_all_records(token: str) -> List[str]:
    """分页拉取飞书表全部 record_id（仅查记录，不动表结构）"""
    log("开始查询飞书表内全部记录 ...")
    url = f"{FEISHU_BASE}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/search"
    record_ids: List[str] = []
    page_token = None
    while True:
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        # 使用 search 接口，body 留空即可拉全部
        body = feishu_request("POST", url, token, params=params, payload={})
        if body.get("code") != 0:
            raise RuntimeError(f"拉取记录失败：{body}")
        data = body.get("data") or {}
        items = data.get("items") or []
        record_ids.extend(it["record_id"] for it in items)
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    log(f"飞书当前共 {len(record_ids)} 条记录")
    return record_ids


def batch_delete_records(token: str, record_ids: List[str], batch_size: int = 1000) -> int:
    """
    分批删除飞书记录（只删记录，不会删表）。
    返回成功删除条数。
    """
    if not record_ids:
        log("无需清空（飞书表内当前没有记录）")
        return 0

    url = f"{FEISHU_BASE}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/batch_delete"
    total_batches = (len(record_ids) + batch_size - 1) // batch_size
    success_count = 0
    failed_ids: List[str] = []

    log(f"准备清空 {len(record_ids)} 条记录，按 {batch_size} 条一批，共 {total_batches} 批")

    idx = 0
    batch_idx = 0
    while idx < len(record_ids):
        batch = record_ids[idx : idx + batch_size]
        batch_idx += 1
        log(f"  第 {batch_idx}/{total_batches} 批记录删除中（{len(batch)} 条）...")
        body = feishu_request("POST", url, token, payload={"records": batch})
        code = body.get("code")
        if code == 0:
            success_count += len(batch)
            log(f"    本批删除成功，累计已清空记录数：{success_count}")
            idx += batch_size
        elif batch_size > 200:
            new_size = 500 if batch_size == 1000 else 200
            log(f"    本批失败（code={code} msg={body.get('msg')}），批大小降级 {batch_size} -> {new_size}")
            batch_size = new_size
            total_batches = (len(record_ids) - idx + batch_size - 1) // batch_size + batch_idx - 1
        else:
            log(f"    本批删除失败：{body}")
            failed_ids.extend(batch)
            idx += batch_size

    if failed_ids:
        log(f"清空阶段失败 {len(failed_ids)} 条")
    return success_count


# ─────────────────────────────────────────────────────────────────────────────
# 五、字段映射加载 + 转换数据 + 批量写入
# ─────────────────────────────────────────────────────────────────────────────

def load_field_mapping() -> dict:
    """
    加载字段映射配置：
      1) 优先读取脚本同目录下的 field_mapping.json
      2) 文件不存在 → 写出 DEFAULT_FIELD_MAPPING 作为默认模板，并使用默认值
      3) 文件存在但格式异常 → 退回默认值并打印警告（不阻塞主流程）
    """
    if FIELD_MAPPING_FILE.exists():
        try:
            data = json.loads(FIELD_MAPPING_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("根节点不是 JSON 对象")
            if not isinstance(data.get("fields"), list) or not data["fields"]:
                raise ValueError("缺少非空 fields 列表")
            if not data.get("student_id_field"):
                raise ValueError("缺少 student_id_field")
            log(f"已加载字段映射：{FIELD_MAPPING_FILE.name}（{len(data['fields'])} 个字段）")
            return data
        except Exception as e:
            log(f"读取 {FIELD_MAPPING_FILE.name} 失败，回退默认映射：{e}")
            return DEFAULT_FIELD_MAPPING

    # 首次运行：自动写出默认模板，方便用户后续编辑
    try:
        FIELD_MAPPING_FILE.write_text(
            json.dumps(DEFAULT_FIELD_MAPPING, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(f"已生成默认字段映射模板：{FIELD_MAPPING_FILE}")
    except Exception as e:
        log(f"生成 {FIELD_MAPPING_FILE.name} 失败（不影响运行）：{e}")
    return DEFAULT_FIELD_MAPPING


def transform_records(
    students: list,
    mapping: dict,
    field_types: dict,
) -> List[dict]:
    """
    用 mapping 配置展平意向学员 JSON → 飞书 records 列表。

    展平规则：
      - 一个学员多个来源 → 每个来源各生成 1 条飞书记录（每行都带学员级字段）
      - 学员无任何来源   → 仍生成 1 条记录，仅保留学员级字段
      - 学员唯一标识字段为空 → 跳过整条学员

    字段写入规则：
      - 空值（None / "" / 日期解析失败）统一不写入该 key，飞书显示空白单元格
      - type_hint="date" 且飞书字段类型为 5（日期）：YYYYMMDD → 毫秒时间戳
      - 其余情况：保持字符串

    诊断输出（关键，用于排查"字段为什么没写到飞书"）：
      - 每个映射字段的「非空填充次数 / 总记录数」
      - 前 3 条来源对象样本，看后端到底返回了哪些 key
    """
    student_id_feishu = mapping.get("student_id_field") or "意向学员总微信号"
    fields_def = mapping.get("fields") or []

    # 解析配置：拆成"学员级字段"与"来源级字段"两组
    student_field_defs: list = []  # list[(feishu_name, export_key, type_hint)]
    source_field_defs: list = []
    invalid_defs: list = []
    for fd in fields_def:
        if not isinstance(fd, dict):
            invalid_defs.append(fd)
            continue
        feishu_name = fd.get("feishu")
        source_path = fd.get("source") or ""
        type_hint = fd.get("type_hint")
        if not feishu_name or not source_path:
            invalid_defs.append(fd)
            continue
        if source_path.startswith("student."):
            student_field_defs.append((feishu_name, source_path[len("student."):], type_hint))
        elif source_path.startswith("source."):
            source_field_defs.append((feishu_name, source_path[len("source."):], type_hint))
        else:
            invalid_defs.append(fd)
    if invalid_defs:
        log(f"⚠ 跳过 {len(invalid_defs)} 个无效映射项（source 必须以 'student.' 或 'source.' 开头）")

    # 诊断计数：按"飞书字段名"聚合非空写入次数
    nonempty_count: dict = {
        fd.get("feishu", ""): 0
        for fd in fields_def
        if isinstance(fd, dict) and fd.get("feishu")
    }

    def _convert(feishu_name: str, raw_value, type_hint):
        """根据 type_hint + 飞书字段类型转换；返回 (是否写入, 实际值)。"""
        v = s(raw_value)
        if not v:
            return False, None
        if type_hint == "date" and field_types.get(feishu_name) == 5:
            ms = yyyymmdd_to_ms(v)
            if ms is None:
                return False, None
            return True, ms
        return True, v

    # 抽样：前 3 条来源对象（原始 dict）便于诊断后端实际返回结构
    sample_sources: list = []
    out: List[dict] = []
    skipped_no_id = 0

    for stu in students or []:
        # 收集学员级字段
        student_fields: dict = {}
        for feishu_name, export_key, type_hint in student_field_defs:
            ok, val = _convert(feishu_name, stu.get(export_key), type_hint)
            if ok:
                student_fields[feishu_name] = val
                nonempty_count[feishu_name] = nonempty_count.get(feishu_name, 0) + 1

        # 学员唯一标识缺失 → 跳过整条
        if not student_fields.get(student_id_feishu):
            skipped_no_id += 1
            continue

        sources = stu.get("来源") or []
        if not sources:
            out.append({"fields": dict(student_fields)})
            continue

        for src in sources:
            if isinstance(src, dict) and len(sample_sources) < 3:
                sample_sources.append(src)
            row = dict(student_fields)
            for feishu_name, export_key, type_hint in source_field_defs:
                ok, val = _convert(feishu_name, src.get(export_key), type_hint)
                if ok:
                    row[feishu_name] = val
                    nonempty_count[feishu_name] = nonempty_count.get(feishu_name, 0) + 1
            out.append({"fields": row})

    # ── 诊断日志 ────────────────────────────────────────────────
    log(f"展平后待写入飞书 {len(out)} 条；因「{student_id_feishu}」为空跳过 {skipped_no_id} 条学员")
    log("各字段非空填充次数（按飞书字段名）：")
    for fd in fields_def:
        if not isinstance(fd, dict):
            continue
        name = fd.get("feishu")
        if not name:
            continue
        count = nonempty_count.get(name, 0)
        ratio = (count / len(out) * 100) if out else 0.0
        log(f"  - {name:<20} {count:>6} / {len(out):<6} ({ratio:5.1f}%)  source={fd.get('source')}")

    if sample_sources:
        log("前 3 条来源对象样本（用于排查后端是否真的返回该字段）：")
        for i, sample in enumerate(sample_sources, 1):
            try:
                log(f"  [{i}] keys = {list(sample.keys())}")
                preview = {k: (str(v)[:30] if v is not None else None) for k, v in sample.items()}
                log(f"      values = {preview}")
            except Exception:
                pass

    return out


def backup_sync_payload(
    students: list,
    new_records: List[dict],
    mapping: dict,
    dry_run: bool,
) -> Optional[Path]:
    """
    把本次同步的关键信息备份成一个 JSON 文件，便于事后排查。

    文件位置：脚本同目录下的 backups/ 子目录
    文件名  ：同步意向学员到飞书_YYYYMMDD_HHMMSS.json

    备份内容：
      - meta      ：执行时间、是否 DRY_RUN、学员数 / 展平后记录数、所用字段映射
      - students  ：原始意向学员 JSON（保留来源细节，便于回溯）
      - records   ：本次准备写入飞书的展平记录（与实际写入飞书的数据一致）

    备份失败不会抛异常，只打印一条警告日志，避免影响主同步流程。
    """
    try:
        SYNC_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = SYNC_BACKUP_DIR / f"{SYNC_BACKUP_FILENAME_PREFIX}_{ts}.json"

        payload = {
            "meta": {
                "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "dry_run": bool(dry_run),
                "students_count": len(students or []),
                "records_count": len(new_records or []),
                "field_mapping": mapping,
            },
            "students": students or [],
            "records": new_records or [],
        }

        backup_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(f"本次同步数据已备份：{backup_path}")
        return backup_path
    except Exception as exc:
        log(f"⚠ 备份本次同步数据失败（不影响主流程）：{exc}")
        return None


def batch_insert_records(token: str, records: List[dict], batch_size: int = 1000) -> Tuple[int, List[dict]]:
    """
    分批写入飞书，返回 (成功条数, 失败记录列表)。
    单批失败时自动降级 1000 -> 500 -> 200。
    """
    if not records:
        return 0, []

    url = f"{FEISHU_BASE}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/batch_create"
    total_batches = (len(records) + batch_size - 1) // batch_size
    success_count = 0
    failed_records: List[dict] = []

    log(f"准备导入 {len(records)} 条记录，按 {batch_size} 条一批，共 {total_batches} 批")

    idx = 0
    batch_idx = 0
    while idx < len(records):
        batch = records[idx : idx + batch_size]
        batch_idx += 1
        log(f"  第 {batch_idx}/{total_batches} 批记录导入中（{len(batch)} 条）...")
        body = feishu_request("POST", url, token, payload={"records": batch})
        code = body.get("code")
        if code == 0:
            success_count += len(batch)
            log(f"    本批导入成功，累计已导入记录数：{success_count}")
            idx += batch_size
        elif batch_size > 200:
            new_size = 500 if batch_size == 1000 else 200
            log(f"    本批失败（code={code} msg={body.get('msg')}），批大小降级 {batch_size} -> {new_size}")
            batch_size = new_size
            total_batches = (len(records) - idx + batch_size - 1) // batch_size + batch_idx - 1
        else:
            log(f"    本批写入失败：code={code} msg={body.get('msg')}")
            failed_records.extend(batch)
            idx += batch_size

    return success_count, failed_records


# ─────────────────────────────────────────────────────────────────────────────
# 六、主流程
# ─────────────────────────────────────────────────────────────────────────────

def confirm_real_run() -> bool:
    """真实执行前的二次终端确认（可由配置「同步」.require_confirm 关闭）"""
    if not REQUIRE_CONFIRM:
        return True
    print("\n" + "=" * 60)
    print("⚠️  将清空当前表内所有记录并重新导入。")
    print("    注意：只删除记录内容，表结构、字段、视图都不会被删除。")
    print(f"    Base : {APP_TOKEN}")
    print(f"    Table: {TABLE_ID}")
    print("    是否继续？")
    print("=" * 60)
    ans = input("请输入 yes 确认执行，其他任意输入将取消：").strip().lower()
    return ans == "yes"


def sync_to_feishu(students: list) -> dict:
    """
    Step3：把意向学员数据同步到飞书。
    返回执行汇总，便于 GUI 控制台复用。
    """
    feishu_token = get_feishu_token()
    field_types = get_field_types(feishu_token)

    # 加载字段映射（外置 JSON，可热改）
    mapping = load_field_mapping()
    required_fields = [
        fd.get("feishu") for fd in (mapping.get("fields") or [])
        if isinstance(fd, dict) and fd.get("feishu")
    ]
    missing = [f for f in required_fields if f not in field_types]
    if missing:
        raise RuntimeError(
            f"飞书表缺少必要字段：{missing}；"
            f"请检查 {FIELD_MAPPING_FILE.name} 中 'feishu' 与飞书表实际字段名是否一致。"
            f" 已查到字段：{list(field_types.keys())}"
        )

    # 打印各映射字段的飞书侧字段类型（5=日期, 1=文本, 3=单选, 4=多选）
    for fd in mapping.get("fields") or []:
        if not isinstance(fd, dict):
            continue
        name = fd.get("feishu")
        if name and name in field_types:
            log(f"飞书字段「{name}」类型 = {field_types[name]}（参考：5=日期, 1=文本, 3=单选）")

    new_records = transform_records(students, mapping, field_types)

    # 每次同步先把「本次准备写入飞书的全量记录」落盘备份
    # 放在 transform_records 之后、清空/写入飞书之前，保证：
    #   1) DRY_RUN 也能拿到备份（只统计不写飞书时也保留现场）
    #   2) 即使后续清空/写入失败，备份文件已经落地
    backup_sync_payload(
        students=students,
        new_records=new_records,
        mapping=mapping,
        dry_run=DRY_RUN,
    )

    existing_ids = list_all_records(feishu_token)

    if DRY_RUN:
        log("──────── DRY_RUN 模式，仅统计 ────────")
        log(f"将清空飞书表内记录：{len(existing_ids)} 条（仅删记录，表结构保留）")
        log(f"将导入新记录数量  ：{len(new_records)} 条")
        log("如需真实执行：sync_to_feishu.config.json 中「同步」.dry_run=false，或加 --execute")
        return {
            "deleted": 0,
            "exported": len(students),
            "prepared": len(new_records),
            "inserted": 0,
            "failed": 0,
        }

    deleted = batch_delete_records(feishu_token, existing_ids, batch_size=BATCH_SIZES[0])
    log(f"清空阶段完成，已清空记录数：{deleted}")

    inserted, failed = batch_insert_records(feishu_token, new_records, batch_size=BATCH_SIZES[0])
    if failed:
        FAILED_FILE.write_text(
            json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log(f"失败 {len(failed)} 条已写入：{FAILED_FILE}")

    if not failed:
        log("飞书同步成功")

    return {
        "deleted": deleted,
        "exported": len(students),
        "prepared": len(new_records),
        "inserted": inserted,
        "failed": len(failed),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="意向学员 JSON → 飞书多维表格（全量覆盖）")
    parser.add_argument("--config", type=str, default=None, help="sync_to_feishu.config.json 路径")
    parser.add_argument("--export-first", action="store_true",
                        help="同步前先运行 export_prospect_students.py")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写飞书")
    parser.add_argument("--execute", action="store_true", help="真实清空并写入飞书（覆盖 DRY_RUN）")
    return parser.parse_args()


def main() -> None:
    global DRY_RUN
    _ensure_utf8_stdio()
    args = _parse_args()

    config_path = Path(args.config) if args.config else None
    if config_path:
        config_path = config_path.resolve()
    config = load_sync_config(config_path)
    try:
        apply_runtime_config(config)
    except RuntimeError as e:
        log(str(e))
        sys.exit(1)

    if args.execute:
        DRY_RUN = False
    elif args.dry_run:
        DRY_RUN = True

    log(f"DRY_RUN = {DRY_RUN}（配置见 {SYNC_CONFIG_FILE.name}）")
    log(f"脚本目录：{SCRIPT_DIR}")

    try:
        students = fetch_students(config=config, export_first=args.export_first)
    except RuntimeError as e:
        log(str(e))
        sys.exit(1)

    if not DRY_RUN and not confirm_real_run():
        log("已取消执行")
        return

    # Step3：同步到飞书
    try:
        summary = sync_to_feishu(students)
    except RuntimeError as e:
        log(str(e))
        sys.exit(1)

    # 汇总
    log("════════ 执行汇总 ════════")
    log(f"已清空记录数        ：{summary['deleted']}")
    log(f"导出接口学员总数    ：{summary['exported']}")
    log(f"展平后待导入记录数  ：{summary['prepared']}")
    log(f"已导入记录数        ：{summary['inserted']}")
    log(f"导入失败记录数      ：{summary['failed']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CANCELLED]")
        sys.exit(130)
