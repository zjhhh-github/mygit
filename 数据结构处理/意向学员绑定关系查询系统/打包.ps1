# One-click packaging script for 导入控制台.py
# Usage (in current dir, PowerShell):
#   powershell -ExecutionPolicy Bypass -File .\build.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$appName = [char]0x5BFC + [char]0x5165 + [char]0x63A7 + [char]0x5236 + [char]0x53F0   # "导入控制台"
$mainPy  = ".\" + $appName + ".py"
$incPy   = ".\" + [char]0x589E + [char]0x91CF + [char]0x5BFC + [char]0x5165 + ".py"  # "增量导入.py"
$diagPy  = ".\" + [char]0x8BCA + [char]0x65AD + [char]0x5FAE + [char]0x4FE1 + [char]0x53F7 + ".py"  # "诊断微信号.py"
# "上传用户结构.py" —— 控制台 Tab 5「用户上传」运行时通过 spec_from_file_location 动态加载
$uploadPy = ".\" + [char]0x4E0A + [char]0x4F20 + [char]0x7528 + [char]0x6237 + [char]0x7ED3 + [char]0x6784 + ".py"
# "内部备注导入.py" —— 控制台 Tab 2「内部备注导入」运行时动态加载，必须随 exe 同目录发布
$notePy   = ".\" + [char]0x5185 + [char]0x90E8 + [char]0x5907 + [char]0x6CE8 + [char]0x5BFC + [char]0x5165 + ".py"
# "定时拷贝任务.py" —— 控制台 Tab 3「定时拷贝」运行时动态加载，必须随 exe 同目录发布
$copyTaskPy = ".\" + [char]0x5B9A + [char]0x65F6 + [char]0x62F7 + [char]0x8D1D + [char]0x4EFB + [char]0x52A1 + ".py"
# "示例插件.py" —— Tab 6「扩展功能」示例，方便用户照着写自己的插件脚本
$samplePluginPy = ".\" + [char]0x793A + [char]0x4F8B + [char]0x63D2 + [char]0x4EF6 + ".py"
# "意向学员关系导入到飞书多维表格.py" —— 控制台 Tab 4「飞书全量同步」运行时动态加载（项目同目录）
$feishuSyncPy = ".\" + [char]0x610F + [char]0x5411 + [char]0x5B66 + [char]0x5458 + [char]0x5173 + [char]0x7CFB + [char]0x5BFC + [char]0x5165 + [char]0x5230 + [char]0x98DE + [char]0x4E66 + [char]0x591A + [char]0x7EF4 + [char]0x8868 + [char]0x683C + ".py"

Write-Host "==> Working dir: $PSScriptRoot"

$PY = "py"
$PYARGS = @("-3")
function Invoke-Py { & $PY @PYARGS @args }

Write-Host "==> Python version used for build:"
Invoke-Py --version

Write-Host "==> Ensuring dependencies (pyinstaller, requests) ..."
Invoke-Py -m pip install --upgrade pip
Invoke-Py -m pip install --upgrade pyinstaller requests

# 先把 dist 里已存在的运行时配置 JSON 备份到内存，打包结束后还原
# 这样用户在 GUI 里保存的脚本路径 / 启用状态等不会因为重新打包而丢失
$preservedConfigs = @{}
$preservedConfigNames = @(
    "user_upload_console_config.json",
    "note_import_console_config.json",
    "feishu_sync_console_config.json",
    "tab1_console_config.json",
    "custom_plugins.json",
    "copy_tasks.json"
)
$existingDist = Join-Path $PSScriptRoot ("dist\" + $appName)
foreach ($cfgName in $preservedConfigNames) {
    $cfgPath = Join-Path $existingDist $cfgName
    if (Test-Path $cfgPath) {
        try {
            $preservedConfigs[$cfgName] = Get-Content -Raw -Path $cfgPath -Encoding UTF8
            Write-Host "==> Preserved existing config: $cfgName"
        } catch {
            Write-Warning "Preserve config failed ($cfgName): $_"
        }
    }
}

if (Test-Path ".\build") { Remove-Item -Recurse -Force ".\build" }
if (Test-Path ".\dist")  { Remove-Item -Recurse -Force ".\dist"  }
Get-ChildItem -Filter "*.spec" -File | Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "==> Running PyInstaller ..."
Invoke-Py -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name $appName `
    --collect-submodules requests `
    --collect-submodules pandas `
    --collect-submodules numpy `
    --collect-data openpyxl `
    --hidden-import sqlite3 `
    --hidden-import pandas `
    --hidden-import numpy `
    --hidden-import openpyxl `
    --hidden-import xlrd `
    --hidden-import tqdm `
    --hidden-import tkinter `
    --hidden-import tkinter.ttk `
    --hidden-import tkinter.scrolledtext `
    --hidden-import tkinter.messagebox `
    --hidden-import tkinter.filedialog `
    --hidden-import task_runner `
    --hidden-import custom_plugins `
    $mainPy

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed, exit code: $LASTEXITCODE"
    exit $LASTEXITCODE
}

$distDir = Join-Path $PSScriptRoot ("dist\" + $appName)
Copy-Item -Force $incPy (Join-Path $distDir (Split-Path $incPy -Leaf))

if (Test-Path $diagPy) {
    Copy-Item -Force $diagPy (Join-Path $distDir (Split-Path $diagPy -Leaf))
}

# 「用户上传」Tab 通过 spec_from_file_location 动态加载，必须随 exe 同目录发布
if (Test-Path $uploadPy) {
    Copy-Item -Force $uploadPy (Join-Path $distDir (Split-Path $uploadPy -Leaf))
    Write-Host "==> Copied: $uploadPy"
} else {
    Write-Warning "未找到 上传用户结构.py，打包后「用户上传」Tab 将不可用。"
}

# 「内部备注导入」Tab 同样通过动态加载，必须随 exe 同目录发布
if (Test-Path $notePy) {
    Copy-Item -Force $notePy (Join-Path $distDir (Split-Path $notePy -Leaf))
    Write-Host "==> Copied: $notePy"
} else {
    Write-Warning "未找到 内部备注导入.py，打包后「内部备注导入」Tab 将不可用。"
}

# 「定时拷贝」Tab 同样通过动态加载，必须随 exe 同目录发布
if (Test-Path $copyTaskPy) {
    Copy-Item -Force $copyTaskPy (Join-Path $distDir (Split-Path $copyTaskPy -Leaf))
    Write-Host "==> Copied: $copyTaskPy"
} else {
    Write-Warning "未找到 定时拷贝任务.py，打包后「定时拷贝」Tab 将回退内置实现。"
}

# 「飞书全量同步」Tab 的外置脚本（项目同目录）
if (Test-Path $feishuSyncPy) {
    Copy-Item -Force $feishuSyncPy (Join-Path $distDir (Split-Path $feishuSyncPy -Leaf))
    Write-Host "==> Copied: $feishuSyncPy"
} else {
    Write-Warning "未找到 意向学员关系导入到飞书多维表格.py，打包后「飞书全量同步」Tab 将不可用。"
}

# 扩展功能示例插件：拷一个给用户参考；他们可以照着这个文件写自己的插件
if (Test-Path $samplePluginPy) {
    Copy-Item -Force $samplePluginPy (Join-Path $distDir (Split-Path $samplePluginPy -Leaf))
    Write-Host "==> Copied: $samplePluginPy"
}

# 还原"打包前备份"的运行时配置 JSON，避免 GUI 设置被清掉
foreach ($cfgName in $preservedConfigs.Keys) {
    try {
        $target = Join-Path $distDir $cfgName
        Set-Content -Path $target -Value $preservedConfigs[$cfgName] -Encoding UTF8 -NoNewline
        Write-Host "==> Restored config: $cfgName"
    } catch {
        Write-Warning "Restore config failed ($cfgName): $_"
    }
}

$readmeLines = @(
    "Package output.",
    "Double-click 导入控制台.exe to run.",
    "",
    "Layout:",
    "  导入控制台.exe                main program",
    "  增量导入.py                   Tab1 增量导入 - 修改后需重启 exe 生效",
    "  内部备注导入.py               Tab2 内部备注导入 - 修改后立即生效, 无需重启",
    "  定时拷贝任务.py               Tab3 定时拷贝外置脚本 - 修改后立即生效, 无需重启",
    "  意向学员关系导入到飞书多维表格.py  Tab4 飞书全量同步 - 修改后立即生效, 无需重启",
    "  上传用户结构.py               Tab5 用户上传 (Feishu -> Miaoda import-users), 修改后立即生效",
    "  示例插件.py                   Tab6 扩展功能 示例脚本, 可照着写自己的插件",
    "  诊断微信号.py                 optional diagnostic (requires Python + requests installed)",
    "  _internal\\                    PyInstaller runtime deps, DO NOT DELETE",
    "",
    "Auto-generated config files (会自动出现在本目录, 保留你 GUI 上的设置):",
    "  tab1_console_config.json              Tab1 首次触发时间等",
    "  note_import_console_config.json       Tab2 脚本路径 / DB 路径 / 导出 JSON 路径 / 上传模式",
    "  feishu_sync_console_config.json       Tab4 脚本路径 / 间隔 / 首次触发时间 / DRY_RUN",
    "  user_upload_console_config.json       Tab5 脚本路径 / 视图 ID / 间隔 / 首次触发时间 / 模式",
    "  custom_plugins.json                   Tab6 扩展功能 插件列表（首次运行自动生成空模板）",
    "  copy_tasks.json                       Tab3 定时拷贝任务",
    "",
    "Tab6 扩展功能 - 不用重新打包就能加新功能:",
    "  1) 准备一个 Python 脚本, 暴露 def run(log, **params) -> bool",
    "  2) 在 Tab6 点「+ 新增功能」, 填名称 / 脚本路径 / 参数(JSON) 保存",
    "  3) 之后随时改脚本立即生效 (立即执行时会重新加载)",
    "  4) 「立即执行」默认进全局队列, 顶部状态栏可见当前 + 等待任务",
    "  各 Tab 右侧的「加入全局队列」复选框: 勾上则提交到队列, 不勾保持原 skip 行为",
    "",
    "Tab1 增量导入   - edit 增量导入.py CONFIG:",
    "  SUPABASE_URL / ANON_KEY / USERNAME / PASSWORD",
    "  SKIP_ENROLLED_OR_BOUND / SKIP_CASE_INSENSITIVE / DEBUG_WXIDS",
    "",
    "Tab2 内部备注导入 - edit 内部备注导入.py if needed:",
    "  SQL 筛选规则 (load_from_db) / DEFAULT_BATCH_SIZE / 重试策略",
    "",
    "Tab4 飞书全量同步 - edit 意向学员关系导入到飞书多维表格.py:",
    "  APP_ID / APP_SECRET / APP_TOKEN / TABLE_ID / 秒哒用户 JWT 等参数",
    "",
    "Tab5 用户上传   - edit 上传用户结构.py top section if needed:",
    "  Feishu: APP_TOKEN / TABLE_ID / VIEW_ID / FIELD_ID / FIELD_PASSWORD / FIELD_WECHAT",
    "  Miaoda: SUPABASE_URL / SUPABASE_ANON_KEY / SUPABASE_EMAIL / SUPABASE_PASSWORD",
    "  Other : UPLOAD_BATCH_SIZE (default 200)",
    "",
    "敏感文件 (需要手动放进本目录, 不会自动复制):",
    "  .env                          飞书自动化脚本的 Supabase 账号密码"
)
Set-Content -Path (Join-Path $distDir "README.txt") -Value $readmeLines -Encoding UTF8

Write-Host ""
Write-Host "==> DONE. Output dir:"
Write-Host "    $distDir"
