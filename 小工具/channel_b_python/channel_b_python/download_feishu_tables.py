# -*- coding: utf-8 -*-
"""
独立下载脚本：仅拉取本次渠道计算所需的飞书业务表到本地 JSON 缓存。

运行示例（PowerShell）：
    cd "D:\\桌面文件\\新建文件夹\\小工具\\channel_b_python\\channel_b_python"
    & "d:/桌面文件/新建文件夹/.venv/Scripts/python.exe" ".\\download_feishu_tables.py"

可选参数：
    --output-dir    自定义输出目录
    --workers       并发下载线程数
    --tables        仅下载指定表名（可传多个）
    --use-cache     启用本地缓存复用（不传则每次强制下载）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional

import config
from feishu_client import FeishuBitableClient

飞书业务字段白名单 = {
    "意向通讯录": ["意向学员编号", "推荐人编号", "绑定状态"],
    "内部通讯录": ["编号", "推荐人编号", "渠道B编号", "带领B编号"],
    "合伙宝妈": ["编号", "学员1编号", "学员2编号", "学员3编号", "学员4编号", "学员5编号"],
    "特殊渠道带领指定": ["编号", "渠道B编号", "带领B编号"],
    "个性带领B指定": ["编号", "新带领B编号"],
    "通用带领B指定": ["原带领B编号", "新带领B编号"],
}


def 检查配置():
    """启动前检查飞书密钥，避免下载时才报配置错误。"""
    app_secret = (config.FEISHU_APP_SECRET or "").strip()
    if not app_secret or "请填写" in app_secret:
        raise RuntimeError(
            "飞书 FEISHU_APP_SECRET 未配置。"
            "请在 config.py 填写，或执行：$env:FEISHU_APP_SECRET=\"你的 app_secret\""
        )


def 解析参数():
    """解析命令行参数，支持覆盖输出目录、并发数和目标表。"""
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--output-dir",
        default=config.LOCAL_FEISHU_CACHE_DIR,
        help="飞书表本地缓存目录，默认读取 config.LOCAL_FEISHU_CACHE_DIR",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=config.FEISHU_DOWNLOAD_WORKERS,
        help="并发下载线程数，默认读取 config.FEISHU_DOWNLOAD_WORKERS",
    )
    parser.add_argument(
        "--tables",
        nargs="+",
        default=[],
        help="仅下载指定表名（可传多个）；不传则下载本脚本默认的 6 张业务表",
    )
    parser.add_argument(
        "--index-out",
        default="",
        help="下载索引文件输出路径（JSON，内容为表名->缓存文件路径映射）",
    )
    parser.add_argument(
        "--reuse-fresh-minutes",
        type=int,
        default=config.FEISHU_CACHE_REUSE_MINUTES,
        help="缓存复用窗口（分钟）；仅在 --use-cache 时生效",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="启用本地缓存复用；不传则每次强制下载飞书表",
    )
    return parser.parse_args()


def _安全文件名(name: str) -> str:
    """将表名转换为安全文件名，和缓存模块保持一致。"""
    替换字符 = '<>:"/\\|?*'
    文件名 = name
    for ch in 替换字符:
        文件名 = 文件名.replace(ch, "_")
    return 文件名.strip() or "unknown_table"


def _查找可复用缓存(输出目录: str, 表名: str, 复用窗口分钟: int) -> str:
    """在缓存目录查找指定表的最新文件，若足够新则返回路径。"""
    if 复用窗口分钟 <= 0 or not os.path.isdir(输出目录):
        return ""

    后缀 = "_" + _安全文件名(表名) + ".json"
    候选路径 = []
    for 文件名 in os.listdir(输出目录):
        if 文件名.endswith(后缀):
            路径 = os.path.join(输出目录, 文件名)
            if os.path.isfile(路径):
                候选路径.append(路径)
    if not 候选路径:
        return ""

    最新路径 = max(候选路径, key=os.path.getmtime)
    文件年龄秒 = time.time() - os.path.getmtime(最新路径)
    if 文件年龄秒 <= 复用窗口分钟 * 60:
        return 最新路径
    return ""


def _下载单表(
    client: FeishuBitableClient,
    表名: str,
    table_id: str,
    view_id: str,
    field_names: Optional[List[str]] = None,
    max_workers: int = 6,
) -> List[dict]:
    """下载单张飞书表，返回原始 records 列表。"""
    # 内外层并发统一：单表分页并发线程数与脚本参数 --workers 保持一致，避免日志混淆。
    records = client.list_records(
        table_id=table_id,
        table_name=表名,
        view_id=view_id,
        field_names=field_names,
        max_workers=max(1, int(max_workers)),
    )
    print(f"飞书下载完成：{表名}（{len(records)} 条）")
    return records


def 并发下载飞书业务表到本地(
    client: FeishuBitableClient,
    表配置: Dict[str, dict],
    表名列表: List[str],
    输出目录: str,
    并发数: int = 6,
    表字段白名单: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, str]:
    """
    并发下载指定业务表到本地 JSON。

    返回：表名 -> 本地 JSON 路径
    """
    os.makedirs(输出目录, exist_ok=True)
    时间戳 = datetime.now().strftime("%Y%m%d_%H%M%S")
    结果路径映射: Dict[str, str] = {}

    # 先并发拉取所有 records，确保计算使用同一批次快照。
    下载结果: Dict[str, List[dict]] = {}
    with ThreadPoolExecutor(max_workers=max(1, 并发数)) as executor:
        future_map = {}
        for 表名 in 表名列表:
            cfg = 表配置.get(表名, {})
            table_id = cfg.get("table_id", "")
            view_id = cfg.get("view_id", "")
            field_names = (表字段白名单 or {}).get(表名)
            if not table_id:
                raise RuntimeError(f"表配置缺失 table_id：{表名}")
            future = executor.submit(_下载单表, client, 表名, table_id, view_id, field_names, 并发数)
            future_map[future] = 表名

        for future in as_completed(future_map):
            表名 = future_map[future]
            下载结果[表名] = future.result()

    # 全部下载成功后再落盘，避免混杂“半成功”状态文件。
    for 表名 in 表名列表:
        records = 下载结果.get(表名, [])
        文件名 = f"{时间戳}_{_安全文件名(表名)}.json"
        路径 = os.path.join(输出目录, 文件名)
        payload = {
            "表名": 表名,
            "下载时间": 时间戳,
            "记录数": len(records),
            "records": records,
        }
        with open(路径, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        结果路径映射[表名] = 路径
        print(f"本地缓存已写入：{表名} -> {路径}")

    return 结果路径映射


def main():
    检查配置()
    args = 解析参数()

    默认业务表列表 = [
        "意向通讯录",
        "内部通讯录",
        "合伙宝妈",
        "特殊渠道带领指定",
        "个性带领B指定",
        "通用带领B指定",
    ]
    目标表列表 = args.tables if args.tables else 默认业务表列表

    # 提前做表名校验，避免下载到一半才发现参数错误。
    不存在表名 = [name for name in 目标表列表 if name not in config.TABLES]
    if 不存在表名:
        raise RuntimeError(f"以下表名未在 config.TABLES 中配置：{不存在表名}")

    client = FeishuBitableClient(
        app_id=config.FEISHU_APP_ID,
        app_secret=config.FEISHU_APP_SECRET,
        app_token=config.APP_TOKEN,
        batch_size=config.BATCH_SIZE,
    )

    print("开始下载飞书业务表到本地缓存...")
    print("目标表：", "、".join(目标表列表))
    print("输出目录：", args.output_dir)
    print("并发线程数：", args.workers)
    if args.use_cache:
        print("缓存复用：启用")
        print("缓存复用窗口（分钟）：", args.reuse_fresh_minutes)
    else:
        print("缓存复用：关闭（本次强制下载所有目标表）")

    # 默认强制下载；只有显式 --use-cache 才尝试复用缓存。
    缓存路径映射 = {}
    if args.use_cache:
        待下载表列表 = []
        for 表名 in 目标表列表:
            复用路径 = _查找可复用缓存(args.output_dir, 表名, max(0, int(args.reuse_fresh_minutes)))
            if 复用路径:
                缓存路径映射[表名] = 复用路径
                print(f"复用本地缓存：{表名} -> {复用路径}")
            else:
                待下载表列表.append(表名)
    else:
        待下载表列表 = list(目标表列表)

    if 待下载表列表:
        print("需要下载的表：", "、".join(待下载表列表))
        新下载映射 = 并发下载飞书业务表到本地(
            client=client,
            表配置=config.TABLES,
            表名列表=待下载表列表,
            输出目录=args.output_dir,
            并发数=max(1, int(args.workers)),
            表字段白名单=飞书业务字段白名单,
        )
        缓存路径映射.update(新下载映射)
    else:
        print("所有目标表都命中新鲜缓存，跳过飞书请求。")

    # 下载索引用于主程序复用，避免主程序再做下载实现逻辑。
    索引路径 = args.index_out.strip() or os.path.join(args.output_dir, "最新飞书下载索引.json")
    索引内容 = {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "输出目录": args.output_dir,
        "目标表": 目标表列表,
        "缓存路径映射": 缓存路径映射,
    }
    with open(索引路径, "w", encoding="utf-8") as f:
        json.dump(索引内容, f, ensure_ascii=False, indent=2)

    print("全部下载完成，共 {} 张表：".format(len(缓存路径映射)))
    for 表名 in 目标表列表:
        print(f"  {表名}: {缓存路径映射.get(表名, '')}")
    print("下载索引：", 索引路径)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("下载失败：", exc)
        sys.exit(1)
