#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书多维表格读取工具（外部独立脚本）。

通过飞书「自建应用」凭据（app_id / app_secret），调用开放平台 OpenAPI，
读取指定「多维表格 (Bitable)」中指定「数据表 (Table)」的全部记录，
分页拉取后保存到本地 JSON 文件，便于后续与 manjike 后端做同步 / 比对。

特点：
- 单文件，仅依赖 Python 标准库（urllib + json）。
- 无需 pip install，可直接拷贝到任何能访问飞书 open.feishu.cn 的机器跑。
- 支持配置文件（feishu-read-bitable.config.json）+ CLI 参数覆盖。
- 自动分页拉取，直到 `has_more = false`。
- 默认把结果写入 ./feishu-bitable.json，方便后续脚本消费。

前置条件（重要）：
1. 已在飞书开放平台创建「自建应用」，并拿到 app_id / app_secret。
2. 已在飞书多维表格中，把这个应用添加为「文档协作者」（可编辑或可阅读权限）。
   否则会报 91402 / 99991672 之类的权限错误。
3. 应用已开启权限：bitable:app（多维表格基础权限）。

用法示例：
    # 1) 使用同目录配置文件
    python feishu-read-bitable.py --config feishu-read-bitable.config.json

    # 2) 全部用 CLI 参数（不需要配置文件）
    python feishu-read-bitable.py \
        --app-id cli_xxx --app-secret xxx \
        --app-token Zk05bwki2abD8XsBBOccaFsPn8e \
        --table-id tblKa8wryhV4d7F4 \
        --output feishu-bitable.json

    # 3) 只打印前几条到控制台，不写文件
    python feishu-read-bitable.py --config feishu-read-bitable.config.json \
        --preview 3 --no-output

CLI 参数完整列表请运行：
    python feishu-read-bitable.py --help

安全提示：
- 配置文件中包含 app_secret，请勿提交到公共仓库（建议加入 .gitignore）。
"""

import argparse
import json
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# 脚本所在目录：用于把相对路径（配置文件 / 输出文件）锚定到脚本目录，
# 而不是当前工作目录（cwd）。这样无论从哪个目录运行、或双击运行，
# 都能稳定找到同目录的 feishu-read-bitable.config.json 并把结果写在同目录。
SCRIPT_DIR = Path(__file__).resolve().parent


def resolve_relative_to_script(path_str: Optional[str]) -> Optional[Path]:
    """把路径字符串解析为 Path：绝对路径原样返回；相对路径锚定到脚本所在目录。"""
    if not path_str:
        return None
    p = Path(path_str)
    return p if p.is_absolute() else (SCRIPT_DIR / p)


# ============================================================
# 飞书开放平台基础地址 & 接口路径
# ============================================================
# 国内版统一域名（如使用海外版 lark，需要替换为 open.larksuite.com）
FEISHU_HOST = "https://open.feishu.cn"

# 获取 tenant_access_token（应用维度访问令牌）
URL_TENANT_TOKEN = "/open-apis/auth/v3/tenant_access_token/internal"

# 多维表格 - 列出记录
# GET /open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records
URL_LIST_RECORDS_TPL = "/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"


# ============================================================
# HTTP 工具：仅基于标准库，统一处理 JSON 请求与错误
# ============================================================

def http_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """发送 JSON 请求，返回解析后的 dict。

    - method: GET / POST
    - url: 完整 URL（包含 query string）
    - headers: 额外请求头（Content-Type / Authorization 等）
    - body: dict 形式的请求体，自动 json.dumps
    - 任何 HTTPError / URLError 都会以 RuntimeError 抛出，方便上层捕获
    """
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
    except HTTPError as e:
        # 飞书错误同样是 JSON，尽量把错误体打印出来便于排查
        err_body = ""
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            pass
        raise RuntimeError(
            f"HTTP {e.code} 调用失败：{url}\n响应体：{err_body or e.reason}"
        ) from e
    except URLError as e:
        raise RuntimeError(f"网络错误：{url}\n原因：{e.reason}") from e

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"响应不是合法 JSON：{raw[:500]}") from e


# ============================================================
# 飞书业务接口封装
# ============================================================

def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    """获取自建应用的 tenant_access_token。

    文档：https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal
    """
    url = FEISHU_HOST + URL_TENANT_TOKEN
    resp = http_json("POST", url, body={"app_id": app_id, "app_secret": app_secret})

    code = resp.get("code")
    if code != 0:
        raise RuntimeError(
            f"获取 tenant_access_token 失败：code={code}, msg={resp.get('msg')}, raw={resp}"
        )
    token = resp.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"响应缺少 tenant_access_token 字段：{resp}")
    return token


def list_bitable_records(
    token: str,
    app_token: str,
    table_id: str,
    page_size: int = 100,
    max_retry: int = 3,
    field_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """分页拉取一张数据表的全部记录。

    文档：https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list

    返回值：原样保留飞书返回的每条记录（含 record_id / fields 等字段）。
    """
    base_url = FEISHU_HOST + URL_LIST_RECORDS_TPL.format(
        app_token=app_token, table_id=table_id
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
        if field_names:
            # 飞书要求 field_names 是 JSON 字符串形式的数组，例如：
            # field_names=["编号","密码"]
            params["field_names"] = json.dumps(field_names, ensure_ascii=False)
        url = base_url + "?" + urlencode(params)

        # 简单重试：网络抖动 / 偶发 5xx 时再试几次
        last_err: Optional[Exception] = None
        for attempt in range(1, max_retry + 1):
            try:
                resp = http_json("GET", url, headers=headers)
                last_err = None
                break
            except Exception as e:
                last_err = e
                if attempt < max_retry:
                    time.sleep(1.0 * attempt)
        if last_err is not None:
            raise last_err  # type: ignore[misc]

        code = resp.get("code")
        if code != 0:
            raise RuntimeError(
                f"拉取记录失败：code={code}, msg={resp.get('msg')}, raw={resp}"
            )

        data = resp.get("data") or {}
        items = data.get("items") or []
        all_records.extend(items)

        has_more = bool(data.get("has_more"))
        page_token = data.get("page_token")
        print(
            f"  - 第 {page_index} 页：本页 {len(items)} 条，累计 {len(all_records)} 条，"
            f"has_more={has_more}"
        )
        if not has_more or not page_token:
            break

    return all_records


# ============================================================
# 配置加载 & CLI 解析
# ============================================================

def load_config(config_path: Optional[Path]) -> Dict[str, Any]:
    """读取配置文件。文件不存在或未指定时返回空 dict。"""
    if not config_path:
        return {}
    if not config_path.exists():
        print(f"[warn] 配置文件不存在，忽略：{config_path}")
        return {}
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_args_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="读取飞书多维表格记录并保存到本地 JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--config", type=str, default="feishu-read-bitable.config.json",
                   help="配置文件路径（默认：同目录 feishu-read-bitable.config.json）")
    p.add_argument("--app-id", type=str, help="飞书自建应用 App ID")
    p.add_argument("--app-secret", type=str, help="飞书自建应用 App Secret")
    p.add_argument("--app-token", type=str, help="多维表格 app_token（即多维表格 ID）")
    p.add_argument("--table-id", type=str, help="数据表 table_id")
    p.add_argument("--page-size", type=int, help="分页大小，默认 100（飞书最大 500）")
    p.add_argument("--output", type=str, help="结果输出文件，默认 feishu-bitable.json")
    p.add_argument("--no-output", action="store_true", help="不写文件，只在控制台预览")
    p.add_argument("--preview", type=int, default=0,
                   help="额外在控制台打印前 N 条记录预览，默认 0 不打印")
    p.add_argument("--fields", type=str,
                   help="只读取这些列，用英文逗号分隔，例如：编号,孩子中文全名,密码")
    p.add_argument("--no-flatten", action="store_true",
                   help="不拍平多行文本字段（默认会把 [{text,type}] 数组拼成字符串）")
    return p


# ============================================================
# 字段拍平：把飞书多行文本字段 [{text,type}] 转成纯字符串
# ============================================================

def flatten_field_value(value: Any) -> Any:
    """把飞书「多行文本」类型的数组结构拍平成字符串。

    - 多行文本字段：`[{"text": "xxx", "type": "text"}, ...]`  ->  "xxx..."
    - 其他类型（字符串、数字、附件数组、单选等）保持原样返回。
    """
    if isinstance(value, list) and value and all(
        isinstance(item, dict) and "text" in item and "type" in item
        for item in value
    ):
        return "".join(str(item.get("text", "")) for item in value)
    return value


def flatten_record_fields(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """对每条记录的 fields 做一次拍平，原始 record 其他字段保留。"""
    out: List[Dict[str, Any]] = []
    for rec in records:
        new_rec = dict(rec)
        fields = rec.get("fields") or {}
        new_rec["fields"] = {k: flatten_field_value(v) for k, v in fields.items()}
        out.append(new_rec)
    return out


class _SafeDict(dict):
    """字符串模板用的字典：取不到的 key 返回空字符串，避免 KeyError。"""

    def __missing__(self, key: str) -> str:  # type: ignore[override]
        return ""


def render_template(template: str, fields: Dict[str, Any]) -> str:
    """渲染 `{字段名}` 风格的字符串模板。

    - 占位符里的字段名 = 飞书原列名（拍平后的字符串）。
    - 缺失字段会被替换为空字符串，不会报错。
    - 字段值非字符串时会调用 str() 兜底。
    """
    safe: Dict[str, Any] = _SafeDict()
    for k, v in fields.items():
        safe[k] = "" if v is None else (v if isinstance(v, str) else str(v))
    return template.format_map(safe)


def map_records_to_shape(
    records: List[Dict[str, Any]],
    output_mapping: Dict[str, str],
    output_constants: Optional[Dict[str, Any]] = None,
    output_templates: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """按 output_mapping / output_templates / output_constants 把记录转换为扁平结构。

    - output_mapping  : 目标字段名 -> 源 fields 字段名（一对一改名）
        例如：{"账号": "编号", "微信号": "合伙宝妈总微信号"}
    - output_templates: 目标字段名 -> 字符串模板，模板里用 {源字段名} 引用原列
        例如：{"内部备注": "¿¿¿{编号}-{孩子中文全名}"}
    - output_constants: 目标字段名 -> 固定值（每条都一样）
        例如：{"角色": "普通用户"}

    所有源字段值都会先经过 flatten_field_value，把飞书多行文本拍平成字符串。
    生成顺序：mapping -> templates -> constants（后写入的会覆盖前面的同名字段）。
    """
    constants = output_constants or {}
    templates = output_templates or {}
    result: List[Dict[str, Any]] = []
    for rec in records:
        raw_fields = rec.get("fields") or {}
        # 先把所有源字段都拍平一遍，模板和映射统一从这里取
        flat_fields: Dict[str, Any] = {
            k: flatten_field_value(v) for k, v in raw_fields.items()
        }

        item: Dict[str, Any] = {}
        for target_key, source_key in output_mapping.items():
            item[target_key] = flat_fields.get(source_key)
        for target_key, tpl in templates.items():
            item[target_key] = render_template(tpl, flat_fields)
        for k, v in constants.items():
            item[k] = v
        result.append(item)
    return result


def resolve_config(cli: argparse.Namespace) -> Dict[str, Any]:
    """合并配置文件与 CLI 参数，CLI 优先级更高。"""
    # 相对路径锚定到脚本目录：无论从哪个 cwd 运行，都能找到同目录配置文件
    config_path = resolve_relative_to_script(cli.config)
    file_cfg = load_config(config_path)

    # fields 支持配置文件里写数组，也支持 CLI 用逗号分隔字符串
    fields_cli: Optional[List[str]] = None
    if cli.fields:
        fields_cli = [s.strip() for s in cli.fields.split(",") if s.strip()]
    fields_cfg = file_cfg.get("fields")
    fields_final: Optional[List[str]] = fields_cli or (
        list(fields_cfg) if isinstance(fields_cfg, list) and fields_cfg else None
    )

    merged: Dict[str, Any] = {
        "app_id": cli.app_id or file_cfg.get("app_id"),
        "app_secret": cli.app_secret or file_cfg.get("app_secret"),
        "app_token": cli.app_token or file_cfg.get("app_token"),
        "table_id": cli.table_id or file_cfg.get("table_id"),
        "page_size": cli.page_size or file_cfg.get("page_size") or 100,
        "output_file": cli.output or file_cfg.get("output_file") or "feishu-bitable.json",
        "no_output": cli.no_output,
        "preview": cli.preview,
        "fields": fields_final,
        "flatten": (not cli.no_flatten) and bool(file_cfg.get("flatten", True)),
        "output_mapping": file_cfg.get("output_mapping") or {},
        "output_constants": file_cfg.get("output_constants") or {},
        "output_templates": file_cfg.get("output_templates") or {},
    }

    missing = [k for k in ("app_id", "app_secret", "app_token", "table_id")
               if not merged.get(k)]
    if missing:
        raise SystemExit(
            "缺少必要参数：" + ", ".join(missing)
            + "\n请在配置文件中提供，或通过 CLI 参数传入。"
        )

    return merged


# ============================================================
# 主流程
# ============================================================

def main() -> int:
    parser = build_args_parser()
    cli = parser.parse_args()
    cfg = resolve_config(cli)

    app_id: str = cfg["app_id"]
    app_secret: str = cfg["app_secret"]
    app_token: str = cfg["app_token"]
    table_id: str = cfg["table_id"]
    page_size: int = int(cfg["page_size"])
    output_file: str = cfg["output_file"]

    print("=" * 60)
    print("飞书多维表格读取工具")
    print(f"  app_id     : {app_id}")
    print(f"  app_token  : {app_token}")
    print(f"  table_id   : {table_id}")
    print(f"  page_size  : {page_size}")
    print(f"  fields     : {cfg['fields'] if cfg['fields'] else '(全部列)'}")
    print(f"  flatten    : {cfg['flatten']}")
    print(f"  output     : {'(不输出文件)' if cfg['no_output'] else output_file}")
    print("=" * 60)

    print("[1/2] 获取 tenant_access_token ...")
    token = get_tenant_access_token(app_id, app_secret)
    print(f"      ok, token=***{token[-6:]}")

    print("[2/2] 分页拉取记录 ...")
    records = list_bitable_records(
        token, app_token, table_id,
        page_size=page_size,
        field_names=cfg["fields"],
    )
    print(f"      总计拉取 {len(records)} 条记录")

    if cfg["flatten"]:
        records = flatten_record_fields(records)

    # 若配置了 output_mapping，则把数据转成扁平的目标结构
    output_mapping: Dict[str, str] = cfg["output_mapping"]
    output_constants: Dict[str, Any] = cfg["output_constants"]
    output_templates: Dict[str, str] = cfg["output_templates"]
    shaped: Optional[List[Dict[str, Any]]] = None
    if output_mapping or output_templates or output_constants:
        shaped = map_records_to_shape(
            records,
            output_mapping,
            output_constants=output_constants,
            output_templates=output_templates,
        )
        print(f"      已按 output_mapping/templates/constants 转换，共 {len(shaped)} 条")

    if cfg["preview"]:
        preview_data: List[Any] = shaped if shaped is not None else records
        n = min(cfg["preview"], len(preview_data))
        print(f"\n--- 前 {n} 条预览 ---")
        if shaped is not None:
            print(json.dumps(preview_data[:n], ensure_ascii=False, indent=2))
        else:
            for i, rec in enumerate(preview_data[:n], start=1):
                print(f"[{i}] record_id={rec.get('record_id')}")
                print(json.dumps(rec.get("fields"), ensure_ascii=False, indent=2))

    if not cfg["no_output"]:
        # 输出路径同样锚定到脚本目录：相对路径不再受运行时 cwd 影响
        out_path = resolve_relative_to_script(output_file) or Path(output_file)
        # 有 output_mapping 时直接输出扁平数组；否则输出包含元信息的对象
        if shaped is not None:
            payload: Any = shaped
        else:
            payload = {
                "app_token": app_token,
                "table_id": table_id,
                "total": len(records),
                "records": records,
            }
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n已写入：{out_path.resolve()}")

        # 额外再保存一份带时间戳的历史副本到 users_logs/ 子目录，
        # 文件名形如：users_logs/feishu-users_20260528_111005.json
        # 原件留在当前目录方便覆盖使用，备份按时间戳长期累积，方便回溯。
        backup_dir = out_path.parent / "users_logs"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ts_path = backup_dir / f"{out_path.stem}_{ts}{out_path.suffix}"
        with ts_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"已备份：{ts_path.resolve()}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:  # 顶层兜底，打印精简错误
        print(f"[error] {e}")
        sys.exit(1)
