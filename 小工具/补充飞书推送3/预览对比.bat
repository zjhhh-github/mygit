@echo off
chcp 65001 >nul
cd /d "%~dp0\dist"
feishu_compare.exe --preview %*
pause
