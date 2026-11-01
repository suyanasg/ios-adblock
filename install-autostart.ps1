# install-autostart.ps1 — 把小火箭模块推送服务注册成开机自启
# 用法: powershell -ExecutionPolicy Bypass -File install-autostart.ps1
# 卸载: powershell -ExecutionPolicy Bypass -File install-autostart.ps1 -Uninstall

param(
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$TaskName = 'ios-adblock-serve'
$Root = $PSScriptRoot

function Remove-Task {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已删除计划任务: $TaskName"
    } else {
        Write-Host "计划任务不存在: $TaskName"
    }
}

if ($Uninstall) {
    Remove-Task
    exit 0
}

# pythonw 优先用系统 Python，便携版可能随工具升级被删
$Py = 'C:\Program Files\Python312\pythonw.exe'
if (-not (Test-Path $Py)) {
    $Py = 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe'
}
if (-not (Test-Path $Py)) {
    Write-Host "找不到 pythonw.exe，请确认 Python 安装位置" -ForegroundColor Red
    exit 1
}

Remove-Task

$action = New-ScheduledTaskAction -Execute $Py -Argument 'serve.py' -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'ios-adblock: 在 8000 端口推送小火箭去广告模块' | Out-Null

Write-Host "已注册计划任务: $TaskName"
Write-Host "  python : $Py"
Write-Host "  目录   : $Root"
Write-Host "  触发   : 登录时"
Write-Host "  日志   : $Root\logs\serve.log"
Write-Host ""
Write-Host "立即启动一次:"

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 4

$conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Write-Host "  服务已在 8000 监听" -ForegroundColor Green
} else {
    Write-Host "  端口未监听，看日志: $Root\logs\serve.log" -ForegroundColor Yellow
}
