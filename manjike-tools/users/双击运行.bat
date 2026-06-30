@echo off
chcp 65001 > nul
setlocal

REM 切到 .bat 所在目录，确保相对路径解析正确
cd /d "%~dp0"

REM 优先用 Python 3.14；找不到再 fallback 到系统 PATH 里的 python
set "PY_EXE=D:\Program Files\Python314\python.exe"
if not exist "%PY_EXE%" (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] 未找到 Python 解释器，请安装 Python 3.7+ 或修改本脚本里的 PY_EXE。
        pause
        exit /b 1
    )
    set "PY_EXE=python"
)

"%PY_EXE%" upload-users.py %*
endlocal
