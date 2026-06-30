@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ========================================
echo 售前通讯录查询工具 - 打包脚本
echo ========================================
echo.

REM 检查依赖
echo [1/3] 检查依赖...
python -c "import pyinstaller" 2>nul
if errorlevel 1 (
    echo PyInstaller 未安装，正在安装...
    python -m pip install pyinstaller --quiet
    if errorlevel 1 (
        echo ✗ 安装失败！
        pause
        exit /b 1
    )
)
echo ✓ 依赖检查完成

echo.
echo [2/3] 开始打包（请勿操作）...
echo.

REM 清理旧文件
if exist dist\售前通讯录查询.exe del /f /q dist\售前通讯录查询.exe 2>nul
if exist build rmdir /s /q build 2>nul
if exist "售前通讯录查询.spec" del /f /q "售前通讯录查询.spec" 2>nul

REM 执行打包（使用自定义图标）
python -m PyInstaller ^
    --onefile ^
    --noconsole ^
    --add-data "售前通讯录.txt;." ^
    --name "售前通讯录查询" ^
    --icon=icon.ico ^
    --noconfirm ^
    --clean ^
    --noupx ^
    db_viewer.py

if errorlevel 1 (
    echo.
    echo ✗ 打包失败！
    echo.
    pause
    exit /b 1
)

echo.
echo ✓ 打包完成

echo.
echo [3/3] 清理临时文件...
if exist build rmdir /s /q build 2>nul
if exist "售前通讯录查询.spec" del /f /q "售前通讯录查询.spec" 2>nul
echo ✓ 清理完成

echo.
echo ========================================
echo 打包成功！
echo 可执行文件: dist\售前通讯录查询.exe
echo.
echo 重要提示：
echo 1. 打包已完成，程序不会自动运行
echo 2. 请手动进入 dist 目录运行
echo 3. 首次运行可能需要等待数据加载
echo ========================================
echo.
pause

REM 强制退出，不执行任何后续操作
exit /b 0
