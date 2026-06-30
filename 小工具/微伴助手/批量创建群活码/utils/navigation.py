# -*- coding: utf-8 -*-
"""
页面导航操作：在微伴助手后台中点击顶部导航菜单。
"""
from __future__ import annotations

import time
from collections.abc import Callable

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# 企微码导航：同一元素带两个 class，或外层容器内含 EnterpriseMicrocode 图标
ENTERPRISE_MICROCODE_COMBINED_SELECTOR = (
    ".dashboard-navigation-bar-nav-category-dd.fill_EnterpriseMicrocode"
)
ENTERPRISE_MICROCODE_NAV_SELECTOR = (
    ".dashboard-navigation-bar-nav-category-dd .fill_EnterpriseMicrocode"
)
NAV_DROPDOWN_SELECTOR = ".dashboard-navigation-bar-nav-category-dd"

# 按优先级依次尝试的选择器
ENTERPRISE_MICROCODE_SELECTORS = (
    ENTERPRISE_MICROCODE_COMBINED_SELECTOR,
    ENTERPRISE_MICROCODE_NAV_SELECTOR,
)

# 企微码菜单内「自动拉群」按钮
AUTO_GROUP_BUTTON_TEXT = "自动拉群"
AME_LINK_BUTTON_SELECTOR = (
    ".ame-btn.ame-btn-link.ame-btn-block.ame-btn-md"
    ".ame-btn-link-color-default.pc.button.__ameButton__"
)
AUTO_GROUP_BUTTON_XPATH = (
    "//*[contains(@class,'ame-btn') and contains(@class,'__ameButton__')"
    f" and contains(normalize-space(.), '{AUTO_GROUP_BUTTON_TEXT}')]"
)

# 自动拉群页面「新建拉群」按钮
CREATE_AUTO_GROUP_BUTTON_TEXT = "新建拉群"
CREATE_AUTO_GROUP_BUTTON_XPATH = (
    "//*[contains(normalize-space(.), '新建拉群') and "
    "(self::button or self::a or contains(@class,'ame-btn'))]"
)

# 新建拉群表单「直接入群」Tab
DIRECT_JOIN_GROUP_BUTTON_TEXT = "直接入群"
DIRECT_JOIN_GROUP_TAB_SELECTOR = (
    ".tab-for-create-item.join-group-type-tab-item.__tabsForCreateItem__"
)
DIRECT_JOIN_GROUP_TAB_XPATH = (
    "//*[contains(@class,'tab-for-create-item') "
    "and contains(@class,'join-group-type-tab-item') "
    "and contains(@class,'__tabsForCreateItem__')]"
)
DIRECT_JOIN_GROUP_TAB_WITH_TEXT_XPATH = (
    "//*[contains(@class,'tab-for-create-item') "
    "and contains(@class,'join-group-type-tab-item') "
    "and contains(@class,'__tabsForCreateItem__') "
    f"and contains(normalize-space(.), '{DIRECT_JOIN_GROUP_BUTTON_TEXT}')]"
)
DIRECT_JOIN_GROUP_BUTTON_XPATH = (
    "//*[contains(normalize-space(.), '直接入群') and "
    "(self::button or self::a or contains(@class,'ame-btn') or "
    "contains(@class,'radio') or contains(@class,'ame-radio'))]"
)

# 直接入群表单内「企微活码拉群」单选项
WEWORK_LIVE_CODE_GROUP_RADIO_TEXT = "企微活码拉群"
AME_RADIO_WRAPPER_SELECTOR = ".ame-radio-wrapper.__ameRadio__"
WEWORK_LIVE_CODE_GROUP_RADIO_WRAPPER_XPATH = (
    "//*[contains(@class,'ame-radio-wrapper') and contains(@class,'__ameRadio__') "
    f"and contains(normalize-space(.), '{WEWORK_LIVE_CODE_GROUP_RADIO_TEXT}')]"
)
WEWORK_LIVE_CODE_GROUP_RADIO_INPUT_XPATH = (
    "//*[contains(@class,'ame-radio-wrapper') and contains(@class,'__ameRadio__') "
    f"and contains(normalize-space(.), '{WEWORK_LIVE_CODE_GROUP_RADIO_TEXT}')]"
    "//input[contains(@class,'ame-radio-input')]"
)


def click_enterprise_microcode_nav(driver: WebDriver, timeout: int = 30) -> None:
    """进入页面后点击顶部「企微码」导航下拉项。

    参数:
        driver: 已打开微伴助手页面的 Chrome WebDriver
        timeout: 最长等待秒数

    异常:
        TimeoutException: 超时仍未找到可点击元素
    """
    wait = WebDriverWait(driver, timeout)

    for selector in ENTERPRISE_MICROCODE_SELECTORS:
        try:
            element = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            )
            _safe_click(driver, element)
            print("已点击「企微码」导航。")
            return
        except TimeoutException:
            continue

    # 兜底：遍历所有导航下拉，找到内含 EnterpriseMicrocode 图标的那一项
    dropdowns = driver.find_elements(By.CSS_SELECTOR, NAV_DROPDOWN_SELECTOR)
    for dropdown in dropdowns:
        icons = dropdown.find_elements(By.CSS_SELECTOR, ".fill_EnterpriseMicrocode")
        if icons:
            _safe_click(driver, dropdown)
            print("已点击「企微码」导航（兜底匹配）。")
            return

    raise TimeoutException(
        "未找到「企微码」导航元素，请检查页面是否已登录且导航栏已加载。"
    )


def click_ame_link_button(driver: WebDriver, timeout: int = 30) -> None:
    """点击企微码菜单中的「自动拉群」按钮。"""
    wait = WebDriverWait(driver, timeout)

    # 企微码下拉菜单展开后，先等待「自动拉群」按钮可见
    try:
        wait.until(
            EC.visibility_of_element_located((By.XPATH, AUTO_GROUP_BUTTON_XPATH))
        )
    except TimeoutException:
        pass

    def _locate_auto_group_button(current_driver: WebDriver):
        for button in current_driver.find_elements(By.XPATH, AUTO_GROUP_BUTTON_XPATH):
            text = button.text.replace("\n", "").replace(" ", "")
            if AUTO_GROUP_BUTTON_TEXT in text and button.is_displayed():
                return button
        for button in current_driver.find_elements(
            By.CSS_SELECTOR, AME_LINK_BUTTON_SELECTOR
        ):
            text = button.text.replace("\n", "").replace(" ", "")
            if AUTO_GROUP_BUTTON_TEXT in text and button.is_displayed():
                return button
        return None

    _click_with_stale_retry(
        driver,
        _locate_auto_group_button,
        timeout=timeout,
        description=AUTO_GROUP_BUTTON_TEXT,
    )
    print("已点击「自动拉群」菜单。")


def click_create_auto_group_button(driver: WebDriver, timeout: int = 30) -> None:
    """点击自动拉群页面中的「新建拉群」按钮。

    参数:
        driver: 已打开微伴助手页面的 Chrome WebDriver
        timeout: 最长等待秒数

    异常:
        TimeoutException: 超时仍未找到可点击元素
    """
    wait = WebDriverWait(driver, timeout)

    try:
        button = wait.until(
            EC.element_to_be_clickable((By.XPATH, CREATE_AUTO_GROUP_BUTTON_XPATH))
        )
        _safe_click(driver, button)
        print("已点击「新建拉群」按钮。")
        return
    except TimeoutException:
        pass

    def _find_create_auto_group_button(current_driver: WebDriver):
        candidates = current_driver.find_elements(
            By.XPATH, "//button|//a|//*[contains(@class,'ame-btn')]"
        )
        for button in candidates:
            text = button.text.replace("\n", "").replace(" ", "")
            if CREATE_AUTO_GROUP_BUTTON_TEXT in text and button.is_displayed():
                return button
        return False

    button = wait.until(_find_create_auto_group_button)
    _safe_click(driver, button)
    print("已点击「新建拉群」按钮（兜底匹配）。")


def click_direct_join_group_option(driver: WebDriver, timeout: int = 30) -> None:
    """点击新建拉群页面中的「直接入群」Tab。

    参数:
        driver: 已打开微伴助手页面的 Chrome WebDriver
        timeout: 最长等待秒数

    异常:
        TimeoutException: 超时仍未找到可点击元素
    """
    wait = WebDriverWait(driver, timeout)

    # 优先：按 Tab class 精确匹配
    for selector in (
        DIRECT_JOIN_GROUP_TAB_WITH_TEXT_XPATH,
        DIRECT_JOIN_GROUP_TAB_SELECTOR,
        DIRECT_JOIN_GROUP_TAB_XPATH,
    ):
        try:
            by = By.CSS_SELECTOR if not selector.startswith("//") else By.XPATH
            option = wait.until(EC.element_to_be_clickable((by, selector)))
            _safe_click(driver, option)
            print("已点击「直接入群」Tab。")
            return
        except TimeoutException:
            continue

    # 次选：仅按文字匹配
    try:
        option = wait.until(
            EC.element_to_be_clickable((By.XPATH, DIRECT_JOIN_GROUP_BUTTON_XPATH))
        )
        _safe_click(driver, option)
        print("已点击「直接入群」选项。")
        return
    except TimeoutException:
        pass

    def _find_direct_join_group_option(current_driver: WebDriver):
        for option in current_driver.find_elements(
            By.CSS_SELECTOR, DIRECT_JOIN_GROUP_TAB_SELECTOR
        ):
            if option.is_displayed():
                return option
        candidates = current_driver.find_elements(
            By.XPATH,
            "//button|//a|//label|//*[contains(@class,'ame-btn')]"
            "|//*[contains(@class,'radio')]|//*[contains(@class,'ame-radio')]",
        )
        for option in candidates:
            text = option.text.replace("\n", "").replace(" ", "")
            if DIRECT_JOIN_GROUP_BUTTON_TEXT in text and option.is_displayed():
                return option
        return False

    option = wait.until(_find_direct_join_group_option)
    _safe_click(driver, option)
    print("已点击「直接入群」选项（兜底匹配）。")


def click_ame_radio_input(driver: WebDriver, timeout: int = 30) -> None:
    """点击「企微活码拉群」单选项。

    说明：
        在 ame-radio-wrapper 中按文字匹配目标项，优先点 input，失败则点 wrapper。

    参数:
        driver: 已打开微伴助手页面的 Chrome WebDriver
        timeout: 最长等待秒数

    异常:
        TimeoutException: 超时仍未找到可点击元素
    """
    wait = WebDriverWait(driver, timeout)

    # 优先：定位包含目标文字的 wrapper，再点击内部 input
    try:
        wrapper = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, WEWORK_LIVE_CODE_GROUP_RADIO_WRAPPER_XPATH)
            )
        )
        inputs = wrapper.find_elements(By.CSS_SELECTOR, ".ame-radio-input")
        if inputs:
            _safe_click(driver, inputs[0])
            print("已选择「企微活码拉群」。")
            return
        _safe_click(driver, wrapper)
        print("已选择「企微活码拉群」（点击 wrapper）。")
        return
    except TimeoutException:
        pass

    # 次选：直接定位目标 input
    try:
        radio = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, WEWORK_LIVE_CODE_GROUP_RADIO_INPUT_XPATH)
            )
        )
        _safe_click(driver, radio)
        print("已选择「企微活码拉群」。")
        return
    except TimeoutException:
        pass

    # 兜底：遍历所有单选 wrapper，按文字筛选
    def _find_wework_live_code_group_radio(current_driver: WebDriver):
        for wrapper in current_driver.find_elements(
            By.CSS_SELECTOR, AME_RADIO_WRAPPER_SELECTOR
        ):
            text = wrapper.text.replace("\n", "").replace(" ", "")
            if WEWORK_LIVE_CODE_GROUP_RADIO_TEXT in text and wrapper.is_displayed():
                return wrapper
        return False

    wrapper = wait.until(_find_wework_live_code_group_radio)
    inputs = wrapper.find_elements(By.CSS_SELECTOR, ".ame-radio-input")
    if inputs:
        _safe_click(driver, inputs[0])
        print("已选择「企微活码拉群」（兜底匹配）。")
        return

    _safe_click(driver, wrapper)
    print("已选择「企微活码拉群」（兜底点击 wrapper）。")


def _safe_click(
    driver: WebDriver,
    element,
    *,
    relocate: Callable[[], object] | None = None,
    retries: int = 3,
) -> None:
    """先滚动到可见区域再点击；遇到 stale 时可重新定位后重试。"""
    current = element
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                current,
            )
            try:
                current.click()
            except StaleElementReferenceException:
                raise
            except Exception:
                driver.execute_script("arguments[0].click();", current)
            return
        except StaleElementReferenceException as exc:
            last_error = exc
            if relocate is None or attempt >= retries:
                raise
            time.sleep(0.4)
            current = relocate()

    if last_error is not None:
        raise last_error


def _click_with_stale_retry(
    driver: WebDriver,
    locate: Callable[[WebDriver], object | None],
    *,
    timeout: int = 30,
    description: str = "目标元素",
) -> None:
    """循环定位并点击，直到成功或超时（专门处理 Vue 页面 DOM 刷新）。"""
    deadline = time.time() + timeout
    last_error: Exception | None = None

    while time.time() < deadline:
        element = locate(driver)
        if element is None:
            time.sleep(0.3)
            continue

        try:
            _safe_click(driver, element, relocate=lambda: locate(driver))
            return
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.3)
            continue

    if last_error is not None:
        raise TimeoutException(
            f"点击「{description}」时元素多次过期，请确认页面菜单已完全展开。"
        ) from last_error

    raise TimeoutException(f"超时未找到可点击的「{description}」。")
