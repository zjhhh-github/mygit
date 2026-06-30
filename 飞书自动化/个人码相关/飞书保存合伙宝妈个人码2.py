# -*- coding: utf-8 -*-
"""
飞书多维表格 → 下载二维码 + 生成台账 txt
==============================================

业务功能：
1. 通过 internal 应用方式自动获取 tenant_access_token
2. 分页拉取多维表格全部记录（page_size=100）
3. 针对每条记录处理以下字段：
   - 个人码2          → 附件，下载到本地
   - 编号             → 文本
   - 孩子中文全名     → 文本（同时转拼音）
   - 手机号           → 文本
4. 图片保存到 C:\\Users\\LENOVO\\Desktop\\保存的二维码\\
   文件名：编号-孩子中文全名.<真实后缀>，已存在则跳过
5. 追加写入 C:\\Users\\LENOVO\\Desktop\\读取后的内容.txt
   每行格式（逗号分隔）：
       编号-孩子中文全名,图片路径,拼音,手机号

运行方式（PowerShell，从脚本所在目录）：
    cd D:\\桌面文件\\新建文件夹\\飞书自动化
    python .\\飞书保存合伙宝妈个人码2.py

依赖：
    pip install requests pypinyin
"""

import json
import re
import time
from pathlib import Path
from typing import Any

import requests

# 拼音库（必需）
try:
    from pypinyin import lazy_pinyin, Style
except ImportError as e:
    raise SystemExit(
        "缺少依赖 pypinyin，请先执行：\n"
        "  d:\\桌面文件\\新建文件夹\\.venv\\Scripts\\pip.exe install pypinyin"
    ) from e


# ────────────────────────── 基础配置（按需修改） ──────────────────────────
APP_ID = "cli_a96f36ed1538dbcf"
APP_SECRET = "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB"

APP_TOKEN = "Zk05bwki2abD8XsBBOccaFsPn8e"
TABLE_ID = "tblKa8wryhV4d7F4"
# 仅读取该视图下的数据（视图里筛选/排序后的结果）。
# 如需读取整表，置为空字符串 "" 即可。
VIEW_ID = "vewTVvKOzr"

# 表里实际字段名（如有改名，改这里即可，不动业务逻辑）
FIELD_IMAGE = "个人码2"          # 附件字段
FIELD_NUMBER = "编号"            # 文本
FIELD_CHILD_NAME = "孩子中文全名"  # 文本
FIELD_PHONE = "手机号"           # 文本

# 是否真的下载图片（False 时仅生成 txt，图片路径仍按规则推算）
DOWNLOAD_IMAGES = True

# ── 图片命名规则（可自定义） ─────────────────────────────────────────────
# 模板中可用的占位符：{编号} {孩子中文全名} {手机号} {拼音}
# 例如：
#   "{编号}-{孩子中文全名}"            → 001-张三.png
#   "{手机号}-{编号}-{孩子中文全名}"   → 13800138000-001-张三.png
#   "{孩子中文全名}_{拼音}_{手机号}"   → 张三_zhangsan_13800138000.png
IMAGE_NAME_TEMPLATE = "¿¿¿{编号}-{孩子中文全名}"

# 扩展名策略："force_png" 强制 .png；"auto" 按真实 Content-Type 推断
IMAGE_EXT_STRATEGY = "force_png"

# 落盘冲突策略："overwrite" 覆盖；"skip" 跳过；"suffix" 自动加 (1)(2)
IMAGE_CONFLICT_STRATEGY = "overwrite"

# txt 写入模式：True=追加，False=覆盖（每次运行先清空再写，默认）
APPEND_TXT = False

# 输出位置（绝对路径，避免依赖当前工作目录）
IMAGE_DIR = Path(r"C:\Users\LENOVO\Desktop\保存的二维码")
OUT_TXT_PATH = Path(r"C:\Users\LENOVO\Desktop\读取后的内容.txt")

# 飞书开放平台域名
FEISHU_HOST = "https://open.feishu.cn"

# 单次请求超时（秒）
REQUEST_TIMEOUT = 30

# Content-Type → 文件后缀，自动识别图片真实后缀（加分项）
_CONTENT_TYPE_EXT = {
    "image/png":  ".png",
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",
    "image/gif":  ".gif",
    "image/webp": ".webp",
    "image/bmp":  ".bmp",
    "image/svg+xml": ".svg",
}

# 文件名非法字符（Windows 不允许）
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


# ────────────────────────── 通用工具 ──────────────────────────
def _request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    json_body: dict | None = None,
    stream: bool = False,
) -> requests.Response:
    """
    统一封装的请求函数，失败自动重试一次。

    - 网络异常 / 5xx / 429 时会触发重试
    - 其他 4xx 直接返回，由调用方根据业务判断
    """
    last_exc: Exception | None = None
    for attempt in range(2):  # 最多两次（首次 + 重试一次）
        try:
            resp = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                stream=stream,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code >= 500 or resp.status_code == 429:
                last_exc = RuntimeError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
                if attempt == 0:
                    print(f"  [重试] {method} {url} → {resp.status_code}")
                    time.sleep(1)
                    continue
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt == 0:
                print(f"  [重试] {method} {url} → {e}")
                time.sleep(1)
                continue
    raise RuntimeError(f"请求最终失败: {url}, 原因: {last_exc}")


def _extract_text(value: Any) -> str:
    """
    把飞书字段值统一转换为字符串。

    飞书 bitable 文本字段常见返回形态：
        1. 纯字符串："001"
        2. 富文本结构：[{"type": "text", "text": "001"}, ...]
        3. 数字 / None
    本函数把以上情况统一抽成普通字符串。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        # bitable 中的数字字段，去掉 float 末尾的 .0
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                # 富文本：取 text；超链接：取 text
                parts.append(str(item.get("text", "")).strip())
            elif item is not None:
                parts.append(str(item).strip())
        return "".join(parts).strip()
    if isinstance(value, dict):
        return str(value.get("text", "")).strip()
    return str(value).strip()


def _sanitize_filename(name: str) -> str:
    """去除文件名非法字符，避免写盘失败"""
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("_", name)
    # 去掉首尾空白和点（Windows 不允许文件名以点结尾）
    return cleaned.strip().strip(".")


def _to_pinyin(chinese: str) -> str:
    """
    将中文转为全拼小写无声调，例如：
        张三  → zhangsan
        李小明 → lixiaoming
        王Anna → wangAnna（非中文字符原样保留）
    """
    if not chinese:
        return ""
    parts = lazy_pinyin(chinese, style=Style.NORMAL)
    return "".join(parts).lower()


# ────────────────────────── 1. 鉴权 ──────────────────────────
def get_tenant_access_token() -> str:
    """
    获取 tenant_access_token（internal 应用方式）。
    文档：POST /open-apis/auth/v3/tenant_access_token/internal
    """
    url = f"{FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal"
    body = {"app_id": APP_ID, "app_secret": APP_SECRET}
    print("[鉴权] 正在获取 tenant_access_token ...")
    resp = _request_with_retry("POST", url, json_body=body)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 token 失败: {data}")
    token = data["tenant_access_token"]
    print(f"[鉴权] 获取成功，有效期 {data.get('expire')} 秒")
    return token


# ────────────────────────── 2. 分页拉取记录 ──────────────────────────
def get_all_records(token: str) -> list[dict]:
    """
    分页拉取多维表格全部记录。
    文档：GET /open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records
    """
    url = (
        f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{APP_TOKEN}"
        f"/tables/{TABLE_ID}/records"
    )
    headers = {"Authorization": f"Bearer {token}"}

    all_records: list[dict] = []
    page_token: str | None = None
    page_index = 0

    while True:
        page_index += 1
        params: dict[str, Any] = {"page_size": 100}
        # 限定读取指定视图，避免拉取整张表
        if VIEW_ID:
            params["view_id"] = VIEW_ID
        if page_token:
            params["page_token"] = page_token

        print(f"[拉取] 第 {page_index} 页 ...", end="", flush=True)
        resp = _request_with_retry("GET", url, headers=headers, params=params)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"拉取记录失败: {data}")

        items = data["data"].get("items") or []
        all_records.extend(items)
        print(f" 本页 {len(items)} 条，累计 {len(all_records)} 条")

        if not data["data"].get("has_more"):
            break
        page_token = data["data"].get("page_token")
        if not page_token:
            break

    print(f"[拉取] 完成，共 {len(all_records)} 条记录")
    return all_records


# ────────────────────────── 3. 下载图片 ──────────────────────────
def _guess_extension(att: dict, content_type: str | None) -> str:
    """
    自动判断图片真实扩展名（加分项）：
        优先：附件对象自带的 name（如 abc.jpg）→ 取后缀
        其次：响应头 Content-Type
        兜底：.png
    """
    # 先看附件对象的 name
    name = att.get("name") or ""
    if "." in name:
        ext = "." + name.rsplit(".", 1)[-1].lower()
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}:
            return ".jpg" if ext == ".jpeg" else ext
    # 再看 Content-Type
    if content_type:
        ext = _CONTENT_TYPE_EXT.get(content_type.split(";")[0].strip().lower())
        if ext:
            return ext
    return ".png"


def _try_download_via_bitable(
    tenant_access_token: str,
    app_token: str,
    table_id: str,
    record_id: str,
    field_name: str,
    file_token: str,
) -> requests.Response:
    """
    方案 A：bitable 专用下载接口（按用户要求实现）。
    GET /open-apis/bitable/v1/apps/{app_token}/tables/{table_id}
        /records/{record_id}/attachments/{field_name}/{file_token}/download
    """
    url = (
        f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{app_token}"
        f"/tables/{table_id}/records/{record_id}/attachments"
        f"/{field_name}/{file_token}/download"
    )
    headers = {"Authorization": f"Bearer {tenant_access_token}"}
    return _request_with_retry("GET", url, headers=headers, stream=True)


def _try_download_via_drive_extra(
    tenant_access_token: str,
    app_token: str,
    file_token: str,
) -> requests.Response:
    """
    方案 B（fallback）：drive medias + extra 参数。
    用于在 bitable 专用接口失败时兜底。
    """
    url = f"{FEISHU_HOST}/open-apis/drive/v1/medias/{file_token}/download"
    headers = {"Authorization": f"Bearer {tenant_access_token}"}
    extra = json.dumps({"bitablePerm": {"tableId": app_token}}, ensure_ascii=False)
    params = {"extra": extra}
    return _request_with_retry(
        "GET", url, headers=headers, params=params, stream=True
    )


def _decide_ext(content_type: str | None) -> str:
    """
    根据配置决定最终落盘的扩展名。
    - IMAGE_EXT_STRATEGY = "force_png" → 强制 .png
    - "auto"  → 按真实 Content-Type 推断
    - 其他   → 视为 force_png
    """
    if IMAGE_EXT_STRATEGY == "auto":
        return _guess_extension({}, content_type)
    return ".png"


def _resolve_conflict(target_path: Path) -> Path:
    """
    根据 IMAGE_CONFLICT_STRATEGY 处理目标文件已存在的情形。
    - overwrite → 直接返回 target_path（之后 with open(..., "wb") 会覆盖）
    - skip      → 若已存在，返回原路径（外层判断 .exists() 来决定是否真下载）
    - suffix    → 自动加 (1)(2) 直到不冲突
    """
    if IMAGE_CONFLICT_STRATEGY == "overwrite":
        return target_path
    if IMAGE_CONFLICT_STRATEGY == "skip":
        return target_path
    if IMAGE_CONFLICT_STRATEGY == "suffix":
        if not target_path.exists():
            return target_path
        stem = target_path.stem
        suffix = target_path.suffix
        i = 1
        while True:
            candidate = target_path.with_name(f"{stem}({i}){suffix}")
            if not candidate.exists():
                return candidate
            i += 1
    return target_path


def _render_image_name(
    template: str,
    *,
    number: str,
    child_name: str,
    phone: str,
    pinyin: str,
) -> str:
    """
    按模板渲染图片文件名（不含扩展名）。
    支持占位符：{编号} {孩子中文全名} {手机号} {拼音}
    渲染后会做一次文件名非法字符清洗。
    """
    name = (
        template
        .replace("{编号}", number)
        .replace("{孩子中文全名}", child_name)
        .replace("{手机号}", phone)
        .replace("{拼音}", pinyin)
    )
    return _sanitize_filename(name)


def download_image_by_url(
    url: str, tenant_access_token: str
) -> tuple[bytes, str | None]:
    """
    直接通过 URL 下载图片（用于飞书"图片"字段类型）。

    实测飞书 bitable 图片字段返回的 url / tmp_url **不是**公网直链，
    访问时仍需带租户级 token，否则会返回 "Missing access token"。

    返回：(二进制内容, Content-Type)
    """
    headers = {"Authorization": f"Bearer {tenant_access_token}"}
    resp = _request_with_retry("GET", url, headers=headers, stream=True)
    if resp.status_code != 200:
        raise RuntimeError(
            f"按 URL 下载失败 status={resp.status_code} "
            f"body={resp.text[:200]}"
        )
    content_type = resp.headers.get("Content-Type")
    # 一次性读全部二进制（图片体积小，无需分块）
    return resp.content, content_type


def download_image(
    tenant_access_token: str,
    app_token: str,
    table_id: str,
    record_id: str,
    field_name: str,
    file_token: str,
    target_path: Path,
    *,
    url: str | None = None,
) -> Path:
    """
    下载多维表格附件到 target_path（已存在则跳过）。

    优先使用 bitable 专用接口：
        GET /bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}
            /attachments/{field_name}/{file_token}/download
    若该接口失败（4xx），自动 fallback 到 drive medias + extra 参数。

    参数：
        tenant_access_token: 租户级 token
        app_token / table_id / record_id / field_name / file_token: 定位附件
        target_path: 期望落盘路径（默认 .png，下载后会按真实 Content-Type 修正后缀）

    返回：最终落盘的文件路径（可能是带真实后缀的兄弟路径）。
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. 冲突处理：根据 IMAGE_CONFLICT_STRATEGY 决定是跳过、覆盖还是加后缀
    target_path = _resolve_conflict(target_path)
    if target_path is None:
        # skip 策略下，已存在 → 直接告知调用方"使用旧文件"
        # 这里通过抛特殊语义的方式不太好，改回返回原路径即可
        # 实际不会走到这里，_resolve_conflict 已自洽
        raise RuntimeError("内部错误：冲突策略路径计算异常")

    # 2. 最优先：如果附件对象自带签名 URL，直接 GET 下载（适用于"图片"字段类型）
    if url:
        try:
            content, content_type = download_image_by_url(
                url, tenant_access_token
            )
            real_ext = _decide_ext(content_type)
            final_path = target_path.with_suffix(real_ext)
            with final_path.open("wb") as f:
                f.write(content)
            return final_path
        except Exception as e:
            print(
                f"  [警告] URL 直接下载失败：{e}，"
                f"尝试 fallback 到 bitable / drive 接口..."
            )

    # 3. 次优先方案：bitable 专用接口（适用于"附件"字段类型）
    resp = _try_download_via_bitable(
        tenant_access_token, app_token, table_id,
        record_id, field_name, file_token,
    )

    # 3. 若 4xx 则 fallback 到 drive medias + extra
    if resp.status_code != 200:
        body_preview = resp.text[:200] if resp.headers.get("Content-Type", "").startswith("application/json") else "(binary)"
        print(
            f"  [警告] bitable 专用接口失败 status={resp.status_code} "
            f"body={body_preview}，尝试 fallback 到 drive medias..."
        )
        resp.close()
        resp = _try_download_via_drive_extra(
            tenant_access_token, app_token, file_token
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"下载失败 file_token={file_token}, "
            f"status={resp.status_code}, body={resp.text[:200]}"
        )

    # 4. 决定后缀（受 IMAGE_EXT_STRATEGY 控制）
    real_ext = _decide_ext(resp.headers.get("Content-Type"))
    final_path = target_path.with_suffix(real_ext)

    # 5. 写入二进制（冲突已在前置 _resolve_conflict 中处理）
    with final_path.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return final_path


# ────────────────────────── 4. 处理记录 ──────────────────────────
def process_records(token: str, records: list[dict]) -> None:
    """
    遍历记录：
    - 抽取 编号 / 孩子中文全名 / 手机号
    - 抽取 个人码2 中第一张附件的 file_token
    - 下载图片到指定目录，文件名 编号-孩子中文全名.<ext>
    - 生成拼音
    - 追加写入 txt
    """
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_TXT_PATH.parent.mkdir(parents=True, exist_ok=True)

    txt_mode = "a" if APPEND_TXT else "w"
    if not APPEND_TXT and OUT_TXT_PATH.exists():
        # 覆盖模式：先清空一次，避免 with-mode w 在多次内部调用时被反复清空
        OUT_TXT_PATH.unlink()

    total = len(records)
    success = 0
    skipped = 0
    failed = 0

    with OUT_TXT_PATH.open(txt_mode, encoding="utf-8") as fout:
        for idx, rec in enumerate(records, start=1):
            print(f"[处理] 正在处理第 {idx}/{total} 条 ...")
            fields = rec.get("fields", {}) or {}
            record_id = rec.get("record_id", "")

            # 1. 抽取必要文本字段
            number = _extract_text(fields.get(FIELD_NUMBER))
            child_name = _extract_text(fields.get(FIELD_CHILD_NAME))
            phone = _extract_text(fields.get(FIELD_PHONE))

            # 2. 校验关键字段：编号 / 孩子中文全名 至少要有，否则跳过
            if not number or not child_name:
                print(
                    f"  [跳过] 第 {idx} 条字段缺失（编号={number!r}, "
                    f"孩子中文全名={child_name!r}），不处理"
                )
                skipped += 1
                continue

            # 3. 构造目标文件名（按 IMAGE_NAME_TEMPLATE 渲染，并清洗非法字符）
            #    先算拼音，便于模板里使用 {拼音}
            pinyin = _to_pinyin(child_name)
            base_name = _render_image_name(
                IMAGE_NAME_TEMPLATE,
                number=number,
                child_name=child_name,
                phone=phone,
                pinyin=pinyin,
            )
            target_path = IMAGE_DIR / f"{base_name}.png"  # 默认占位后缀，下载时按 _decide_ext 修正

            # 4. 抽取个人码2 → 第一张附件
            attachments = fields.get(FIELD_IMAGE) or []
            if not isinstance(attachments, list) or not attachments:
                print(f"  [跳过] 第 {idx} 条没有 {FIELD_IMAGE} 附件")
                skipped += 1
                continue
            first_att = attachments[0] or {}
            file_token = first_att.get("file_token", "")
            # 飞书"图片"字段会带签名 URL；优先用它，避免走 file_token 接口踩坑
            image_url = first_att.get("url") or first_att.get("tmp_url") or ""
            if not file_token and not image_url:
                print(f"  [跳过] 第 {idx} 条 {FIELD_IMAGE} 中无 file_token / url")
                skipped += 1
                continue
            if not image_url and not record_id:
                # 退化为 file_token 路径时必须有 record_id；正常情况下不会缺
                print(f"  [跳过] 第 {idx} 条缺少 record_id，无法走附件下载接口")
                skipped += 1
                continue

            # 5. 下载（或跳过）
            try:
                if DOWNLOAD_IMAGES:
                    saved_path = download_image(
                        tenant_access_token=token,
                        app_token=APP_TOKEN,
                        table_id=TABLE_ID,
                        record_id=record_id,
                        field_name=FIELD_IMAGE,
                        file_token=file_token,
                        target_path=target_path,
                        url=image_url,
                    )
                    if saved_path.exists():
                        msg = "已存在跳过" if saved_path.stat().st_mtime < time.time() - 1 else "已下载"
                        print(f"  [图片] {msg}: {saved_path}")
                else:
                    saved_path = target_path  # 仅推算路径，不真实下载
            except Exception as e:
                print(f"  [失败] 第 {idx} 条下载图片失败: {e}")
                failed += 1
                continue

            # 6. 写入 txt（每行：图片基础名,图片路径,拼音,手机号）
            #    第一列使用模板渲染后的 base_name，与图片文件名严格对应
            line = f"{base_name},{saved_path},{pinyin},{phone}\n"
            fout.write(line)
            success += 1

    print("=" * 60)
    print(f"[完成] 成功 {success} 条 / 跳过 {skipped} 条 / 失败 {failed} 条")
    print(f"[完成] 图片目录：{IMAGE_DIR}")
    print(f"[完成] 台账 txt：{OUT_TXT_PATH}")


# ────────────────────────── 5. 主流程 ──────────────────────────
def main() -> None:
    print("=" * 60)
    print("飞书多维表格 → 下载二维码 + 生成台账 txt")
    print(f"  app_token   = {APP_TOKEN}")
    print(f"  table_id    = {TABLE_ID}")
    print(f"  view_id     = {VIEW_ID or '(整表)'}")
    print(f"  下载图片    = {DOWNLOAD_IMAGES}")
    print(f"  命名模板    = {IMAGE_NAME_TEMPLATE}.{('真实后缀' if IMAGE_EXT_STRATEGY=='auto' else 'png')}")
    print(f"  冲突策略    = {IMAGE_CONFLICT_STRATEGY}")
    print(f"  txt 写入模式 = {'追加' if APPEND_TXT else '覆盖'}")
    print(f"  图片目录    = {IMAGE_DIR}")
    print(f"  台账 txt    = {OUT_TXT_PATH}")
    print("=" * 60)

    token = get_tenant_access_token()
    records = get_all_records(token)
    process_records(token, records)


if __name__ == "__main__":
    main()
