# -*- coding: utf-8 -*-
"""
将「前三步对比」打包为 Windows 可执行文件。

路径说明：
- 脚本相对路径：小工具/补充飞书推送3/build_compare_exe.py
- 推荐执行目录：小工具/补充飞书推送3/

从项目根目录执行（Windows PowerShell）示例：
    cd D:\\桌面文件\\新建文件夹\\小工具\\补充飞书推送3
    python build_compare_exe.py

生成结果：
    dist/feishu_compare.exe
    dist/运行对比前三步.bat
"""

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "compare_only.py"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
SPEC_DIR = ROOT / "spec"
# 使用英文文件名，避免 PowerShell 无法识别中文 exe 名
EXE_NAME = "feishu_compare"
LAUNCHER_BAT = "运行对比前三步.bat"


def _run(cmd):
    # type: (list) -> None
    print("执行：{}".format(" ".join(cmd)))
    subprocess.check_call(cmd, cwd=str(ROOT))


def main():
    # type: () -> int
    if not ENTRY.exists():
        print("入口文件不存在：{}".format(ENTRY))
        return 1

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("未安装 PyInstaller，正在安装 ...")
        _run([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # 不删除 dist（exe 可能正在运行或被终端占用），只清理 build/spec
    for folder in (BUILD_DIR, SPEC_DIR):
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--console",
            "--name",
            EXE_NAME,
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(BUILD_DIR),
            "--specpath",
            str(SPEC_DIR),
            "--hidden-import",
            "requests",
            str(ENTRY),
        ]
    )

    exe_path = DIST_DIR / "{}.exe".format(EXE_NAME)
    if not exe_path.exists():
        print("打包失败，未找到：{}".format(exe_path))
        return 1

    launcher_path = DIST_DIR / LAUNCHER_BAT
    launcher_path.write_text(
        "\n".join(
            [
                "@echo off",
                "chcp 65001 >nul",
                'cd /d "%~dp0"',
                "feishu_compare.exe %*",
                "pause",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print("打包完成：{}".format(exe_path))
    print("启动器：{}".format(launcher_path))
    print("用法示例：")
    print('  "{}"'.format(exe_path))
    print('  "{}" --preview'.format(exe_path))
    print('  "{}" --output-json compare_result.json'.format(exe_path))
    print('  "{}" --output-txt 待新增编号.txt'.format(exe_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
