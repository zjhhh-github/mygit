[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# $source = "C:\Users\LENOVO\Documents\chatlog\wxid_42272spv9uq522_6ded"
$source = "C:\Users\LENOVO\Documents\chatlog\wxid_1hac0y31mvc512_9895"
$destRoot = "X:\chatlog_backup"

# 生成备份目录时间戳（使用标准 ASCII 引号，避免解析异常）
$timestamp = Get-Date -Format "yyyyMMdd_HHmm"

# 拼接本次备份目录
$dest = Join-Path $destRoot ("wxid_42272spv9uq522_6ded_" + $timestamp)

# 创建目标目录并执行增量复制
New-Item -Path $dest -ItemType Directory -Force | Out-Null
robocopy $source $dest /E /Z /R:3 /W:5

# 输出完成信息
Write-Host "Backup completed: $dest"