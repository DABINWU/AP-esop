param([string]$Url='http://127.0.0.1:18081')
$ErrorActionPreference = 'Stop'
try {
    $result = Invoke-RestMethod -Uri "$($Url.TrimEnd('/'))/api/v1/health" -TimeoutSec 5
    if ($result.status -ne 'ok') { exit 1 }
    Write-Host 'Relay healthy'
    exit 0
} catch { Write-Host 'Relay unhealthy or unreachable'; exit 1 }
