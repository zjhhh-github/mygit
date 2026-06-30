# -*- coding: utf-8 -*-
"""
页面动作模块（业务级）
- 登录态检测：以「财务管理」菜单是否存在作为已登录标志
- 自动登录（多策略 placeholder/name/type/class）
- 菜单导航：财务管理 → 收款订单查询（hover + click 双兜底）
- 业务页就绪等待（日期组件 / 查询按钮 / 表格）
- 表格刷新等待（el-loading-mask 消失）
- 日期 JS 注入（Vue / ElementUI 双向绑定）
- 多策略点击查询 / 下载
- 关键步骤截图
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from utils.safe import (
    SET_VALUE_JS,
    find_by_text,
    find_first_visible,
    hover_then_click,
    install_network_hook,
    safe_click,
    safe_click_by_text,
    safe_click_checkbox,
    safe_input,
    scroll_into_view,
    switch_into_iframe_if_any,
    wait_clickable,
    wait_element,
    wait_page_ready,
    wait_until_page_stable,
)
from utils.slider import drag_slider

if TYPE_CHECKING:
    from selenium import webdriver
    from selenium.webdriver.remote.webelement import WebElement


# ============================================================
# 多策略 locator
# ============================================================
# 账号 / 密码：按页面真实结构使用精确 selector，多余兜底保留以应对样式微调
USERNAME_LOCATORS = [
    (By.CSS_SELECTOR, 'input.login-field.login-field-divide[placeholder="请输入账号"]'),
    (By.CSS_SELECTOR, 'input[placeholder="请输入账号"]'),
    (By.CSS_SELECTOR, "input[placeholder*='账号']"),
    (By.CSS_SELECTOR, "input[name='username']"),
    (By.XPATH, "//input[contains(@placeholder,'账号') or contains(@placeholder,'用户名')]"),
]

PASSWORD_LOCATORS = [
    (By.CSS_SELECTOR, 'input.login-field.login-field-divide[placeholder="请输入密码"]'),
    (By.CSS_SELECTOR, 'input[placeholder="请输入密码"]'),
    (By.CSS_SELECTOR, "input[type='password']"),
    (By.CSS_SELECTOR, "input[placeholder*='密码']"),
    (By.XPATH, "//input[contains(@placeholder,'密码')]"),
]

# 协议复选框：页面真实元素是隐藏 input.ant-checkbox-input（antd 把 input 用 opacity:0 隐藏）
# safe_click_checkbox 内部会自动用 JS click 触发，绕开"不可见 input 不能 send click"的限制
AGREEMENT_CHECKBOX_INNER = ".ant-checkbox-input"
AGREEMENT_CHECKED_CLASS = "ant-checkbox-checked"

LOGIN_BUTTON_LOCATORS = [
    (By.CSS_SELECTOR, ".button"),
    (By.XPATH, "//button[.//span[normalize-space(text())='登录']]"),
    (By.XPATH, "//button[normalize-space(text())='登录']"),
    (By.XPATH, "//*[contains(@class,'button') and (contains(normalize-space(.),'登录') or contains(normalize-space(.),'登 录'))]"),
    (By.CSS_SELECTOR, "button.el-button--primary"),
    (By.CSS_SELECTOR, "button[type='submit']"),
]

# 「微信扫码登录」链接：页面元素特征是行内 style 设置了主题蓝 rgb(0, 137, 250)
# CSS 属性选择器优先；保留一个文本兜底防止以后 style 改色
WECHAT_LOGIN_LOCATORS = [
    (By.CSS_SELECTOR, '[style*="rgb(0, 137, 250)"]'),
    (By.CSS_SELECTOR, '[style*="0, 137, 250"]'),
    (By.XPATH, "//*[contains(normalize-space(.),'微信扫码登录')]"),
]

# 「微信授权登录」按钮：扫码前的二次确认按钮（位于切换到微信登录之后、二维码出现之前）
# 没有特殊样式，按文本定位最稳；尽量收紧到 button / a / span 等可点击节点上
WECHAT_AUTH_LOCATORS = [
    (By.XPATH, "//button[.//span[normalize-space(text())='微信授权登录']]"),
    (By.XPATH, "//button[normalize-space(text())='微信授权登录']"),
    (By.XPATH, "//a[normalize-space(text())='微信授权登录']"),
    (By.XPATH, "//*[normalize-space(text())='微信授权登录']"),
    (By.XPATH, "//*[contains(normalize-space(.),'微信授权登录')]"),
]

# 微信扫码等待时长：300 秒（5 分钟），覆盖大多数手机扫码 + 确认所需时间
QR_WAIT_TIMEOUT = 300

# 滑块按钮：页面真实元素是 .anticon.anticon-double-right
# drag_slider 内部 ActionChains 按住 → 拟人轨迹 → release，自动计算距离，不要硬编码
SLIDER_SELECTOR = ".anticon.anticon-double-right"

# 后台是 Ant Design：菜单按 .ant-menu-submenu-title / .ant-menu-item 取，
# 再用文本"财务管理 / 收款订单查询"精确匹配，避免误点同结构的其他菜单项。
FIRST_MENU_CSS = ".ant-menu-submenu-title"
FIRST_MENU_TEXT = "财务管理"

SECOND_MENU_CSS = ".ant-menu-item"
SECOND_MENU_TEXT = "收款订单查询"

# 查询按钮：站点页面里同时存在多个 .ant-btn-primary（导出按钮也是 primary）
# 必须按文本"查询"精确锁定
QUERY_BUTTON_CSS = ".ant-btn.ant-btn-primary"
QUERY_BUTTON_TEXT = "查询"

# 导出报表按钮：primary + ghost 组合 class 已经能基本唯一定位，
# 但仍按文本"导出报表"再过一遍，避免页面有同样组合的其他按钮
DOWNLOAD_BUTTON_CSS = ".ant-btn.ant-btn-primary.ant-btn-background-ghost"
DOWNLOAD_BUTTON_TEXT = "导出报表"

# 业务页就绪检测：日期组件 + 主表格（antd 表头）
PAGE_READY_LOCATORS = {
    "date":  (By.CSS_SELECTOR, '.ant-picker-input input[placeholder="开始日期"], .ant-picker-input input[placeholder="结束日期"]'),
    "table": (By.CSS_SELECTOR, ".ant-table, table"),
}

# 用于"已登录后用 find_first_visible 兜底"的菜单 locator 列表，
# is_logged_in / wait_logged_in 仍用得到
FIRST_MENU_LOCATORS = [
    (By.CSS_SELECTOR, FIRST_MENU_CSS),
    (By.XPATH, "//*[self::span or self::a][normalize-space(text())='财务管理']"),
    (By.XPATH, "//li[.//span[normalize-space(text())='财务管理']]"),
]


# ============================================================
# 截图
# ============================================================
def take_screenshot(driver: "webdriver.Chrome", logs_dir: str | Path, tag: str) -> Path:
    """关键步骤 / 错误截图。"""
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    name = f"{tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    path = logs_dir / name
    try:
        driver.save_screenshot(str(path))
    except Exception:
        pass
    return path


# ============================================================
# 登录态检测 & 自动登录
# ============================================================
def open_and_settle(driver: "webdriver.Chrome", url: str, timeout: float = 30.0) -> None:
    """打开页面 → 注入网络 hook → 等待页面稳定。
    所有 driver.get 后都建议走这个入口，省掉外面再写一遍等待。
    """
    driver.get(url)
    install_network_hook(driver)
    wait_until_page_stable(driver, timeout=timeout, stable_window=2.0, check_table=False)


def is_logged_in(driver: "webdriver.Chrome", timeout: int = 8) -> bool:
    """以「财务管理」菜单是否出现作为已登录的判定标准。

    多重判断（任一命中即视为已登录）：
        1. 用 FIRST_MENU_LOCATORS 在当前 frame 找；
        2. 显式回到 default_content 再用 locator 找一次（QR 登录后路由切换，
           可能 driver 还残留在登录页的 iframe 里）；
        3. JS 全局兜底：在整篇 document 里搜"财务管理"文本，且元素 offsetParent
           不为 null（确实可见、不是 display:none）。
    """
    # 1) 优先：在 antd 菜单结构里按文本精确找"财务管理"
    try:
        find_by_text(driver, FIRST_MENU_CSS, FIRST_MENU_TEXT, timeout=timeout)
        return True
    except TimeoutException:
        pass

    # 2) 切回主框架再用同一方法找一次（QR 跳转后有时仍残留在 iframe）
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    try:
        find_by_text(driver, FIRST_MENU_CSS, FIRST_MENU_TEXT, timeout=2)
        return True
    except TimeoutException:
        pass

    # 3) JS 全局兜底：document 里任意可见元素含"财务管理"文本即算
    try:
        ok = driver.execute_script(
            """
            const nodes = document.querySelectorAll('*');
            for (const n of nodes) {
                const t = (n.innerText || n.textContent || '').trim();
                if (t === '财务管理' && n.offsetParent !== null) return true;
            }
            return false;
            """
        )
        return bool(ok)
    except Exception:
        return False


def _slider_success_check(driver: "webdriver.Chrome") -> bool:
    """滑块验证是否通过。

    背景：
        新 selector 是 `.anticon.anticon-double-right`（滑块按钮里的双箭头图标）。
        验证通过后，前端通常会把这个图标换成 ✓ / 移除 / 隐藏，
        旧版检测见到"元素不存在"就 return false，反而把"成功"误判成"失败"。

    多重判断（任一命中即算通过）：
        1. 图标已从 DOM 消失或被隐藏（width/height 为 0）  → 大概率通过
        2. 页面文本出现"验证通过 / 验证成功 / 滑动验证成功"
        3. 图标自身 / 任一近祖先 className 含 success / done / finish / pass
        4. 找到真正的轨道父级，图标右沿距轨道右沿 < 30px（容忍 icon 在 handle 内的留白）
    """
    try:
        result = driver.execute_script(
            """
            const sel = arguments[0];

            // 0) 最强正向信号：页面出现 .anticon-check-circle（✓ 图标）
            //    本站滑块通过后会把按钮里的图标 class 从 anticon-double-right
            //    切换为 anticon-check-circle，命中即可立即判通过。
            if (document.querySelector('.anticon.anticon-check-circle, .anticon-check-circle')) {
                return { ok: true, reason: 'check-circle-shown' };
            }

            const el = document.querySelector(sel);

            // 1) 原图标消失 / 不可见 → 视为通过（class 切换后原 selector 也会查不到）
            if (!el) return { ok: true, reason: 'icon-removed' };
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return { ok: true, reason: 'icon-hidden' };

            // 2) 页面文本含"验证成功/通过"
            try {
                const txt = (document.body && document.body.innerText) || '';
                if (/(验证(通过|成功))|滑动验证成功|拼图.{0,4}成功/.test(txt)) {
                    return { ok: true, reason: 'text-pass' };
                }
            } catch (e) {}

            // 3) className 含 success / done / finish / pass（自身或近 6 层祖先）
            let node = el;
            for (let i = 0; i < 6 && node; i++) {
                const cls = (node.className || '').toString();
                if (/(success|done|finish|pass|check)/i.test(cls)) {
                    return { ok: true, reason: 'success-class:' + cls };
                }
                node = node.parentElement;
            }

            // 4) 找真正的轨道父级（class 含 captcha/slide/track/drag/bar/verify
            //    且宽度明显大于滑块自身），看图标是否已贴近右沿
            const sw = r.width;
            let track = null;
            let p = el.parentElement;
            for (let i = 0; i < 8 && p; i++) {
                const cls = (p.className || '').toString();
                const w = p.getBoundingClientRect().width;
                if (/captcha|slide|track|drag|bar|verify/i.test(cls) && w > sw + 80) {
                    track = p;
                    break;
                }
                p = p.parentElement;
            }
            if (!track) {
                // 兜底：找第一个明显比滑块宽的祖先
                let q = el.parentElement;
                for (let i = 0; i < 8 && q; i++) {
                    const w = q.getBoundingClientRect().width;
                    if (w > sw + 80) { track = q; break; }
                    q = q.parentElement;
                }
            }
            if (track) {
                const t = track.getBoundingClientRect();
                const remaining = t.right - r.right;
                if (remaining < 30) {
                    return { ok: true, reason: 'reached-end', remaining };
                }
                return { ok: false, reason: 'still-incomplete', remaining };
            }

            return { ok: false, reason: 'no-track-found' };
            """,
            SLIDER_SELECTOR,
        )

        if isinstance(result, dict):
            ok = bool(result.get("ok"))
            reason = result.get("reason")
            remaining = result.get("remaining")
            if ok:
                logger.debug("滑块成功判定：reason={}, remaining={}", reason, remaining)
            else:
                logger.debug("滑块仍未通过：reason={}, remaining={}", reason, remaining)
            return ok
        return bool(result)
    except Exception as e:
        logger.debug("滑块成功校验异常：{}", e)
        return False


def _fill_login_form(driver: "webdriver.Chrome", username: str, password: str, timeout: int) -> None:
    """填账号 / 密码 / 勾选协议（每次重试都重新填，防止页面刷新清空）。"""
    user_el = find_first_visible(driver, USERNAME_LOCATORS, timeout=timeout, require_clickable=True)
    safe_input(driver, user_el, username, use_js=False)

    pwd_el = find_first_visible(driver, PASSWORD_LOCATORS, timeout=timeout, require_clickable=True)
    safe_input(driver, pwd_el, password, use_js=False)

    ok = safe_click_checkbox(
        driver,
        inner_selector=AGREEMENT_CHECKBOX_INNER,
        checked_class=AGREEMENT_CHECKED_CLASS,
        timeout=8,
        retries=3,
    )
    if ok:
        logger.info("已勾选用户协议（ant-checkbox-checked 已生效）")
    else:
        logger.warning("未能勾选协议复选框 {}，将继续尝试登录", AGREEMENT_CHECKBOX_INNER)


def auto_login(driver: "webdriver.Chrome", username: str, password: str, timeout: int = 30, max_retries: int = 3) -> None:
    """完整登录：账号 / 密码 / 协议 / 滑块 / 登录，最多重试 max_retries 次。

    任一环节失败均自动重试，直到出现「财务管理」菜单视为登录成功。
    """
    logger.info("开始自动登录: {}", username)
    wait_page_ready(driver, timeout=timeout)
    switch_into_iframe_if_any(driver, PASSWORD_LOCATORS[0], timeout=5)

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        logger.info("登录尝试 {} / {}", attempt, max_retries)
        try:
            _fill_login_form(driver, username, password, timeout)

            ok = drag_slider(
                driver,
                slider_selector=SLIDER_SELECTOR,
                max_retries=3,
                success_check=_slider_success_check,
            )
            if not ok:
                logger.warning("滑块验证失败，刷新页面后重试整体登录")
                driver.refresh()
                wait_page_ready(driver, timeout=timeout)
                continue

            safe_click(driver, LOGIN_BUTTON_LOCATORS, timeout=timeout)
            logger.info("已点击登录按钮")

            # ─── 切到「微信扫码登录」 ─────────────────────────────────
            # 部分租户配置下，账号密码登录之后还需要再点一次"微信扫码登录"
            # 才会出现二维码；找不到这个入口（可能 UI 已经直接给二维码或直接登入）
            # 也不算失败，继续走"等财务管理菜单"的判定即可
            try:
                safe_click(driver, WECHAT_LOGIN_LOCATORS, timeout=10)
                logger.info("已切换到「微信扫码登录」")
            except TimeoutException:
                logger.info("未发现「微信扫码登录」入口，按已经在二维码 / 主框架处理")

            # ─── 点击「微信授权登录」按钮（扫码前的二次确认） ───────
            # 该按钮可能存在也可能不存在（不同租户配置不同）；找不到不视为失败
            try:
                safe_click(driver, WECHAT_AUTH_LOCATORS, timeout=10)
                logger.info("已点击「微信授权登录」")
            except TimeoutException:
                logger.info("未发现「微信授权登录」按钮，假定二维码已直接展示")

            # ─── 等待用户手机扫码 ────────────────────────────────────
            # 扫码阶段是"人在中间"，不能再走整体重试逻辑（会刷新页面打断扫码），
            # 因此这里直接 break 出整体 retry 循环：单次 WebDriverWait 等 300s。
            # 显式切回 default_content：登录前为了找密码框可能进过 iframe，
            # QR 跳转后菜单一定在主框架里，不切回会一直找不到「财务管理」。
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

            logger.info("请使用微信扫码登录...")
            try:
                wait_logged_in(driver, timeout=QR_WAIT_TIMEOUT)
                logger.success("微信扫码登录成功")
                return
            except TimeoutException as e:
                last_err = e
                logger.error(
                    "等待微信扫码超时（{}s 内未检测到「财务管理」菜单）",
                    QR_WAIT_TIMEOUT,
                )
                break

        except Exception as e:
            last_err = e
            logger.warning("登录流程异常: {}，刷新页面重试", e)
            try:
                driver.refresh()
                wait_page_ready(driver, timeout=timeout)
            except Exception:
                pass

    raise TimeoutException(f"登录失败（已重试 {max_retries} 次），最后错误: {last_err}")


def wait_logged_in(driver: "webdriver.Chrome", timeout: int = 60) -> None:
    """等待出现「财务管理」菜单。
    每 15s 打一次心跳日志（当前 URL），便于在长时间扫码等待时确认脚本"还活着"
    以及定位"扫完了但菜单没识别"这种边界情况。
    """
    end = time.time() + timeout
    started = time.time()
    next_heartbeat = started + 15

    while time.time() < end:
        if is_logged_in(driver, timeout=3):
            logger.success("登录成功，已进入主框架")
            return

        now = time.time()
        if now >= next_heartbeat:
            elapsed = int(now - started)
            try:
                cur_url = driver.current_url
            except Exception:
                cur_url = "<unknown>"
            logger.info("等待登录中... 已 {}s / {}s, URL={}", elapsed, timeout, cur_url)
            next_heartbeat = now + 15

        time.sleep(1)

    # 超时前最后一次抓诊断信息，方便你贴日志给我定位
    try:
        cur_url = driver.current_url
        title = driver.title
    except Exception:
        cur_url, title = "<unknown>", "<unknown>"
    logger.error("登录后等待主框架超时：URL={}, title={}", cur_url, title)
    raise TimeoutException("登录后等待主框架超时")


# ============================================================
# 菜单导航
# ============================================================
def navigate_to_payorder(driver: "webdriver.Chrome", timeout: int = 30) -> None:
    """模拟真实点击：财务管理 → 收款订单查询。

    实现：
        1. 在所有 .ant-menu-submenu-title 里按文本"财务管理"挑出唯一目标，点击展开。
           即使页面同结构有"系统管理 / 数据管理"等其他菜单也不会误点。
        2. 在所有 .ant-menu-item 里按文本"收款订单查询"挑唯一目标，点击进入。
           safe_click_by_text 内部会等待 + 滚动 + 重试，二级菜单展开慢也能扛。
    """
    logger.info("点击一级菜单：财务管理")
    safe_click_by_text(
        driver, FIRST_MENU_CSS, FIRST_MENU_TEXT,
        timeout=timeout, retries=3,
    )

    logger.info("点击二级菜单：收款订单查询")
    safe_click_by_text(
        driver, SECOND_MENU_CSS, SECOND_MENU_TEXT,
        timeout=timeout, retries=3,
    )


# ============================================================
# 业务页就绪 & 表格加载
# ============================================================
def wait_page_loaded(driver: "webdriver.Chrome", timeout: int = 60) -> None:
    """等待业务页就绪：日期组件 + 查询按钮 + 主表格（antd 版）。"""
    logger.info("等待业务页加载（日期 / 查询按钮 / 表格）...")
    wait_page_ready(driver, timeout=timeout)

    wait_element(driver, PAGE_READY_LOCATORS["date"], timeout=timeout)
    logger.info("  - 日期组件 OK")

    # 查询按钮：按 CSS+文本兜底 find_by_text，兼容 page_loaded 时按钮还没渲染好的情况
    try:
        find_by_text(driver, QUERY_BUTTON_CSS, QUERY_BUTTON_TEXT, timeout=timeout)
        logger.info("  - 查询按钮 OK")
    except TimeoutException:
        logger.warning("未明确识别到「查询」按钮，但继续后续流程")

    wait_element(driver, PAGE_READY_LOCATORS["table"], timeout=timeout)
    logger.info("  - 主表格 OK")

    logger.success("业务页已就绪")


# loading 遮罩 / 旋转动画的 antd 选择器（含通用 .loading / .spin 兜底）
_LOADING_SELECTOR = ".ant-spin-spinning, .ant-spin-nested-loading .ant-spin, .loading, .spin"


def _get_first_row_signature(driver: "webdriver.Chrome") -> str:
    """取表格第一行（首个数据行）的可见文本作为指纹，用于检测"刷新后内容是否变了"。"""
    try:
        return driver.execute_script(
            """
            const row = document.querySelector('.ant-table-tbody tr');
            return row ? (row.innerText || '').trim() : '';
            """
        ) or ""
    except Exception:
        return ""


def wait_table_loaded(driver: "webdriver.Chrome", timeout: int = 60) -> None:
    """等表格刷新完成 → 直接复用底层 wait_until_page_stable。

    底层会同时检测：
        - readyState complete
        - antd 转圈 / .loading 不可见
        - fetch / XHR 在途数 == 0
        - .ant-table-tbody 行数稳定
    且要求"连续 2 秒稳定"才放行，比"等 spin 消失就走"严格一档。
    """
    logger.info("等待表格刷新完成...")
    wait_until_page_stable(driver, timeout=timeout, stable_window=2.0, check_table=True)
    logger.success("表格已稳定")


# ============================================================
# 日期 / 查询 / 下载
# ============================================================
# antd 日期输入精确 selector（按用户给的真实结构）
_START_DATE_SELECTORS = [
    '.ant-picker-input.ant-picker-input-active input[placeholder="开始日期"]',
    '.ant-picker-input input[placeholder="开始日期"]',
    'input[placeholder="开始日期"]',
]
_END_DATE_SELECTORS = [
    '.ant-picker-input input[placeholder="结束日期"]',
    'input[placeholder="结束日期"]',
]


def _find_date_input(driver: "webdriver.Chrome", selectors: list[str], timeout: int = 15) -> "WebElement":
    """按候选 selector 顺序找日期输入框，返回第一个存在且可见的。"""
    end_at = time.time() + timeout
    last_exc: Exception | None = None
    while True:
        for sel in selectors:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    try:
                        if el.is_displayed():
                            return el
                    except StaleElementReferenceException:
                        continue
            except Exception as e:
                last_exc = e
                continue
        if time.time() >= end_at:
            break
        time.sleep(0.3)
    raise TimeoutException(f"未找到可见的日期输入框: {selectors}, last: {last_exc}")


# ============================================================
# Ant Design DatePicker 生产级日期选择
# ============================================================
def _parse_ymd(date_str: str) -> tuple[int, int, int]:
    """解析 'YYYY-MM-DD'。"""
    y, m, d = date_str.split("-")
    return int(y), int(m), int(d)


def _is_picker_dropdown_open(driver: "webdriver.Chrome") -> bool:
    """判断是否有可见的 .ant-picker-dropdown。
    注意：antd 的浮层是 Portal，挂在 body 下，不在 input 父级里——必须全局查。
    """
    return bool(driver.execute_script(
        """
        const dd = document.querySelectorAll('.ant-picker-dropdown');
        for (const d of dd) {
            if (d.classList.contains('ant-picker-dropdown-hidden')) continue;
            if (d.offsetParent !== null) return true;
        }
        return false;
        """
    ))


def _read_panel_year_month(driver: "webdriver.Chrome") -> tuple[int, int] | None:
    """读取当前可见日期面板顶部显示的年/月。"""
    return driver.execute_script(
        r"""
        const dd = document.querySelectorAll('.ant-picker-dropdown');
        for (const d of dd) {
            if (d.classList.contains('ant-picker-dropdown-hidden')) continue;
            if (d.offsetParent === null) continue;
            const view = d.querySelector('.ant-picker-header-view');
            if (view) {
                const t = (view.textContent || '').trim();
                // 兼容 "2026年5月" / "May 2026" / "2026-05" 等
                const m = t.match(/(\d{4})\D+(\d{1,2})|(\d{1,2})\D+(\d{4})/);
                if (m) {
                    const y = m[1] ? parseInt(m[1]) : parseInt(m[4]);
                    const mo = m[2] ? parseInt(m[2]) : parseInt(m[3]);
                    if (y && mo) return [y, mo];
                }
            }
        }
        return null;
        """
    )


def _click_panel_nav(driver: "webdriver.Chrome", direction: str) -> bool:
    """点击日期面板的上一月 / 下一月按钮。"""
    btn_sel = ".ant-picker-header-prev-btn" if direction == "prev" else ".ant-picker-header-next-btn"
    return bool(driver.execute_script(
        """
        const sel = arguments[0];
        const dd = document.querySelectorAll('.ant-picker-dropdown');
        for (const d of dd) {
            if (d.classList.contains('ant-picker-dropdown-hidden')) continue;
            if (d.offsetParent === null) continue;
            const btn = d.querySelector(sel);
            if (btn) { btn.click(); return true; }
        }
        return false;
        """,
        btn_sel,
    ))


def _click_day_cell(driver: "webdriver.Chrome", target_date: str) -> bool:
    """在已打开的面板中点击 title='YYYY-MM-DD' 的可用日期单元格。"""
    return bool(driver.execute_script(
        """
        const target = arguments[0];
        const dd = document.querySelectorAll('.ant-picker-dropdown');
        for (const d of dd) {
            if (d.classList.contains('ant-picker-dropdown-hidden')) continue;
            if (d.offsetParent === null) continue;
            const cell = d.querySelector(
                '.ant-picker-cell-in-view[title="' + target + '"]:not(.ant-picker-cell-disabled)'
            );
            if (cell) {
                const inner = cell.querySelector('.ant-picker-cell-inner') || cell;
                inner.click();
                return true;
            }
        }
        return false;
        """,
        target_date,
    ))


def _open_picker(driver: "webdriver.Chrome", input_el: "WebElement", timeout: float = 8.0) -> None:
    """点击日期 input 打开 antd 浮层。
    部分 antd 版本要点 .ant-picker 容器才弹出，这里做了两道兜底。
    """
    if _is_picker_dropdown_open(driver):
        # 已经开着（RangePicker 选完起始后焦点切到结束 input 时常见）
        try:
            input_el.click()
        except Exception:
            pass
        return

    try:
        input_el.click()
    except Exception:
        pass

    end = time.time() + timeout
    while time.time() < end:
        if _is_picker_dropdown_open(driver):
            return
        time.sleep(0.15)

    # 兜底：点击 input 的 .ant-picker 包裹层
    try:
        driver.execute_script(
            """
            let p = arguments[0];
            for (let i = 0; i < 6 && p; i++) {
                if ((p.className || '').toString().indexOf('ant-picker') !== -1) {
                    p.click();
                    return;
                }
                p = p.parentElement;
            }
            """,
            input_el,
        )
    except Exception:
        pass

    end = time.time() + timeout
    while time.time() < end:
        if _is_picker_dropdown_open(driver):
            return
        time.sleep(0.15)

    raise TimeoutException("点击日期输入框后未弹出 .ant-picker-dropdown")


def pick_date(
    driver: "webdriver.Chrome",
    input_el: "WebElement",
    target_date: str,
    max_month_jumps: int = 36,
) -> None:
    """在 Ant Design DatePicker / RangePicker 上选择 target_date='YYYY-MM-DD'。

    完整流程：
        1. 已等于目标值 → 跳过
        2. 点击 input 打开浮层
        3. 翻月直到面板顶部年/月与目标一致（最多 max_month_jumps 次）
        4. 点击 .ant-picker-cell[title=target_date]
        5. 轮询校验 input.value 等于 target_date，最多 5s
    """
    cur_value = (input_el.get_attribute("value") or "").strip()
    if cur_value == target_date:
        logger.info("日期 {} 已是当前值，跳过", target_date)
        return

    target_y, target_m, _ = _parse_ymd(target_date)

    # 2) 打开浮层
    _open_picker(driver, input_el, timeout=8.0)

    # 3) 翻月到目标年月
    for _ in range(max_month_jumps):
        ym = _read_panel_year_month(driver)
        if not ym:
            break
        cur_y, cur_m = ym
        if cur_y == target_y and cur_m == target_m:
            break
        direction = "next" if (cur_y, cur_m) < (target_y, target_m) else "prev"
        if not _click_panel_nav(driver, direction):
            logger.warning("未找到翻月按钮（{}），将直接尝试点 cell", direction)
            break
        time.sleep(0.18)

    # 4) 点目标日期格
    if not _click_day_cell(driver, target_date):
        raise TimeoutException(
            f"日期面板未找到 title={target_date} 的可点击 cell（可能仍在错误年月）"
        )

    # 5) 校验 value（轮询，最多 5s 等组件 onChange 走完）
    deadline = time.time() + 5.0
    while time.time() < deadline:
        cur = (input_el.get_attribute("value") or "").strip()
        if cur == target_date:
            logger.info("日期已设置：{}", target_date)
            return
        time.sleep(0.2)

    cur = (input_el.get_attribute("value") or "").strip()
    raise TimeoutException(
        f"日期点击后 value 校验失败：期望 {target_date}, 实际 {cur!r}"
    )


def set_date_range(driver: "webdriver.Chrome", start_date: str, end_date: str) -> None:
    """Ant Design DatePicker / RangePicker 生产级日期范围设置。

    全程 UI 交互（点 input → 翻月 → 点 cell → 校验 value），
    不直接修改 value、不 send_keys、不 removeAttribute('readonly')。
    """
    logger.info("设置日期范围: {} ~ {}", start_date, end_date)

    # 1) 开始日期
    start_el = _find_date_input(driver, _START_DATE_SELECTORS, timeout=15)
    pick_date(driver, start_el, start_date)
    wait_until_page_stable(driver, timeout=15.0, stable_window=1.0, check_table=False)

    # 2) 结束日期（RangePicker 此时浮层一般仍开着，焦点已切到结束 input；
    #    单 DatePicker 则需要重新点开）
    end_el = _find_date_input(driver, _END_DATE_SELECTORS, timeout=15)
    pick_date(driver, end_el, end_date)
    wait_until_page_stable(driver, timeout=15.0, stable_window=1.0, check_table=False)

    # 3) 关闭浮层（如果还开着），点 body 收起
    try:
        if _is_picker_dropdown_open(driver):
            driver.execute_script("document.body && document.body.click();")
    except Exception:
        pass

    logger.info("日期设置完成（开始={}, 结束={}）", start_date, end_date)


def click_query(driver: "webdriver.Chrome") -> None:
    """在所有 .ant-btn-primary 里按文本"查询"挑唯一目标点击，不会误点导出报表等同 class 按钮。"""
    logger.info("点击「查询」按钮")
    safe_click_by_text(
        driver, QUERY_BUTTON_CSS, QUERY_BUTTON_TEXT,
        timeout=15, retries=3,
    )


def click_download(driver: "webdriver.Chrome") -> None:
    """点击「导出报表」按钮（primary + ghost 组合 class，再按文本精确匹配）。"""
    logger.info("点击「导出报表」按钮")
    safe_click_by_text(
        driver, DOWNLOAD_BUTTON_CSS, DOWNLOAD_BUTTON_TEXT,
        timeout=15, retries=3,
    )
