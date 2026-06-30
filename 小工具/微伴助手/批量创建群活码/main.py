# -*- coding: utf-8 -*-
"""
批量创建群活码 - 打开微伴助手并自动填写创建表单

功能：
    1. 从 contact_内部专用.db 查询 nick_name（专属带领群*）
    2. 启动 Chrome（使用 chrome_profile 保存登录态）
    3. 自动导航：企微码 → 自动拉群 → 新建拉群 → 直接入群 → 企微活码拉群
    4. 填写表单、创建活码，并点击第一条「下载」，等待文件保存完成
    5. 自动记录进度（batch_progress.json / 创建记录.log），中断后可续跑
    6. 所有处理完成后，将二维码目录整体复制到 X:\\backup
    7. 自动将本次下载的二维码上传到飞书多维表「专属带领群二维码」列

运行方式（PowerShell）：
    cd D:\\桌面文件\\新建文件夹\\小工具\\微伴助手\\批量创建群活码
    ..\\..\\..\\.venv\\Scripts\\python.exe .\\main.py
"""
from __future__ import annotations

import shutil
import sys
import time
import traceback
from pathlib import Path

from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait

from utils.browser import build_driver
from utils.db import fetch_group_nick_names
from utils.form import (
    configure_group_chat,
    click_create_live_code_button,
    click_first_download_button,
    disable_auto_create_new_group,
    fill_qrcode_name,
)
from utils.navigation import (
    click_ame_link_button,
    click_ame_radio_input,
    click_create_auto_group_button,
    click_direct_join_group_option,
    click_enterprise_microcode_nav,
)
from utils.progress import ProgressTracker
from utils.feishu_upload import upload_qrcodes_to_feishu

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "chrome_profile"
CREATE_PAGE_URL = "https://weibanzhushou.com/dashboard"

# 二维码下载目录（与 browser.py / form.py 保持一致）
QRCODE_DIR = Path(r"C:\Users\LENOVO\Desktop\专属带领群二维码")
# 备份目标目录，完成后把 QRCODE_DIR 整体复制到此处
BACKUP_DIR = Path(r"X:\backup")

# 试运行条数：1 表示只处理第一条；None 表示处理全部
PROCESS_LIMIT = None
# 手动指定从第几条开始（1 表示不限制）；通常无需修改，程序会自动跳过已完成项
PROCESS_START_INDEX = 1
# True 表示清空进度记录后从头开始（不会删除 创建记录.log 历史）
RESET_PROGRESS = False


def _wait_dashboard_ready(driver: WebDriver, timeout: int = 30) -> None:
    """等待 dashboard 页面加载完成，避免连续批量时 DOM 未稳定。"""
    WebDriverWait(driver, timeout).until(
        lambda current_driver: current_driver.execute_script("return document.readyState")
        == "complete"
    )
    time.sleep(0.5)


def run_create_flow(driver: WebDriver, nick_name: str) -> None:
    """执行一次创建页导航，填写二维码名称并配置群聊。"""
    click_enterprise_microcode_nav(driver)
    click_ame_link_button(driver)
    click_create_auto_group_button(driver)
    click_direct_join_group_option(driver)
    click_ame_radio_input(driver)
    fill_qrcode_name(driver, nick_name)
    configure_group_chat(driver, nick_name)
    disable_auto_create_new_group(driver)
    click_create_live_code_button(driver)
    click_first_download_button(driver)


def _backup_qrcode_dir() -> None:
    """将二维码目录整体复制到 X:\\backup。

    说明：
    - 目标路径为 X:\\backup\\专属带领群二维码（与源目录同名）
    - 若目标已存在，先删除再复制，确保内容最新
    - X 盘不存在或不可访问时，打印警告而非中断程序
    """
    if not QRCODE_DIR.exists():
        print(f"\n⚠ 二维码目录不存在，跳过备份：{QRCODE_DIR}")
        return

    # 检查 X 盘是否可访问
    backup_root = Path(r"X:\\")
    if not backup_root.exists():
        print(f"\n⚠ X 盘不可访问，跳过备份。请手动将以下目录复制到 U 盘/备份盘：\n   {QRCODE_DIR}")
        return

    dest = BACKUP_DIR / QRCODE_DIR.name
    try:
        if dest.exists():
            print(f"备份目标已存在，逐文件覆盖：{dest}")
            # 逐文件复制覆盖，不删除目标目录，避免文件系统延迟问题
            for src_file in QRCODE_DIR.iterdir():
                if src_file.is_file():
                    shutil.copy2(str(src_file), str(dest / src_file.name))
        else:
            shutil.copytree(str(QRCODE_DIR), str(dest))
        file_count = sum(1 for f in dest.iterdir() if f.is_file())
        print(f"\n✓ 备份完成：已将 {file_count} 个文件复制到 {dest}")
    except Exception as exc:
        print(f"\n⚠ 备份失败，请手动复制。原始错误：{exc}")


def main() -> int:
    """批量读取数据库并自动填写二维码名称。"""
    driver = None
    tracker = ProgressTracker(BASE_DIR)

    try:
        if RESET_PROGRESS:
            tracker.reset()
            print("已清空进度记录，将从头开始处理。")

        print("正在查询数据库 ...")
        nick_names = fetch_group_nick_names()
        if not nick_names:
            print("未查询到 nick_name 以「专属带领群」开头的记录。")
            return 1

        total_count = len(nick_names)
        print(f"共查询到 {total_count} 条记录。")

        if PROCESS_LIMIT is not None:
            nick_names = nick_names[:PROCESS_LIMIT]
            total_count = len(nick_names)
            print(f"当前为试运行模式，仅处理前 {total_count} 条。")

        print()
        tracker.print_summary(total_count)

        pending_items = tracker.build_pending_items(
            nick_names,
            start_index=PROCESS_START_INDEX,
        )
        if not pending_items:
            print("\n所有记录均已处理完成，无需继续运行。")
            return 0

        print(f"\n本次待处理 {len(pending_items)} 条，从第 {pending_items[0][0]} 条开始。")
        for index, name in pending_items[:5]:
            print(f"  待处理 [{index}] {name}")
        if len(pending_items) > 5:
            print(f"  ... 还有 {len(pending_items) - 5} 条")

        print("\n正在启动 Chrome ...")
        driver = build_driver(profile_dir=PROFILE_DIR, headless=False)
        driver.get(CREATE_PAGE_URL)
        _wait_dashboard_ready(driver)

        failed_items: list[tuple[int, str, str]] = []

        for step, (index, nick_name) in enumerate(pending_items, 1):
            print(
                f"\n===== [{index}/{total_count}] "
                f"本次第 {step}/{len(pending_items)} 条：{nick_name} ====="
            )
            if step > 1:
                driver.get(CREATE_PAGE_URL)
                _wait_dashboard_ready(driver)

            try:
                run_create_flow(driver, nick_name)
                tracker.mark_completed(index, nick_name, total_count)
                print(f"已记录进度：第 {index} 条创建成功。")
            except Exception as exc:
                error_text = str(exc)
                print(f"第 {index} 条处理失败：{error_text}")
                traceback.print_exc()
                tracker.mark_failed(index, nick_name, error_text, total_count)
                failed_items.append((index, nick_name, error_text))
                continue

        print()
        tracker.print_summary(total_count)
        if failed_items:
            print(f"\n本次运行仍有 {len(failed_items)} 条失败（下次运行会自动重试失败项）：")
            for item_index, item_name, item_error in failed_items:
                print(f"  - [{item_index}] {item_name} -> {item_error}")
        else:
            print("\n本次待处理项全部成功。")

        print("- 详细记录见：创建记录.log")
        print("- 请在浏览器中核对活码是否创建成功")
        print("- 完成操作后，回到此窗口按 Enter 关闭浏览器")
        print()
        input("按 Enter 关闭浏览器并退出 ...")

        # 关闭浏览器后，将二维码目录整体复制到 X:\backup
        _backup_qrcode_dir()

        # 自动将二维码上传到飞书多维表
        upload_qrcodes_to_feishu()

        return 0
    except KeyboardInterrupt:
        print("\n用户中断，已保存的进度不会丢失，下次运行会自动续跑。")
        tracker.print_summary(len(nick_names) if "nick_names" in locals() else 0)
        return 130
    except Exception as exc:
        print(f"运行失败：{exc}")
        traceback.print_exc()
        return 1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
