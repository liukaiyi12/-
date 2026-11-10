# 新平食品 P1 · Windows 上跑起来（不用 Docker）
# 用法：右键「使用 PowerShell 运行」，或者  powershell -ExecutionPolicy Bypass -File deploy\run-windows.ps1
#
# 默认是「演示模式」（localhost 能开、别人打不开）。
# 要让同一局域网的人也能打开：加 -Lan 参数，会绑 0.0.0.0 并提示防火墙放行命令。

param(
    [switch]$Lan,
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$env:P1_ENV = 'dev'
$env:P1_PORT = "$Port"
$env:P1_HOST = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }
$env:PYTHONIOENCODING = 'utf-8'

$py = 'python'
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host '找不到 python，请先安装 Python 3.10+' -ForegroundColor Red
    exit 1
}

Write-Host "工作目录：$root" -ForegroundColor DarkGray
Write-Host "监听：$($env:P1_HOST):$Port  （Ctrl+C 停止）" -ForegroundColor Cyan

if ($Lan) {
    Write-Host ''
    Write-Host '要让同网段的朋友打开，还需要防火墙放行一次（管理员 PowerShell 执行）：' -ForegroundColor Yellow
    Write-Host "  netsh advfirewall firewall add rule name=`"xinping-$Port`" dir=in action=allow protocol=TCP localport=$Port"
    Write-Host ''
    Write-Host '然后把你本机的内网地址发给对方（ipconfig 里的 IPv4）：' -ForegroundColor Yellow
    Write-Host "  http://<你的内网IP>:$Port/"
    Write-Host ''
}

python 07_p1_server.py
