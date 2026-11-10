# 新平食品 P1 · 备份与恢复
# 备份 = 打包数据目录（数据库 + 打卡照片 + 签名密钥），三样东西缺一不可。
#
# 备份：powershell -File deploy\backup.ps1
# 恢复：把解压出来的目录放回 $P1_DATA，重启服务即可

param(
    [string]$DataDir = "",
    [string]$OutDir = "",
    [int]$KeepDays = 14
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not $DataDir) { $DataDir = Join-Path $root '_work' }
if (-not $OutDir) { $OutDir = Join-Path $root 'backups' }
if (-not (Test-Path $DataDir)) { Write-Host "数据目录不存在：$DataDir" -ForegroundColor Red; exit 1 }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$zip = Join-Path $OutDir "p1-$stamp.zip"

# SQLite 是单文件，直接拷会拿到「正在写入」的半个文件；先做一致性快照再打包
$db = Join-Path $DataDir 'p1.db'
if (Test-Path $db) {
    $snap = Join-Path $env:TEMP "p1-snap-$stamp.db"
    python -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" $db $snap
    Compress-Archive -Path $snap -DestinationPath $zip -Force
    Remove-Item $snap -Force
    # 把图片和密钥追加进同一个 zip
    $tmp = Join-Path $env:TEMP "p1-extra-$stamp"
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    if (Test-Path (Join-Path $DataDir 'uploads')) { Copy-Item (Join-Path $DataDir 'uploads') $tmp -Recurse -Force }
    if (Test-Path (Join-Path $DataDir '.secret')) { Copy-Item (Join-Path $DataDir '.secret') $tmp -Force }
    Compress-Archive -Path (Join-Path $tmp '*') -DestinationPath $zip -Update
    Remove-Item $tmp -Recurse -Force
} else {
    Compress-Archive -Path (Join-Path $DataDir '*') -DestinationPath $zip -Force
}

$size = [math]::Round((Get-Item $zip).Length / 1KB, 1)
Write-Host "已备份：$zip（$size KB）" -ForegroundColor Green

# 清理过期备份
$old = Get-ChildItem $OutDir -Filter 'p1-*.zip' | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) }
if ($old) {
    $old | Remove-Item -Force
    Write-Host "清理了 $($old.Count) 个 $KeepDays 天前的备份" -ForegroundColor DarkGray
}
