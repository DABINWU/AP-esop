# Requires elevated Windows PowerShell, a tested WinSW 2.12.0 binary, and an existing relay config.
# Installs only a temporary uniquely named service; always stops/uninstalls it in finally.
param(
    [Parameter(Mandatory=$true)][string]$ConfigFile,
    [Parameter(Mandatory=$true)][string]$WinSWPath,
    [string]$PythonExe = '',
    [string]$OutputDir = ''
)
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $PythonExe) { $PythonExe=Join-Path $repo '.venv\Scripts\python.exe' }
if (-not $OutputDir) { $OutputDir=Join-Path $repo ('test-output\relay-service-validation\run-'+[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$OutputDir=(Resolve-Path -LiteralPath $OutputDir).Path
$serviceId='AP-ESOP-Relay-Test-'+[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$listener=New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,0)
$listener.Start();$port=$listener.LocalEndpoint.Port;$listener.Stop()
$testConfig=Join-Path $OutputDir 'relay.env'
$config=Get-Content -LiteralPath $ConfigFile -Raw -Encoding UTF8
$config=[regex]::Replace($config,'(?m)^RELAY_PORT=.*$',"RELAY_PORT=$port")
$dataDir=(Join-Path $OutputDir 'data').Replace('\','/')
$config=[regex]::Replace($config,'(?m)^RELAY_DATA_DIR=.*$',"RELAY_DATA_DIR=$dataDir")
$config=[regex]::Replace($config,'(?m)^RELAY_HOST=.*$','RELAY_HOST=127.0.0.1')
$config | Set-Content -LiteralPath $testConfig -Encoding UTF8
$manage=Join-Path $repo 'relay\windows\Manage-Service.ps1'
$serviceDir=Join-Path $OutputDir 'service'
$installed=$false
$checks=New-Object Collections.Generic.List[string]
function Wait-Healthy {
    $deadline=[DateTime]::UtcNow.AddSeconds(50)
    do {
        try {
            $state=Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/v1/health" -TimeoutSec 2
            if ($state.status -eq 'ok') { return }
        } catch {}
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Temporary relay service did not become healthy'
}
try {
    & $manage -Action Install -ServiceDir $serviceDir -ProgramRoot $repo -PythonExe $PythonExe -ConfigFile $testConfig -WinSWPath $WinSWPath -ServiceId $serviceId
    $installed=$true
    & $manage -Action Start -ServiceDir $serviceDir -ServiceId $serviceId
    Wait-Healthy
    $service=Get-CimInstance Win32_Service -Filter "Name='$serviceId'"
    $children=@(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($service.ProcessId)" | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine.Contains($testConfig) })
    if ($children.Count -ne 1) { throw 'Could not uniquely identify temporary relay child process' }
    $childId=[int]$children[0].ProcessId
    if ((Get-Process -Id $childId).SessionId -ne 0) { throw 'Service child was not running in session 0' }
    $checks.Add('install, automatic delayed start configuration, SCM start, session 0, HTTP health')
    Stop-Process -Id $childId -Force
    Start-Sleep -Seconds 12
    Wait-Healthy
    $service=Get-CimInstance Win32_Service -Filter "Name='$serviceId'"
    $newChildren=@(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($service.ProcessId)" | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine.Contains($testConfig) })
    if ($newChildren.Count -ne 1 -or [int]$newChildren[0].ProcessId -eq $childId) { throw 'Service failure recovery did not create a new child' }
    $checks.Add('forced relay process termination -> SCM recovery -> new child PID -> HTTP health')
    & $manage -Action Stop -ServiceDir $serviceDir -ServiceId $serviceId
    if ((Get-Service -Name $serviceId).Status -ne 'Stopped') { throw 'Service did not stop' }
    & $manage -Action Start -ServiceDir $serviceDir -ServiceId $serviceId
    Wait-Healthy
    $checks.Add('SCM stop and start; persisted database retained')
} finally {
    if ($installed) {
        try { & $manage -Action Stop -ServiceDir $serviceDir -ServiceId $serviceId }
        finally { & $manage -Action Uninstall -ServiceDir $serviceDir -ServiceId $serviceId }
    }
}
if (Get-Service -Name $serviceId -ErrorAction SilentlyContinue) { throw 'Temporary service still registered' }
$checks.Add('service uninstall; configuration and data retained')
$result=@{kind='Windows SCM service validation, no Android hardware';checks=$checks;service_id=$serviceId;artifacts=$OutputDir;reboot_tested=$false;firewall_modified=$false}
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $OutputDir 'result.json') -Encoding UTF8
$result | ConvertTo-Json -Depth 5
