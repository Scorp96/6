param(
  [string]$ServiceName = 'ScorpPrivilegedBroker',
  [string]$InstallDir = 'C:\ScorpAgent\privileged-broker',
  [string]$RuntimeDir = 'C:\ScorpAgent\privileged-broker-runtime'
)
$ErrorActionPreference = 'Stop'
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
  if ($svc.Status -ne 'Stopped') {
    Stop-Service -Name $ServiceName -Force
    $svc.WaitForStatus('Stopped', (New-TimeSpan -Seconds 30))
  }
  & sc.exe delete $ServiceName | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'BROKER_SERVICE_DELETE_FAILED' }
}
if (Test-Path -LiteralPath $InstallDir) {
  Remove-Item -LiteralPath $InstallDir -Recurse -Force
}
if (Test-Path -LiteralPath $RuntimeDir) {
  Remove-Item -LiteralPath $RuntimeDir -Recurse -Force
}
Write-Output 'BROKER_UNINSTALL_PASS STATE_DATA=PRESERVED'
# Intentionally preserve C:\ProgramData\ScorpAgent\privileged-broker
