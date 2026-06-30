# -*- coding: utf-8 -*-
"""
飞书多维表上传模块：将本地二维码图片写入「专属带领群二维码」列。

说明：
- 从二维码文件名中提取编号（¡¡¡ 后面的6位数字）
- 拉取飞书表格所有记录，建立「编号 → record_id」映射
- 跳过该列已有附件的行，避免重复上传
- 上传文件拿到 file_token 后，更新对应行的附件列
- 进度保存在 qr_upload_progress.json，中断后可续跑

被 main.py 的 upload_qrcodes_to_feishu() 调用。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

# ===== 飞书应用配置 =====
APP_ID = "cli_a96f36ed1538dbcf"
APP_SECRET = "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB"

# ===== 多维表配置 =====
APP_TOKEN = "Zk05bwki2abD8XsBBOccaFsPn8e"
TABLE_ID = "tblKa8wryhV4d7F4"
QRCODE_FIELD_NAME = "专属带领群二维码"   # 类型17=附件

# ===== 本地路径配置 =====
QRCODE_DIR = Path(r"C:\Users\LENOVO\Desktop\专属带领群二维码")
PROGRESS_FILE = Path(r"C:\Users\LENOVO\Desktop\qr_upload_progress.json")
LOG_FILE = Path(r"C:\Users\LENOVO\Desktop\qr_upload_log.txt")

BASE_URL = "https://open.feishu.cn/open-apis"
REQUEST_INTERVAL = 0.3  # 每次 API 调用之间的间隔（秒），避免触发限流


# ───────────────────────── 认证 ─────────────────────────

def _get_tenant_token() -> str:
    """获取应用级 tenant_access_token。"""
    resp = requests.post(
        f"{BASE_URL}/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取飞书 token 失败：{data}")
    return data["tenant_access_token"]


# ───────────────────────── 表格操作 ─────────────────────────

def _fetch_all_records(token: str) -> list[dict]:
    """分页拉取多维表所有记录，返回完整列表。"""
    records: list[dict] = []
    page_token: str | None = None
    url = f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records"

    while True:
        params: dict = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"拉取表格记录失败：{data}")

        records.extend(data["data"].get("items", []))
        if not data["data"].get("has_more", False):
            break
        page_token = data["data"].get("page_token")
        time.sleep(REQUEST_INTERVAL)

    return records


def _upload_file(token: str, file_path: Path) -> str:
    """
    将本地图片上传到飞书，返回 file_token。

    说明：
    - 使用 /drive/v1/medias/upload_all 接口
    - parent_type 必须是 bitable_image，parent_node 为多维表 app_token
    """
    with open(str(file_path), "rb") as f:
        resp = requests.post(
            f"{BASE_URL}/drive/v1/medias/upload_all",
            headers={"Authorization": f"Bearer {token}"},
            data={
                "file_name": file_path.name,
                "parent_type": "bitable_image",
                "parent_node": APP_TOKEN,
                "size": str(file_path.stat().st_size),
            },
            files={"file": (file_path.name, f, "image/png")},
            timeout=60,
        )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"上传文件失败：{data}")
    return data["data"]["file_token"]


def _update_record(token: str, record_id: str, file_token: str, file_name: str) -> None:
    """将已上传的图片写入指定记录的「专属带领群二维码」附件列。"""
    resp = requests.put(
        f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{record_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "fields": {
                QRCODE_FIELD_NAME: [{"file_token": file_token, "name": file_name}]
            }
        },
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"更新记录失败 [record_id={record_id}]：{data}")


# ───────────────────────── 本地文件处理 ─────────────────────────

def _extract_number(filename: str) -> str | None:
    """
    从文件名中提取6位编号。

    示例：
    - 专属带领群-anyulu-¡¡¡000085.png → 000085
    """
    # ¡ 的 unicode 是 \u00a1，¿ 是 \u00bf；兼容多种编码情况
    m = re.search(r'[\u00a1\u00bf]{3}(\d+)', filename)
    if m:
        return m.group(1).zfill(6)
    # 兜底：取文件名末尾最后一段纯数字
    m = re.search(r'-(\d{5,})(?:\.png)?$', filename, re.IGNORECASE)
    if m:
        return m.group(1).zfill(6)
    return None


def _build_file_map() -> dict[str, Path]:
    """扫描本地二维码目录，返回 {编号: 文件路径} 映射。"""
    result: dict[str, Path] = {}
    if not QRCODE_DIR.exists():
        print(f"  ⚠ 二维码目录不存在：{QRCODE_DIR}")
        return result
    for file in QRCODE_DIR.iterdir():
        if not file.is_file() or file.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        num = _extract_number(file.name)
        if num:
            result[num] = file
        else:
            print(f"  ⚠ 无法从文件名提取编号，跳过：{file.name}")
    return result


# ───────────────────────── 进度管理 ─────────────────────────

def _load_progress() -> set[str]:
    """加载已成功上传的编号集合。"""
    if not PROGRESS_FILE.exists():
        return set()
    with open(str(PROGRESS_FILE), "r", encoding="utf-8") as f:
        return set(json.load(f).get("done", []))


def _save_progress(done: set[str]) -> None:
    """保存已完成的编号集合。"""
    with open(str(PROGRESS_FILE), "w", encoding="utf-8") as f:
        json.dump({"done": sorted(done)}, f, ensure_ascii=False, indent=2)


def _log(msg: str) -> None:
    """追加写入运行日志。"""
    with open(str(LOG_FILE), "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


# ───────────────────────── 对外接口 ─────────────────────────

def upload_qrcodes_to_feishu() -> None:
    """
    批量将本地二维码上传到飞书多维表「专属带领群二维码」列。

    流程：
    1. 获取飞书 token
    2. 扫描本地二维码目录
    3. 拉取飞书表格所有记录
    4. 逐一上传并写入，跳过已有附件的行
    5. 打印汇总结果

    失败时只打印警告，不抛出异常，避免影响主流程已完成的结果。
    """
    print("\n" + "=" * 60)
    print("开始上传二维码到飞书多维表...")
    print("=" * 60)

    try:
        # 1. token
        print("\n[1/4] 获取飞书 token ...")
        token = _get_tenant_token()
        print("  OK")

        # 2. 本地文件
        print(f"\n[2/4] 扫描本地二维码目录：{QRCODE_DIR}")
        num_to_file = _build_file_map()
        print(f"  找到 {len(num_to_file)} 个有效文件")
        if not num_to_file:
            print("  无文件可上传，跳过。")
            return

        # 3. 拉取表格记录
        print("\n[3/4] 拉取飞书表格记录...")
        all_records = _fetch_all_records(token)
        print(f"  共 {len(all_records)} 条记录")

        # 建立 编号 → {record_id, has_qrcode} 映射
        num_to_record: dict[str, dict] = {}
        for rec in all_records:
            fields = rec.get("fields", {})
            number = str(fields.get("编号", "")).strip().zfill(6)
            if not number or number == "000000":
                continue
            num_to_record[number] = {
                "record_id": rec["record_id"],
                "has_qrcode": bool(fields.get(QRCODE_FIELD_NAME)),
            }

        # 4. 批量上传
        print("\n[4/4] 开始批量上传...")
        done = _load_progress()
        success = skipped = no_record = failed = 0
        failed_list: list[str] = []

        numbers = sorted(num_to_file.keys())
        total = len(numbers)

        for i, number in enumerate(numbers, 1):
            # 进度文件中已有，跳过
            if number in done:
                skipped += 1
                continue

            # 表格里没有该编号
            if number not in num_to_record:
                no_record += 1
                print(f"  [{i}/{total}] no.{number}: 表格中无记录，跳过")
                _log(f"SKIP_NO_RECORD {number}")
                continue

            rec_info = num_to_record[number]

            # 该行已有二维码，跳过
            if rec_info["has_qrcode"]:
                skipped += 1
                done.add(number)
                continue

            file_path = num_to_file[number]
            print(f"  [{i}/{total}] no.{number}: uploading...")

            try:
                file_token = _upload_file(token, file_path)
                time.sleep(REQUEST_INTERVAL)
                _update_record(token, rec_info["record_id"], file_token, file_path.name)
                time.sleep(REQUEST_INTERVAL)

                done.add(number)
                _save_progress(done)
                success += 1
                _log(f"OK {number} {file_path.name} -> {rec_info['record_id']}")
                print(f"    OK")

            except Exception as exc:
                failed += 1
                failed_list.append(number)
                print(f"    FAIL: {exc}")
                _log(f"FAIL {number} {exc}")
                # token 可能过期，尝试刷新一次
                try:
                    token = _get_tenant_token()
                except Exception:
                    pass

        # 汇总
        print("\n" + "-" * 40)
        print(f"上传完成：成功 {success} | 跳过 {skipped} | 无记录 {no_record} | 失败 {failed}")
        if failed_list:
            print(f"失败编号：{', '.join(failed_list)}")
        print(f"日志：{LOG_FILE}")

    except Exception as exc:
        # 整体异常也不抛出，只打印警告
        print(f"\n⚠ 上传飞书时发生错误，已跳过：{exc}")
        import traceback as _tb
        _tb.print_exc()
