param(
    [Parameter(Mandatory=$true)][ValidateSet('Install','Start','Stop','Restart','Status','Uninstall')][string]$Action,
    [string]$ServiceDir = 'C:\ProgramData\AP-ESOP\relay-service',
    [string]$ProgramRoot = '',
    [string]$PythonExe = '',
    [string]$ConfigFile = 'C:\ProgramData\AP-ESOP\relay.env',
    [string]$WinSWPath = '',
    [string]$ServiceId = 'AP-ESOP-Relay'
)
$ErrorActionPreference = 'Stop'
if ($ServiceId -notmatch '^[A-Za-z][A-Za-z0-9-]{1,60}$') { throw 'Invalid ServiceId' }
$serviceRoot = [IO.Path]::GetFullPath($ServiceDir)
$wrapper = Join-Path $serviceRoot "$ServiceId.exe"
$xmlPath = Join-Path $serviceRoot "$ServiceId.xml"
function Escape-Xml([string]$Value) { [Security.SecurityElement]::Escape($Value) }
if ($Action -eq 'Install') {
    if (-not $ProgramRoot) { $ProgramRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }
    $ProgramRoot = (Resolve-Path -LiteralPath $ProgramRoot).Path
    $PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
    $ConfigFile = (Resolve-Path -LiteralPath $ConfigFile).Path
    $WinSWPath = (Resolve-Path -LiteralPath $WinSWPath).Path
    if ((Get-FileHash -LiteralPath $WinSWPath -Algorithm SHA256).Hash -ne '923111C7142B3DC783A3C722B19B8A21BCB78222D7A136AC33F0CA8A29F4CB66') {
        throw 'Expected official WinSW.NET4.exe v2.12.0 (pinned SHA256). See relay/README.md.'
    }
    if (Get-Service -Name $ServiceId -ErrorAction SilentlyContinue) { throw 'Service already exists; stop before updating its program/configuration.' }
    Push-Location -LiteralPath $ProgramRoot
    try {
        & $PythonExe -m relay --config $ConfigFile --check
        if ($LASTEXITCODE -ne 0) { throw 'Relay configuration check failed' }
    } finally { Pop-Location }
    New-Item -ItemType Directory -Force -Path $serviceRoot | Out-Null
    Copy-Item -LiteralPath $WinSWPath -Destination $wrapper
    $pythonXml = Escape-Xml $PythonExe
    $rootXml = Escape-Xml $ProgramRoot
    $configXml = Escape-Xml $ConfigFile
    $logsXml = Escape-Xml (Join-Path $serviceRoot 'logs')
    @"
<service>
  <id>$ServiceId</id>
  <name>AP ESOP Workshop Relay</name>
  <description>ESOP read-only workshop cache and report forwarding. No local task creation.</description>
  <executable>$pythonXml</executable>
  <arguments>-m relay --config &quot;$configXml&quot;</arguments>
  <workingdirectory>$rootXml</workingdirectory>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <stoptimeout>45 sec</stoptimeout>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="30 sec"/>
  <onfailure action="restart" delay="60 sec"/>
  <resetfailure>1 hour</resetfailure>
  <logpath>$logsXml</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>5</keepFiles></log>
</service>
"@ | Set-Content -LiteralPath $xmlPath -Encoding UTF8
    & $wrapper install
    if ($LASTEXITCODE -ne 0) { throw 'WinSW installation failed; run elevated PowerShell.' }
    Write-Host 'Installed. Start with -Action Start. Service installation does not change firewall rules.'
} else {
    if (-not (Test-Path -LiteralPath $wrapper)) { throw 'Service wrapper not installed at ServiceDir' }
    $state=Get-Service -Name $ServiceId -ErrorAction SilentlyContinue
    if ($Action -eq 'Stop' -and $state -and $state.Status -eq 'Stopped') { Write-Host 'Service already stopped'; return }
    if ($Action -eq 'Start' -and $state -and $state.Status -eq 'Running') { Write-Host 'Service already running'; return }
    & $wrapper $Action.ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) { throw "WinSW $Action failed" }
    if ($Action -in @('Start','Restart','Stop')) {
        $state=Get-Service -Name $ServiceId
        $expected=if ($Action -eq 'Stop') { 'Stopped' } else { 'Running' }
        $state.WaitForStatus($expected, [TimeSpan]::FromSeconds(60))
    }
}
