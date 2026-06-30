@echo off
chcp 65001 >nul
cd /d "%~dp0"
python export_prospect_students.py
if errorlevel 1 pause
exit /b %errorlevel%
