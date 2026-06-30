# One-click packaging script for 一键全流程.py
# Usage (PowerShell, run from this directory):
#   powershell -ExecutionPolicy Bypass -File .\打包.ps1
#
# Output:
#   .\dist\一键全流程\一键全流程.exe
#   .\dist\一键全流程\个人码相关\           (4 step scripts + 个人码回写\ 6 modules)
#   .\dist\一键全流程\_internal\            (PyInstaller runtime)
#   .\dist\一键全流程\README.txt

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 中文文件名 / 目录名一律用 [char] 转义，避免 .ps1 被 GBK 误解
$appName    = [char]0x4E00 + [char]0x952E + [char]0x5168 + [char]0x6D41 + [char]0x7A0B    # 一键全流程
$mainPy     = ".\" + $appName + ".py"
$stepDir    = ".\" + [char]0x4E2A + [char]0x4EBA + [char]0x7801 + [char]0x76F8 + [char]0x5173    # 个人码相关

Write-Host "==> Working dir: $PSScriptRoot"

$PY = "py"
$PYARGS = @("-3")
function Invoke-Py { & $PY @PYARGS @args }

Write-Host "==> Python version used for build:"
Invoke-Py --version

Write-Host "==> Ensuring dependencies (pyinstaller + runtime libs) ..."
Invoke-Py -m pip install --upgrade pip
Invoke-Py -m pip install --upgrade `
    pyinstaller `
    requests `
    pypinyin `
    opencv-python `
    numpy `
    psd-tools `
    Pillow

if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install failed, exit code: $LASTEXITCODE"
    exit $LASTEXITCODE
}

# 输出到 dist_new\ / build_new\，避免被 IDE / 文件管理器持有的 dist\ 旧目录冲突
# （Cursor IDE 把 build/dist 内的 exe 加入"recently viewed"会持有句柄导致无法清空）
$distOut  = ".\dist_new"
$buildOut = ".\build_new"

# 尽力清理上次的同名输出目录
if (Test-Path $buildOut) {
    Remove-Item -Recurse -Force $buildOut -ErrorAction SilentlyContinue
}
if (Test-Path $distOut) {
    Remove-Item -Recurse -Force $distOut -ErrorAction SilentlyContinue
}
Get-ChildItem -Filter "*.spec" -File | Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "==> Running PyInstaller ..."
# CLI 工具：保留 --console 让用户看到 print / 进度
# --onedir 配合外置 个人码相关\ 子目录，方便用户编辑各步骤脚本
Invoke-Py -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name $appName `
    --distpath $distOut `
    --workpath $buildOut `
    --collect-submodules requests `
    --collect-submodules pypinyin `
    --collect-data pypinyin `
    --collect-submodules psd_tools `
    --collect-data psd_tools `
    --collect-binaries cv2 `
    --hidden-import cv2 `
    --hidden-import numpy `
    --hidden-import PIL `
    --hidden-import PIL.Image `
    --hidden-import PIL.ImageDraw `
    --hidden-import PIL.ImageFont `
    --hidden-import requests `
    --hidden-import pypinyin `
    --hidden-import psd_tools `
    --hidden-import tkinter `
    --hidden-import tkinter.ttk `
    --hidden-import tkinter.scrolledtext `
    --hidden-import tkinter.messagebox `
    --hidden-import tkinter.filedialog `
    $mainPy

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed, exit code: $LASTEXITCODE"
    exit $LASTEXITCODE
}

# 拷贝整个"个人码相关"目录到产物根目录（PyInstaller --onedir 不会自动收集动态加载的脚本）
# 中文路径在 PowerShell 下用 Python 操作最稳，避免 Robocopy / Copy-Item 的中文路径折腾
$distDir = Join-Path $PSScriptRoot (($distOut.TrimStart('.','\')) + "\" + $appName)
$srcStep = Join-Path $PSScriptRoot $stepDir
$dstStep = Join-Path $distDir $stepDir

Write-Host "==> Copying step scripts: $stepDir -> $distOut\$appName\$stepDir"

$pyCopyCmd = @"
import os, shutil, sys
src = r'$srcStep'
dst = r'$dstStep'
if os.path.isdir(dst):
    shutil.rmtree(dst)
def _ignore(_dir, names):
    return [n for n in names if n == '__pycache__']
shutil.copytree(src, dst, ignore=_ignore)
print('copied step dir to', dst)
"@
Invoke-Py -c $pyCopyCmd

if ($LASTEXITCODE -ne 0) {
    Write-Error "拷贝 个人码相关 失败，exit code: $LASTEXITCODE"
    exit $LASTEXITCODE
}

# 写 README.txt（保持中文，文件已是 UTF-8 BOM）
$readmeLines = @(
    "Package output of 一键全流程.py",
    "Double-click 一键全流程.exe to launch the GUI.",
    "Or open PowerShell here and use CLI args (--cli / --only / --skip / --strict / --no-clean / --force-clean).",
    "",
    "GUI vs CLI dispatch:",
    "  - No CLI args   -> GUI mode (Tkinter window)",
    "  - --cli         -> CLI default (run all 4 steps + cleanup)",
    "  - --only / --skip / others -> CLI mode (backwards compatible)",
    "",
    "Layout:",
    "  一键全流程.exe          main program (CLI orchestrator)",
    "  _internal\              PyInstaller runtime deps, DO NOT DELETE",
    "  个人码相关\             4 step scripts (editable, restart exe to apply)",
    "    飞书保存合伙宝妈个人码2.py   Step 1: download QR + generate ledger txt",
    "    批量执行动作_二维码.py      Step 2: OpenCV preprocess QR images",
    "    生成个人码3.py              Step 3: PSD compose personal QR3",
    "    个人码回写\                  Step 4 module dir",
    "      main.py / config.py / feishu_api.py / file_parser.py /",
    "      logger.py / encoding_utils.py",
    "",
    "Common CLI usage (open PowerShell in this dir):",
    "  .\一键全流程.exe                          # run all 4 steps + cleanup",
    "  .\一键全流程.exe --only 1,4               # run only step 1 and 4",
    "  .\一键全流程.exe --skip 3                 # skip step 3",
    "  .\一键全流程.exe --strict                 # stop on first failure",
    "  .\一键全流程.exe --no-clean               # do not clean intermediate files",
    "  .\一键全流程.exe --force-clean            # cleanup even when previous steps failed",
    "",
    "Editing tips:",
    "  - Feishu APP_ID / APP_SECRET / APP_TOKEN / TABLE_ID",
    "      → 个人码相关\飞书保存合伙宝妈个人码2.py / 个人码相关\个人码回写\config.py",
    "  - PSD template path / output dir",
    "      → 个人码相关\生成个人码3.py (PSD_PATH / OUTPUT_DIR / BACKUP_DIR)",
    "  - Cleanup target list / backup directory",
    "      → 一键全流程.exe is built from 一键全流程.py; CLEANUP_TARGETS",
    "        is baked into the exe. Edit source + repackage if needed."
)
Set-Content -Path (Join-Path $distDir "README.txt") -Value $readmeLines -Encoding UTF8

Write-Host ""
Write-Host "==> DONE. Output dir:"
Write-Host "    $distDir"
