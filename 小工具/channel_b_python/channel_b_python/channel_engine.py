# -*- coding: utf-8 -*-
"""
渠道B / 带领B 核心算法。

迁移原则：
- 保留原影刀中文函数名
- 保留原影刀判断原因日志
- 保留原影刀返回结构
- 只去掉 package.variables / xbot 依赖
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from utils import 是合法编号

import config

# 合伙宝妈计算跳过名单（标准化为 6 位编号字符串，便于快速比对）
合伙宝妈跳过编号集合 = {
    str(编号).strip()
    for 编号 in getattr(config, "合伙宝妈计算跳过编号集合", set())
    if 是合法编号(编号)
}


def 编号在合伙宝妈跳过名单(编号: Any) -> bool:
    """判断编号是否在合伙宝妈跳过名单中；名单内编号不参与合伙宝妈相关计算。"""
    if not 是合法编号(编号):
        return False
    return str(编号).strip() in 合伙宝妈跳过编号集合


@dataclass
class ChannelContext:
    数据库学员编号列表: List[str] = field(default_factory=list)
    学员来源映射: Dict[str, str] = field(default_factory=dict)
    推荐人编号映射推荐的学员列表: Dict[str, List[str]] = field(default_factory=dict)
    学员编号映射推荐人编号: Dict[str, str] = field(default_factory=dict)
    学员编号映射固定渠道B带领B: Dict[str, List[str]] = field(default_factory=dict)
    宝妈前五学员映射: Dict[str, List[str]] = field(default_factory=dict)
    通用带领B指定映射表: Dict[str, str] = field(default_factory=dict)
    个性带领B指定映射表: Dict[str, str] = field(default_factory=dict)
    特殊渠道带领指定学员编号映射_渠道B_带领B: Dict[str, List[str]] = field(default_factory=dict)
    调试日志_查找推荐人和渠道B: bool = True

    # 运行态
    飞书学员编号列表: List[str] = field(default_factory=list)
    合伙宝妈编号列表: List[str] = field(default_factory=list)
    新增学员编号: List[str] = field(default_factory=list)
    新增学员编号集合: set = field(default_factory=set)
    本轮新增合伙宝妈编号集合: set = field(default_factory=set)
    飞书宝妈前五学员映射: Dict[str, List[str]] = field(default_factory=dict)
    原因日志: List[str] = field(default_factory=list)

    def log(self, text: str):
        """
        打印并记录一条原因日志。
        在文字前面加上当前时间，这样无论是看终端输出还是回看保存的
        原因日志_*.txt 文件，都能知道每条日志具体发生在什么时间点。
        """
        时间字符串 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        带时间的文本 = f"[{时间字符串}] {text}"
        print(带时间的文本)
        self.原因日志.append(带时间的文本)


def 初始化运行态(ctx: ChannelContext):
    if not isinstance(ctx.数据库学员编号列表, list):
        ctx.数据库学员编号列表 = []
    if not isinstance(ctx.学员来源映射, dict):
        ctx.学员来源映射 = {}
    if not isinstance(ctx.推荐人编号映射推荐的学员列表, dict):
        ctx.推荐人编号映射推荐的学员列表 = {}
    if not isinstance(ctx.学员编号映射推荐人编号, dict):
        ctx.学员编号映射推荐人编号 = {}
    if not isinstance(ctx.学员编号映射固定渠道B带领B, dict):
        ctx.学员编号映射固定渠道B带领B = {}
    if not isinstance(ctx.宝妈前五学员映射, dict):
        ctx.宝妈前五学员映射 = {}
    if not isinstance(ctx.通用带领B指定映射表, dict):
        ctx.通用带领B指定映射表 = {}
    if not isinstance(ctx.个性带领B指定映射表, dict):
        ctx.个性带领B指定映射表 = {}
    if not isinstance(ctx.特殊渠道带领指定学员编号映射_渠道B_带领B, dict):
        ctx.特殊渠道带领指定学员编号映射_渠道B_带领B = {}

    # 飞书合伙宝妈数据中剔除跳过名单编号，后续上溯与动态晋升均不再视为合伙宝妈
    if isinstance(ctx.宝妈前五学员映射, dict) and 合伙宝妈跳过编号集合:
        for _跳过编号 in list(ctx.宝妈前五学员映射.keys()):
            if 编号在合伙宝妈跳过名单(_跳过编号):
                del ctx.宝妈前五学员映射[_跳过编号]
                ctx.log("[合伙宝妈-跳过] 飞书数据忽略编号={}".format(str(_跳过编号).strip()))

    ctx.飞书学员编号列表 = list(ctx.学员编号映射推荐人编号.keys()) if isinstance(ctx.学员编号映射推荐人编号, dict) else []
    ctx.合伙宝妈编号列表 = [
        编号
        for 编号 in (list(ctx.宝妈前五学员映射.keys()) if isinstance(ctx.宝妈前五学员映射, dict) else [])
        if not 编号在合伙宝妈跳过名单(编号)
    ]

    已记录新增学员编号 = set()
    飞书学员编号集合 = set()
    for 编号 in ctx.飞书学员编号列表:
        if 是合法编号(编号):
            飞书学员编号集合.add(str(编号).strip())

    ctx.新增学员编号 = []
    for 编号 in ctx.数据库学员编号列表:
        if not 是合法编号(编号):
            continue
        编号 = str(编号).strip()
        if 编号 in 飞书学员编号集合:
            continue
        if 编号 in 已记录新增学员编号:
            continue
        ctx.新增学员编号.append(编号)
        已记录新增学员编号.add(编号)
    ctx.新增学员编号集合 = set(ctx.新增学员编号)

    ctx.本轮新增合伙宝妈编号集合 = set()

    # 飞书宝妈前五学员映射：初始快照，运行中不再修改
    ctx.飞书宝妈前五学员映射 = {}
    for _宝妈编号, _前五编号列表 in ctx.宝妈前五学员映射.items():
        ctx.飞书宝妈前五学员映射[_宝妈编号] = list(_前五编号列表) if isinstance(_前五编号列表, list) else _前五编号列表

    # 初始扫描兜底：推荐人原本 >= 5，但飞书还没同步
    for _推荐人编号, _原推荐学员列表 in list(ctx.推荐人编号映射推荐的学员列表.items()):
        if not 是合法编号(_推荐人编号):
            continue
        _推荐人编号 = str(_推荐人编号).strip()
        if 编号在合伙宝妈跳过名单(_推荐人编号):
            continue
        if _推荐人编号 in ctx.宝妈前五学员映射:
            continue
        if not isinstance(_原推荐学员列表, list):
            continue

        _清洗后的推荐学员列表 = []
        _已存在的推荐学员集合 = set()
        for _x in _原推荐学员列表:
            if not 是合法编号(_x):
                continue
            _x = str(_x).strip()
            if _x in _已存在的推荐学员集合:
                continue
            _清洗后的推荐学员列表.append(_x)
            _已存在的推荐学员集合.add(_x)

        if len(_清洗后的推荐学员列表) < 5:
            continue

        _前5编号 = _清洗后的推荐学员列表[:5]
        ctx.宝妈前五学员映射[_推荐人编号] = _前5编号
        if _推荐人编号 not in ctx.合伙宝妈编号列表:
            ctx.合伙宝妈编号列表.append(_推荐人编号)
        ctx.推荐人编号映射推荐的学员列表[_推荐人编号] = _清洗后的推荐学员列表
        ctx.本轮新增合伙宝妈编号集合.add(_推荐人编号)

        ctx.log("[动态晋升-初始扫描] 推荐人编号={}，原推荐学员数={}，飞书未同步，补录为合伙宝妈".format(_推荐人编号, len(_清洗后的推荐学员列表)))
        ctx.log("[动态晋升-初始扫描] 已写入宝妈前五学员映射：{} -> {}".format(_推荐人编号, _前5编号))


def 获取固定渠道B带领B(ctx: ChannelContext, 学员编号: Any) -> Tuple[str, str]:
    """读取内部通讯录里已填写的固定渠道B / 带领B。"""
    键 = "" if 学员编号 is None else str(学员编号).strip()
    固定值 = ctx.学员编号映射固定渠道B带领B.get(键, ["", ""])
    if not isinstance(固定值, (list, tuple)) or len(固定值) < 2:
        return "", ""
    渠道B = "" if 固定值[0] is None else str(固定值[0]).strip()
    带领B = "" if 固定值[1] is None else str(固定值[1]).strip()
    return 渠道B, 带领B


def 获取上溯下一跳编号(ctx: ChannelContext, 当前学员编号: Any) -> str:
    """
    查找渠道 B 时的下一跳：优先推荐人，推荐人为空再用内部固定渠道B / 带领B。
    """
    键 = "" if 当前学员编号 is None else str(当前学员编号).strip()
    if not 是合法编号(键):
        return ""

    推荐人编号 = ctx.学员来源映射.get(键, "")
    if 是合法编号(推荐人编号):
        return str(推荐人编号).strip()

    固定渠道B, 固定带领B = 获取固定渠道B带领B(ctx, 键)
    if 是合法编号(固定渠道B):
        if ctx.调试日志_查找推荐人和渠道B:
            ctx.log("[上溯-固定渠道B] 学员={} 推荐人为空，使用内部固定渠道B={}".format(键, 固定渠道B))
        return str(固定渠道B).strip()

    if 是合法编号(固定带领B):
        if ctx.调试日志_查找推荐人和渠道B:
            ctx.log("[上溯-固定带领B] 学员={} 推荐人为空，使用内部固定带领B={}".format(键, 固定带领B))
        return str(固定带领B).strip()

    return ""


def 获取渠道A上溯下一跳编号(ctx: ChannelContext, 当前学员编号: Any) -> str:
    """
    计算渠道A 时的下一跳：优先内部固定渠道B，再推荐人，最后固定带领B。

    例：000186 的渠道B=000076，000076 在内部表固定渠道B=000032，
    则渠道A 应为 000032，而不是沿推荐人链误算成 000111。
    """
    键 = "" if 当前学员编号 is None else str(当前学员编号).strip()
    if not 是合法编号(键):
        return ""

    固定渠道B, 固定带领B = 获取固定渠道B带领B(ctx, 键)
    if 是合法编号(固定渠道B):
        if ctx.调试日志_查找推荐人和渠道B:
            ctx.log("[渠道A上溯-固定渠道B] 学员={} 使用内部固定渠道B={}".format(键, 固定渠道B))
        return str(固定渠道B).strip()

    推荐人编号 = ctx.学员来源映射.get(键, "")
    if 是合法编号(推荐人编号):
        return str(推荐人编号).strip()

    if 是合法编号(固定带领B):
        if ctx.调试日志_查找推荐人和渠道B:
            ctx.log("[渠道A上溯-固定带领B] 学员={} 使用内部固定带领B={}".format(键, 固定带领B))
        return str(固定带领B).strip()

    return ""


def 获取带领A上溯下一跳编号(ctx: ChannelContext, 当前学员编号: Any) -> str:
    """
    计算带领A 时的下一跳：优先内部固定带领B，再推荐人，最后固定渠道B。

    例：000207 的带领B=000171，000171 在内部表固定带领B=000112、固定渠道B=000035，
    则带领A 应为 000112，而不是误用固定渠道B 算成 000035。
    """
    键 = "" if 当前学员编号 is None else str(当前学员编号).strip()
    if not 是合法编号(键):
        return ""

    固定渠道B, 固定带领B = 获取固定渠道B带领B(ctx, 键)
    if 是合法编号(固定带领B):
        if ctx.调试日志_查找推荐人和渠道B:
            ctx.log("[带领A上溯-固定带领B] 学员={} 使用内部固定带领B={}".format(键, 固定带领B))
        return str(固定带领B).strip()

    推荐人编号 = ctx.学员来源映射.get(键, "")
    if 是合法编号(推荐人编号):
        return str(推荐人编号).strip()

    if 是合法编号(固定渠道B):
        if ctx.调试日志_查找推荐人和渠道B:
            ctx.log("[带领A上溯-固定渠道B] 学员={} 使用内部固定渠道B={}".format(键, 固定渠道B))
        return str(固定渠道B).strip()

    return ""


def 获取特殊指定渠道B带领B(ctx: ChannelContext, 报名学员编号: Any):
    """
    命中条件：编号存在于特殊渠道带领指定表。
    返回表中真实的渠道B、带领B（允许为空字符串；为空则计算结果也为空，不补算）。
    未命中返回 (None, None)。
    """
    键 = "" if 报名学员编号 is None else str(报名学员编号).strip()
    if 键 not in ctx.特殊渠道带领指定学员编号映射_渠道B_带领B:
        return None, None

    指定值 = ctx.特殊渠道带领指定学员编号映射_渠道B_带领B.get(键)
    if not isinstance(指定值, (list, tuple)) or len(指定值) < 2:
        return None, None

    指定渠道B原值 = 指定值[0]
    指定带领B原值 = 指定值[1]
    指定渠道B = "" if 指定渠道B原值 is None else str(指定渠道B原值).strip()
    指定带领B = "" if 指定带领B原值 is None else str(指定带领B原值).strip()
    return 指定渠道B, 指定带领B


def 查找推荐人和渠道B(ctx: ChannelContext, 报名学员编号: Any):
    推荐人编号 = ""
    渠道B编号 = ""

    if ctx.调试日志_查找推荐人和渠道B:
        ctx.log("[查找渠道B-入口] 报名学员编号={}".format(报名学员编号))

    if not 是合法编号(报名学员编号):
        if ctx.调试日志_查找推荐人和渠道B:
            ctx.log("[查找渠道B-返回空] 入口编号不合法，报名学员编号={}".format(报名学员编号))
        return 推荐人编号, 渠道B编号, None

    原始报名学员编号 = str(报名学员编号).strip()
    当前报名学员编号 = 原始报名学员编号
    已访问编号集合 = set()
    _轮次 = 0

    while True:
        _轮次 += 1

        指定渠道B编号, 指定带领B编号 = 获取特殊指定渠道B带领B(ctx, 当前报名学员编号)
        if 指定渠道B编号 is not None:
            if ctx.调试日志_查找推荐人和渠道B:
                ctx.log("[查找渠道B-特殊表命中] 第{}轮 当前报名学员编号={}，指定渠道B={}，指定带领B={}".format(_轮次, 当前报名学员编号, 指定渠道B编号, 指定带领B编号))
            return 推荐人编号, 指定渠道B编号, 指定带领B编号

        if 当前报名学员编号 in 已访问编号集合:
            if ctx.调试日志_查找推荐人和渠道B:
                ctx.log("[查找渠道B-返回空] 第{}轮 当前编号已访问过（防环），当前报名学员编号={}，已确定推荐人编号={}".format(_轮次, 当前报名学员编号, 推荐人编号))
            return 推荐人编号, "", None

        已访问编号集合.add(当前报名学员编号)

        if not 是合法编号(当前报名学员编号):
            if ctx.调试日志_查找推荐人和渠道B:
                ctx.log("[查找渠道B-返回空] 第{}轮 当前编号不合法，当前报名学员编号={}，已确定推荐人编号={}".format(_轮次, 当前报名学员编号, 推荐人编号))
            return 推荐人编号, "", None

        当前报名学员编号 = str(当前报名学员编号).strip()
        上一级推荐人编号 = 获取上溯下一跳编号(ctx, 当前报名学员编号)

        if not 是合法编号(上一级推荐人编号):
            ctx.log("[推荐人缺失] 学员={} 推荐人={} 类型={} 第{}轮".format(当前报名学员编号, repr(ctx.学员来源映射.get(当前报名学员编号, "")), type(ctx.学员来源映射.get(当前报名学员编号, "")).__name__, _轮次))
            return 推荐人编号, "", None

        上一级推荐人编号 = str(上一级推荐人编号).strip()
        _首次写入推荐人 = (推荐人编号 == "")
        if _首次写入推荐人:
            推荐人编号 = 上一级推荐人编号

        if ctx.调试日志_查找推荐人和渠道B:
            ctx.log("[查找渠道B-轮次] 第{}轮 当前报名学员编号={}，上一级推荐人编号={}，是否首次写入推荐人={}".format(_轮次, 当前报名学员编号, 上一级推荐人编号, _首次写入推荐人))

        前五学员编号列表 = ctx.宝妈前五学员映射.get(上一级推荐人编号)
        if 编号在合伙宝妈跳过名单(上一级推荐人编号):
            前五学员编号列表 = None

        if 前五学员编号列表 is None:
            if ctx.调试日志_查找推荐人和渠道B:
                ctx.log("[查找渠道B-非合伙宝妈] 第{}轮 上一级推荐人={} 不在 宝妈前五学员映射，继续向上找".format(_轮次, 上一级推荐人编号))
            当前报名学员编号 = 上一级推荐人编号
            continue

        合法前五学员编号集合 = set()
        for 前五学员编号 in 前五学员编号列表:
            if 是合法编号(前五学员编号):
                合法前五学员编号集合.add(str(前五学员编号).strip())

        _当前是否在前五 = 当前报名学员编号 in 合法前五学员编号集合
        if ctx.调试日志_查找推荐人和渠道B:
            ctx.log("[查找渠道B-命中前五] 第{}轮 命中合伙宝妈={}，前五学员={}，当前报名学员编号={}，是否在前五={}".format(_轮次, 上一级推荐人编号, 前五学员编号列表, 当前报名学员编号, _当前是否在前五))

        if not _当前是否在前五:
            渠道B编号 = 上一级推荐人编号
            if ctx.调试日志_查找推荐人和渠道B:
                ctx.log("[查找渠道B-命中] 第{}轮 当前节点不在前五，渠道B={}".format(_轮次, 渠道B编号))
            return 推荐人编号, 渠道B编号, None

        # 直属放行：只对本轮新增合伙宝妈生效，老飞书数据不受影响
        if 当前报名学员编号 == 原始报名学员编号 and 上一级推荐人编号 in ctx.本轮新增合伙宝妈编号集合:
            渠道B编号 = 上一级推荐人编号
            if ctx.调试日志_查找推荐人和渠道B:
                ctx.log("[查找渠道B-直属放行] 第{}轮 原始报名学员首次命中【本轮新增】直属合伙宝妈，虽然在前五中，仍直接认定渠道B={}".format(_轮次, 渠道B编号))
            return 推荐人编号, 渠道B编号, None

        if ctx.调试日志_查找推荐人和渠道B:
            ctx.log("[查找渠道B-继续向上] 第{}轮 当前节点在前五，继续向上找，上移到={}".format(_轮次, 上一级推荐人编号))
        当前报名学员编号 = 上一级推荐人编号


def 计算带领B编号(ctx: ChannelContext, 报名学员编号: Any, 渠道B编号: Any) -> str:
    if not 是合法编号(渠道B编号):
        return ""
    渠道B编号 = str(渠道B编号).strip()

    通用指定带领B编号 = ctx.通用带领B指定映射表.get(渠道B编号, "")
    if 是合法编号(通用指定带领B编号):
        return str(通用指定带领B编号).strip()

    if 是合法编号(报名学员编号):
        报名学员编号 = str(报名学员编号).strip()
        个性指定带领B编号 = ctx.个性带领B指定映射表.get(报名学员编号, "")
        if 是合法编号(个性指定带领B编号):
            return str(个性指定带领B编号).strip()

    return 渠道B编号


def 查找上层合伙宝妈编号(ctx: ChannelContext, 起始报名学员编号: Any, 用途: str = "渠道A") -> str:
    """
    从给定节点向上找上层合伙宝妈。

    用途：
    - 渠道A：优先固定渠道B 上溯
    - 带领A：优先固定带领B 上溯（渠道B 与带领B 在飞书中可能不同）
    """
    if not 是合法编号(起始报名学员编号):
        return ""

    if 用途 == "带领A":
        获取下一跳 = 获取带领A上溯下一跳编号
    else:
        获取下一跳 = 获取渠道A上溯下一跳编号

    当前报名学员编号 = str(起始报名学员编号).strip()
    已访问编号集合 = set()

    while True:
        if 当前报名学员编号 in 已访问编号集合:
            return ""
        已访问编号集合.add(当前报名学员编号)

        if not 是合法编号(当前报名学员编号):
            return ""
        当前报名学员编号 = str(当前报名学员编号).strip()

        上一级推荐人编号 = 获取下一跳(ctx, 当前报名学员编号)
        if not 是合法编号(上一级推荐人编号):
            return ""
        上一级推荐人编号 = str(上一级推荐人编号).strip()

        前五学员编号列表 = ctx.宝妈前五学员映射.get(上一级推荐人编号)
        if 编号在合伙宝妈跳过名单(上一级推荐人编号):
            前五学员编号列表 = None
        if 前五学员编号列表 is None:
            当前报名学员编号 = 上一级推荐人编号
            continue

        合法前五学员编号集合 = {str(x).strip() for x in 前五学员编号列表 if 是合法编号(x)}
        if 当前报名学员编号 not in 合法前五学员编号集合:
            return 上一级推荐人编号
        当前报名学员编号 = 上一级推荐人编号


def 计算渠道A编号(ctx: ChannelContext, 渠道B编号: Any) -> str:
    if not 是合法编号(渠道B编号):
        return ""

    原始渠道B编号 = str(渠道B编号).strip()
    当前起点编号 = 原始渠道B编号
    已访问编号集合 = set()

    while True:
        if 当前起点编号 in 已访问编号集合:
            if ctx.调试日志_查找推荐人和渠道B:
                ctx.log("[计算渠道A-返回空] 原始渠道B={}，发现循环，当前={}".format(原始渠道B编号, 当前起点编号))
            return ""
        已访问编号集合.add(当前起点编号)

        上层合伙宝妈编号 = 查找上层合伙宝妈编号(ctx, 当前起点编号)
        if not 是合法编号(上层合伙宝妈编号):
            if ctx.调试日志_查找推荐人和渠道B:
                ctx.log("[计算渠道A-返回空] 原始渠道B={}，未找到上层合伙宝妈".format(原始渠道B编号))
            return ""

        if ctx.调试日志_查找推荐人和渠道B:
            ctx.log("[计算渠道A-命中] 原始渠道B={}，渠道A={}".format(原始渠道B编号, 上层合伙宝妈编号))
        return 上层合伙宝妈编号


def 计算带领A编号(ctx: ChannelContext, 带领B编号: Any) -> str:
    if not 是合法编号(带领B编号):
        return ""

    原始带领B编号 = str(带领B编号).strip()
    当前起点编号 = 原始带领B编号
    已访问编号集合 = set()

    while True:
        if 当前起点编号 in 已访问编号集合:
            if ctx.调试日志_查找推荐人和渠道B:
                ctx.log("[计算带领A-返回空] 原始带领B={}，发现循环，当前={}".format(原始带领B编号, 当前起点编号))
            return ""
        已访问编号集合.add(当前起点编号)

        # 带领A 上溯优先使用内部固定带领B，与渠道A 的上溯规则分开
        上层合伙宝妈编号 = 查找上层合伙宝妈编号(ctx, 当前起点编号, 用途="带领A")
        if not 是合法编号(上层合伙宝妈编号):
            if ctx.调试日志_查找推荐人和渠道B:
                ctx.log("[计算带领A-返回空] 原始带领B={}，未找到上层合伙宝妈".format(原始带领B编号))
            return ""

        if ctx.调试日志_查找推荐人和渠道B:
            ctx.log("[计算带领A-命中] 原始带领B={}，带领A={}".format(原始带领B编号, 上层合伙宝妈编号))
        return 上层合伙宝妈编号


def 获取学员当前带领B(ctx: ChannelContext, 学员编号: Any, 渠道B编号: Any) -> str:
    """
    获取学员当前带领B：
    - 特殊指定表命中时，按表内真实带领B（允许为空）；
    - 未命中时，按“与渠道B解耦”的优先级推导：
      1) 内部固定带领B
      2) 个性带领B指定
      3) 通用带领B指定（依赖渠道B）
      4) 渠道B兜底
    """
    指定渠道B编号, 指定带领B编号 = 获取特殊指定渠道B带领B(ctx, 学员编号)
    if 指定渠道B编号 is not None:
        return "" if 指定带领B编号 is None else str(指定带领B编号).strip()

    键 = "" if 学员编号 is None else str(学员编号).strip()
    if not 是合法编号(键):
        return ""

    固定渠道B, 固定带领B = 获取固定渠道B带领B(ctx, 键)
    if 是合法编号(固定带领B):
        return str(固定带领B).strip()

    个性指定带领B编号 = ctx.个性带领B指定映射表.get(键, "")
    if 是合法编号(个性指定带领B编号):
        return str(个性指定带领B编号).strip()

    if 是合法编号(渠道B编号):
        渠道B编号 = str(渠道B编号).strip()
        通用指定带领B编号 = ctx.通用带领B指定映射表.get(渠道B编号, "")
        if 是合法编号(通用指定带领B编号):
            return str(通用指定带领B编号).strip()
        return 渠道B编号

    return ""


def 尝试动态晋升合伙宝妈(ctx: ChannelContext, 报名学员编号: Any):
    if not 是合法编号(报名学员编号):
        return

    报名学员编号 = str(报名学员编号).strip()
    推荐人编号 = ctx.学员来源映射.get(报名学员编号, "")
    if not 是合法编号(推荐人编号):
        return
    推荐人编号 = str(推荐人编号).strip()

    if 编号在合伙宝妈跳过名单(推荐人编号):
        return

    if 推荐人编号 in ctx.宝妈前五学员映射:
        return

    原始推荐学员列表 = ctx.推荐人编号映射推荐的学员列表.get(推荐人编号, [])
    if not isinstance(原始推荐学员列表, list):
        原始推荐学员列表 = []

    清洗后的推荐学员列表 = []
    已存在的推荐学员集合 = set()
    for _编号 in 原始推荐学员列表:
        if not 是合法编号(_编号):
            continue
        _编号 = str(_编号).strip()
        if _编号 in 已存在的推荐学员集合:
            continue
        清洗后的推荐学员列表.append(_编号)
        已存在的推荐学员集合.add(_编号)

    if len(清洗后的推荐学员列表) < 5:
        if 报名学员编号 not in 已存在的推荐学员集合:
            清洗后的推荐学员列表.append(报名学员编号)
            已存在的推荐学员集合.add(报名学员编号)

    ctx.推荐人编号映射推荐的学员列表[推荐人编号] = 清洗后的推荐学员列表

    if len(清洗后的推荐学员列表) >= 5:
        前5编号 = 清洗后的推荐学员列表[:5]
        ctx.宝妈前五学员映射[推荐人编号] = 前5编号
        if 推荐人编号 not in ctx.合伙宝妈编号列表:
            ctx.合伙宝妈编号列表.append(推荐人编号)
        ctx.本轮新增合伙宝妈编号集合.add(推荐人编号)

        ctx.log("[动态晋升-新增触发] 推荐人编号={}，原推荐学员数={}，飞书未同步，补录为合伙宝妈".format(推荐人编号, len(清洗后的推荐学员列表)))
        ctx.log("[动态晋升-新增触发] 已写入宝妈前五学员映射：{} -> {}".format(推荐人编号, 前5编号))


# =========================
# 特殊覆盖规则（独立于通用带领B / 固定渠道B逻辑，只在最后一步生效）
# =========================
# 命中名单里的推荐人，且最终算出的渠道B/带领B（或渠道A/带领A）都等于
# 特殊覆盖来源编号（003369）时，只把带领B（或带领A）改成特殊覆盖带领编号（001903），
# 渠道B/渠道A保持原值不变。
特殊覆盖推荐人编号集合 = {
    "006679", "006776", "006878", "006897", "007512", "007568",
    "007641", "008210", "008467", "008750", "009545", "009741",
}
特殊覆盖来源编号 = "003369"
特殊覆盖带领编号 = "001903"


def 应用推荐人特殊覆盖规则(
    ctx: ChannelContext,
    报名学员编号: str,
    推荐人编号: str,
    渠道B编号: str,
    带领B编号: str,
    渠道A编号: str,
    带领A编号: str,
) -> Tuple[str, str]:
    """
    特殊覆盖规则：仅在推荐人编号命中 特殊覆盖推荐人编号集合 时生效。

    规则1：如果 渠道B == 带领B == 特殊覆盖来源编号（003369）
           -> 只把带领B改成 特殊覆盖带领编号（001903），渠道B保持不变。
    规则2：如果 渠道A == 带领A == 特殊覆盖来源编号（003369）
           -> 只把带领A改成 特殊覆盖带领编号（001903），渠道A保持不变。

    要求：
    - 必须在渠道B、带领B、渠道A、带领A全部计算完成后调用（最后一步覆盖）；
    - 不修改渠道B / 渠道A，也不影响名单外的其他学员；
    - 独立函数，不混入 计算带领B编号 等通用带领B规则。

    返回：(带领B编号, 带领A编号) —— 只有这两个值可能被改写，其余值原样返回给调用方使用。
    """
    if not 是合法编号(推荐人编号):
        return 带领B编号, 带领A编号

    推荐人编号 = str(推荐人编号).strip()
    if 推荐人编号 not in 特殊覆盖推荐人编号集合:
        return 带领B编号, 带领A编号

    新带领B编号 = 带领B编号
    新带领A编号 = 带领A编号

    if 渠道B编号 == 特殊覆盖来源编号 and 带领B编号 == 特殊覆盖来源编号:
        新带领B编号 = 特殊覆盖带领编号
        ctx.log(
            "[特殊覆盖-带领B] 学员={} 推荐人={} 渠道B={} 带领B由{}改为{}".format(
                报名学员编号, 推荐人编号, 渠道B编号, 特殊覆盖来源编号, 特殊覆盖带领编号
            )
        )

    if 渠道A编号 == 特殊覆盖来源编号 and 带领A编号 == 特殊覆盖来源编号:
        新带领A编号 = 特殊覆盖带领编号
        ctx.log(
            "[特殊覆盖-带领A] 学员={} 推荐人={} 渠道A={} 带领A由{}改为{}".format(
                报名学员编号, 推荐人编号, 渠道A编号, 特殊覆盖来源编号, 特殊覆盖带领编号
            )
        )

    return 新带领B编号, 新带领A编号


def 执行渠道计算(ctx: ChannelContext, 输出文件路径: Optional[str] = None):
    初始化运行态(ctx)

    学员编号映射编号列表_推荐人_渠道B_带领B = {}
    总数量 = 0
    成功数量 = 0
    空结果数量 = 0

    文件 = open(输出文件路径, "w", encoding="utf-8") if 输出文件路径 else None
    try:
        for 报名学员编号 in ctx.数据库学员编号列表:
            总数量 += 1
            推荐人编号 = ""
            渠道B编号 = ""
            带领B编号 = ""
            渠道A编号 = ""
            带领A编号 = ""
            原始报名学员编号 = "" if 报名学员编号 is None else str(报名学员编号).strip()

            if 是合法编号(报名学员编号):
                if 原始报名学员编号 in ctx.新增学员编号集合:
                    尝试动态晋升合伙宝妈(ctx, 原始报名学员编号)

                推荐人编号, 渠道B编号, 指定带领B编号 = 查找推荐人和渠道B(ctx, 原始报名学员编号)
                # 特殊表命中时（含带领B 为空字符串），直接使用表内真实值，不走带领B 补算
                if 指定带领B编号 is not None:
                    带领B编号 = 指定带领B编号
                else:
                    带领B编号 = 计算带领B编号(ctx, 原始报名学员编号, 渠道B编号)

                # 新规则（解耦）：
                # 1) 父渠道B空只影响渠道A（父节点按“渠道链父节点”判定，即当前学员的渠道B节点）
                # 2) 父带领B空只影响带领A（父节点按“带领链父节点”判定，即当前学员的带领B节点）
                渠道A编号 = 计算渠道A编号(ctx, 渠道B编号)
                带领A编号 = 计算带领A编号(ctx, 带领B编号)

                if 是合法编号(渠道B编号):
                    父渠道链节点编号 = str(渠道B编号).strip()
                    _父推荐人, 父渠道节点渠道B编号, _父指定带领B = 查找推荐人和渠道B(ctx, 父渠道链节点编号)
                    _ = _父推荐人
                    if not 是合法编号(父渠道节点渠道B编号):
                        渠道A编号 = ""
                        if ctx.调试日志_查找推荐人和渠道B:
                            ctx.log("[新规则-渠道A置空] 学员={} 渠道链父节点={} 父渠道B为空，渠道A强制为空".format(原始报名学员编号, 父渠道链节点编号))

                if 是合法编号(带领B编号):
                    父带领链节点编号 = str(带领B编号).strip()
                    _父推荐人, 父带领节点渠道B编号, 父带领节点指定带领B编号 = 查找推荐人和渠道B(ctx, 父带领链节点编号)
                    _ = _父推荐人
                    if 父带领节点指定带领B编号 is not None:
                        父带领节点带领B编号 = "" if 父带领节点指定带领B编号 is None else str(父带领节点指定带领B编号).strip()
                    else:
                        父带领节点带领B编号 = 获取学员当前带领B(ctx, 父带领链节点编号, 父带领节点渠道B编号)
                    if not 是合法编号(父带领节点带领B编号):
                        带领A编号 = ""
                        if ctx.调试日志_查找推荐人和渠道B:
                            ctx.log("[新规则-带领A置空] 学员={} 带领链父节点={} 父带领B为空，带领A强制为空".format(原始报名学员编号, 父带领链节点编号))

                if 推荐人编号 and not 渠道B编号:
                    ctx.log("[结果异常排查] 报名学员={}，推荐人={}，渠道B为空，带领B为空，渠道A为空，带领A为空".format(原始报名学员编号, 推荐人编号))

                # 特殊覆盖规则：必须放在渠道B/带领B/渠道A/带领A全部计算完成之后，
                # 作为最后一步覆盖，不影响上面任何通用计算逻辑。
                带领B编号, 带领A编号 = 应用推荐人特殊覆盖规则(
                    ctx, 原始报名学员编号, 推荐人编号, 渠道B编号, 带领B编号, 渠道A编号, 带领A编号
                )

            学员编号映射编号列表_推荐人_渠道B_带领B[原始报名学员编号] = [推荐人编号, 渠道B编号, 带领B编号, 渠道A编号, 带领A编号]

            if 文件:
                文件.write(推荐人编号 + "\t" + 渠道B编号 + "\t" + 带领B编号 + "\t" + 渠道A编号 + "\t" + 带领A编号 + "\n")

            if 推荐人编号 or 渠道B编号 or 带领B编号 or 渠道A编号 or 带领A编号:
                成功数量 += 1
            else:
                空结果数量 += 1
    finally:
        if 文件:
            文件.close()

    新增合伙宝妈编号映射前5编号 = {}
    for _宝妈编号, _前五编号列表 in ctx.宝妈前五学员映射.items():
        if _宝妈编号 in ctx.飞书宝妈前五学员映射:
            continue
        新增合伙宝妈编号映射前5编号[_宝妈编号] = _前五编号列表

    ctx.log("处理完成")
    if 输出文件路径:
        ctx.log("输出文件：" + 输出文件路径)
    ctx.log("已生成字典：学员编号映射编号列表_推荐人_渠道B_带领B")
    ctx.log("总数量：{}".format(总数量))
    ctx.log("成功数量：{}".format(成功数量))
    ctx.log("空结果数量：{}".format(空结果数量))
    ctx.log("字典数量：{}".format(len(学员编号映射编号列表_推荐人_渠道B_带领B)))
    ctx.log("新增学员编号数量：{}".format(len(ctx.新增学员编号)))
    ctx.log("新增学员编号：{}".format(ctx.新增学员编号))
    ctx.log("[新增合伙宝妈] 数量：{}".format(len(新增合伙宝妈编号映射前5编号)))
    ctx.log("[新增合伙宝妈] 内容：{}".format(新增合伙宝妈编号映射前5编号))

    return {
        "学员编号映射编号列表_推荐人_渠道B_带领B": 学员编号映射编号列表_推荐人_渠道B_带领B,
        "新增学员编号": ctx.新增学员编号,
        "新增合伙宝妈编号映射前5编号": 新增合伙宝妈编号映射前5编号,
        "总数量": 总数量,
        "成功数量": 成功数量,
        "空结果数量": 空结果数量,
        "原因日志": ctx.原因日志,
    }
