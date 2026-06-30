@echo off
chcp 65001 >nul
cd /d "%~dp0"
python upload_internal.py
if errorlevel 1 pause
exit /b %errorlevel%
