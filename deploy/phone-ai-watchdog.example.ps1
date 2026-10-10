$ErrorActionPreference = 'Stop'
$state = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'watchdog-status.json'
while ($true) {
    $health = $null
    $asr = $null
    try { $health = Invoke-RestMethod 'http://127.0.0.1:18880/health' -TimeoutSec 3 } catch {}
    try { $asr = Invoke-RestMethod 'http://127.0.0.1:18883/health' -TimeoutSec 3 } catch {}
    [ordered]@{
        time = (Get-Date).ToString('o')
        audioHealthy = ($health.ok -eq $true)
        asrHealthy = ($asr.ok -eq $true)
    } | ConvertTo-Json | Set-Content -LiteralPath $state -Encoding UTF8
    Start-Sleep -Seconds 15
}
