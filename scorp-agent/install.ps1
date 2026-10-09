param(
    [switch]$InstallFreshLimited,
    [ValidatePattern('^[0-9a-fA-F]{64}$')][string]$PinnedRelaySha256,
    [string]$OperatorAcknowledgement = ''
)
$ErrorActionPreference = 'Stop'

# This legacy installer previously stopped/deleted any existing task,
# registered an elevated Highest principal and automatically started the relay.
# Default behavior must not be allowed to mutate the original R1 installation.
if (-not $InstallFreshLimited) {
    throw 'P0_LEGACY_INSTALLER_BLOCKED: no automatic install or replacement; explicit fresh-only review required'
}
if ($OperatorAcknowledgement -cne 'FRESH_LIMITED_ONLY_NO_EXISTING_TASK' -or
    [string]::IsNullOrWhiteSpace($PinnedRelaySha256)) {
    throw 'P0_FRESH_INSTALL_OPERATOR_AND_RELAY_PIN_REQUIRED'
}

$Root = 'C:\ScorpAgent'
$Relay = Join-Path $Root 'scorp-agent\relay.ps1'
$TaskName = 'ScorpComputerAgent'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $task) {
    throw 'P0_FRESH_ONLY_EXISTING_TASK_REFUSED: never stop, unregister or replace an R1/V4 task'
}
if (-not (Test-Path -LiteralPath $Relay -PathType Leaf)) {
    throw 'P0_FRESH_INSTALL_RELAY_NOT_FOUND'
}
$relayHash = (Get-FileHash -LiteralPath $Relay -Algorithm SHA256).Hash
if ($relayHash -cne $PinnedRelaySha256.ToUpperInvariant()) {
    throw 'P0_FRESH_INSTALL_RELAY_HASH_MISMATCH'
}
$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$userSid = [string]$currentIdentity.User.Value
if ($userSid -eq 'S-1-5-18' -or $userSid -match '\-500$') {
    throw 'P0_FRESH_INSTALL_PRIVILEGED_BUILTIN_ACCOUNT_REFUSED'
}
$uac = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name EnableLUA -ErrorAction Stop).EnableLUA
if ([int]$uac -ne 1) { throw 'P0_FRESH_INSTALL_UAC_REQUIRED' }

function Test-Administrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
    # Re-elevation is for registering a fresh Limited Task Scheduler entry,
    # not permission to start it or to change the existing production task.
    $args = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -InstallFreshLimited -PinnedRelaySha256 ' + $PinnedRelaySha256 + ' -OperatorAcknowledgement FRESH_LIMITED_ONLY_NO_EXISTING_TASK'
    Start-Process powershell.exe -Verb RunAs -ArgumentList $args -ErrorAction Stop
    exit
}
# Re-check on the elevated identity; elevation must not permit a race with an
# administrator creating the same task while UAC was pending.
$elevatedSid = [string][Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($elevatedSid -eq 'S-1-5-18' -or $elevatedSid -match '\-500$') {
    throw 'P0_FRESH_INSTALL_ELEVATED_BUILTIN_ACCOUNT_REFUSED'
}
if ($null -ne (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    throw 'P0_FRESH_ONLY_TASK_APPEARED_DURING_ELEVATION'
}
if ((Get-FileHash -LiteralPath $Relay -Algorithm SHA256).Hash -cne $PinnedRelaySha256.ToUpperInvariant()) {
    throw 'P0_FRESH_INSTALL_RELAY_CHANGED_DURING_ELEVATION'
}
if (-not (Get-Command gh.exe -ErrorAction SilentlyContinue)) {
    throw 'P0_FRESH_INSTALL_GH_NOT_INSTALLED'
}
& gh.exe auth status --hostname github.com | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'P0_FRESH_INSTALL_GH_NOT_AUTHENTICATED' }
if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    throw 'P0_FRESH_INSTALL_CODEX_NOT_INSTALLED: no auto npm installation'
}
& codex login status | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'P0_FRESH_INSTALL_CODEX_NOT_AUTHENTICATED: no automatic browser login' }

$userId = [string][Security.Principal.WindowsIdentity]::GetCurrent().Name
$actionArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Relay + '"'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -Disable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Days 3650)

# -Force is deliberately forbidden: racing task creation must not overwrite
# an existing scheduler item. Do NOT Start-ScheduledTask or send a GPT prompt.
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'SCORP candidate fresh-only Limited task; manual start required' -ErrorAction Stop | Out-Null
$installed = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if ([string]$installed.Principal.RunLevel -cne 'Limited' -or
    [string]$installed.Principal.LogonType -cne 'Interactive' -or
    [string]$installed.Principal.UserId -cne $userId -or
    [string]$installed.State -cne 'Disabled') {
    throw 'P0_FRESH_INSTALL_PRINCIPAL_REVIEW_REQUIRED: task not started; inspect Task Scheduler'
}
Write-Output 'P0_FRESH_LIMITED_TASK_REGISTERED_DISABLED_NOT_STARTED'
Write-Output 'P0_OPERATOR_MUST_VERIFY_ACLS_AND_AUTHORITY_BEFORE_MANUAL_START'
Write-Output 'P0_UNATTENDED_BEFORE_INTERACTIVE_LOGON_NOT_PROVEN'
