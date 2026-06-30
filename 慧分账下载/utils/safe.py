# -*- coding: utf-8 -*-
"""
通用安全操作封装（生产级）
- wait_element / wait_clickable
- scroll_into_view
- safe_click / safe_input
- hover_then_click（一级菜单悬停展开兜底）
- wait_page_ready
- switch_into_iframe_if_any / back_to_top_frame
- find_first_visible（多 locator 兜底）
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Iterable, Tuple

from loguru import logger
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

if TYPE_CHECKING:
    from selenium import webdriver
    from selenium.webdriver.remote.webelement import WebElement


Locator = Tuple[str, str]


# ============================================================
# 状态驱动核心：网络 hook + 页面稳定判定
# ============================================================
# 在页面里注入 fetch / XHR 计数器，可让"页面稳定"包含"无在途请求"维度。
# 该脚本是 idempotent 的：重复执行无副作用。每次页面跳转后需要重新注入。
NETWORK_HOOK_JS = r"""
(function(){
  if (window.__net_hook_installed__) return;
  window.__net_hook_installed__ = true;
  window.__pending_requests__ = 0;

  // fetch hook
  const _fetch = window.fetch;
  if (_fetch) {
    window.fetch = function() {
      window.__pending_requests__++;
      return _fetch.apply(this, arguments).finally(function(){
        window.__pending_requests__ = Math.max(0, window.__pending_requests__ - 1);
      });
    };
  }

  // XHR hook
  const XHR = window.XMLHttpRequest;
  if (XHR && XHR.prototype) {
    const _open = XHR.prototype.open;
    const _send = XHR.prototype.send;
    XHR.prototype.open = function() {
      this.__net_tracked__ = true;
      return _open.apply(this, arguments);
    };
    XHR.prototype.send = function() {
      if (this.__net_tracked__) {
        window.__pending_requests__++;
        const dec = function() {
          window.__pending_requests__ = Math.max(0, window.__pending_requests__ - 1);
        };
        this.addEventListener('load',  dec);
        this.addEventListener('error', dec);
        this.addEventListener('abort', dec);
      }
      return _send.apply(this, arguments);
    };
  }
})();
"""


def install_network_hook(driver: "webdriver.Chrome") -> None:
    """注入 fetch / XHR 计数器（幂等）。每次页面切换后建议再调一次。"""
    try:
        driver.execute_script(NETWORK_HOOK_JS)
    except Exception:
        pass


# 一次性探针：把"是否稳定"判断完全推到页面里完成，避免多次往返开销。
# 仅检测"正在转圈"的 active 选择器，不检测 .ant-spin-container（永远在 DOM）。
_STABILITY_PROBE_JS = r"""
const loadingSel = arguments[0];
const checkTable = arguments[1];

// 1) document ready
if (document.readyState !== 'complete') {
    return { ok: false, reason: 'readyState=' + document.readyState };
}

// 2) 任何"可见的"loading 元素都视为不稳定
const spins = document.querySelectorAll(loadingSel);
for (const s of spins) {
    if (s.offsetParent !== null) {
        return { ok: false, reason: 'loading-visible' };
    }
}

// 3) 网络请求计数（hook 未注入则跳过）
if (typeof window.__pending_requests__ === 'number' && window.__pending_requests__ > 0) {
    return { ok: false, reason: 'pending=' + window.__pending_requests__ };
}

// 4) 表格行数（用于让调用方判断"行数是否仍在变化"）
let rowCount = -1;
if (checkTable) {
    const tbody = document.querySelector('.ant-table-tbody');
    if (tbody) {
        rowCount = tbody.querySelectorAll('tr').length;
    }
}
return { ok: true, rowCount: rowCount };
"""

# 既包含 antd 转圈的 active 类，也兼容站点自定义 .loading
_LOADING_ACTIVE_SEL = ".ant-spin-spinning, .ant-spin-dot-spin, .loading"


def wait_until_page_stable(
    driver: "webdriver.Chrome",
    timeout: float = 30.0,
    stable_window: float = 2.0,
    check_table: bool = True,
    poll_interval: float = 0.25,
) -> bool:
    """等待页面"持续稳定"达到 stable_window 秒。

    判定（任一不满足都视为不稳定，重新计时）：
        1. document.readyState === 'complete'
        2. .ant-spin-spinning / .ant-spin-dot-spin / .loading 全部不可见
        3. 注入了 hook 时 window.__pending_requests__ === 0（fetch + XHR 在途数）
        4. 当存在 .ant-table-tbody 时，行数在 stable_window 期内不变化

    超时不抛异常，只 warning 后继续——避免一处接口异常把整个流程卡死。
    返回值：是否在 timeout 内达到稳定。
    """
    install_network_hook(driver)

    end_at = time.time() + timeout
    stable_since: float | None = None
    last_row_count: int | None = None

    while time.time() < end_at:
        try:
            result = driver.execute_script(
                _STABILITY_PROBE_JS, _LOADING_ACTIVE_SEL, bool(check_table),
            )
        except Exception:
            result = {"ok": False, "reason": "js-error"}

        if not result or not result.get("ok"):
            stable_since = None
            time.sleep(poll_interval)
            continue

        if check_table:
            cur = int(result.get("rowCount", -1))
            if cur != -1 and last_row_count is not None and cur != last_row_count:
                # 行数变化 → 稳定计时归零（页面在异步刷表）
                stable_since = None
                last_row_count = cur
                time.sleep(poll_interval)
                continue
            last_row_count = cur

        if stable_since is None:
            stable_since = time.time()
        elif time.time() - stable_since >= stable_window:
            return True

        time.sleep(poll_interval)

    logger.warning(
        "wait_until_page_stable 超时（{}s 内未达到连续 {}s 稳定），按已稳定继续",
        int(timeout), stable_window,
    )
    return False


# ------------------------------------------------------------
# 等待
# ------------------------------------------------------------
def wait_page_ready(driver: "webdriver.Chrome", timeout: int = 30) -> None:
    """等待 document.readyState == 'complete'。"""
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def wait_element(driver: "webdriver.Chrome", locator: Locator, timeout: int = 15) -> "WebElement":
    """等元素出现并可见。"""
    return WebDriverWait(driver, timeout).until(EC.visibility_of_element_located(locator))


def wait_clickable(driver: "webdriver.Chrome", locator: Locator, timeout: int = 15) -> "WebElement":
    """等元素可点击。"""
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))


def find_by_text(
    driver: "webdriver.Chrome",
    css_selector: str,
    target_text: str,
    timeout: int = 15,
    poll_interval: float = 0.3,
    exact: bool = True,
    require_clickable: bool = False,
) -> "WebElement":
    """先按 CSS selector 拿一组元素，再按文本筛选，返回第一个文本匹配且可见的元素。

    比"按文本写 XPath"更稳：
      - selector 锁定结构（如 .ant-menu-submenu-title / .ant-btn-primary），
      - 文本只用于在同结构的若干候选里挑一个，不依赖 contains() 的脆弱拼接。

    参数:
        css_selector: 候选元素的 CSS（如 .ant-menu-submenu-title）
        target_text:  期望的文本（默认精确匹配）
        exact:        True=精确等于 / False=包含
        require_clickable: True=要求 is_enabled()
    """
    end_at = time.time() + timeout
    last_seen_texts: list[str] = []
    while True:
        last_seen_texts = []
        try:
            els = driver.find_elements(By.CSS_SELECTOR, css_selector)
        except Exception:
            els = []

        for el in els:
            try:
                if not el.is_displayed():
                    continue
                text = (el.text or "").strip()
                last_seen_texts.append(text)
                matched = (text == target_text) if exact else (target_text in text)
                if not matched:
                    continue
                if require_clickable and not el.is_enabled():
                    continue
                return el
            except StaleElementReferenceException:
                continue
            except Exception:
                continue

        if time.time() >= end_at:
            break
        time.sleep(poll_interval)

    raise TimeoutException(
        f"在 {css_selector} 中找不到文本为 {target_text!r} 的可见元素，"
        f"已观察到的文本样本: {last_seen_texts[:8]}"
    )


def safe_click_by_text(
    driver: "webdriver.Chrome",
    css_selector: str,
    target_text: str,
    timeout: int = 15,
    retries: int = 3,
    exact: bool = True,
    *,
    wait_after: bool = True,
    stable_timeout: float = 30.0,
    stable_window: float = 2.0,
) -> "WebElement":
    """find_by_text + scroll + 点击 + 自动等待页面稳定。"""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            el = find_by_text(
                driver, css_selector, target_text,
                timeout=timeout, require_clickable=True, exact=exact,
            )
            scroll_into_view(driver, el)
            try:
                el.click()
            except (ElementClickInterceptedException, ElementNotInteractableException):
                driver.execute_script("arguments[0].click();", el)
            if wait_after:
                wait_until_page_stable(driver, timeout=stable_timeout, stable_window=stable_window)
            return el
        except StaleElementReferenceException as e:
            last_exc = e
            time.sleep(0.4)
            continue
        except TimeoutException as e:
            last_exc = e
            if attempt == retries:
                raise
            time.sleep(0.4)
    raise RuntimeError(f"safe_click_by_text 未知失败: {last_exc}")


def find_first_visible(
    driver: "webdriver.Chrome",
    locators: Iterable[Locator],
    timeout: int = 10,
    require_clickable: bool = False,
    poll_interval: float = 0.3,
) -> "WebElement":
    """并行轮询多个 locator，返回最先满足条件的元素。

    与旧实现的关键区别：
        旧：对每个 locator 串行等满 timeout，N 个 locator 最坏耗时 N*timeout。
        新：所有 locator 共用同一个 timeout，每 poll_interval 把全部 locator 扫一遍，
            谁先出现就返回谁；总耗时上限 = timeout。

    这是登录全流程"明明首选 selector 没问题，却跑得很慢"的根因修复：
        - 首选命中 → 一两次轮询（<1s）就返回；
        - 首选偶尔渲染稍慢，备用 selector 在 0.x 秒内出现也能立刻返回；
        - 全都没有 → 完整等 timeout 后抛出，行为与旧版一致。
    """
    locators = list(locators)
    if not locators:
        raise TimeoutException("locators 为空")

    end_at = time.time() + timeout
    last_exc: Exception | None = None

    while True:
        for by, sel in locators:
            try:
                els = driver.find_elements(by, sel)
            except StaleElementReferenceException as e:
                last_exc = e
                continue
            except Exception as e:
                last_exc = e
                continue

            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    if require_clickable and not el.is_enabled():
                        continue
                    return el
                except StaleElementReferenceException:
                    continue
                except Exception:
                    continue

        if time.time() >= end_at:
            break
        time.sleep(poll_interval)

    raise TimeoutException(f"所有定位策略均失败: {locators}, last: {last_exc}")


# ------------------------------------------------------------
# iframe 兜底
# ------------------------------------------------------------
def switch_into_iframe_if_any(driver: "webdriver.Chrome", target_locator: Locator, timeout: int = 5) -> bool:
    """目标元素在主文档找不到时，逐个切入 iframe 寻找。"""
    by, sel = target_locator
    driver.switch_to.default_content()

    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, sel)))
        return True
    except TimeoutException:
        pass

    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    for frame in iframes:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(frame)
            WebDriverWait(driver, 2).until(EC.presence_of_element_located((by, sel)))
            return True
        except (TimeoutException, StaleElementReferenceException):
            continue

    driver.switch_to.default_content()
    return False


def back_to_top_frame(driver: "webdriver.Chrome") -> None:
    try:
        driver.switch_to.default_content()
    except Exception:
        pass


# ------------------------------------------------------------
# 滚动
# ------------------------------------------------------------
def scroll_into_view(driver: "webdriver.Chrome", el: "WebElement") -> None:
    """把元素滚到视口中央，避开固定头/底栏遮挡。"""
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'center'});", el
        )
    except Exception:
        pass


# ------------------------------------------------------------
# 安全点击 / 安全输入
# ------------------------------------------------------------
def safe_click(
    driver: "webdriver.Chrome",
    locators: Iterable[Locator] | "WebElement",
    timeout: int = 15,
    retries: int = 3,
    *,
    wait_after: bool = True,
    stable_timeout: float = 30.0,
    stable_window: float = 2.0,
) -> "WebElement":
    """安全点击 + 自动等待页面稳定（状态驱动核心入口之一）。

    步骤：
        1. 等待 / 滚动 / 普通点击 + 拦截兜底 + stale 重试
        2. 点完后默认 wait_until_page_stable，把"点击 + 等待页面稳定"封装为
           单一原子动作，业务层无需再各自加等待。

    参数：
        wait_after:    点完后是否自动等稳定，默认 True
        stable_timeout/stable_window: 透传给 wait_until_page_stable
    """
    for attempt in range(1, retries + 1):
        try:
            el = locators if hasattr(locators, "click") \
                else find_first_visible(driver, locators, timeout=timeout, require_clickable=True)

            scroll_into_view(driver, el)

            try:
                el.click()
            except (ElementClickInterceptedException, ElementNotInteractableException):
                driver.execute_script("arguments[0].click();", el)

            if wait_after:
                wait_until_page_stable(driver, timeout=stable_timeout, stable_window=stable_window)
            return el

        except StaleElementReferenceException:
            if attempt == retries:
                raise
            time.sleep(0.4)
        except TimeoutException:
            if attempt == retries:
                raise
            time.sleep(0.4)
    raise RuntimeError("safe_click 未知失败")


SET_VALUE_JS = """
const el = arguments[0];
const value = arguments[1];
const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value').set;
nativeSetter.call(el, value);
el.dispatchEvent(new Event('input',  { bubbles: true }));
el.dispatchEvent(new Event('change', { bubbles: true }));
el.dispatchEvent(new Event('blur',   { bubbles: true }));
"""


def safe_input(
    driver: "webdriver.Chrome",
    locators: Iterable[Locator] | "WebElement",
    value: str,
    timeout: int = 15,
    retries: int = 3,
    use_js: bool = False,
    *,
    wait_after: bool = True,
    stable_timeout: float = 15.0,
    stable_window: float = 1.0,
) -> "WebElement":
    """安全输入 + 自动等待页面稳定。

    输入触发的稳定窗口默认比 click 短（1s），因为只是字段赋值，通常不会引起
    后端请求；但仍要等 antd 校验类异步收尾。
    """
    for attempt in range(1, retries + 1):
        try:
            el = locators if hasattr(locators, "send_keys") \
                else find_first_visible(driver, locators, timeout=timeout, require_clickable=True)

            scroll_into_view(driver, el)

            if use_js:
                driver.execute_script(SET_VALUE_JS, el, value)
            else:
                try:
                    el.clear()
                except Exception:
                    pass
                el.send_keys(value)

            if wait_after:
                wait_until_page_stable(driver, timeout=stable_timeout, stable_window=stable_window)
            return el

        except StaleElementReferenceException:
            if attempt == retries:
                raise
            time.sleep(0.4)
        except TimeoutException:
            if attempt == retries:
                raise
            time.sleep(0.4)
    raise RuntimeError("safe_input 未知失败")


def safe_click_checkbox(
    driver: "webdriver.Chrome",
    inner_selector: str = ".ant-checkbox-input",
    checked_class: str = "ant-checkbox-checked",
    timeout: int = 10,
    retries: int = 3,
    *,
    wait_after: bool = True,
    stable_timeout: float = 10.0,
    stable_window: float = 1.0,
) -> bool:
    """安全勾选 Ant Design 复选框。

    兼容两种 selector：
        - .ant-checkbox-input  → 真实的隐藏 <input type="checkbox">，opacity:0 不可点
                                 → 用 JS click 触发 native 事件，antd 会响应
        - .ant-checkbox-inner  → 可见的视觉方块，普通 click 即可
        - .ant-checkbox        → 包裹层，普通 click 也行

    流程:
        1. 先校验是否已勾选（外层容器 className 是否含 ant-checkbox-checked）；
           已勾选则直接返回，避免重复点击。
        2. 用 presence_of_element_located 等元素出现（不要求"可点击"，
           因为 .ant-checkbox-input 是隐藏 input，永远等不到 clickable）。
        3. 优先 JS click（隐藏元素也能触发）；普通 click 作为后备。
        4. 点击后再次校验，未生效自动重试，最多 retries 次。
    """
    def _is_checked() -> bool:
        try:
            return bool(driver.execute_script(
                """
                const inner = document.querySelector(arguments[0]);
                if (!inner) return false;
                // 隐藏 input 自身有 checked 属性，先看它
                if (inner.tagName === 'INPUT' && inner.checked) return true;
                let p = inner.parentElement;
                for (let i = 0; i < 4 && p; i++) {
                    if ((p.className || '').toString().indexOf(arguments[1]) !== -1) return true;
                    p = p.parentElement;
                }
                return false;
                """,
                inner_selector,
                checked_class,
            ))
        except Exception:
            return False

    if _is_checked():
        return True

    for attempt in range(1, retries + 1):
        try:
            # 用 presence 而非 clickable，兼容隐藏 input
            el = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, inner_selector))
            )
            scroll_into_view(driver, el)
            time.sleep(0.2)

            # 隐藏 input 直接 JS click 最稳；可见元素 JS click 也兼容
            clicked = False
            try:
                driver.execute_script("arguments[0].click();", el)
                clicked = True
            except Exception:
                pass

            if not clicked:
                try:
                    el.click()
                except (ElementClickInterceptedException, ElementNotInteractableException):
                    driver.execute_script("arguments[0].click();", el)

            if wait_after:
                wait_until_page_stable(driver, timeout=stable_timeout, stable_window=stable_window)

            if _is_checked():
                return True

        except StaleElementReferenceException:
            if attempt == retries:
                return False
            time.sleep(0.4)
            continue
        except TimeoutException:
            if attempt == retries:
                return False
            time.sleep(0.4)
            continue

    return _is_checked()


def hover_then_click(
    driver: "webdriver.Chrome",
    locators: Iterable[Locator],
    timeout: int = 15,
) -> "WebElement":
    """悬停 → 等待目标可点击 → 点击。用于一级菜单 hover 才展开二级菜单的场景。"""
    el = find_first_visible(driver, locators, timeout=timeout, require_clickable=True)
    scroll_into_view(driver, el)
    try:
        ActionChains(driver).move_to_element(el).pause(0.3).perform()
    except Exception:
        pass
    return safe_click(driver, el, timeout=timeout)
