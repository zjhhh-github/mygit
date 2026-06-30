# -*- coding: utf-8 -*-
"""
示例插件
=========

最小可运行的扩展功能脚本，给「导入控制台 → 扩展功能」Tab 做参考。

约定接口（必须满足）：
    def run(log, **params) -> bool:
        ...
        return True  # True=成功，False=失败，None 视为成功

调用方（导入控制台）会把：
    - log    ：控制台的 logger（log.info / log.warning / log.error 都可用）
    - params ：custom_plugins.json 里 "params" 字段（任意键值对）
注入进来。

修改本文件后立即生效，不需要重新打包 exe。
"""

from __future__ import annotations

import os
import time
from datetime import datetime


def run(log, **params) -> bool:
    """示例执行体：打印参数 / 当前时间 / 一些环境信息。

    可在 custom_plugins.json 的 params 里随便加键值对：

        "params": {
            "title": "我的第一个插件",
            "sleep_seconds": 3
        }

    在脚本里通过 params.get(...) 取用即可。
    """
    title = params.get("title", "示例插件")
    sleep_seconds = float(params.get("sleep_seconds", 0))

    log.info(f"[示例插件] {title} —— 启动")
    log.info(f"[示例插件] 当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"[示例插件] 进程 PID：{os.getpid()}")
    log.info(f"[示例插件] 接收到的 params：{params}")

    if sleep_seconds > 0:
        log.info(f"[示例插件] 模拟耗时任务，sleep {sleep_seconds} 秒")
        time.sleep(sleep_seconds)

    log.info("[示例插件] 完成")
    return True
