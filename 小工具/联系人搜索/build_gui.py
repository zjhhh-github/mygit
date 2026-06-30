# -*- coding: utf-8 -*-
"""
将「搜索联系人_GUI.py」打包为 Windows 可执行程序（单文件 exe）。

路径说明：
- 脚本相对路径：小工具/联系人搜索/build_gui.py
- 推荐执行目录：小工具/联系人搜索/

从项目根目录执行（Windows PowerShell）示例：
    cd D:\\桌面文件\\新建文件夹\\小工具\\联系人搜索
    ..\\..\\.venv\\Scripts\\python.exe build_gui.py

或直接双击同目录下的「打包.ps1」。

生成结果：
    dist/联系人搜索.exe          ← 单文件，可单独复制到桌面运行
    dist/启动联系人搜索.bat
    dist/分发说明.txt
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "搜索联系人_GUI.py"
DIST_ROOT = ROOT / "dist"
BUILD_DIR = ROOT / "build"
SPEC_DIR = ROOT / "spec"
EXE_NAME = "联系人搜索"
LAUNCHER_BAT = "启动联系人搜索.bat"
README_NAME = "分发说明.txt"
ICON_FILE = ROOT / "icon.ico"


def _run(cmd: list[str]) -> None:
    """执行外部命令并在失败时抛出异常。"""
    print("执行：{}".format(" ".join(cmd)))
    subprocess.check_call(cmd, cwd=str(ROOT))


def _ensure_pyinstaller() -> None:
    """确保已安装 PyInstaller。"""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("未安装 PyInstaller，正在安装 ...")
        _run([sys.executable, "-m", "pip", "install", "pyinstaller", "pillow"])


def main() -> int:
    if not ENTRY.exists():
        print("入口文件不存在：{}".format(ENTRY))
        return 1

    _ensure_pyinstaller()

    for folder in (BUILD_DIR, SPEC_DIR):
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)

    DIST_ROOT.mkdir(parents=True, exist_ok=True)

    # 清理旧产物（含此前 onedir 模式的子目录）
    旧exe = DIST_ROOT / "{}.exe".format(EXE_NAME)
    旧目录 = DIST_ROOT / EXE_NAME
    if 旧exe.is_file():
        旧exe.unlink()
    if 旧目录.is_dir():
        shutil.rmtree(旧目录, ignore_errors=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        EXE_NAME,
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(SPEC_DIR),
        "--hidden-import",
        "PIL",
        "--hidden-import",
        "PIL._tkinter_finder",
        "--hidden-import",
        "PIL.Image",
        "--hidden-import",
        "PIL.ImageTk",
        "--hidden-import",
        "sqlite3",
        "--collect-submodules",
        "PIL",
    ]

    if ICON_FILE.is_file():
        cmd.extend(["--icon", str(ICON_FILE), "--add-data", "{};.".format(ICON_FILE)])

    cmd.append(str(ENTRY))
    _run(cmd)

    exe_path = DIST_ROOT / "{}.exe".format(EXE_NAME)
    if not exe_path.is_file():
        print("打包失败：未找到 {}.exe".format(EXE_NAME))
        return 1

    bat_path = DIST_ROOT / LAUNCHER_BAT
    bat_path.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "cd /d \"%~dp0\"\r\n"
        "start \"\" \"%~dp0{}.exe\"\r\n".format(EXE_NAME),
        encoding="utf-8",
    )

    readme_path = DIST_ROOT / README_NAME
    readme_path.write_text(
        "联系人搜索工具 - 分发说明\r\n"
        "========================\r\n\r\n"
        "1. 直接双击「联系人搜索.exe」即可运行。\r\n"
        "2. 本程序为单文件打包，可单独复制 exe 到桌面或其它目录使用。\r\n"
        "3. 无需再复制 _internal 文件夹。\r\n"
        "4. 首次启动可能稍慢（约 3~10 秒），属正常现象。\r\n"
        "5. 若仍提示缺少 DLL，请安装 Microsoft Visual C++ 2015-2022 运行库。\r\n"
        "6. 设备、拷贝任务等设置保存在 Windows 注册表，不会生成额外配置文件。\r\n",
        encoding="utf-8",
    )

    print("")
    print("==> 打包完成")
    print("    可执行文件：{}".format(exe_path))
    print("    启动脚本：{}".format(bat_path))
    print("    说明文件：{}".format(readme_path))
    print("")
    print("提示：请将 dist\\联系人搜索.exe 复制到桌面即可运行（无需 _internal 文件夹）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
