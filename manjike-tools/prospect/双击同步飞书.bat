@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/2] 导出 manjike 意向学员 JSON ...
python export_prospect_students.py
if errorlevel 1 (
  echo 导出失败，已中止。
  pause
  exit /b 1
)
echo.
echo [2/2] 同步飞书（默认 DRY_RUN，真实写入请加 --execute 或改 sync_to_feishu.config.json）...
python 同步意向学员到飞书.py --export-first
pause
exit /b %errorlevel%
