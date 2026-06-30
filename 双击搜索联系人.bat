@echo off
chcp 65001 >nul
cd /d "%~dp0"
python search_contact.py %*
pause