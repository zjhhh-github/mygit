@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/2] 从 manjike 导出意向学员 JSON ...
python "..\manjike-tools\prospect\export_prospect_students.py"
if errorlevel 1 (
  echo 导出失败，已中止。
  pause
  exit /b 1
)
echo.
echo [2/2] 同步到飞书（配置见 manjike-tools\prospect\sync_to_feishu.config.json，默认 dry_run 仅统计）...
python "同步意向学员到飞书.py"
pause
exit /b %errorlevel%
