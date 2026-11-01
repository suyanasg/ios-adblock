# daily-build.ps1 — 本地每日同步去广告规则
#
# 干三件事：抓上游 → 构建模块 → （可选）更新 Gist
# 没有 GitHub 仓库也能跑，构建完 dist/ 就是最新的，手机重导一次即可。
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File daily-build.ps1             # 跑一次
#   powershell -ExecutionPolicy Bypass -File daily-build.ps1 -Install    # 注册每天 04:00
#   powershell -ExecutionPolicy Bypass -File daily-build.ps1 -Uninstall
#   powershell -ExecutionPolicy Bypass -File daily-build.ps1 -NoSync     # 跳过抓取，只重建

param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$NoSync,
    [string]$ScriptUrl = ""
)

$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 默认按 GBK 读子进程输出，python 吐的是 UTF-8，
# 不设这两行日志里全是乱码。
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }
$env:PYTHONIOENCODING = 'utf-8'

$TaskName = 'ios-adblock-daily'
$Root = $PSScriptRoot
$LogDir = Join-Path $Root 'logs'
$Log = Join-Path $LogDir 'daily.log'

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -Path $Log -Value $line -Encoding utf8
}

function Find-Python {
    $cands = @(
        'C:\Program Files\Python312\python.exe',
        'C:\Program Files\Python311\python.exe',
        'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe'
    )
    foreach ($c in $cands) {
        if (Test-Path $c) { return $c }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "找不到 python.exe"
}

function Remove-Task {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已删除计划任务: $TaskName"
    } else {
        Write-Host "计划任务不存在: $TaskName"
    }
}

if ($Uninstall) { Remove-Task; exit 0 }

if ($Install) {
    $py = Find-Python
    Remove-Task
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument ('-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File "{0}"' -f (Join-Path $Root 'daily-build.ps1')) `
        -WorkingDirectory $Root
    $trigger = New-ScheduledTaskTrigger -Daily -At '04:00'
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 2 `
        -RestartInterval (New-TimeSpan -Minutes 15) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    Register-ScheduledTask -TaskName $TaskName `
        -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description 'ios-adblock: 每天 04:00 同步上游规则并重建小火箭模块' | Out-Null

    Write-Host "已注册计划任务: $TaskName（每天 04:00）"
    Write-Host "  python : $py"
    Write-Host "  日志   : $Log"
    Write-Host ""
    Write-Host "想立刻跑一次验证："
    Write-Host "  powershell -ExecutionPolicy Bypass -File daily-build.ps1"
    exit 0
}

# ---------------------------------------------------------------- 正经干活

$py = Find-Python

Write-Log "=== 每日构建开始 ==="

Push-Location $Root

# 1. 抓上游
if (-not $NoSync) {
    try {
        Write-Log "抓取上游规则 ..."
        $out = & $py 'sync.py' 2>&1
        $out | ForEach-Object { Write-Log "  $_" }
        if ($LASTEXITCODE -ne 0) {
            Write-Log "!! sync 失败（退出码 $LASTEXITCODE），沿用上一轮 auto.list 继续构建"
        }
    } catch {
        Write-Log "!! sync 异常: $_"
    }
} else {
    Write-Log "跳过抓取（-NoSync）"
}

# 2. 构建
Write-Log "构建模块 ..."
$buildArgs = @('build.py')
if ($ScriptUrl -ne '') {
    $buildArgs += @('--script-url', $ScriptUrl)
}
$out = & $py @buildArgs 2>&1
$out | ForEach-Object { Write-Log "  $_" }
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Log "!! 构建失败（退出码 $code），dist/ 保持上一轮内容"
    Pop-Location
    exit 1
}

# 3. 更新 Gist（有 token 才做）
$token = $env:GIST_TOKEN
$gistId = $env:GIST_ID
if ($token -and $gistId) {
    Write-Log "更新 Gist $gistId ..."
    $out = & $py 'deploy-gist.py' '--update' $gistId 2>&1
    $out | ForEach-Object { Write-Log "  $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "!! Gist 更新失败，模块本身已经是最新的"
    }
} else {
    Write-Log "没配 GIST_TOKEN / GIST_ID，跳过 Gist。手机重导一次 dist/ 里的模块即可"
}

Pop-Location

$size = (Get-ChildItem (Join-Path $Root 'dist') -Filter '*.module' |
         Measure-Object -Property Length -Sum).Sum
Write-Log ("=== 完成，产物合计 {0:N1} KB ===" -f ($size / 1024))
