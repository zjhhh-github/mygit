from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# 目标网页地址：运行脚本后会用系统默认浏览器打开该页面。
TARGET_URL = "https://yhk.postar.cn/system/role"

# 登录账号密码。这里只用于本地自动化脚本，避免每次手动输入。
USERNAME = "YS882812"
PASSWORD = "Aa732044"

# 页面加载和元素等待超时时间，单位：秒；设置得短一些可以减少无效等待。
WAIT_TIMEOUT = 10

# 浏览器用户数据目录：保存登录态，第二次运行通常会更快。
BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "chrome_profile"


def build_driver():
    """启动 Selenium 自己管理的 Chrome 浏览器。"""
    chrome_options = Options()
    chrome_options.page_load_strategy = "eager"
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-popup-blocking")
    return webdriver.Chrome(options=chrome_options)


def find_input_by_keywords(driver, keywords):
    """根据 placeholder / type / autocomplete 等属性寻找输入框。"""
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    xpath_parts = []

    for keyword in keywords:
        xpath_parts.append(f"contains(@placeholder, '{keyword}')")
        xpath_parts.append(f"contains(@autocomplete, '{keyword}')")
        xpath_parts.append(f"contains(@name, '{keyword}')")
        xpath_parts.append(f"contains(@type, '{keyword}')")

    xpath = f"//input[{' or '.join(xpath_parts)}]"
    return wait.until(EC.presence_of_element_located((By.XPATH, xpath)))


def fill_input(element, value):
    """清空输入框并填写内容，兼容部分 Element Plus 输入框。"""
    element.click()
    element.send_keys(Keys.CONTROL, "a")
    element.send_keys(value)


def click_login_button(driver):
    """等待并点击 Element Plus 的登录按钮。"""
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    button_locators = [
        (
            By.XPATH,
            "//button[contains(@class, 'el-button') "
            "and contains(@class, 'el-button--primary') "
            "and contains(@class, 'el-button--large') "
            "and contains(normalize-space(.), '登录')]",
        ),
        (
            By.CSS_SELECTOR,
            "button.el-button.el-button--primary.el-button--large",
        ),
    ]

    login_button = None

    for locator in button_locators:
        try:
            login_button = wait.until(EC.element_to_be_clickable(locator))
            break
        except Exception:
            continue

    if login_button is None:
        raise RuntimeError(
            "没有找到登录按钮，请确认页面上是否已经出现登录按钮，或按钮 class 是否发生变化。"
        )

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", login_button)
    login_button.click()


def login_if_needed(driver):
    """如果页面出现登录表单，则自动填写账号密码并点击登录。"""
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    try:
        username_input = find_input_by_keywords(driver, ["账号", "用户名", "user", "text"])
        password_input = find_input_by_keywords(driver, ["密码", "password"])
    except Exception:
        print("未检测到登录表单，可能已经登录，跳过登录步骤。")
        return

    fill_input(username_input, USERNAME)
    fill_input(password_input, PASSWORD)
    click_login_button(driver)

    # 等待页面跳转或登录按钮消失，避免过早结束。
    try:
        wait.until_not(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//button[contains(@class, 'el-button--primary') "
                    "and contains(normalize-space(.), '登录')]",
                )
            )
        )
    except Exception:
        print("已点击登录按钮，如果页面没有跳转，请检查账号密码或验证码。")


def main():
    """打开国通系统角色管理页面，必要时自动登录。"""
    driver = build_driver()
    driver.get(TARGET_URL)
    login_if_needed(driver)

    # 保持浏览器窗口打开，方便查看执行结果。
    input("脚本已执行完成，按回车键关闭浏览器...")
    driver.quit()


if __name__ == "__main__":
    main()
