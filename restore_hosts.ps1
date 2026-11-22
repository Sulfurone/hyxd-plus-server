#Requires -RunAsAdministrator
<#
  还原 hosts —— 撤销 apply_hosts.ps1 的全部改动。
#>
$ErrorActionPreference = 'Stop'
$hosts = "$env:SystemRoot\System32\drivers\etc\hosts"
$backup = Join-Path $PSScriptRoot 'hosts.backup.txt'

if (Test-Path $backup) {
    Copy-Item $backup $hosts -Force
    Write-Host "[OK] hosts 已从备份还原（$backup）"
} else {
    Write-Host '[!] 找不到 hosts.backup.txt，改用条目删除方式'
    $mark = '# >>> 荒野行动Plus 本地服务端 >>>'
    $end  = '# <<< 荒野行动Plus 本地服务端 <<<'
    $out = @(); $skip = $false
    foreach ($l in (Get-Content $hosts)) {
        if ($l -eq $mark) { $skip = $true; continue }
        if ($l -eq $end)  { $skip = $false; continue }
        if (-not $skip) { $out += $l }
    }
    Set-Content -Path $hosts -Value $out -Encoding ASCII
    Write-Host '[OK] 已删除本项目条目'
}
ipconfig /flushdns | Out-Null
