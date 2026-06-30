@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 优先使用项目根目录 .venv，避免系统默认 python 版本过旧
set "PY=%~dp0..\..\..\.venv\Scripts\python.exe"
if exist "%PY%" (
  "%PY%" main.py
) else (
  py -3 main.py
)
pause
