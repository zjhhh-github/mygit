# -*- coding: utf-8 -*-
"""
慧分账自动下载脚本（生产级，长期稳定运行版）

完整流程:
    1. 启动 Chrome（独立 user-data-dir 持久化登录态 + 反检测 + 强制下载目录）
    2. 打开站点首页，先以「财务管理」菜单为标志检测是否已登录
       - 已登录 → 直接进入下一步
       - 未登录 → 自动账号密码登录，登录态由 chrome_profile 自动保留到下次
    3. 模拟真实点击：财务管理 → 收款订单查询
    4. 等待业务页就绪（日期 / 查询按钮 / 表格）
    5. JS 注入设置「开始日期 = 昨天」「结束日期 = 今天」
    6. 点击查询 → 等表格刷新
    7. 点击下载 → 轮询检测下载完成
    8. 输出成功日志，关闭浏览器
"""
from __future__ import annotations

import shutil
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from utils.browser import build_driver
from utils.downloader import DEFAULT_ALLOWED_EXTS, snapshot_dir, wait_download_finish
from utils.logger import setup_logger
from utils.page_actions import (
    auto_login,
    click_download,
    click_query,
    is_logged_in,
    navigate_to_payorder,
    open_and_settle,
    set_date_range,
    take_screenshot,
    wait_logged_in,
    wait_page_loaded,
    wait_table_loaded,
)
from utils.safe import wait_until_page_stable

# ============================================================
# 全局配置
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
DOWNLOAD_DIR = BASE_DIR / "downloads"
PROFILE_DIR = BASE_DIR / "chrome_profile"

HOME_URL = "https://paas.hbsk.com/"

USERNAME = "fahuojishu"
PASSWORD = "fahuojishu"

HEADLESS = False
LOGIN_TIMEOUT = 60
PAGE_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 120  # 单次下载等待上限；超时后会刷新页面 + 重做整套业务流程

# 失败恢复
MAX_RETRY = 3                                          # 业务流程最多重试次数
DESKTOP_DIR = Path.home() / "Desktop"                  # 下载完成后剪切到此目录
TARGET_BASENAME = "收款订单"                           # 桌面目标文件名（不含扩展名）

# 日期：昨天 ~ 今天（系统时间，跨月跨年自动正确）
_today = datetime.now()
_yesterday = _today - timedelta(days=1)
START_DATE = _yesterday.strftime("%Y-%m-%d")
END_DATE = _today.strftime("%Y-%m-%d")


# ============================================================
# 登录策略：先检测，未登录再自动登录
# ============================================================
def ensure_logged_in(driver) -> None:
    """打开首页 → 注入网络 hook → 等待页面稳定 → 检测登录态 → 必要时自动登录。"""
    from loguru import logger

    open_and_settle(driver, HOME_URL, timeout=30.0)

    if is_logged_in(driver, timeout=8):
        logger.success("检测到已登录（chrome_profile 持久化生效），跳过登录")
        return

    logger.info("未登录，执行自动账号密码登录")
    auto_login(driver, USERNAME, PASSWORD, timeout=LOGIN_TIMEOUT)
    wait_logged_in(driver, timeout=LOGIN_TIMEOUT)


# ============================================================
# 业务段：可重入的"导航 → 日期 → 查询 → 导出 → 等下载"
# ============================================================
def run_business_flow(driver) -> Path:
    """单次业务流程：跑一遍完整业务，返回新生成的下载文件 Path。

    任何步骤失败都向上抛异常；下载等待超时抛 TimeoutError，
    外层 retry_full_process 据此判断是否要 driver.refresh() 重试。
    """
    from loguru import logger

    navigate_to_payorder(driver, timeout=PAGE_TIMEOUT)
    take_screenshot(driver, LOGS_DIR, "after_nav")

    wait_page_loaded(driver, timeout=PAGE_TIMEOUT)
    take_screenshot(driver, LOGS_DIR, "page_ready")

    set_date_range(driver, START_DATE, END_DATE)
    take_screenshot(driver, LOGS_DIR, "after_set_date")

    click_query(driver)
    wait_table_loaded(driver, timeout=PAGE_TIMEOUT)
    take_screenshot(driver, LOGS_DIR, "after_query")

    before_files = snapshot_dir(DOWNLOAD_DIR)
    click_download(driver)

    logger.info("等待下载完成（最长 {} 秒）...", DOWNLOAD_TIMEOUT)
    downloaded = wait_download_finish(
        directory=DOWNLOAD_DIR,
        before_files=before_files,
        timeout=DOWNLOAD_TIMEOUT,
        allowed_exts=DEFAULT_ALLOWED_EXTS,
    )
    take_screenshot(driver, LOGS_DIR, "after_download")
    return downloaded


def retry_full_process(driver) -> Path:
    """业务流程重试封装：最多 MAX_RETRY 次。

    失败时（含下载超时 / 任何业务异常）：
        1. driver.refresh() 回到主框架
        2. 等待页面重新稳定
        3. 重新执行 run_business_flow
    超过 MAX_RETRY 后抛出最后一次异常。
    """
    from loguru import logger

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRY + 1):
        logger.info("─" * 40)
        logger.info("业务流程尝试 {}/{}", attempt, MAX_RETRY)
        try:
            return run_business_flow(driver)
        except TimeoutError as e:
            last_err = e
            logger.warning("第 {} 次：下载等待超时（{}s）", attempt, DOWNLOAD_TIMEOUT)
        except Exception as e:
            last_err = e
            logger.warning("第 {} 次：业务流程异常 {}", attempt, e)
            logger.warning(traceback.format_exc())

        if attempt >= MAX_RETRY:
            break

        # 自动恢复：刷新 → 等稳定 → 下一轮重做
        logger.info("自动刷新页面后重新执行整套业务流程")
        try:
            driver.refresh()
        except Exception as e:
            logger.warning("driver.refresh() 异常：{}（继续等稳定）", e)
        wait_until_page_stable(driver, timeout=30.0, stable_window=2.0, check_table=False)

    take_screenshot(driver, LOGS_DIR, "error_retry_exhausted")
    raise RuntimeError(f"业务流程已重试 {MAX_RETRY} 次仍失败，最后错误：{last_err}")


# ============================================================
# 文件后处理：重命名 → 剪切到桌面（覆盖旧文件）
# ============================================================
def move_to_desktop(src: Path, base_name: str = TARGET_BASENAME) -> Path:
    """把下载得到的文件重命名为 <base_name>.<原扩展名> 后剪切到桌面。

    - 扩展名命中 .xlsx/.xls/.csv 时保留原扩展名；其它一律按 .xlsx 处理（极少见）。
    - 桌面已存在同名文件 → 先 unlink 再 move，不弹窗、不报错。
    - 使用 shutil.move（剪切），下载目录不留旧文件。
    """
    from loguru import logger

    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)

    ext = src.suffix.lower()
    if ext not in DEFAULT_ALLOWED_EXTS:
        ext = ".xlsx"

    dst = DESKTOP_DIR / f"{base_name}{ext}"

    if dst.exists():
        try:
            dst.unlink()
        except Exception as e:
            logger.warning("删除桌面旧文件失败：{}（将尝试覆盖）", e)

    shutil.move(str(src), str(dst))
    return dst


# ============================================================
# 主流程
# ============================================================
def main() -> int:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    logger = setup_logger(log_dir=LOGS_DIR)

    logger.info("=" * 60)
    logger.info("启动慧分账自动下载脚本")
    logger.info("账号: {}   日期范围: {} ~ {}", USERNAME, START_DATE, END_DATE)
    logger.info("下载目录: {}", DOWNLOAD_DIR)
    logger.info("Chrome Profile: {}", PROFILE_DIR)
    logger.info("=" * 60)

    driver = None
    try:
        driver = build_driver(
            download_dir=DOWNLOAD_DIR,
            profile_dir=PROFILE_DIR,
            profile_name="Default",
            headless=HEADLESS,
        )

        ensure_logged_in(driver)
        take_screenshot(driver, LOGS_DIR, "after_login")

        # 业务段（可重试）：导航 → 日期 → 查询 → 导出 → 等下载完成
        downloaded = retry_full_process(driver)
        logger.success("导出完成：{}", downloaded.name)

        # 重命名 + 剪切到桌面（覆盖同名旧文件）
        final_path = move_to_desktop(downloaded)
        logger.success("导出成功：桌面/{}", final_path.name)

        print(f"[SUCCESS] 导出成功：{final_path}")
        return 0

    except Exception as e:
        logger.error("流程失败：{}", e)
        logger.error(traceback.format_exc())
        if driver is not None:
            shot = take_screenshot(driver, LOGS_DIR, "error")
            logger.error("已保存错误截图：{}", shot)
        return 1

    finally:
        if driver is not None:
            try:
                driver.quit()
                # 注意：不要 quit 后立即操作 chrome_profile 目录，让 Chrome 自己写入完毕
                from loguru import logger as _lg
                _lg.info("浏览器已关闭")
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
