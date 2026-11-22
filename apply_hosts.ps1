#Requires -RunAsAdministrator
<#
  荒野行动Plus —— 本地服务端 hosts 接管脚本
  作用：把「更新服 / 补丁服」域名指向 127.0.0.1，让启动器不再连已停服的网易服务器。
  只动 3 个域名，其它网易服务（网易邮箱、其它游戏）完全不受影响。
  卸载：powershell -File restore_hosts.ps1
#>
$ErrorActionPreference = 'Stop'
$hosts = "$env:SystemRoot\System32\drivers\etc\hosts"
$backup = Join-Path $PSScriptRoot 'hosts.backup.txt'
$mark = '# >>> 荒野行动Plus 本地服务端 >>>'
$end  = '# <<< 荒野行动Plus 本地服务端 <<<'

$domains = @(
    'pgx.update.netease.com',
    'autopatch.pgx.163.com',
    'update.pg.163.com'
)

if (-not (Test-Path $backup)) {
    Copy-Item $hosts $backup
    Write-Host "[OK] 已备份 hosts -> $backup"
}

$lines = Get-Content $hosts
if ($lines -contains $mark) {
    Write-Host '[i] hosts 里已经有本项目的条目，先清理旧条目…'
    $out = @(); $skip = $false
    foreach ($l in $lines) {
        if ($l -eq $mark) { $skip = $true; continue }
        if ($l -eq $end)  { $skip = $false; continue }
        if (-not $skip) { $out += $l }
    }
    $lines = $out
}

$block = @($mark) + ($domains | ForEach-Object { "127.0.0.1`t$_" }) + @($end)
Set-Content -Path $hosts -Value ($lines + $block) -Encoding ASCII

ipconfig /flushdns | Out-Null
Write-Host '[OK] hosts 已接管以下域名 -> 127.0.0.1 :'
$domains | ForEach-Object { Write-Host "     $_" }
Write-Host ''
Write-Host '下一步：双击 run_server.bat 启动本地服务端，然后运行 hyp_start.exe。'
