# -*- coding: utf-8 -*-
"""
浏览器模块
- 使用 webdriver-manager 自动管理 chromedriver
- 使用项目内独立 user-data-dir 持久化登录态（替代 cookies.pkl）
- 反检测 + 隐藏 webdriver 特征
- 强制下载目录到 ./downloads
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


def _release_profile_locks(profile_dir: Path) -> None:
    """启动前清掉 chrome_profile 里的会话锁文件。

    背景：用户上一次跑被 Ctrl+C 打断时，Chrome 进程没正常退出，会在 user-data-dir
    里留下 SingletonLock / SingletonCookie / SingletonSocket 这种"独占文件"。
    新 Chrome 启动如果检测到这些文件指向的进程还在，会立刻退出，报
    "session not created: Chrome instance exited"。

    清理规则（仅清"锁"，不动登录态）：
        - <profile>/Singleton*
        - <profile>/Default/Singleton*
        - <profile>/lockfile
    Cookies / localStorage 这些登录态文件位于 <profile>/Default/ 下的其他文件，
    本函数不动它们。
    """
    candidates: list[Path] = []
    for p in (profile_dir, profile_dir / "Default"):
        if not p.exists():
            continue
        candidates.extend(p.glob("Singleton*"))
        candidates.append(p / "lockfile")

    for f in candidates:
        try:
            if f.exists():
                f.unlink()
                logger.debug("已清理 profile 锁文件：{}", f)
        except Exception as e:
            logger.warning("清理 profile 锁文件失败 {}：{}", f, e)


HIDE_WEBDRIVER_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
"""


def build_driver(
    download_dir: str | Path,
    profile_dir: str | Path,
    profile_name: str = "Default",
    headless: bool = False,
) -> webdriver.Chrome:
    """创建并返回一个配置完成的 Chrome WebDriver。

    参数:
        download_dir: 下载文件保存目录（绝对路径）
        profile_dir: Chrome 用户数据目录（项目内，独立于系统 Chrome）
        profile_name: 子 profile 名（默认 "Default"）
        headless: 是否无头模式（首次登录建议 False）

    返回:
        webdriver.Chrome 实例

    注意:
        若同一个 profile 已被另一个 Chrome 进程占用，会启动失败。
        请先关闭使用相同 profile 的 Chrome 实例。
    """
    download_dir = Path(download_dir).resolve()
    profile_dir = Path(profile_dir).resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    # 启动前清理上一轮残留的 SingletonLock，避免 "Chrome instance exited" 报错
    _release_profile_locks(profile_dir)

    options = Options()

    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument(f"--profile-directory={profile_name}")

    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--start-maximized")
    options.add_argument("--lang=zh-CN")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    prefs = {
        "download.default_directory": str(download_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "profile.default_content_setting_values.automatic_downloads": 1,
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    }
    options.add_experimental_option("prefs", prefs)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": HIDE_WEBDRIVER_JS},
        )
    except Exception:
        pass

    driver.execute_cdp_cmd(
        "Page.setDownloadBehavior",
        {"behavior": "allow", "downloadPath": str(download_dir)},
    )

    return driver
