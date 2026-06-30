# 联系人搜索 GUI 一键打包脚本
# 用法（PowerShell，推荐在本脚本所在目录执行）：
#   powershell -ExecutionPolicy Bypass -File .\打包.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "==> 工作目录: $PSScriptRoot"

# 优先使用上级目录的虚拟环境 Python
$VenvPy = Join-Path (Split-Path $PSScriptRoot -Parent | Split-Path -Parent) ".venv\Scripts\python.exe"
if (Test-Path $VenvPy) {
    $PY = $VenvPy
    Write-Host "==> 使用虚拟环境: $PY"
} else {
    $PY = "py"
    $PYARGS = @("-3")
    function Invoke-Py { & $PY @PYARGS @args }
    Write-Host "==> 使用系统 Python: py -3"
    Invoke-Py --version
    Invoke-Py -m pip install --upgrade pip pyinstaller pillow
    & $PY @PYARGS (Join-Path $PSScriptRoot "build_gui.py")
    exit $LASTEXITCODE
}

& $PY (Join-Path $PSScriptRoot "build_gui.py")
exit $LASTEXITCODE
