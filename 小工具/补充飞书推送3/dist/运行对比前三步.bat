@echo off
chcp 65001 >nul
cd /d "%~dp0"
feishu_compare.exe %*
pause
