param([string]$Audit=(Join-Path $PSScriptRoot '..\verify-host-p0-readonly.ps1'))
$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $Audit).Path
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($source,[ref]$tokens,[ref]$errors)
if ($errors.Count -gt 0) { throw 'P0_HOST_AUDIT_PARSER_FAILED' }
$scriptText = [IO.File]::ReadAllText($source)
$required = @(
    'Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256',
    'Get-Service -Name $ServiceName -ErrorAction Stop',
    'Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop',
    'Get-Acl -LiteralPath $key -ErrorAction Stop',
    'running_broker_loaded_python_modules_attested = $false',
    'production_cutover_authorized = $false',
    'production_mutations = 0',
    'browser_actions = 0'
)
foreach ($needle in $required) {
    if (-not $scriptText.Contains($needle)) { throw 'P0_HOST_AUDIT_REQUIRED_FENCE_MISSING' }
}
# Do not run dangerous local commands; inspect the original source AST only.
$commands = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
},$true))
$forbidden = @(
    'Set-Content','Add-Content','Out-File','Remove-Item','Move-Item',
    'Copy-Item','Set-Item','Set-Acl','New-Item','Start-Service',
    'Stop-Service','Restart-Service','Start-ScheduledTask',
    'Stop-ScheduledTask','Register-ScheduledTask','Unregister-ScheduledTask',
    'Set-ScheduledTask','Invoke-WebRequest','Invoke-RestMethod','Invoke-Expression',
    'Start-Process','icacls.exe','sc.exe','schtasks.exe','git.exe','curl.exe'
)
foreach ($node in $commands) {
    $name = $node.GetCommandName()
    if ($name -and $name -in $forbidden) { throw ('P0_HOST_AUDIT_MUTATION_COMMAND:' + $name) }
}
if ($scriptText -match '(?i)(Get-Content|ReadAllBytes|ReadAllText).*(secret\.key|config\.toml|cookie)') {
    throw 'P0_HOST_AUDIT_MAY_READ_SECRET'
}
$folder = Join-Path $env:RUNNER_TEMP 'scorp-p0-host-audit-fixture'
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$files = @('broker_service.py','broker_core.py','broker_ops.py','broker_client.py')
$hashes = @()
foreach ($name in $files) {
    $p = Join-Path $folder $name
    [IO.File]::WriteAllText($p,'isolated-ci-fixture-'+$name,[Text.Encoding]::UTF8)
    $hashes += (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash
}
$args = @(
    '-BrokerInstallDir',$folder,
    '-ServiceName','SCORP_NONEXISTENT_P0_FIXTURE',
    '-TaskName','SCORP_NONEXISTENT_TASK_FIXTURE',
    '-ExpectedSourceCommit',('a' * 40),
    '-BrokerServiceSha256',$hashes[0],
    '-BrokerCoreSha256',$hashes[1],
    '-BrokerOpsSha256',$hashes[2],
    '-BrokerClientSha256',$hashes[3]
)
$output = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $source @args
if ($LASTEXITCODE -ne 0 -or @($output).Count -ne 1) { throw 'P0_HOST_AUDIT_READONLY_EXECUTION_FAILED' }
$r = $output[-1] | ConvertFrom-Json
if ($r.protocol -cne 'scorp.host-p0-readonly-attestation/1' -or
    -not $r.broker_disk_files_match_review_pins -or
    $r.running_broker_loaded_python_modules_attested -or
    $r.production_cutover_authorized -or
    $r.production_mutations -ne 0 -or
    $r.browser_actions -ne 0) {
    throw 'P0_HOST_AUDIT_FALSE_PRODUCTION_APPROVAL'
}
foreach ($name in $files) {
    if ($r.broker_source_sha256_checks.$name -cne 'MATCH') {
        throw 'P0_HOST_AUDIT_FIXTURE_HASH_CHECK_FAILED'
    }
}
$negative = @(
    '-BrokerInstallDir',$folder,
    '-ServiceName','SCORP_NONEXISTENT_P0_FIXTURE',
    '-TaskName','SCORP_NONEXISTENT_TASK_FIXTURE'
)
$blocked = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $source @negative | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $blocked.broker_disk_files_match_review_pins -or
    $blocked.production_cutover_authorized) { throw 'P0_HOST_AUDIT_MISSING_PIN_NOT_BLOCKED' }
foreach ($name in $files) {
    if ($blocked.broker_source_sha256_checks.$name -cne 'UNPINNED') {
        throw 'P0_HOST_AUDIT_MISSING_PIN_ACCEPTED'
    }
}
Write-Output 'P0_HOST_READONLY_SOURCE_AND_WINDOWS_FIXTURE_PASS'
