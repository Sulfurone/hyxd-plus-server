$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = 'C:\Users\SHI\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`""
    )
    exit
}

& $python -B (Join-Path $PSScriptRoot 'offline_client_patch.py') --apply
if ($LASTEXITCODE -ne 0) { throw 'Offline resource verification failed' }

$clientPath = Join-Path $root 'game\hyp_beta.exe'
$client = Get-Process hyp_beta -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $clientPath } | Select-Object -First 1
if ($client) {
    Write-Output "Local game is already running: $($client.Id)"
    exit
}

$stackPath = Join-Path $PSScriptRoot 'local_stack.py'
$stack = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*$stackPath*" } | Select-Object -First 1
if (-not $stack) {
    Start-Process $python -WindowStyle Hidden -WorkingDirectory $PSScriptRoot -ArgumentList @(
        '-B', '-u', "`"$stackPath`""
    ) -RedirectStandardOutput (Join-Path $PSScriptRoot 'logs\stack.stdout.log') `
      -RedirectStandardError (Join-Path $PSScriptRoot 'logs\stack.stderr.log')
}

$launcherPath = Join-Path $root 'bootloader.exe'
$launcher = Get-Process bootloader -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $launcherPath -and $_.MainWindowHandle -ne 0 } |
    Select-Object -First 1
if (-not $launcher) {
    Start-Process (Join-Path $root 'hyp_start.exe') -WorkingDirectory $root -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(60)
    do {
        Start-Sleep -Milliseconds 500
        $launcher = Get-Process bootloader -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -eq $launcherPath -and $_.MainWindowHandle -ne 0 } |
            Select-Object -First 1
    } until ($launcher -or (Get-Date) -ge $deadline)
}
if (-not $launcher) { throw 'The original launcher did not become ready' }

$redirectHandle = $launcher.MainWindowHandle.ToInt64()
$game = Start-Process $clientPath -WorkingDirectory (Join-Path $root 'game') `
    -ArgumentList @('-p', '1', '-redirect', "$redirectHandle") -WindowStyle Hidden -PassThru
Write-Output "Started local offline client: $($game.Id)"
