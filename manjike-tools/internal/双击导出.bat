@echo off
chcp 65001 >nul
cd /d "%~dp0"
python export_internal_contacts.py
if errorlevel 1 pause
exit /b %errorlevel%
