@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 即将实际写入飞书，请确认预览结果无误。
py -3.14 main.py --write
pause
