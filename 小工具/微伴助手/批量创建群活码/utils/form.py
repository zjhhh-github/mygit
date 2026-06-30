# -*- coding: utf-8 -*-
"""
表单操作：在微伴助手创建页填写表单字段。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# 下载目录（与 browser.py 保持一致）
_DOWNLOAD_DIR = Path(r"C:\Users\LENOVO\Desktop\专属带领群二维码")
# 等待下载完成的最长秒数（二维码图片通常很小，30 秒足够）
_DOWNLOAD_WAIT_SECONDS = 30

QRCODE_NAME_LABEL = "二维码名称"
MANUAL_SETTING_TEXT = "手动设置"
SELECT_GROUP_CHAT_TEXT = "选择群聊"
ADD_GROUP_CHAT_TEXT = "添加群聊"
GROUP_NAME_LABEL = "群名称"
CONFIRM_BUTTON_TEXT = "确定"
AME_CHECKBOX_INPUT_SELECTOR = ".ame-checkbox-input"
GROUP_SELECT_DIALOG_SELECTOR = "[class*='ame-select-group-table-dialog']"
GROUP_SELECT_DIALOG_OVERLAY_SELECTOR = "[class*='ame-select-group-table-dialog__full']"
GROUP_NAME_SEARCH_INPUT_SELECTOR = "input.ame-input[placeholder*='搜索']"
AUTO_CREATE_NEW_GROUP_TEXT = "自动创建新群"
AUTO_CREATE_NEW_GROUP_SWITCH_ON_XPATH = (
    "//*[contains(normalize-space(.), '自动创建新群')]"
    "/ancestor::*[contains(@class,'ame-form-item') or contains(@class,'form-item')"
    " or contains(@class,'switch')][1]"
    "//*[contains(@class,'ame-switch-track') and contains(@class,'ame-switch-checked')]"
)
AUTO_CREATE_NEW_GROUP_SWITCH_TRACK_FOLLOW_XPATH = (
    "//*[contains(normalize-space(.), '自动创建新群')]"
    "/following::*[contains(@class,'ame-switch-track') and contains(@class,'ame-switch-checked')][1]"
)
CREATE_LIVE_CODE_BUTTON_TEXT = "创建活码"
DOWNLOAD_BUTTON_TEXT = "下载"
MANUAL_SETTING_RADIO_WRAPPER_XPATH = (
    "//*[contains(@class,'ame-radio-wrapper') "
    f"and contains(normalize-space(.), '{MANUAL_SETTING_TEXT}')]"
)
MANUAL_SETTING_RADIO_INPUT_XPATH = (
    "//*[contains(@class,'ame-radio-wrapper') "
    f"and contains(normalize-space(.), '{MANUAL_SETTING_TEXT}')]"
    "//input[contains(@class,'ame-radio-input')]"
)
MANUAL_SETTING_AME_RADIO_INPUT_XPATH = (
    "//*[contains(@class,'ame-radio') "
    f"and contains(normalize-space(.), '{MANUAL_SETTING_TEXT}')]"
    "//input[contains(@class,'ame-radio-input')]"
)
AME_RADIO_WRAPPER_SELECTOR = ".ame-radio-wrapper.__ameRadio__"
# 「二维码名称」输入框定位（按优先级）
QRCODE_NAME_INPUT_SELECTORS: tuple[tuple[str, str], ...] = (
    (
        By.XPATH,
        "//*[contains(normalize-space(.), '二维码名称')]"
        "/following::input[contains(@class,'ame-input')][1]",
    ),
    (
        By.XPATH,
        "//label[contains(normalize-space(.), '二维码名称')]"
        "/following::input[1]",
    ),
    (By.XPATH, "//input[contains(@placeholder, '二维码名称')]"),
    (
        By.XPATH,
        "//*[contains(normalize-space(.), '二维码名称')]"
        "/ancestor::*[contains(@class,'ame-form-item')][1]//input",
    ),
    (By.CSS_SELECTOR, "input[placeholder*='二维码名称']"),
)


def fill_qrcode_name(driver: WebDriver, name: str, timeout: int = 30) -> None:
    """在「二维码名称」输入框填入指定内容。

    参数:
        driver: 已打开创建页的 Chrome WebDriver
        name: 要填入的二维码名称（通常来自数据库 nick_name）
        timeout: 最长等待秒数

    异常:
        TimeoutException: 超时仍未找到输入框
        ValueError: name 为空
    """
    text = name.strip()
    if not text:
        raise ValueError("二维码名称不能为空")

    wait = WebDriverWait(driver, timeout)
    last_error: Exception | None = None

    for by, selector in QRCODE_NAME_INPUT_SELECTORS:
        try:
            input_el = wait.until(EC.element_to_be_clickable((by, selector)))
            _fill_input(driver, input_el, text)
            print(f"已填写二维码名称：{text}")
            return
        except TimeoutException as exc:
            last_error = exc
            continue

    raise TimeoutException(
        f"未找到「{QRCODE_NAME_LABEL}」输入框，请确认页面已加载完成。"
    ) from last_error


def select_manual_setting(driver: WebDriver, timeout: int = 30) -> None:
    """选择「手动设置」单选项（ame-radio 内的 ame-radio-input）。"""
    wait = WebDriverWait(driver, timeout)

    # 优先：定位包含「手动设置」文字的 ame-radio-wrapper，再点内部 input
    try:
        wrapper = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, MANUAL_SETTING_RADIO_WRAPPER_XPATH)
            )
        )
        inputs = wrapper.find_elements(By.CSS_SELECTOR, ".ame-radio-input")
        if inputs:
            _safe_click(driver, inputs[0])
            print("已选择「手动设置」。")
            return
        _safe_click(driver, wrapper)
        print("已选择「手动设置」（点击 wrapper）。")
        return
    except TimeoutException:
        pass

    # 次选：ame-radio 结构下直接定位 input
    for xpath in (MANUAL_SETTING_RADIO_INPUT_XPATH, MANUAL_SETTING_AME_RADIO_INPUT_XPATH):
        try:
            radio = wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
            _safe_click(driver, radio)
            print("已选择「手动设置」。")
            return
        except TimeoutException:
            continue

    # 兜底：遍历 ame-radio-wrapper，按文字筛选
    def _find_manual_setting_radio(current_driver: WebDriver):
        for wrapper in current_driver.find_elements(
            By.CSS_SELECTOR, AME_RADIO_WRAPPER_SELECTOR
        ):
            text = wrapper.text.replace("\n", "").replace(" ", "")
            if MANUAL_SETTING_TEXT in text and wrapper.is_displayed():
                return wrapper
        for wrapper in current_driver.find_elements(
            By.CSS_SELECTOR, ".ame-radio-wrapper"
        ):
            text = wrapper.text.replace("\n", "").replace(" ", "")
            if MANUAL_SETTING_TEXT in text and wrapper.is_displayed():
                return wrapper
        return False

    wrapper = wait.until(_find_manual_setting_radio)
    inputs = wrapper.find_elements(By.CSS_SELECTOR, ".ame-radio-input")
    if inputs:
        _safe_click(driver, inputs[0])
        print("已选择「手动设置」（兜底匹配）。")
        return

    _safe_click(driver, wrapper)
    print("已选择「手动设置」（兜底点击 wrapper）。")


def click_select_group_chat(driver: WebDriver, timeout: int = 30) -> None:
    """点击「选择群聊」按钮，并等待选择群聊弹窗出现（手动设置模式）。"""
    _click_by_text(driver, SELECT_GROUP_CHAT_TEXT, timeout=timeout)
    wait = WebDriverWait(driver, timeout)
    _wait_group_select_dialog(driver, wait)
    print("已点击「选择群聊」，弹窗已打开。")


def click_add_group_chat(driver: WebDriver, timeout: int = 30) -> None:
    """点击「添加群聊」按钮，并等待选择群聊弹窗出现。"""
    _click_by_text(driver, ADD_GROUP_CHAT_TEXT, timeout=timeout)
    wait = WebDriverWait(driver, timeout)
    _wait_group_select_dialog(driver, wait)
    print("已点击「添加群聊」，弹窗已打开。")


def search_and_select_group(
    driver: WebDriver, group_name: str, timeout: int = 45
) -> None:
    """在弹窗群名称搜索框搜索并勾选匹配结果。"""
    text = group_name.strip()
    if not text:
        raise ValueError("群名称不能为空")

    wait = WebDriverWait(driver, timeout, poll_frequency=0.5)
    dialog = _wait_group_select_dialog(driver, wait)
    search_input = _find_group_name_input(dialog, wait)
    _search_group_name(driver, search_input, text)
    print(f"已在群名称框搜索：{text}")

    target = wait.until(lambda d: _find_group_select_target(d, text, dialog))
    _safe_click(driver, target)
    print(f"已勾选群聊：{text}")


def click_confirm_button(driver: WebDriver, timeout: int = 30) -> None:
    """点击弹窗中的「确定」按钮。"""
    wait = WebDriverWait(driver, timeout)
    dialog = _wait_group_select_dialog(driver, wait)

    confirm_selectors: tuple[tuple[str, str], ...] = (
        (
            By.XPATH,
            ".//button[contains(normalize-space(.), '确定')]"
            "[contains(@class,'ame-btn')]",
        ),
        (By.XPATH, ".//button[contains(normalize-space(.), '确定')]"),
        (
            By.XPATH,
            ".//*[contains(@class,'ame-btn') and contains(normalize-space(.), '确定')]",
        ),
    )

    for by, selector in confirm_selectors:
        try:
            button = dialog.find_element(by, selector)
            if button.is_displayed():
                _safe_click(driver, button)
                print("已点击「确定」。")
                return
        except Exception:
            continue

    _click_by_text_in_root(driver, dialog, CONFIRM_BUTTON_TEXT)
    print("已点击「确定」（兜底匹配）。")


def disable_auto_create_new_group(driver: WebDriver, timeout: int = 30) -> None:
    """关闭「自动创建新群」开关（从 ame-switch-checked 切为关闭）。"""
    wait = WebDriverWait(driver, timeout)

    for xpath in (
        AUTO_CREATE_NEW_GROUP_SWITCH_ON_XPATH,
        AUTO_CREATE_NEW_GROUP_SWITCH_TRACK_FOLLOW_XPATH,
    ):
        try:
            track = wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
            if _is_switch_checked(track):
                _safe_click(driver, track)
                time.sleep(0.3)
                if _is_switch_checked(track):
                    switch_btn = track.find_element(
                        By.XPATH,
                        "./ancestor::*[contains(@class,'ame-switch')][1]",
                    )
                    _safe_click(driver, switch_btn)
                print("已关闭「自动创建新群」开关。")
                return
        except TimeoutException:
            continue

    if _find_auto_create_new_group_switch(driver):
        print("「自动创建新群」开关已是关闭状态。")
        return

    raise TimeoutException("未找到「自动创建新群」开关，请确认页面已加载完成。")


def click_create_live_code_button(driver: WebDriver, timeout: int = 30) -> None:
    """点击页面底部的「创建活码」按钮。"""
    wait = WebDriverWait(driver, timeout)

    button_selectors: tuple[tuple[str, str], ...] = (
        (
            By.XPATH,
            "//button[contains(normalize-space(.), '创建活码')]"
            "[contains(@class,'ame-btn')]",
        ),
        (
            By.XPATH,
            "//*[contains(@class,'ame-btn') and contains(normalize-space(.), '创建活码')]",
        ),
        (By.XPATH, "//button[contains(normalize-space(.), '创建活码')]"),
        (By.XPATH, "//a[contains(normalize-space(.), '创建活码')]"),
    )

    for by, selector in button_selectors:
        try:
            button = wait.until(EC.element_to_be_clickable((by, selector)))
            _safe_click(driver, button)
            print("已点击「创建活码」。")
            return
        except TimeoutException:
            continue

    _click_by_text(driver, CREATE_LIVE_CODE_BUTTON_TEXT, timeout=timeout)
    print("已点击「创建活码」（兜底匹配）。")


def wait_download_complete(timeout: int = _DOWNLOAD_WAIT_SECONDS) -> Path:
    """等待下载目录出现新的非临时文件，并返回该文件路径。

    说明：
    - Chrome 下载中文件以 .crdownload 为后缀，下载完成后后缀消失
    - 每隔 0.5 秒扫描一次下载目录，直到超时或发现完整文件
    - 返回最新修改的那个非临时文件（即刚下载完的文件）

    异常:
        TimeoutError: 超时仍未检测到下载完成的文件
    """
    _DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    deadline = time.time() + timeout
    while time.time() < deadline:
        # 找出目录内所有非临时文件（排除 .crdownload）
        completed_files = [
            f for f in _DOWNLOAD_DIR.iterdir()
            if f.is_file() and not f.suffix.lower() == ".crdownload"
        ]
        if completed_files:
            # 返回最新修改的文件（即刚下载完成的）
            newest = max(completed_files, key=lambda f: f.stat().st_mtime)
            print(f"检测到下载完成：{newest.name}")
            return newest
        time.sleep(0.5)

    raise TimeoutError(
        f"等待下载超时（{timeout}s），请检查 Chrome 下载目录：{_DOWNLOAD_DIR}"
    )


def click_first_download_button(driver: WebDriver, timeout: int = 45) -> Path:
    """创建成功后，点击列表第一条记录的「下载」，并等待文件下载完成。

    返回:
        已下载完成的文件路径（位于 专属带领群二维码 目录内）
    """
    wait = WebDriverWait(driver, timeout, poll_frequency=0.5)

    # 记录点击前目录内已有的文件，用于识别新增的下载文件
    _DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    files_before = {
        f for f in _DOWNLOAD_DIR.iterdir()
        if f.is_file() and not f.suffix.lower() == ".crdownload"
    }

    # 创建后页面可能跳转或刷新列表，稍等再查找
    time.sleep(2)

    first_row_download_selectors: tuple[tuple[str, str], ...] = (
        (
            By.XPATH,
            "(//tbody/tr)[1]//button[contains(normalize-space(.), '下载')]",
        ),
        (
            By.XPATH,
            "(//tbody/tr)[1]//a[contains(normalize-space(.), '下载')]",
        ),
        (
            By.XPATH,
            "(//tbody/tr)[1]//*[contains(@class,'ame-btn')"
            " and contains(normalize-space(.), '下载')]",
        ),
        (
            By.XPATH,
            "(//tr[contains(@class,'ame-table-row')])[1]"
            "//*[contains(normalize-space(.), '下载')]",
        ),
        (
            By.XPATH,
            "(//*[contains(@class,'ame-btn') and contains(normalize-space(.), '下载')])[1]",
        ),
        (By.XPATH, "(//button[contains(normalize-space(.), '下载')])[1]"),
        (By.XPATH, "(//a[contains(normalize-space(.), '下载')])[1]"),
    )

    clicked = False
    for by, selector in first_row_download_selectors:
        try:
            button = wait.until(EC.element_to_be_clickable((by, selector)))
            _safe_click(driver, button)
            print("已点击第一条的「下载」。")
            clicked = True
            break
        except TimeoutException:
            continue

    if not clicked:
        # 兜底：找第一行，再按文字点「下载」
        first_row = _find_first_table_row(driver)
        if first_row is not None:
            for element in first_row.find_elements(
                By.XPATH,
                ".//button|.//a|.//span|.//*[contains(@class,'ame-btn')]",
            ):
                text = element.text.replace("\n", "").replace(" ", "")
                if DOWNLOAD_BUTTON_TEXT in text and element.is_displayed():
                    _safe_click(driver, element)
                    print("已点击第一条的「下载」（兜底匹配）。")
                    clicked = True
                    break

    if not clicked:
        raise TimeoutException("未找到第一条记录的「下载」按钮，请确认活码已创建成功。")

    # 等待新文件出现（过滤掉点击前已存在的文件）
    deadline = time.time() + _DOWNLOAD_WAIT_SECONDS
    while time.time() < deadline:
        current_files = {
            f for f in _DOWNLOAD_DIR.iterdir()
            if f.is_file() and not f.suffix.lower() == ".crdownload"
        }
        new_files = current_files - files_before
        if new_files:
            # 取最新的新文件
            newest = max(new_files, key=lambda f: f.stat().st_mtime)
            print(f"下载完成，文件已保存：{newest}")
            return newest
        time.sleep(0.5)

    raise TimeoutError(
        f"等待下载完成超时（{_DOWNLOAD_WAIT_SECONDS}s），"
        f"请检查 Chrome 下载目录：{_DOWNLOAD_DIR}"
    )


def _find_first_table_row(driver: WebDriver):
    """获取列表中的第一行数据。"""
    row_selectors = (
        "tbody tr",
        "tr.ame-table-row",
        "[class*='table-row']",
        "[class*='list-item']",
    )
    for selector in row_selectors:
        for row in driver.find_elements(By.CSS_SELECTOR, selector):
            if row.is_displayed() and row.text.strip():
                return row
    return None


def _find_auto_create_new_group_switch(driver: WebDriver):
    """查找「自动创建新群」附近的 switch track。"""
    xpaths = (
        "//*[contains(normalize-space(.), '自动创建新群')]"
        "/ancestor::*[contains(@class,'ame-form-item')][1]"
        "//*[contains(@class,'ame-switch-track')]",
        "//*[contains(normalize-space(.), '自动创建新群')]"
        "/following::*[contains(@class,'ame-switch-track')][1]",
    )
    for xpath in xpaths:
        for track in driver.find_elements(By.XPATH, xpath):
            if track.is_displayed():
                return track
    return None


def _is_switch_checked(track) -> bool:
    """判断 ame-switch 是否处于开启（checked）状态。"""
    class_name = track.get_attribute("class") or ""
    if "ame-switch-checked" in class_name:
        return True
    try:
        switch_root = track.find_element(
            By.XPATH,
            "./ancestor::*[contains(@class,'ame-switch')][1]",
        )
        root_class = switch_root.get_attribute("class") or ""
        return "ame-switch-checked" in root_class
    except Exception:
        return False


def configure_group_chat(driver: WebDriver, group_name: str, timeout: int = 30) -> None:
    """完成手动设置、选择群聊、搜索勾选、确定的一整套操作。"""
    select_manual_setting(driver, timeout=timeout)
    click_select_group_chat(driver, timeout=timeout)
    search_and_select_group(driver, group_name, timeout=timeout)
    click_confirm_button(driver, timeout=timeout)


def _wait_group_select_dialog(driver: WebDriver, wait: WebDriverWait):
    """等待「添加群聊」弹窗出现，并等待遮罩层消失。"""
    dialog = wait.until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, GROUP_SELECT_DIALOG_SELECTOR))
    )

    # 弹窗打开时 __full 遮罩可能挡住输入框，等待其消失
    overlay_wait = WebDriverWait(driver, 10, poll_frequency=0.2)
    try:
        overlay_wait.until(
            EC.invisibility_of_element_located(
                (By.CSS_SELECTOR, GROUP_SELECT_DIALOG_OVERLAY_SELECTOR)
            )
        )
    except TimeoutException:
        pass

    time.sleep(0.3)
    return dialog


def _find_group_name_input(dialog, wait: WebDriverWait):
    """定位弹窗内的群名称搜索输入框（避免误选页面上的前缀输入框）。"""
    selectors: tuple[str, ...] = (
        GROUP_NAME_SEARCH_INPUT_SELECTOR,
        "input.ame-input[placeholder*='群名称']:not([placeholder*='前缀'])",
        "input.ame-input[placeholder*='群名']",
        "input.ame-input",
    )

    def _find_input(_driver: WebDriver):
        for selector in selectors:
            for input_el in dialog.find_elements(By.CSS_SELECTOR, selector):
                if not input_el.is_displayed():
                    continue
                placeholder = input_el.get_attribute("placeholder") or ""
                if "前缀" in placeholder and "搜索" not in placeholder:
                    continue
                return input_el
        return False

    return wait.until(_find_input)


def _search_group_name(driver: WebDriver, search_input, text: str) -> None:
    """触发弹窗内群名称搜索（兼容 Vue 输入框）。"""
    _fill_input_by_js(driver, search_input, text)
    time.sleep(0.3)

    try:
        search_input.send_keys(Keys.ENTER)
    except Exception:
        driver.execute_script(
            """
            const el = arguments[0];
            el.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
            }));
            el.dispatchEvent(new KeyboardEvent('keyup', {
                key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
            }));
            """,
            search_input,
        )

    # 等待列表加载（遮罩消失或出现结果行）
    time.sleep(1.5)


def _build_search_keys(group_name: str) -> list[str]:
    """生成用于匹配搜索结果的多组关键字。"""
    keys: list[str] = []
    full = group_name.strip()
    if full:
        keys.append(full)

    compact = _normalize_match_text(full)
    if compact and compact not in keys:
        keys.append(compact)

    for part in full.split("-"):
        part = part.strip()
        if len(part) >= 4 and part not in keys:
            keys.append(part)

    number_match = re.search(r"\d{4,}", full)
    if number_match:
        number = number_match.group()
        if number not in keys:
            keys.append(number)

    return keys


def _normalize_match_text(text: str) -> str:
    """压缩空白字符，便于名称比对。"""
    return (
        text.replace("\n", "")
        .replace("\r", "")
        .replace("\u00a0", "")
        .replace(" ", "")
    )


def _text_matches(group_name: str, candidate: str) -> bool:
    """判断候选文本是否与目标群名称匹配。"""
    target = _normalize_match_text(group_name)
    text = _normalize_match_text(candidate)
    if not target or not text:
        return False
    if target in text or text in target:
        return True

    for key in _build_search_keys(group_name):
        key_norm = _normalize_match_text(key)
        if len(key_norm) >= 4 and key_norm in text:
            return True
    return False


def _find_group_select_target(driver: WebDriver, group_name: str, dialog=None):
    """在弹窗搜索结果中定位可勾选的 checkbox 或行。"""
    root = dialog if dialog is not None else driver

    row_selectors = (
        "tr",
        "[class*='table-row']",
        "[class*='list-item']",
        "[class*='ame-list-item']",
        "[class*='group-item']",
    )
    for row_selector in row_selectors:
        for row in root.find_elements(By.CSS_SELECTOR, row_selector):
            if not row.is_displayed():
                continue
            if not _text_matches(group_name, row.text):
                continue

            for wrapper in row.find_elements(By.CSS_SELECTOR, ".ame-checkbox-wrapper"):
                if wrapper.is_displayed():
                    return wrapper

            checkboxes = row.find_elements(
                By.CSS_SELECTOR, AME_CHECKBOX_INPUT_SELECTOR
            )
            if checkboxes:
                return checkboxes[0]

            return row

    for checkbox in root.find_elements(By.CSS_SELECTOR, AME_CHECKBOX_INPUT_SELECTOR):
        try:
            wrapper = checkbox.find_element(
                By.XPATH,
                "./ancestor::*[contains(@class,'ame-checkbox-wrapper')][1]",
            )
            if not wrapper.is_displayed():
                continue
            if _text_matches(group_name, wrapper.text):
                return wrapper if wrapper.is_displayed() else checkbox
        except Exception:
            continue

    visible = [
        item
        for item in root.find_elements(By.CSS_SELECTOR, AME_CHECKBOX_INPUT_SELECTOR)
        if item.is_displayed()
    ]
    if len(visible) == 1:
        return visible[0]

    return False


def _find_group_checkbox(driver: WebDriver, group_name: str, dialog=None):
    """兼容旧调用：返回搜索结果中的 checkbox 目标。"""
    return _find_group_select_target(driver, group_name, dialog)


def _click_by_text_in_root(driver: WebDriver, root, text: str) -> None:
    """在指定容器内按文字点击元素。"""
    candidates = root.find_elements(
        By.XPATH,
        ".//button|.//a|.//label|.//span|.//*[contains(@class,'ame-btn')]",
    )
    for element in candidates:
        element_text = element.text.replace("\n", "").replace(" ", "")
        if text in element_text and element.is_displayed():
            _safe_click(driver, element)
            return
    raise TimeoutException(f"未在弹窗中找到文字为「{text}」的可点击元素。")


def _click_by_text(driver: WebDriver, text: str, timeout: int = 30) -> None:
    """按可见文字点击按钮、链接或选项。"""
    wait = WebDriverWait(driver, timeout)
    xpath = (
        f"//*[contains(normalize-space(.), '{text}') and "
        "(self::button or self::a or self::span or self::label or "
        "contains(@class,'ame-btn') or contains(@class,'ame-radio') or "
        "contains(@class,'tab-for-create-item'))]"
    )

    try:
        element = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
        _safe_click(driver, element)
        return
    except TimeoutException:
        pass

    def _find_by_text(current_driver: WebDriver):
        candidates = current_driver.find_elements(
            By.XPATH,
            "//button|//a|//label|//span|//*[contains(@class,'ame-btn')]"
            "|//*[contains(@class,'ame-radio-wrapper')]",
        )
        for element in candidates:
            element_text = element.text.replace("\n", "").replace(" ", "")
            if text in element_text and element.is_displayed():
                return element
        return False

    element = wait.until(_find_by_text)
    _safe_click(driver, element)


def _safe_click(
    driver: WebDriver,
    element,
    *,
    relocate=None,
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


def _fill_input(
    driver: WebDriver, element, text: str, *, prefer_js: bool = False
) -> None:
    """清空输入框并填入文本，同时触发前端框架的 input 事件。"""
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
        element,
    )

    if prefer_js:
        _fill_input_by_js(driver, element, text)
        time.sleep(0.8)
        return

    try:
        element.click()
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.BACKSPACE)
        element.send_keys(text)
    except Exception:
        _fill_input_by_js(driver, element, text)

    time.sleep(0.8)
    driver.execute_script(
        """
        const el = arguments[0];
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        """,
        element,
    )


def _fill_input_by_js(driver: WebDriver, element, text: str) -> None:
    """用 JS 聚焦并写入，避免弹窗遮罩导致 click intercepted。"""
    driver.execute_script(
        """
        const el = arguments[0];
        const value = arguments[1];
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;
        el.focus();
        setter.call(el, '');
        setter.call(el, value);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        """,
        element,
        text,
    )
