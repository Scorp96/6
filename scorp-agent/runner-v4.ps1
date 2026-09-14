param(
    [Parameter(Mandatory=$true)][string]$EnvelopePath,
    [Parameter(Mandatory=$true)][string]$ResultPath,
    [Parameter(Mandatory=$true)][string]$LogPath
)

$ErrorActionPreference = "Stop"
$ProtocolVersion = "scorp.exec/v4"

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    $dir = Split-Path -Parent $Path
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding($false)))
}

function Write-JsonAtomic {
    param([string]$Path, $Value)
    $dir = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $id = [guid]::NewGuid().ToString("N")
    $tmp = Join-Path $dir (".{0}.{1}.tmp" -f ([IO.Path]::GetFileName($Path)), $id)
    $bak = Join-Path $dir (".{0}.{1}.bak" -f ([IO.Path]::GetFileName($Path)), $id)
    try {
        $json = $Value | ConvertTo-Json -Depth 20
        Write-Utf8NoBom -Path $tmp -Text $json
        if (Test-Path -LiteralPath $Path -PathType Leaf) { [IO.File]::Replace($tmp, $Path, $bak, $true) }
        else { [IO.File]::Move($tmp, $Path) }
    } finally {
        Remove-Item -LiteralPath $tmp, $bak -Force -ErrorAction SilentlyContinue
    }
}

function Get-Sha256 {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-TailText {
    param($Value, [int]$MaxChars = 8000)
    $text = if ($Value -is [System.Array]) { $Value -join [Environment]::NewLine } else { [string]$Value }
    if ($text.Length -le $MaxChars) { return $text }
    return "[tail truncated]`n" + $text.Substring($text.Length - $MaxChars)
}

function Assert-ApprovedPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { throw "path is required" }
    $full = [IO.Path]::GetFullPath($Path)
    $roots = @(
        [IO.Path]::GetFullPath("C:\ScorpAgent"),
        [IO.Path]::GetFullPath($env:TEMP),
        [IO.Path]::GetFullPath($env:USERPROFILE)
    )
    foreach ($root in $roots) {
        if ($full.Equals($root, [StringComparison]::OrdinalIgnoreCase) -or $full.StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            return $full
        }
    }
    throw "path is outside approved roots: $full"
}

function Assert-ExpectedSha {
    param([string]$Path, $ExpectedSha)
    if ($null -eq $ExpectedSha -or [string]::IsNullOrWhiteSpace([string]$ExpectedSha)) { return }
    $actual = Get-Sha256 -Path $Path
    if ([string]$actual -cne ([string]$ExpectedSha).ToLowerInvariant()) {
        throw "PRECONDITION: sha256 mismatch expected=$ExpectedSha actual=$actual path=$Path"
    }
}

function Write-FileAtomic {
    param([string]$Path, [string]$Content)
    $dir = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $id = [guid]::NewGuid().ToString("N")
    $tmp = Join-Path $dir (".{0}.{1}.tmp" -f ([IO.Path]::GetFileName($Path)), $id)
    $bak = Join-Path $dir (".{0}.{1}.bak" -f ([IO.Path]::GetFileName($Path)), $id)
    try {
        Write-Utf8NoBom -Path $tmp -Text $Content
        if (Test-Path -LiteralPath $Path -PathType Leaf) { [IO.File]::Replace($tmp, $Path, $bak, $true) }
        else { [IO.File]::Move($tmp, $Path) }
    } finally {
        Remove-Item -LiteralPath $tmp, $bak -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Native {
    param([string]$Executable, [object[]]$Arguments, [string]$WorkingDirectory)
    if ($WorkingDirectory) { Push-Location -LiteralPath $WorkingDirectory }
    try {
        $global:LASTEXITCODE = 0
        $output = & $Executable @Arguments 2>&1
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        return [pscustomobject]@{ exit_code = [int]$code; combined_tail = (Get-TailText -Value $output) }
    } finally {
        if ($WorkingDirectory) { Pop-Location }
    }
}

$started = [DateTimeOffset]::Now
$status = "FAILED"
$exitCode = 1
$message = $null
$evidence = [ordered]@{}
$envObj = $null

try {
    if (-not (Test-Path -LiteralPath $EnvelopePath -PathType Leaf)) { throw "envelope file missing" }
    $envObj = Get-Content -LiteralPath $EnvelopePath -Raw | ConvertFrom-Json
    if ([string]$envObj.protocol_version -cne $ProtocolVersion) { throw "unsupported protocol_version" }
    if ([string]::IsNullOrWhiteSpace([string]$envObj.task_id)) { throw "task_id missing" }
    if ([string]$envObj.action_id -notmatch '^[A-Za-z0-9._:-]{8,160}$') { throw "invalid action_id" }
    if ([int]$envObj.timeout_seconds -lt 1 -or [int]$envObj.timeout_seconds -gt 1800) { throw "invalid timeout_seconds" }
    if ([string]$envObj.safety_class -notin @("standard", "approved_admin")) { throw "invalid safety_class" }
    if ([string]$envObj.safety_class -ceq "approved_admin" -and [string]$envObj.authorization -cne "USER_APPROVED_FULL_CONTROL") {
        throw "approved_admin action missing authorization"
    }

    $kind = [string]$envObj.action_kind
    $cwd = $null
    if (-not [string]::IsNullOrWhiteSpace([string]$envObj.working_directory)) {
        $cwd = Assert-ApprovedPath -Path ([string]$envObj.working_directory)
        if (-not (Test-Path -LiteralPath $cwd -PathType Container)) { throw "working_directory does not exist: $cwd" }
    }

    switch ($kind) {
        "powershell" {
            $script = [string]$envObj.payload.script
            if ([string]::IsNullOrWhiteSpace($script)) { throw "powershell payload.script missing" }
            $scriptPath = Join-Path $env:TEMP ("scorp-v4-action-{0}.ps1" -f ([guid]::NewGuid().ToString("N")))
            try {
                Write-Utf8NoBom -Path $scriptPath -Text $script
                $r = Invoke-Native -Executable "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Arguments @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $scriptPath) -WorkingDirectory $cwd
                $exitCode = $r.exit_code
                $evidence.combined_tail = $r.combined_tail
                $status = if ($exitCode -eq 0) { "SUCCEEDED" } else { "FAILED" }
            } finally { Remove-Item -LiteralPath $scriptPath -Force -ErrorAction SilentlyContinue }
        }
        "process" {
            $exe = [string]$envObj.payload.executable
            if ([string]::IsNullOrWhiteSpace($exe)) { throw "process payload.executable missing" }
            $args = @($envObj.payload.argv | ForEach-Object { [string]$_ })
            $r = Invoke-Native -Executable $exe -Arguments $args -WorkingDirectory $cwd
            $exitCode = $r.exit_code
            $evidence.combined_tail = $r.combined_tail
            $status = if ($exitCode -eq 0) { "SUCCEEDED" } else { "FAILED" }
        }
        "git" {
            if (-not $cwd) { throw "git action requires working_directory" }
            $args = @($envObj.payload.argv | ForEach-Object { [string]$_ })
            if ($args.Count -eq 0) { throw "git payload.argv missing" }
            $r = Invoke-Native -Executable "git.exe" -Arguments $args -WorkingDirectory $cwd
            $exitCode = $r.exit_code
            $evidence.combined_tail = $r.combined_tail
            $head = Invoke-Native -Executable "git.exe" -Arguments @("rev-parse", "HEAD") -WorkingDirectory $cwd
            $evidence.git_head = ($head.combined_tail -split "`r?`n")[-1].Trim()
            $status = if ($exitCode -eq 0) { "SUCCEEDED" } else { "FAILED" }
        }
        "file_read" {
            $path = Assert-ApprovedPath -Path ([string]$envObj.payload.path)
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "file not found: $path" }
            $maxBytes = 65536
            if ($null -ne $envObj.payload.max_bytes) { $maxBytes = [Math]::Min([Math]::Max([int]$envObj.payload.max_bytes, 1), 1048576) }
            $bytes = [IO.File]::ReadAllBytes($path)
            $take = [Math]::Min($bytes.Length, $maxBytes)
            $text = [Text.Encoding]::UTF8.GetString($bytes, 0, $take)
            $evidence.path = $path
            $evidence.size_bytes = $bytes.Length
            $evidence.sha256 = Get-Sha256 -Path $path
            $evidence.text = $text
            $evidence.truncated = ($take -lt $bytes.Length)
            $status = "SUCCEEDED"
            $exitCode = 0
        }
        "file_write" {
            $path = Assert-ApprovedPath -Path ([string]$envObj.payload.path)
            Assert-ExpectedSha -Path $path -ExpectedSha $envObj.payload.expected_sha256
            if ([bool]$envObj.payload.must_not_exist -and (Test-Path -LiteralPath $path)) { throw "PRECONDITION: file already exists: $path" }
            Write-FileAtomic -Path $path -Content ([string]$envObj.payload.content)
            $evidence.path = $path
            $evidence.sha256 = Get-Sha256 -Path $path
            $evidence.size_bytes = (Get-Item -LiteralPath $path).Length
            $status = "SUCCEEDED"
            $exitCode = 0
        }
        "file_replace_exact" {
            $path = Assert-ApprovedPath -Path ([string]$envObj.payload.path)
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "PRECONDITION: file not found: $path" }
            Assert-ExpectedSha -Path $path -ExpectedSha $envObj.payload.expected_sha256
            $old = [string]$envObj.payload.old_text
            $new = [string]$envObj.payload.new_text
            if ([string]::IsNullOrEmpty($old)) { throw "old_text missing" }
            $expectedCount = 1
            if ($null -ne $envObj.payload.expected_match_count) { $expectedCount = [int]$envObj.payload.expected_match_count }
            $text = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
            $count = 0
            $index = 0
            while (($index = $text.IndexOf($old, $index, [StringComparison]::Ordinal)) -ge 0) { $count++; $index += $old.Length }
            if ($count -ne $expectedCount) { throw "PRECONDITION: exact match count expected=$expectedCount actual=$count" }
            $updated = $text.Replace($old, $new)
            Write-FileAtomic -Path $path -Content $updated
            $evidence.path = $path
            $evidence.match_count = $count
            $evidence.sha256 = Get-Sha256 -Path $path
            $status = "SUCCEEDED"
            $exitCode = 0
        }
        "health" {
            $evidence.computer = $env:COMPUTERNAME
            $evidence.user = $env:USERNAME
            $evidence.powershell = $PSVersionTable.PSVersion.ToString()
            $evidence.git = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
            $evidence.gh = (Get-Command gh.exe -ErrorAction SilentlyContinue).Source
            $evidence.python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
            $evidence.node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
            $evidence.codex_process_count = @((Get-Process -Name codex -ErrorAction SilentlyContinue)).Count
            $evidence.timestamp = (Get-Date -Format o)
            $status = "SUCCEEDED"
            $exitCode = 0
        }
        default { throw "unsupported action_kind: $kind" }
    }
} catch {
    $message = $_.Exception.Message
    if ($message.StartsWith("PRECONDITION:", [StringComparison]::Ordinal)) { $status = "PRECONDITION_FAILED"; $exitCode = 3 }
    else { $status = "FAILED"; $exitCode = 1 }
    $evidence.exception_type = $_.Exception.GetType().FullName
    $evidence.exception = $message
}

$finished = [DateTimeOffset]::Now
$result = [ordered]@{
    protocol_version = $ProtocolVersion
    task_id = if ($null -ne $envObj) { [string]$envObj.task_id } else { $null }
    issue_number = if ($null -ne $envObj -and $null -ne $envObj.issue_number) { [int]$envObj.issue_number } else { $null }
    claim_token = if ($null -ne $envObj) { [string]$envObj.claim_token } else { $null }
    action_id = if ($null -ne $envObj) { [string]$envObj.action_id } else { $null }
    action_kind = if ($null -ne $envObj) { [string]$envObj.action_kind } else { $null }
    status = $status
    exit_code = $exitCode
    message = $message
    started_at = $started.ToString("o")
    finished_at = $finished.ToString("o")
    duration_ms = [int64]($finished - $started).TotalMilliseconds
    evidence = $evidence
    runner_log_path = $LogPath
}

Write-JsonAtomic -Path $ResultPath -Value $result
$summary = "$(Get-Date -Format o) action=$($result.action_id) kind=$($result.action_kind) status=$status exit=$exitCode result=$ResultPath"
Write-Utf8NoBom -Path $LogPath -Text ($summary + [Environment]::NewLine + ($result | ConvertTo-Json -Depth 20))
exit $exitCode
