# -*- coding: utf-8 -*-
"""
纯 Python 版入口。

运行：
    python main.py

默认流程：
1. 可选复制微信 contact.db
2. 读取微信内部库 / 意向库
3. 读取飞书各业务表
4. 保留原原因日志，计算推荐人 / 渠道B / 带领B / 渠道A / 带领A
5. 根据 config.WRITE_TO_FEISHU 决定写回飞书或保存本地
6. 本地模式：输出 CSV / JSON 供人工检查
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import config
from channel_engine import ChannelContext, 执行渠道计算
from data_loader import (
    特殊渠道映射转列表,
    读取个性带领B指定,
    读取内部通讯录,
    读取合伙宝妈,
    读取意向通讯录,
    读取特殊渠道带领指定,
    读取通用带领B指定,
)
from feishu_cache import 读取本地飞书缓存
from feishu_client import FeishuBitableClient
from output_writer import (
    保存本地检查结果,
    备份内部通讯录,
    写入新增合伙宝妈,
    构造内部通讯录数据,
    构造新增合伙宝妈数据,
    构造汇总通讯录数据,
    重写飞书表,
)
from utils import 复制文件任务
from wechat_db import (
    收集渠道计算学员编号,
    读取内部微信通讯录,
    读取意向微信通讯录,
)


def table(name: str):
    return config.TABLES[name]


def 检查配置():
    """启动前检查飞书密钥是否已配置（支持 config 默认值或环境变量）。"""
    secret = (config.FEISHU_APP_SECRET or "").strip()
    if not secret or "请填写" in secret:
        raise RuntimeError(
            "飞书 FEISHU_APP_SECRET 未配置。"
            "请在 config.py 填写，或执行：$env:FEISHU_APP_SECRET=\"你的 app_secret\""
        )


def 解析运行模式参数():
    """
    解析命令行运行模式：
    - --test：测试模式，只保存本地，不写飞书
    - --prod：正式模式，写回飞书

    返回：
    - True：写飞书
    - False：不写飞书
    - None：未指定，沿用 config.WRITE_TO_FEISHU
    """
    parser = argparse.ArgumentParser(add_help=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--test", action="store_true", help="测试模式：只保存本地，不写回飞书")
    group.add_argument("--prod", action="store_true", help="正式模式：写回飞书")
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="启用本地飞书缓存复用；不传则每次强制下载飞书表",
    )
    args, _unknown = parser.parse_known_args()

    if args.test:
        return False, args.use_cache
    if args.prod:
        return True, args.use_cache
    return None, args.use_cache


def 运行下载脚本并返回缓存映射(业务表列表: list[str], 使用缓存: bool = False) -> dict:
    """
    调用独立下载脚本，获取本次下载的本地缓存路径映射。
    主程序只负责触发脚本和读取索引，不内置下载实现。
    """
    当前文件目录 = Path(__file__).resolve().parent
    下载脚本路径 = 当前文件目录 / "download_feishu_tables.py"
    if not 下载脚本路径.exists():
        raise RuntimeError(f"下载脚本不存在：{下载脚本路径}")

    索引路径 = Path(config.LOCAL_FEISHU_CACHE_DIR) / "main_本次下载索引.json"
    cmd = [
        sys.executable,
        str(下载脚本路径),
        "--output-dir",
        config.LOCAL_FEISHU_CACHE_DIR,
        "--workers",
        str(config.FEISHU_DOWNLOAD_WORKERS),
        "--index-out",
        str(索引路径),
        "--tables",
        *业务表列表,
    ]
    if 使用缓存:
        cmd.extend([
            "--use-cache",
            "--reuse-fresh-minutes",
            str(config.FEISHU_CACHE_REUSE_MINUTES),
        ])

    print("开始调用下载脚本：", 下载脚本路径)
    if 使用缓存:
        print("本次下载模式：启用缓存复用")
    else:
        print("本次下载模式：强制下载（未启用缓存复用）")
    subprocess.run(cmd, check=True)

    if not 索引路径.exists():
        raise RuntimeError(f"下载索引不存在：{索引路径}")
    with open(索引路径, "r", encoding="utf-8") as f:
        payload = json.load(f)
    缓存路径映射 = payload.get("缓存路径映射", {})
    if not isinstance(缓存路径映射, dict):
        raise RuntimeError(f"下载索引格式错误：{索引路径}")

    缺失表 = [name for name in 业务表列表 if name not in 缓存路径映射]
    if 缺失表:
        raise RuntimeError(f"下载索引缺少表路径：{缺失表}")
    return 缓存路径映射


def main():
    检查配置()
    模式覆盖值, 使用缓存 = 解析运行模式参数()
    写飞书开关 = config.WRITE_TO_FEISHU if 模式覆盖值 is None else 模式覆盖值
    运行模式 = "正式模式（写回飞书）" if 写飞书开关 else "测试模式（仅本地输出）"
    print("当前运行模式：", 运行模式)

    if config.COPY_TASKS:
        复制文件任务(config.COPY_TASKS)

    client = FeishuBitableClient(
        app_id=config.FEISHU_APP_ID,
        app_secret=config.FEISHU_APP_SECRET,
        app_token=config.APP_TOKEN,
        batch_size=config.BATCH_SIZE,
    )

    print("开始读取微信数据库...")
    微信原始ID映射微信号_备注 = 读取内部微信通讯录(config.INTERNAL_CONTACT_DB_PATHS)
    微信原始ID映射微信号_备注 = 读取意向微信通讯录(config.PROSPECT_CONTACT_DB_PATH, 微信原始ID映射微信号_备注)

    数据库学员编号列表 = 收集渠道计算学员编号(微信原始ID映射微信号_备注)
    print("参与渠道计算的学员编号数量：", len(数据库学员编号列表))

    print("开始读取飞书业务表...")
    业务表列表 = [
        "意向通讯录",
        "内部通讯录",
        "合伙宝妈",
        "特殊渠道带领指定",
        "个性带领B指定",
        "通用带领B指定",
    ]

    # 下载能力集中在 download_feishu_tables.py：主程序运行时先调用下载脚本。
    缓存路径映射 = 运行下载脚本并返回缓存映射(业务表列表, 使用缓存)

    意向通讯录records = 读取本地飞书缓存(缓存路径映射["意向通讯录"])
    内部通讯录records = 读取本地飞书缓存(缓存路径映射["内部通讯录"])
    合伙宝妈records = 读取本地飞书缓存(缓存路径映射["合伙宝妈"])
    特殊渠道带领指定records = 读取本地飞书缓存(缓存路径映射["特殊渠道带领指定"])
    个性带领B指定records = 读取本地飞书缓存(缓存路径映射["个性带领B指定"])
    通用带领B指定records = 读取本地飞书缓存(缓存路径映射["通用带领B指定"])

    意向学员编号映射推荐人编号 = 读取意向通讯录(client=None, records=意向通讯录records)
    print("意向通讯录：", len(意向学员编号映射推荐人编号), "条")

    学员编号映射推荐人编号, 推荐人编号映射推荐的学员列表, 学员编号映射固定渠道B带领B = 读取内部通讯录(
        client=None,
        records=内部通讯录records,
    )
    print("内部通讯录：", len(学员编号映射推荐人编号), "条")

    合伙宝妈编号映射前五学员编号 = 读取合伙宝妈(client=None, records=合伙宝妈records)
    print("合伙宝妈：", len(合伙宝妈编号映射前五学员编号), "条")

    特殊渠道带领指定学员编号映射_渠道B_带领B = 读取特殊渠道带领指定(
        client=None,
        records=特殊渠道带领指定records,
    )
    特殊渠道带领指定数据 = 特殊渠道映射转列表(特殊渠道带领指定学员编号映射_渠道B_带领B)

    个性带领B指定映射表_编号_新 = 读取个性带领B指定(client=None, records=个性带领B指定records)
    print("个性带领B指定：", len(个性带领B指定映射表_编号_新), "条")

    通用带领B指定映射表_原_新 = 读取通用带领B指定(client=None, records=通用带领B指定records)
    print("通用带领B指定：", len(通用带领B指定映射表_原_新), "条")

    # 推荐链上溯（渠道A / 带领A / 动态晋升）需要完整推荐关系：
    # 先取意向表，再用内部通讯录覆盖，确保内部学员能继续向上找到合伙宝妈（如 000111 -> 000030）
    学员来源映射 = dict(意向学员编号映射推荐人编号)
    学员来源映射.update(学员编号映射推荐人编号)

    print("开始计算渠道B / 带领B...")
    ctx = ChannelContext(
        数据库学员编号列表=数据库学员编号列表,
        学员来源映射=学员来源映射,
        推荐人编号映射推荐的学员列表=推荐人编号映射推荐的学员列表,
        学员编号映射推荐人编号=学员编号映射推荐人编号,
        学员编号映射固定渠道B带领B=学员编号映射固定渠道B带领B,
        宝妈前五学员映射=合伙宝妈编号映射前五学员编号,
        通用带领B指定映射表=通用带领B指定映射表_原_新,
        个性带领B指定映射表=个性带领B指定映射表_编号_新,
        特殊渠道带领指定学员编号映射_渠道B_带领B=特殊渠道带领指定学员编号映射_渠道B_带领B,
        调试日志_查找推荐人和渠道B=config.DEBUG_CHANNEL_B,
    )

    result = 执行渠道计算(ctx, config.OUTPUT_TXT)

    # 构造待写数据（无论写飞书还是本地，数据结构相同）
    汇总数据 = 构造汇总通讯录数据(微信原始ID映射微信号_备注)
    内部数据 = 构造内部通讯录数据(
        微信原始ID映射微信号_备注,
        result["学员编号映射编号列表_推荐人_渠道B_带领B"],
    )
    新增合伙宝妈数据 = 构造新增合伙宝妈数据(result["新增合伙宝妈编号映射前5编号"])

    if 写飞书开关:
        print("开始写回飞书汇总通讯录...")
        t = table("汇总通讯录")
        重写飞书表(client, t["table_id"], 汇总数据, t.get("view_id", ""), t.get("view_type", "ID"))

        print("开始写回飞书内部通讯录...")
        备份内部通讯录(config.BACKUP_DIR, 内部数据)
        t = table("内部通讯录")
        重写飞书表(client, t["table_id"], 内部数据, t.get("view_id", ""), t.get("view_type", "ID"))

        print("开始写入新增合伙宝妈及前五...")
        t = table("合伙宝妈")
        写入新增合伙宝妈(client, t["table_id"], result["新增合伙宝妈编号映射前5编号"])
    else:
        print("当前为本地检查模式（跳过飞书写回）...")
        保存本地检查结果(
            config.LOCAL_OUTPUT_DIR,
            汇总数据,
            内部数据,
            新增合伙宝妈数据,
            result,
            特殊渠道带领指定数据,
        )
        print("渠道计算 tab 结果：", config.OUTPUT_TXT)

    print("全部完成。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("运行失败：", exc)
        sys.exit(1)
