@echo off
setlocal

echo MySQL数据库管理工具打包脚本
echo ================================

REM 检查是否安装了PyInstaller
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo 未找到PyInstaller，正在安装...
    pip install pyinstaller
)

REM 检查是否安装了其他依赖
python -c "import pymysql, pandas, pyperclip" 2>nul
if errorlevel 1 (
    echo 正在安装依赖...
    pip install -r requirements.txt
)

echo.
echo 选择要打包的版本:
echo 1. GUI版本 (图形界面)
echo 2. CLI版本 (命令行界面)
echo 3. 两个版本都打包
set /p choice="请输入选择 (1-3): "

if "%choice%"=="1" goto gui
if "%choice%"=="2" goto cli
if "%choice%"=="3" goto both

echo 无效选择，退出...
exit /b 1

:gui
echo.
echo 开始打包GUI版本...
pyinstaller --onefile --windowed --name="MySQL_GUI_Manager" refactored_gui_db_manager.py
echo GUI版本打包完成！
goto end

:cli
echo.
echo 开始打包CLI版本...
pyinstaller --onefile --console --name="MySQL_CLI_Manager" refactored_cli_db_manager.py
echo CLI版本打包完成！
goto end

:both
echo.
echo 开始打包GUI版本...
pyinstaller --onefile --windowed --name="MySQL_GUI_Manager" refactored_gui_db_manager.py

echo.
echo 开始打包CLI版本...
pyinstaller --onefile --console --name="MySQL_CLI_Manager" refactored_cli_db_manager.py

echo 两个版本都打包完成！
goto end

:end
echo.
echo 打包完成！生成的可执行文件在 dist/ 目录下。
pause