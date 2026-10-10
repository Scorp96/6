<#
.SYNOPSIS
  Read-only SCORP host authority observations. NEVER authorizes deployment.
.DESCRIPTION
  Does not read credentials, browser, .codex, chat history, or service arguments.
  Does not run, stop, update, create or install services, tasks, files or apps.
  The four expected hashes and commit must come from an independently reviewed
  immutable Git artifact. A matching disk hash is NOT running code attestation.
#>
param(
    [string]$BrokerInstallDir = 'C:\ScorpAgent\privileged-broker',
    [string]$ServiceName = 'ScorpPrivilegedBroker',
    [string]$TaskName = 'ScorpComputerAgent',
    [string]$ExpectedSourceCommit = '',
    [string]$BrokerServiceSha256 = '',
    [string]$BrokerCoreSha256 = '',
    [string]$BrokerOpsSha256 = '',
    [string]$BrokerClientSha256 = ''
)
$ErrorActionPreference = 'Stop'
$validHash = '^[0-9a-fA-F]{64}$'
$expectedCommitValid = [bool]($ExpectedSourceCommit -cmatch '^[0-9a-fA-F]{40}$')
$files = [ordered]@{
    'broker_service.py' = $BrokerServiceSha256
    'broker_core.py' = $BrokerCoreSha256
    'broker_ops.py' = $BrokerOpsSha256
    'broker_client.py' = $BrokerClientSha256
}
$hashChecks = [ordered]@{}
foreach ($name in $files.Keys) {
    $pin = [string]$files[$name]
    if ($pin -cnotmatch $validHash) {
        $hashChecks[$name] = 'UNPINNED'
        continue
    }
    $sourcePath = Join-Path $BrokerInstallDir $name
    try {
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            $hashChecks[$name] = 'MISSING'
        } else {
            $actual = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256 -ErrorAction Stop).Hash
            if ($actual -ieq $pin) {
                $hashChecks[$name] = 'MATCH'
            } else {
                $hashChecks[$name] = 'MISMATCH'
            }
        }
    } catch {
        $hashChecks[$name] = 'UNKNOWN'
    }
}
$serviceFound = 'UNKNOWN'
$serviceState = 'UNKNOWN'
$serviceAccount = 'UNKNOWN'
$serviceStartType = 'UNKNOWN'
try {
    $svc = Get-Service -Name $ServiceName -ErrorAction Stop
    $serviceFound = 'TRUE'
    if ([string]$svc.Status -eq 'Running') { $serviceState = 'Running' }
    elseif ([string]$svc.Status -eq 'Stopped') { $serviceState = 'Stopped' }
} catch [Microsoft.PowerShell.Commands.ServiceCommandException] {
    $serviceFound = 'FALSE'
} catch {
    $serviceFound = 'UNKNOWN'
}
try {
    $registry = Get-ItemProperty -LiteralPath ('HKLM:\SYSTEM\CurrentControlSet\Services\' + $ServiceName) -ErrorAction Stop
    $principalText = [string]$registry.ObjectName
    if ($principalText -eq 'LocalSystem') { $serviceAccount = 'LocalSystem' }
    elseif ($principalText) { $serviceAccount = 'Other' }
    if ([int]$registry.Start -eq 2) { $serviceStartType = 'Auto' }
    elseif ([int]$registry.Start -eq 3) { $serviceStartType = 'Manual' }
    elseif ([int]$registry.Start -eq 4) { $serviceStartType = 'Disabled' }
} catch {
    $serviceAccount = 'UNKNOWN'
    $serviceStartType = 'UNKNOWN'
}
$taskRunLevel = 'UNKNOWN'
$taskLogonType = 'UNKNOWN'
$taskMetadataAccess = 'UNKNOWN'
try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $taskMetadataAccess = 'AVAILABLE'
    $level = [string]$task.Principal.RunLevel
    if ($level -eq 'Highest') { $taskRunLevel = 'Highest' }
    elseif ($level -eq 'Limited') { $taskRunLevel = 'Limited' }
    $logon = [string]$task.Principal.LogonType
    if ($logon -eq 'Interactive') { $taskLogonType = 'Interactive' }
    elseif ($logon) { $taskLogonType = 'Other' }
} catch {
    $taskMetadataAccess = 'UNAVAILABLE'
}
$effectiveAdmin = 'UNKNOWN'
try {
    $who = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($who)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $effectiveAdmin = 'TRUE'
    } else {
        $effectiveAdmin = 'FALSE'
    }
} catch {
    $effectiveAdmin = 'UNKNOWN'
}
$keyAcl = 'UNKNOWN'
$keyBroadAccess = 'UNKNOWN'
# Only check the known deployed Broker state path; no secret bytes are read.
$key = 'C:\ProgramData\ScorpAgent\privileged-broker\secret.key'
try {
    if (-not (Test-Path -LiteralPath $key -PathType Leaf)) {
        $keyAcl = 'MISSING'
        $keyBroadAccess = 'NOT_ESTABLISHED'
    } else {
        $acl = Get-Acl -LiteralPath $key -ErrorAction Stop
        $keyAcl = 'READABLE'
        $keyBroadAccess = 'NOT_ESTABLISHED'
        foreach ($entry in @($acl.Access)) {
            $whoText = [string]$entry.IdentityReference
            $rights = [string]$entry.FileSystemRights
            if ([string]$entry.AccessControlType -eq 'Allow' -and
                $whoText -match '(?i)(^|\\)(Everyone|Users|Authenticated Users|INTERACTIVE)$' -and
                $rights -match '(?i)(Read|ReadAndExecute|Modify|FullControl)') {
                $keyBroadAccess = 'POSSIBLE'
            }
        }
    }
} catch {
    $keyAcl = 'UNKNOWN'
    $keyBroadAccess = 'UNKNOWN'
}
$allPinsMatched = $expectedCommitValid
foreach ($value in $hashChecks.Values) {
    if ($value -ne 'MATCH') { $allPinsMatched = $false }
}
$report = [ordered]@{
    protocol = 'scorp.host-p0-readonly-attestation/1'
    reviewed_commit_format_valid = $expectedCommitValid
    broker_source_sha256_checks = $hashChecks
    broker_disk_files_match_review_pins = $allPinsMatched
    broker_service_found = $serviceFound
    broker_service_state = $serviceState
    broker_service_account_class = $serviceAccount
    broker_service_start_type = $serviceStartType
    task_metadata_access = $taskMetadataAccess
    task_runlevel = $taskRunLevel
    task_logon_type_class = $taskLogonType
    observer_effective_admin_token = $effectiveAdmin
    broker_secret_acl_metadata = $keyAcl
    broker_secret_broad_acl_read = $keyBroadAccess
    broker_secret_effective_access_attested = $false
    running_broker_loaded_python_modules_attested = $false
    independent_human_privilege_grant_attested = $false
    two_physical_gpt_workers_attested = $false
    real_24h_soak_attested = $false
    production_mutations = 0
    browser_actions = 0
    production_cutover_authorized = $false
    decision = 'BLOCKED_NEEDS_OPERATOR_AND_LIVE_HOST_PROOF'
}
$report | ConvertTo-Json -Depth 5 -Compress
