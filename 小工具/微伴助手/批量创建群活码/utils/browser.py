# -*- coding: utf-8 -*-
"""
浏览器模块：启动 Chrome 并持久化微伴助手登录态。

说明：
- 使用项目内 chrome_profile 目录保存登录信息，下次运行无需重复登录
- 优先使用本地已缓存的 chromedriver，避免网络异常时无法启动
- 启动前会清理残留锁文件，并在失败时自动重试
- 下载目录统一设置为 C:\\Users\\LENOVO\\Desktop\\专属带领群二维码
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# Chrome 用户目录里常见的锁文件/残留文件
_PROFILE_LOCK_NAMES = (
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
    "lockfile",
    "DevToolsActivePort",
    "DevToolsActivePortLock",
)
# 启动失败时的最大重试次数
_MAX_START_RETRIES = 3
# 重试间隔（秒）
_RETRY_INTERVAL_SECONDS = 2
# webdriver-manager 默认缓存目录
_WDM_CACHE_DIR = Path.home() / ".wdm" / "drivers" / "chromedriver" / "win64"

HIDE_WEBDRIVER_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
window.chrome = window.chrome || { runtime: {} };
"""


def _release_profile_locks(profile_dir: Path) -> None:
    """清理 Chrome 用户目录里残留的锁文件，避免启动失败。"""
    candidates: list[Path] = []

    for folder in (profile_dir, profile_dir / "Default"):
        if not folder.exists():
            continue
        for name in _PROFILE_LOCK_NAMES:
            candidates.append(folder / name)
        candidates.extend(folder.glob("Singleton*"))

    # 崩溃后可能残留的指标文件，也可能阻止再次启动
    for pattern in ("BrowserMetrics", "BrowserMetrics-spare.pma", "DeferredBrowserMetrics"):
        candidates.extend(profile_dir.glob(pattern))

    for file_path in candidates:
        try:
            if file_path.exists():
                file_path.unlink()
        except OSError:
            # 锁文件被占用说明 Chrome 仍在运行，此时不要强行删除
            pass


def _find_cached_chromedrivers() -> list[Path]:
    """扫描本地 webdriver-manager 缓存，返回按版本号倒序排列的 chromedriver 路径。"""
    drivers: list[tuple[str, Path]] = []

    if not _WDM_CACHE_DIR.exists():
        return []

    for folder in _WDM_CACHE_DIR.iterdir():
        if not folder.is_dir():
            continue
        for candidate in (
            folder / "chromedriver-win64" / "chromedriver.exe",
            folder / "chromedriver-win32" / "chromedriver.exe",
        ):
            if candidate.exists():
                drivers.append((folder.name, candidate))
                break

    drivers.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in drivers]


def _resolve_chromedriver_path(project_dir: Path) -> Path:
    """解析 chromedriver 路径：优先本地缓存，在线下载仅作最后尝试。"""
    env_path = os.environ.get("CHROMEDRIVER_PATH", "").strip()
    if env_path:
        driver_path = Path(env_path)
        if driver_path.exists():
            print(f"使用环境变量指定的 chromedriver：{driver_path}")
            return driver_path

    local_driver = project_dir / "drivers" / "chromedriver.exe"
    if local_driver.exists():
        print(f"使用项目内 chromedriver：{local_driver}")
        return local_driver

    cached_drivers = _find_cached_chromedrivers()
    if cached_drivers:
        print(f"使用本地缓存 chromedriver：{cached_drivers[0]}")
        return cached_drivers[0]

    # 仅在没有任何本地驱动时，才尝试联网下载
    try:
        from webdriver_manager.chrome import ChromeDriverManager

        downloaded = Path(ChromeDriverManager().install())
        print(f"已在线下载 chromedriver：{downloaded}")
        return downloaded
    except Exception as exc:
        raise RuntimeError(
            "未找到本地 chromedriver，且联网下载失败。\n"
            "请检查网络/代理，或手动下载 chromedriver 后放到以下任一位置：\n"
            f"1. 环境变量 CHROMEDRIVER_PATH\n"
            f"2. {local_driver}\n"
            f"3. {_WDM_CACHE_DIR}\\<版本号>\\chromedriver-win64\\chromedriver.exe\n"
            f"原始错误：{exc}"
        ) from exc


def _find_chrome_pids_using_profile(profile_dir: Path) -> list[int]:
    """查找仍在占用当前 profile 的 chrome.exe 进程 ID。"""
    profile_text = str(profile_dir.resolve())
    ps_script = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{profile_text}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    pids: list[int] = []
    for line in result.stdout.splitlines():
        text = line.strip()
        if text.isdigit():
            pids.append(int(text))
    return pids


# 下载的二维码统一存入此目录（会自动创建）
DOWNLOAD_DIR = Path(r"C:\Users\LENOVO\Desktop\专属带领群二维码")


def _build_chrome_options(profile_dir: Path, headless: bool) -> Options:
    """组装 Chrome 启动参数。

    说明：
    - 通过 prefs 将下载目录固定为 DOWNLOAD_DIR，避免文件散落在默认下载文件夹
    - 禁用「下载前询问保存位置」弹窗，保证自动下载不卡住
    """
    # 确保下载目录已存在
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--remote-debugging-port=0")
    options.add_argument("--start-maximized")
    options.add_argument("--lang=zh-CN")

    if headless:
        options.add_argument("--headless=new")

    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # 设置自动下载目录，禁用下载询问弹窗
    options.add_experimental_option("prefs", {
        "download.default_directory": str(DOWNLOAD_DIR),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    })

    return options


def _raise_startup_help(profile_dir: Path, last_error: Exception) -> None:
    """在多次重试仍失败时，抛出带中文说明的异常。"""
    pids = _find_chrome_pids_using_profile(profile_dir)
    pid_text = "、".join(str(pid) for pid in pids) if pids else "未检测到"

    message = (
        "Chrome 启动失败，通常是 chrome_profile 仍被占用。\n"
        f"profile 路径：{profile_dir}\n"
        f"检测到的占用进程 PID：{pid_text}\n"
        "请按以下步骤处理后再运行：\n"
        "1. 关闭上一次脚本打开的 Chrome 窗口\n"
        "2. 打开任务管理器，结束仍占用该 profile 的 chrome.exe\n"
        "3. 等待 3~5 秒后重新运行 main.py\n"
        "4. 若仍失败，可临时重命名 chrome_profile 目录后重新登录\n"
        f"原始错误：{last_error}"
    )
    raise RuntimeError(message) from last_error


def build_driver(profile_dir: str | Path, headless: bool = False) -> webdriver.Chrome:
    """创建并返回配置完成的 Chrome WebDriver。"""
    profile_dir = Path(profile_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    project_dir = profile_dir.parent

    log_path = project_dir / "chromedriver.log"
    driver_path = _resolve_chromedriver_path(project_dir)
    options = _build_chrome_options(profile_dir, headless=headless)
    service = Service(str(driver_path), log_output=str(log_path))

    last_error: Exception | None = None
    for attempt in range(1, _MAX_START_RETRIES + 1):
        _release_profile_locks(profile_dir)

        if attempt > 1:
            print(
                f"Chrome 启动失败，正在第 {attempt}/{_MAX_START_RETRIES} 次重试 ..."
            )
            time.sleep(_RETRY_INTERVAL_SECONDS)

        try:
            driver = webdriver.Chrome(service=service, options=options)
            try:
                driver.execute_cdp_cmd(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": HIDE_WEBDRIVER_JS},
                )
            except Exception:
                pass
            return driver
        except SessionNotCreatedException as exc:
            last_error = exc
            continue

    if last_error is None:
        last_error = RuntimeError("未知 Chrome 启动错误")
    _raise_startup_help(profile_dir, last_error)
    raise last_error
