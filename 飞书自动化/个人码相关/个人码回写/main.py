# -*- coding: utf-8 -*-
"""
入口：把"个人码"图片回写到飞书多维表格的"个人码3"字段
==============================================

匹配规则（按需求）：
    if 编号 in clean_filename(去扩展名的文件名):
        命中 → 上传 + 回写"个人码3"

工业级防乱码加固：
    1. 启动时 encoding_utils.setup_console() 把控制台改成 UTF-8 + errors=replace
    2. 文件名用 clean_filename 清洗后再做 in 匹配（修 ¿ / 控制字符）
    3. 上传时 multipart filename 走 ASCII 兜底（修 latin-1 报错）
    4. 全局 try/except，单条失败不影响整体；接口失败自动重试 3 次
    5. 日志文件 errors=replace，写入永不崩溃

运行（PowerShell）：
    cd D:\\桌面文件\\新建文件夹\\飞书自动化\\个人码回写
    $env:FEISHU_APP_ID="cli_xxxxxx"
    $env:FEISHU_APP_SECRET="xxxxxxxx"
    python .\\main.py
"""

from __future__ import annotations

# ────────────────────────── 必须最先做控制台编码修复 ──────────────────────────
import encoding_utils
encoding_utils.setup_console()

import time
from typing import Optional, List, Dict

import config
import logger
import file_parser
import feishu_api


def find_first_match(
    records: List[dict], clean_stem: str
) -> Optional[dict]:
    """
    在 records 中查找第一条 FIELD_NUMBER 值是 clean_stem **子串** 的记录。

    其余命中的记录会通过日志告知（仅用于人工核对）。
    匹配前 number 也会做 clean_filename 清洗，避免飞书侧字段
    残留全角空格 / 控制字符导致漏匹配。
    """
    matched: list[tuple[str, str]] = []
    first: Optional[dict] = None

    for rec in records:
        raw_number = feishu_api.get_record_field_text(rec, config.FIELD_NUMBER)
        if not raw_number:
            continue
        number = encoding_utils.clean_filename(raw_number)
        if not number:
            continue
        # 业务规则："if 编号 in 文件名"
        if number in clean_stem:
            if first is None:
                first = rec
            matched.append((number, rec.get("record_id", "")))

    if len(matched) > 1:
        others = matched[1:]
        logger.warn(
            f"  文件 {clean_stem!r} 命中多条记录，仅取第一条 "
            f"(编号={matched[0][0]!r})；其它已忽略：{others}"
        )
    return first


def process_one(
    token: str,
    records: List[dict],
    item: Dict[str, str],
) -> str:
    """
    处理单张图片，返回三种状态字符串之一：'ok' / 'no_match' / 'failed'

    item 结构来自 file_parser.scan_images：
        raw_name / raw_stem / clean_stem / abs_path
    """
    clean_stem = item["clean_stem"]
    raw_stem   = item["raw_stem"]
    abs_path   = item["abs_path"]

    # 1. 本地匹配（用清洗后的 stem）
    record = find_first_match(records, clean_stem)
    if record is None:
        logger.warn(f"  未匹配：clean={clean_stem!r}  raw={raw_stem!r}")
        return "no_match"

    record_id = record.get("record_id", "")
    number    = encoding_utils.clean_filename(
        feishu_api.get_record_field_text(record, config.FIELD_NUMBER)
    )

    try:
        file_token = feishu_api.upload_attachment(token, abs_path)
        feishu_api.update_record(token, record_id, file_token)
        logger.success(
            f"  匹配 编号={number!r} → record_id={record_id} → 写入成功"
        )
        return "ok"
    except Exception as e:
        logger.error(
            f"  写入失败：{clean_stem} → 编号={number!r} "
            f"record_id={record_id}：{encoding_utils.safe_str(e)}"
        )
        return "failed"


def main() -> None:
    logger.reset()
    config.verify()

    logger.info("=" * 60)
    logger.info("个人码3 回写飞书多维表格（生产级 / 防乱码版）")
    logger.info(f"  扫描目录       = {config.INPUT_DIR}")
    logger.info(f"  app_token      = {config.APP_TOKEN}")
    logger.info(f"  table_id       = {config.TABLE_ID}")
    logger.info(f"  view_id        = {config.VIEW_ID or '(整张表)'}")
    logger.info(f"  匹配字段       = {config.FIELD_NUMBER}（清洗后做包含匹配）")
    logger.info(f"  写入字段       = {config.FIELD_IMAGE}（附件类型）")
    logger.info("=" * 60)

    token = feishu_api.get_tenant_access_token()

    records = feishu_api.list_all_records(token)
    logger.info(f"已缓存 {len(records)} 条记录到内存，后续在本地做包含匹配")

    files = file_parser.scan_images()
    logger.info(f"扫描到 {len(files)} 个待处理文件")
    if not files:
        logger.warn("没有可处理的图片，退出。")
        return

    ok_count, miss_count, fail_count = 0, 0, 0
    started = time.time()

    for idx, item in enumerate(files, start=1):
        logger.info(
            f"[{idx}/{len(files)}] 处理 clean={item['clean_stem']!r} "
            f"(raw={item['raw_stem']!r})"
        )
        try:
            status = process_one(token, records, item)
        except Exception as e:
            # 全局兜底：任何意外都不让整批崩
            logger.error(
                f"  未预期异常：{item.get('clean_stem')}：{encoding_utils.safe_str(e)}"
            )
            status = "failed"

        if status == "ok":
            ok_count += 1
        elif status == "no_match":
            miss_count += 1
        else:
            fail_count += 1

        if idx % 50 == 0:
            logger.info(
                f"  进度 {idx}/{len(files)}：成功 {ok_count}, "
                f"未匹配 {miss_count}, 失败 {fail_count}"
            )

    elapsed = time.time() - started
    logger.info("=" * 60)
    logger.info(
        f"完成：成功 {ok_count} / 未匹配 {miss_count} / 失败 {fail_count} "
        f"/ 总计 {len(files)}，耗时 {elapsed:.1f}s"
    )
    logger.info(f"日志文件：{config.LOG_PATH}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # 顶层兜底：保证任何异常都被打印且写入日志，而不是黑屏退出
        logger.error(f"主流程异常退出：{encoding_utils.safe_str(exc)}")
        raise
