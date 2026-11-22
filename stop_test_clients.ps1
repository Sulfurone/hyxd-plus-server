param([string]$ClientIds)
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\game'))
foreach ($clientIdText in $ClientIds.Split(',')) {
    $clientId = [int]::Parse($clientIdText)
    $client = Get-Process -Id $clientId -ErrorAction SilentlyContinue
    if ($client -and $client.Path -and
        [IO.Path]::GetDirectoryName($client.Path) -eq $root -and
        $client.ProcessName -in @('hyp', 'hyp_beta', 'hypprotect', 'BrowserProxy')) {
        Stop-Process -Id $clientId -Force -ErrorAction Stop
    }
}
