# -*- coding: utf-8 -*-
"""
滑块验证模块（生产级）
- 拟人轨迹生成：先快后慢 + 轻微回拉 + 随机停顿
- 自动计算拖动距离：取滑块父轨道宽度 - 滑块自身宽度 - 边距
- 拖动失败自动重试，最多 3 次
"""
from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, List

from loguru import logger
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By

from utils.safe import wait_until_page_stable

if TYPE_CHECKING:
    from selenium import webdriver
    from selenium.webdriver.remote.webelement import WebElement


# 当无法计算轨道宽度时的兜底拖动距离（像素）
DEFAULT_DISTANCE_FALLBACK = 260
# 拖动到末端再回拉一点点，模拟真人对齐
PULLBACK_PIXELS = (4, 8)
# 安全余量：最终少推几像素，避免冲出轨道
SAFETY_MARGIN = 2


# ============================================================
# 轨迹生成
# ============================================================
def generate_track(distance: int) -> List[int]:
    """生成拟人化拖动轨迹（每一步的 x 偏移）。

    特点:
        - 先加速后减速（前 60% 距离加速，后 40% 减速）
        - 末段步幅极小，模拟「对齐」
        - 总和 ≈ distance + 回拉差值，由调用方再做回拉
    """
    if distance <= 0:
        return []

    track: List[int] = []
    current = 0
    mid = distance * 0.6
    v = 0.0
    t = 0.2

    while current < distance:
        a = random.uniform(2.0, 4.0) if current < mid else random.uniform(-5.0, -3.0)
        v0 = v
        v = v0 + a * t
        if v < 0:
            v = random.uniform(0.5, 1.5)

        move = v0 * t + 0.5 * a * t * t
        move = max(1, int(round(move)))

        if current + move > distance:
            move = distance - current

        if move <= 0:
            break

        track.append(move)
        current += move

    if track and track[-1] > 3:
        last = track.pop()
        for _ in range(last):
            track.append(1)

    return track


# ============================================================
# 距离计算
# ============================================================
def _calc_distance(driver: "webdriver.Chrome", slider_el: "WebElement") -> int:
    """根据滑块及其父轨道，自动计算需要拖动的像素数。

    思路：
        - 沿 DOM 向上 8 层寻找候选轨道父级（class 命中 slide/track/verify/bar/drag/captcha 任一）；
          这一步覆盖了页面真实结构 .anticon.anticon-double-right → .captcha-slider 这种容器命名。
        - 若没有命中任何关键词，则取"宽度明显大于滑块自身"的最近父级作为轨道。
        - 最终距离 = 轨道宽度 - 滑块宽度 - 安全余量；不要硬编码 300。
    """
    try:
        slider_w = slider_el.size.get("width") or 40
    except Exception:
        slider_w = 40

    try:
        track_width = driver.execute_script(
            """
            const el = arguments[0];
            const slider_w = arguments[1];
            const candidates = [];
            let p = el.parentElement;
            for (let i = 0; i < 8 && p; i++) {
                const cls = (p.className || '').toString();
                const w = p.getBoundingClientRect().width;
                // 1) 类名命中常见轨道关键词
                if (/slide|track|verify|bar|drag|captcha|move|range/i.test(cls)) {
                    candidates.push(w);
                }
                p = p.parentElement;
            }
            if (candidates.length > 0) return Math.max(...candidates);

            // 2) 兜底：往上找第一个"明显比滑块宽"的父级
            let q = el.parentElement;
            for (let i = 0; i < 8 && q; i++) {
                const w = q.getBoundingClientRect().width;
                if (w > slider_w + 40) return w;
                q = q.parentElement;
            }
            return el.parentElement ? el.parentElement.getBoundingClientRect().width : 0;
            """,
            slider_el,
            slider_w,
        )
    except Exception:
        track_width = 0

    if track_width and track_width > slider_w + 20:
        return int(track_width - slider_w - SAFETY_MARGIN)

    logger.warning("未能从轨道父元素获取宽度，使用兜底距离 {}px", DEFAULT_DISTANCE_FALLBACK)
    return DEFAULT_DISTANCE_FALLBACK


# ============================================================
# 拖动主流程
# ============================================================
def _do_drag(driver: "webdriver.Chrome", slider_el: "WebElement", distance: int) -> None:
    """执行一次拟人拖动。"""
    actions = ActionChains(driver, duration=0)

    actions.click_and_hold(slider_el).perform()
    time.sleep(random.uniform(0.15, 0.35))

    track = generate_track(distance)
    logger.debug("生成轨迹步数={}，目标距离={}px", len(track), distance)

    moved = 0
    for step in track:
        y_jitter = random.choice([-1, 0, 0, 0, 1])
        ActionChains(driver, duration=0).move_by_offset(step, y_jitter).perform()
        moved += step
        if random.random() < 0.05:
            time.sleep(random.uniform(0.02, 0.06))

    pullback = random.randint(*PULLBACK_PIXELS)
    ActionChains(driver, duration=0).move_by_offset(-pullback, 0).perform()
    time.sleep(random.uniform(0.1, 0.2))
    ActionChains(driver, duration=0).move_by_offset(pullback - 1, 0).perform()
    time.sleep(random.uniform(0.1, 0.2))

    ActionChains(driver, duration=0).release().perform()
    logger.debug("已松开滑块，实际移动 {}px (含末端微调)", moved)

    # 释放后等页面稳定：滑块校验通常会触发后端校验请求 + 状态切换
    # 不在 micro-move 之间等待，否则会打断拟人轨迹
    try:
        wait_until_page_stable(driver, timeout=10.0, stable_window=1.0, check_table=False)
    except Exception:
        pass


def drag_slider(
    driver: "webdriver.Chrome",
    slider_selector: str = ".anticon.anticon-double-right",
    max_retries: int = 3,
    success_check=None,
) -> bool:
    """拖动滑块，失败自动重试。

    参数:
        slider_selector: 滑块 CSS 选择器（默认 .block）
        max_retries: 最大重试次数
        success_check: 可选回调 callable(driver) -> bool；返回 True 视为验证通过。
                       若不传，默认认为单次拖动即成功。

    返回:
        True 表示验证通过；False 表示重试用完仍失败。
    """
    for attempt in range(1, max_retries + 1):
        logger.info("滑块拖动第 {} / {} 次", attempt, max_retries)
        try:
            slider_el = driver.find_element(By.CSS_SELECTOR, slider_selector)
        except (NoSuchElementException, StaleElementReferenceException):
            logger.warning("未找到滑块元素 {}，等待 1s 重试", slider_selector)
            time.sleep(1)
            continue

        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'center'});",
                slider_el,
            )
            time.sleep(0.3)
            distance = _calc_distance(driver, slider_el)
            _do_drag(driver, slider_el, distance)
        except Exception as e:
            logger.warning("拖动过程异常: {}", e)
            time.sleep(1.0)
            continue

        time.sleep(random.uniform(0.8, 1.4))

        if success_check is None:
            return True

        try:
            if success_check(driver):
                logger.success("滑块验证通过")
                return True
        except Exception as e:
            logger.warning("成功校验回调异常: {}", e)

        logger.warning("滑块验证失败，第 {} 次重试", attempt)
        time.sleep(random.uniform(0.8, 1.5))

    logger.error("滑块验证 {} 次后仍失败", max_retries)
    return False
