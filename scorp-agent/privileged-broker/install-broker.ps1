param(
  [string]$SourceDir = $PSScriptRoot,
  [string]$InstallDir = 'C:\ScorpAgent\privileged-broker',
  [string]$RuntimeDir = 'C:\ScorpAgent\privileged-broker-runtime',
  [string]$StateDir = 'C:\ProgramData\ScorpAgent\privileged-broker',
  [string]$ServiceName = 'ScorpPrivilegedBroker'
)
$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = 'C:\ScorpAgent\backups'
$uv = 'C:\Users\scorp\AppData\Local\Microsoft\WinGet\Links\uv.exe'
$python = Join-Path $RuntimeDir 'Scripts\python.exe'
$secretPath = Join-Path $StateDir 'secret.key'
$ledgerPath = Join-Path $StateDir 'ledger.json'
$auditPath = Join-Path $StateDir 'audit.jsonl'
$configPath = Join-Path $StateDir 'config.json'
$candidateDir = "$InstallDir.candidate.$stamp"
$backupInstall = Join-Path $backupRoot "privileged-broker-$stamp"
$backupService = Join-Path $backupRoot "privileged-broker-service-$stamp.json"
$files = @('broker_core.py','broker_ops.py','broker_service.py','broker_client.py')
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$createdService = $false
$priorService = $null
$wasRunning = $false
function Invoke-Sc([string[]]$ScArgs) {
  $output = & sc.exe @ScArgs 2>&1
  if ($LASTEXITCODE -ne 0) { throw "SC_FAILED: $($ScArgs -join ' ') :: $output" }
  return $output
}
function Wait-ServiceDeleted([string]$Name, [int]$TimeoutSeconds = 30) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (-not (Get-Service -Name $Name -ErrorAction SilentlyContinue)) { return }
    Start-Sleep -Milliseconds 250
  }
  throw 'SERVICE_DELETE_TIMEOUT'
}
function Stop-ServiceIfPresent([string]$Name) {
  $svc = Get-Service -Name $Name -ErrorAction SilentlyContinue
  if ($svc -and $svc.Status -ne 'Stopped') {
    Stop-Service -Name $Name -Force
    $svc.WaitForStatus('Stopped', (New-TimeSpan -Seconds 30))
  }
}
function Remove-ServiceIfPresent([string]$Name) {
  Stop-ServiceIfPresent $Name
  if (Get-Service -Name $Name -ErrorAction SilentlyContinue) {
    Invoke-Sc @('delete', $Name) | Out-Null
    Wait-ServiceDeleted $Name
  }
}function New-BrokerService([string]$Name, [string]$BinaryPath) {
  Invoke-Sc @('create', $Name, 'binPath=', $BinaryPath, 'start=', 'auto',
              'obj=', 'LocalSystem', 'DisplayName=', 'Scorp Privileged Broker') | Out-Null
  Invoke-Sc @('description', $Name,
              'LocalSystem allowlisted privilege broker for Scorp Agent.') | Out-Null
}
function Restore-PriorService($Prior) {
  if (-not $Prior) { return }
  $start = switch ($Prior.StartMode) {
    'Auto' {'auto'} 'Manual' {'demand'} 'Disabled' {'disabled'} default {'demand'}
  }
  Invoke-Sc @('create', $Prior.Name, 'binPath=', $Prior.PathName,
              'start=', $start, 'obj=', $Prior.StartName,
              'DisplayName=', $Prior.DisplayName) | Out-Null
  if ($Prior.Description) {
    Invoke-Sc @('description', $Prior.Name, $Prior.Description) | Out-Null
  }
  if ($Prior.State -eq 'Running') { Start-Service -Name $Prior.Name }
}
function Set-StateAcl([string]$Path, [string]$Sid) {
  & icacls.exe $Path '/inheritance:r' '/grant:r' '*S-1-5-18:(OI)(CI)F' `
    '*S-1-5-32-544:(OI)(CI)F' "*$($Sid):(OI)(CI)R" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'STATE_ACL_FAILED' }
}
function Set-SecretAcl([string]$Path, [string]$Sid) {
  & icacls.exe $Path '/inheritance:r' '/grant:r' '*S-1-5-18:F' `
    '*S-1-5-32-544:F' "*$($Sid):R" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'SECRET_ACL_FAILED' }
}$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw 'ADMIN_REQUIRED'
}
if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) { throw 'UV_MISSING' }
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
if (Test-Path -LiteralPath $candidateDir) { Remove-Item $candidateDir -Recurse -Force }
New-Item -ItemType Directory -Path $candidateDir -Force | Out-Null
foreach ($name in $files) {
  $src = Join-Path $SourceDir $name
  if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { throw "SOURCE_FILE_MISSING $name" }
  Copy-Item -LiteralPath $src -Destination (Join-Path $candidateDir $name) -Force
}
$priorCim = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
if ($priorCim) {
  if ($priorCim.StartName -notin @('LocalSystem','NT AUTHORITY\LocalService','NT AUTHORITY\NetworkService')) {
    throw 'BROKER_EXISTING_SERVICE_OWNER_UNEXPECTED'
  }
  $priorService = [ordered]@{
    Name=$priorCim.Name; DisplayName=$priorCim.DisplayName; Description=$priorCim.Description
    PathName=$priorCim.PathName; StartMode=$priorCim.StartMode; StartName=$priorCim.StartName; State=$priorCim.State
  }
  $wasRunning = ($priorCim.State -eq 'Running')
  [IO.File]::WriteAllText($backupService, ($priorService | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding($false)))
}if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  & $uv venv --python 3.14 $RuntimeDir
  if ($LASTEXITCODE -ne 0) { throw 'BROKER_RUNTIME_CREATE_FAILED' }
}
& $uv pip install --python $python 'pywin32==312'
if ($LASTEXITCODE -ne 0) { throw 'BROKER_RUNTIME_DEPENDENCY_FAILED' }
foreach ($name in $files) {
  & $python -m py_compile (Join-Path $candidateDir $name)
  if ($LASTEXITCODE -ne 0) { throw "BROKER_PYCOMPILE_FAILED $name" }
}
if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) {
  $secret = New-Object byte[] 32
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($secret) } finally { $rng.Dispose() }
  [IO.File]::WriteAllBytes($secretPath, $secret)
}
if ((Get-Item -LiteralPath $secretPath).Length -ne 32) { throw 'SECRET_INVALID' }
$config = [ordered]@{
  protocol_version='scorp.broker/config-v1'
  allowed_user_sid=$userSid
  installed_by=[Security.Principal.WindowsIdentity]::GetCurrent().Name
}
[IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding($false)))
Set-StateAcl $StateDir $userSid
Set-SecretAcl $secretPath $userSid
try {
  if (Test-Path -LiteralPath $InstallDir -PathType Container) {
    New-Item -ItemType Directory -Path $backupInstall -Force | Out-Null
    Get-ChildItem -LiteralPath $InstallDir -Force | Copy-Item -Destination $backupInstall -Recurse -Force
  }
  Remove-ServiceIfPresent $ServiceName
  if (-not $priorService) { $createdService = $true }
  if (Test-Path -LiteralPath $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
  Move-Item -LiteralPath $candidateDir -Destination $InstallDir

  $serviceScript = Join-Path $InstallDir 'broker_service.py'
  $clientScript = Join-Path $InstallDir 'broker_client.py'
  $binPath = '"' + $python + '" "' + $serviceScript + '" --service-host'
  New-BrokerService $ServiceName $binPath
  Start-Service -Name $ServiceName
  (Get-Service -Name $ServiceName).WaitForStatus('Running', (New-TimeSpan -Seconds 30))

  $requestId = "install-identity-$stamp"
  $raw = & $python $clientScript identity.get --request-id $requestId --secret $secretPath 2>&1
  if ($LASTEXITCODE -ne 0) { throw "BROKER_IDENTITY_CLIENT_FAILED $raw" }
  $reply = ($raw | Select-Object -Last 1) | ConvertFrom-Json
  if ($reply.result.sid -ne 'S-1-5-18' -or -not [bool]$reply.result.is_local_system) {
    throw 'BROKER_NOT_LOCALSYSTEM'
  }
  $svc = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
  if ($svc.State -ne 'Running' -or $svc.StartName -ne 'LocalSystem' -or $svc.StartMode -ne 'Auto') {
    throw 'BROKER_SERVICE_CONTRACT_FAILED'
  }  [pscustomobject]@{
    status='BROKER_INSTALL_PASS'
    service=$ServiceName
    state=$svc.State
    start_mode=$svc.StartMode
    start_name=$svc.StartName
    identity=$reply.result.identity
    sid=$reply.result.sid
    is_local_system=[bool]$reply.result.is_local_system
    runtime=$python
    install_dir=$InstallDir
    state_dir=$StateDir
    secret_path=$secretPath
    ledger_path=$ledgerPath
    audit_path=$auditPath
  } | ConvertTo-Json -Compress
}
catch {
  $installError = $_
  try { Remove-ServiceIfPresent $ServiceName } catch {}
  try {
    if (Test-Path -LiteralPath $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
    if (Test-Path -LiteralPath $backupInstall -PathType Container) {
      New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
      Get-ChildItem -LiteralPath $backupInstall -Force | Copy-Item -Destination $InstallDir -Recurse -Force
    }
  } catch {}
  try { Restore-PriorService $priorService } catch {}
  try { if (Test-Path -LiteralPath $candidateDir) { Remove-Item $candidateDir -Recurse -Force } } catch {}
  throw "BROKER_INSTALL_FAILED_ROLLBACK_COMPLETED: $($installError.Exception.Message)"
}