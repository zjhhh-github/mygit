#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
微信桌面自动化（基础版）

功能说明：
1) 通过“坐标点击 + 粘贴输入 + 回车发送”的方式，向多个联系人发送消息。
2) 支持“坐标采集模式”，先采集关键 UI 坐标，再执行发送流程。
3) 默认开启 dry_run（演练模式），先不真正发送，确认流程正确后再改为 False。

使用前说明（请务必阅读）：
1) 该方案属于桌面自动化，稳定性受微信版本、分辨率、缩放、窗口位置影响。
2) 个人微信自动化存在风控风险，请控制频率，避免短时间大量发送。
3) 运行脚本时不要移动微信窗口、不要切换前台窗口。
4) 鼠标移动到屏幕左上角可触发 PyAutoGUI 安全中止（FailSafe）。
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

import pyautogui
import pyperclip


# -----------------------------
# 基础配置（先改这里）
# -----------------------------
@dataclass
class Config:
    """运行配置：优先保证可读性和可维护性。"""

    # 要发送的联系人名称（与微信中显示名称一致）
    contacts: List[str]

    # 消息模板，支持 {name} 占位符
    message_template: str

    # 每个联系人重复发送次数（建议从 1 开始）
    send_times: int = 1

    # 每次发送之间的间隔（秒）
    interval_sec: float = 1.2

    # 搜索联系人后等待 UI 稳定的时间（秒）
    after_search_wait_sec: float = 0.5

    # 演练模式：True 时只走流程，不按回车发送
    dry_run: bool = True


# 坐标示例（请先通过 --capture 采集后替换）
POINTS: Dict[str, Tuple[int, int]] = {
    # 微信左上角“搜索”输入框
    "search_box": (220, 120),
    # 聊天窗口中的输入框区域（可点击激活输入焦点）
    "input_box": (950, 760),
}


def log(message: str) -> None:
    """统一日志输出，便于后续排查问题。"""
    now = time.strftime("%H:%M:%S")
    print(f"[{now}] {message}")


def click_point(name: str, points: Dict[str, Tuple[int, int]]) -> None:
    """点击指定坐标点；若未配置则直接抛错，避免误操作。"""
    if name not in points:
        raise KeyError(f"缺少坐标配置：{name}")
    x, y = points[name]
    pyautogui.click(x=x, y=y)


def clear_input_with_hotkeys() -> None:
    """使用全选 + 删除清空当前输入框。"""
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("backspace")


def paste_text(text: str) -> None:
    """通过剪贴板粘贴文本，避免中文输入法干扰。"""
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")


def focus_wechat_with_countdown(seconds: int = 5) -> None:
    """
    给用户预留时间把微信窗口置顶。
    注意：本脚本不做窗口句柄绑定，减少依赖，提升兼容性。
    """
    log("请在倒计时结束前，手动把微信窗口置于最前并保持不动。")
    for i in range(seconds, 0, -1):
        log(f"倒计时：{i}s")
        time.sleep(1)


def capture_points(template_keys: Iterable[str]) -> Dict[str, Tuple[int, int]]:
    """
    交互式采集坐标：
    1) 按提示把鼠标移动到目标位置
    2) 回车确认后记录当前鼠标坐标
    """
    captured: Dict[str, Tuple[int, int]] = {}
    print("\n=== 坐标采集模式 ===")
    for key in template_keys:
        input(f"\n请将鼠标移动到 [{key}] 对应位置，然后按回车采集...")
        pos = pyautogui.position()
        captured[key] = (pos.x, pos.y)
        print(f"{key}: ({pos.x}, {pos.y})")
    print("\n采集完成，请把下面结果复制回脚本的 POINTS：")
    print("{")
    for key, value in captured.items():
        print(f'    "{key}": {value},')
    print("}")
    return captured


def open_contact(contact_name: str, points: Dict[str, Tuple[int, int]], wait_sec: float) -> None:
    """
    打开联系人聊天窗口：
    1) 点击搜索框
    2) 清空旧搜索词
    3) 输入联系人名称
    4) 回车进入联系人会话
    """
    click_point("search_box", points)
    clear_input_with_hotkeys()
    paste_text(contact_name)
    pyautogui.press("enter")
    time.sleep(wait_sec)


def send_message_once(message: str, points: Dict[str, Tuple[int, int]], dry_run: bool) -> None:
    """
    发送一次消息：
    - 先点击输入框，确保焦点正确
    - 再粘贴文本
    - 最后根据 dry_run 决定是否回车发送
    """
    click_point("input_box", points)
    paste_text(message)
    if dry_run:
        log("[演练模式] 已粘贴消息，未执行回车发送。")
    else:
        pyautogui.press("enter")
        log("已执行回车发送。")


def run(config: Config, points: Dict[str, Tuple[int, int]]) -> None:
    """主流程：按联系人顺序逐个发送。"""
    focus_wechat_with_countdown(seconds=5)
    log("开始执行自动化流程。鼠标移动到屏幕左上角可紧急中止。")

    total_targets = len(config.contacts)
    for idx, name in enumerate(config.contacts, start=1):
        log(f"处理联系人 {idx}/{total_targets}: {name}")
        open_contact(name, points, config.after_search_wait_sec)

        # 对同一个联系人可重复发送多次（默认 1 次）
        for i in range(1, config.send_times + 1):
            msg = config.message_template.format(name=name)
            send_message_once(msg, points, config.dry_run)
            if i < config.send_times:
                time.sleep(config.interval_sec)

        # 联系人之间留一点间隔，降低误触概率
        time.sleep(config.interval_sec)

    log("流程执行完成。")


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="微信桌面自动化（基础版）")
    parser.add_argument(
        "--capture",
        action="store_true",
        help="进入坐标采集模式，不执行发送流程",
    )
    return parser.parse_args()


def main() -> int:
    """
    入口函数：
    - 先处理采集模式
    - 再执行发送模式
    """
    args = parse_args()

    # PyAutoGUI 安全配置：启用 FailSafe，设置全局动作间隔
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.12

    if args.capture:
        capture_points(["search_box", "input_box"])
        return 0

    # 你可以在这里替换为自己的联系人和消息
    config = Config(
        contacts=[
            "联系人A",
            "联系人B",
        ],
        message_template="你好 {name}，这是一条自动化测试消息（请忽略）。",
        send_times=1,
        interval_sec=1.2,
        after_search_wait_sec=0.5,
        dry_run=True,  # 第一次运行建议保持 True
    )

    # 基础校验：联系人为空时直接退出，防止无意义运行
    if not config.contacts:
        log("联系人列表为空，请先在 Config.contacts 中填写联系人。")
        return 1

    try:
        run(config, POINTS)
    except pyautogui.FailSafeException:
        log("检测到 FailSafe：你已将鼠标移到左上角，流程已安全中止。")
        return 2
    except Exception as exc:  # noqa: BLE001
        log(f"执行失败：{exc}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
