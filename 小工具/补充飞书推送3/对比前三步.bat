@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.14 compare_only.py --preview --output-json compare_result.json --output-txt 待新增编号.txt
pause
