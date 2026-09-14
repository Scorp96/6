param(
    [Parameter(Mandatory=$true)][string]$EnvelopePath,
    [Parameter(Mandatory=$true)][string]$ResultPath,
    [Parameter(Mandatory=$true)][string]$LogPath,
    [string]$StartGatePath = "",
    [int]$GateWaitSeconds = 60
)

$ErrorActionPreference = "Stop"
$ProtocolVersion = "scorp.exec/v4"

function Write-Utf8NoBom {
    param([string]$Path,[string]$Text)
    $dir=Split-Path -Parent $Path
    if($dir){New-Item -ItemType Directory -Force -Path $dir|Out-Null}
    [IO.File]::WriteAllText($Path,$Text,(New-Object Text.UTF8Encoding($false)))
}

function Write-JsonAtomic {
    param([string]$Path,$Value)
    $dir=Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $dir|Out-Null
    $id=[guid]::NewGuid().ToString("N")
    $tmp=Join-Path $dir (".{0}.{1}.tmp"-f([IO.Path]::GetFileName($Path)),$id)
    $bak=Join-Path $dir (".{0}.{1}.bak"-f([IO.Path]::GetFileName($Path)),$id)
    try{
        Write-Utf8NoBom -Path $tmp -Text ($Value|ConvertTo-Json -Depth 24)
        if(Test-Path -LiteralPath $Path -PathType Leaf){[IO.File]::Replace($tmp,$Path,$bak,$true)}
        else{[IO.File]::Move($tmp,$Path)}
    }finally{Remove-Item -LiteralPath $tmp,$bak -Force -ErrorAction SilentlyContinue}
}

function Get-Sha256 {
    param([string]$Path)
    if(-not(Test-Path -LiteralPath $Path -PathType Leaf)){return $null}
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-BoundedTailText {
    param([string]$Path,[int]$MaxBytes=32768)
    if(-not(Test-Path -LiteralPath $Path -PathType Leaf)){return ""}
    $fs=New-Object IO.FileStream($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
    try{
        $length=[int64]$fs.Length
        if($length-le0){return ""}
        $encoding=New-Object Text.UTF8Encoding($false)
        $probe=New-Object byte[] 3
        $probeCount=$fs.Read($probe,0,[Math]::Min(3,[int]$length))
        if($probeCount-ge2-and$probe[0]-eq0xFF-and$probe[1]-eq0xFE){$encoding=[Text.Encoding]::Unicode}
        elseif($probeCount-ge3-and$probe[0]-eq0xEF-and$probe[1]-eq0xBB-and$probe[2]-eq0xBF){$encoding=New-Object Text.UTF8Encoding($true)}
        $take=[int][Math]::Min([int64][Math]::Max(1,$MaxBytes),$length)
        $start=$length-$take
        if($encoding.CodePage-eq[Text.Encoding]::Unicode.CodePage-and($start%2)-ne0){$start--;$take++}
        [void]$fs.Seek($start,[IO.SeekOrigin]::Begin)
        $buffer=New-Object byte[] $take
        $read=0
        while($read-lt$take){$n=$fs.Read($buffer,$read,$take-$read);if($n-le0){break};$read+=$n}
        $text=$encoding.GetString($buffer,0,$read)
        if($start-eq0){$text=$text.TrimStart([char]0xFEFF)}
        if($start-gt0){return "[tail truncated]`n"+$text}
        return $text
    }finally{$fs.Dispose()}
}

function Assert-ApprovedPath {
    param([string]$Path)
    if([string]::IsNullOrWhiteSpace($Path)){throw "path is required"}
    $full=[IO.Path]::GetFullPath($Path)
    $roots=@([IO.Path]::GetFullPath("C:\ScorpAgent"),[IO.Path]::GetFullPath($env:TEMP),[IO.Path]::GetFullPath($env:USERPROFILE))
    foreach($root in $roots){
        if($full.Equals($root,[StringComparison]::OrdinalIgnoreCase)-or$full.StartsWith($root.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)){return $full}
    }
    throw "path is outside approved roots: $full"
}

function Assert-ExpectedSha {
    param([string]$Path,$ExpectedSha)
    if($null-eq$ExpectedSha-or[string]::IsNullOrWhiteSpace([string]$ExpectedSha)){return}
    $actual=Get-Sha256 -Path $Path
    if([string]$actual-cne([string]$ExpectedSha).ToLowerInvariant()){throw "PRECONDITION: sha256 mismatch expected=$ExpectedSha actual=$actual path=$Path"}
}

function Read-FileBounded {
    param([string]$Path,[int]$MaxBytes)
    $fs=New-Object IO.FileStream($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
    try{
        $size=[int64]$fs.Length
        $take=[int][Math]::Min($size,[int64][Math]::Max(1,$MaxBytes))
        $buffer=New-Object byte[] $take
        $read=0
        while($read-lt$take){$n=$fs.Read($buffer,$read,$take-$read);if($n-le0){break};$read+=$n}
        return [pscustomobject]@{size_bytes=$size;bytes=$buffer;bytes_read=$read;truncated=([int64]$read-lt$size)}
    }finally{$fs.Dispose()}
}

function Write-FileAtomic {
    param([string]$Path,[string]$Content)
    $dir=Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $dir|Out-Null
    $id=[guid]::NewGuid().ToString("N")
    $tmp=Join-Path $dir (".{0}.{1}.tmp"-f([IO.Path]::GetFileName($Path)),$id)
    $bak=Join-Path $dir (".{0}.{1}.bak"-f([IO.Path]::GetFileName($Path)),$id)
    try{
        Write-Utf8NoBom -Path $tmp -Text $Content
        if(Test-Path -LiteralPath $Path -PathType Leaf){[IO.File]::Replace($tmp,$Path,$bak,$true)}
        else{[IO.File]::Move($tmp,$Path)}
    }finally{Remove-Item -LiteralPath $tmp,$bak -Force -ErrorAction SilentlyContinue}
}

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)
    if($Value.Length-gt0-and$Value-cnotmatch'[\s"]'){return $Value}
    $sb=New-Object Text.StringBuilder
    [void]$sb.Append('"');$slashes=0
    foreach($ch in $Value.ToCharArray()){
        if([int]$ch-eq92){$slashes++;continue}
        if($ch-eq'"'){
            if($slashes-gt0){[void]$sb.Append((('\' * ($slashes*2)) -join ''))}
            [void]$sb.Append('\"');$slashes=0;continue
        }
        if($slashes-gt0){[void]$sb.Append((('\' * $slashes)-join''));$slashes=0}
        [void]$sb.Append($ch)
    }
    if($slashes-gt0){[void]$sb.Append((('\' * ($slashes*2))-join''))}
    [void]$sb.Append('"')
    return $sb.ToString()
}

function Invoke-Native {
    param([string]$Executable,[object[]]$Arguments,[string]$WorkingDirectory,[string]$CaptureBase)
    if([string]::IsNullOrWhiteSpace($CaptureBase)){throw "CaptureBase is required"}
    $stdoutPath=$CaptureBase+".stdout.log";$stderrPath=$CaptureBase+".stderr.log"
    $captureDir=Split-Path -Parent $stdoutPath
    if($captureDir){New-Item -ItemType Directory -Force -Path $captureDir|Out-Null}
    Remove-Item -LiteralPath $stdoutPath,$stderrPath -Force -ErrorAction SilentlyContinue
    $argLine=(@($Arguments|ForEach-Object{ConvertTo-NativeArgument -Value ([string]$_)}) -join ' ')
    $startParams=@{FilePath=$Executable;ArgumentList=$argLine;PassThru=$true;Wait=$true;NoNewWindow=$true;RedirectStandardOutput=$stdoutPath;RedirectStandardError=$stderrPath}
    if($WorkingDirectory){$startParams.WorkingDirectory=$WorkingDirectory}
    $proc=Start-Process @startParams
    $code=[int]$proc.ExitCode
    $stdoutTail=Get-BoundedTailText -Path $stdoutPath
    $stderrTail=Get-BoundedTailText -Path $stderrPath
    return [pscustomobject]@{
        exit_code=$code;combined_tail="STDOUT:`n$stdoutTail`nSTDERR:`n$stderrTail"
        stdout_tail=$stdoutTail;stderr_tail=$stderrTail;stdout_log_path=$stdoutPath;stderr_log_path=$stderrPath
        stdout_sha256=Get-Sha256 $stdoutPath;stderr_sha256=Get-Sha256 $stderrPath
    }
}

function Wait-StartGate {
    param([string]$Path,[int]$Seconds)
    if([string]::IsNullOrWhiteSpace($Path)){return}
    $deadline=[DateTimeOffset]::Now.AddSeconds([Math]::Max(1,$Seconds))
    while([DateTimeOffset]::Now-lt$deadline){
        if(Test-Path -LiteralPath $Path -PathType Leaf){
            $gate=(Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue).Trim()
            if($gate-ceq"GO"){return}
        }
        Start-Sleep -Milliseconds 100
    }
    throw "START_GATE_TIMEOUT: action was not durably authorized to begin before gate deadline"
}

function Get-BrokerGenerationToken {
    param([string]$TaskId)
    if([string]::IsNullOrWhiteSpace($TaskId)){throw "task_id missing"}
    $m=[regex]::Match($TaskId,'(?i)/g([0-9]+)$')
    if($m.Success){return ("g{0}"-f$m.Groups[1].Value)}
    return "direct"
}

function Get-DeterministicBrokerRequestId {
    param([string]$TaskId,[string]$ActionId,[string]$GenerationToken)
    if([string]::IsNullOrWhiteSpace($TaskId)-or[string]::IsNullOrWhiteSpace($ActionId)-or[string]::IsNullOrWhiteSpace($GenerationToken)){throw "broker request identity missing"}
    $material="scorp.exec/v4`n$TaskId`n$ActionId`n$GenerationToken"
    $sha=[Security.Cryptography.SHA256]::Create()
    try{$hex=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($material)))-replace'-','').ToLowerInvariant()}finally{$sha.Dispose()}
    return "v4.$hex"
}

function Get-BrokerRequestPath {
    param([string]$RequestId)
    if($RequestId-notmatch'^v4\.[0-9a-f]{64}$'){throw "broker request id invalid"}
    $root='C:\ScorpAgent\state-v4\broker-requests'
    New-Item -ItemType Directory -Force -Path $root|Out-Null
    return (Join-Path $root ($RequestId+'.json'))
}

function Invoke-PrivilegedBrokerAction {
    param($Envelope,[string]$CaptureBase)
    $python='C:\ScorpAgent\privileged-broker-runtime\Scripts\python.exe'
    $client='C:\ScorpAgent\privileged-broker\broker_client.py'
    if(-not(Test-Path -LiteralPath $python -PathType Leaf)){throw "privileged broker runtime missing"}
    if(-not(Test-Path -LiteralPath $client -PathType Leaf)){throw "privileged broker client missing"}
    $generationToken=Get-BrokerGenerationToken -TaskId ([string]$Envelope.task_id)
    $requestId=Get-DeterministicBrokerRequestId -TaskId ([string]$Envelope.task_id) -ActionId ([string]$Envelope.action_id) -GenerationToken $generationToken
    $requestPath=Get-BrokerRequestPath -RequestId $requestId
    $operation=[string]$Envelope.payload.operation
    $paramsJson=$Envelope.payload.params|ConvertTo-Json -Depth 20 -Compress
    $args=@($client,'--request-file',$requestPath,'--request-id',$requestId,'--operation',$operation,'--params-json',$paramsJson)
    $native=Invoke-Native -Executable $python -Arguments $args -WorkingDirectory $null -CaptureBase $CaptureBase
    $response=$null
    if($native.exit_code-eq0){
        $raw=[string]$native.stdout_tail
        if($raw.StartsWith('[tail truncated]',[StringComparison]::Ordinal)){throw "broker response unexpectedly truncated"}
        try{$response=$raw.Trim()|ConvertFrom-Json}catch{throw "broker client response invalid"}
        if([string]$response.request_id-cne$requestId){throw "broker response request_id mismatch"}
    }
    return [pscustomobject]@{native=$native;request_id=$requestId;generation_token=$generationToken;request_path=$requestPath;operation=$operation;response=$response}
}
function Add-NativeEvidence {
    param([System.Collections.IDictionary]$Evidence,$NativeResult)
    $Evidence.combined_tail=$NativeResult.combined_tail
    $Evidence.stdout_tail=$NativeResult.stdout_tail;$Evidence.stderr_tail=$NativeResult.stderr_tail
    $Evidence.stdout_log_path=$NativeResult.stdout_log_path;$Evidence.stderr_log_path=$NativeResult.stderr_log_path
    $Evidence.stdout_sha256=$NativeResult.stdout_sha256;$Evidence.stderr_sha256=$NativeResult.stderr_sha256
}

$started=[DateTimeOffset]::Now
$status="FAILED";$exitCode=1;$message=$null
$evidence=[ordered]@{runner_pid=$PID;runner_started_at=$started.ToString("o")}
$envObj=$null

try{
    if(-not(Test-Path -LiteralPath $EnvelopePath -PathType Leaf)){throw "envelope file missing"}
    $envObj=Get-Content -LiteralPath $EnvelopePath -Raw|ConvertFrom-Json
    if([string]$envObj.protocol_version-cne$ProtocolVersion){throw "unsupported protocol_version"}
    if([string]::IsNullOrWhiteSpace([string]$envObj.task_id)){throw "task_id missing"}
    if([string]$envObj.action_id-notmatch'^[A-Za-z0-9._:-]{8,160}$'){throw "invalid action_id"}
    if([int]$envObj.timeout_seconds-lt1-or[int]$envObj.timeout_seconds-gt1800){throw "invalid timeout_seconds"}
    if([string]$envObj.safety_class-notin@("standard","approved_admin")){throw "invalid safety_class"}
    if([string]$envObj.safety_class-ceq"approved_admin"-and[string]$envObj.authorization-cne"USER_APPROVED_FULL_CONTROL"){throw "approved_admin action missing authorization"}
    Wait-StartGate -Path $StartGatePath -Seconds $GateWaitSeconds

    $kind=[string]$envObj.action_kind
    $cwd=$null
    if(-not[string]::IsNullOrWhiteSpace([string]$envObj.working_directory)){
        $cwd=Assert-ApprovedPath -Path ([string]$envObj.working_directory)
        if(-not(Test-Path -LiteralPath $cwd -PathType Container)){throw "working_directory does not exist: $cwd"}
    }

    switch($kind){
        "powershell"{
            $script=[string]$envObj.payload.script
            if([string]::IsNullOrWhiteSpace($script)){throw "powershell payload.script missing"}
            $scriptPath=Join-Path $env:TEMP ("scorp-v4-action-{0}.ps1"-f([guid]::NewGuid().ToString("N")))
            try{
                Write-Utf8NoBom -Path $scriptPath -Text $script
                $r=Invoke-Native -Executable "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Arguments @("-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-File",$scriptPath) -WorkingDirectory $cwd -CaptureBase ($LogPath+".powershell")
                $exitCode=$r.exit_code;Add-NativeEvidence $evidence $r
                $status=if($exitCode-eq0){"SUCCEEDED"}else{"FAILED"}
            }finally{Remove-Item -LiteralPath $scriptPath -Force -ErrorAction SilentlyContinue}
        }
        "process"{
            $exe=[string]$envObj.payload.executable
            if([string]::IsNullOrWhiteSpace($exe)){throw "process payload.executable missing"}
            $r=Invoke-Native -Executable $exe -Arguments @($envObj.payload.argv|ForEach-Object{[string]$_}) -WorkingDirectory $cwd -CaptureBase ($LogPath+".process")
            $exitCode=$r.exit_code;Add-NativeEvidence $evidence $r
            $status=if($exitCode-eq0){"SUCCEEDED"}else{"FAILED"}
        }
        "git"{
            if(-not$cwd){throw "git action requires working_directory"}
            $args=@($envObj.payload.argv|ForEach-Object{[string]$_})
            if($args.Count-eq0){throw "git payload.argv missing"}
            $r=Invoke-Native -Executable "git.exe" -Arguments $args -WorkingDirectory $cwd -CaptureBase ($LogPath+".git")
            $exitCode=$r.exit_code;Add-NativeEvidence $evidence $r
            $head=Invoke-Native -Executable "git.exe" -Arguments @("rev-parse","HEAD") -WorkingDirectory $cwd -CaptureBase ($LogPath+".git-head")
            $evidence.git_head=($head.stdout_tail-split"`r?`n")[-1].Trim()
            $status=if($exitCode-eq0){"SUCCEEDED"}else{"FAILED"}
        }
        "file_read"{
            $path=Assert-ApprovedPath -Path ([string]$envObj.payload.path)
            if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "file not found: $path"}
            $maxBytes=if($null-ne$envObj.payload.max_bytes){[Math]::Min([Math]::Max([int]$envObj.payload.max_bytes,1),1048576)}else{65536}
            $read=Read-FileBounded -Path $path -MaxBytes $maxBytes
            $evidence.path=$path;$evidence.size_bytes=$read.size_bytes;$evidence.sha256=Get-Sha256 -Path $path
            $evidence.text=[Text.Encoding]::UTF8.GetString($read.bytes,0,$read.bytes_read);$evidence.truncated=[bool]$read.truncated
            $status="SUCCEEDED";$exitCode=0
        }
        "file_write"{
            $path=Assert-ApprovedPath -Path ([string]$envObj.payload.path)
            Assert-ExpectedSha -Path $path -ExpectedSha $envObj.payload.expected_sha256
            if([bool]$envObj.payload.must_not_exist-and(Test-Path -LiteralPath $path)){throw "PRECONDITION: file already exists: $path"}
            Write-FileAtomic -Path $path -Content ([string]$envObj.payload.content)
            $evidence.path=$path;$evidence.sha256=Get-Sha256 -Path $path;$evidence.size_bytes=(Get-Item -LiteralPath $path).Length
            $status="SUCCEEDED";$exitCode=0
        }
        "file_replace_exact"{
            $path=Assert-ApprovedPath -Path ([string]$envObj.payload.path)
            if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "PRECONDITION: file not found: $path"}
            Assert-ExpectedSha -Path $path -ExpectedSha $envObj.payload.expected_sha256
            $item=Get-Item -LiteralPath $path
            if([int64]$item.Length-gt16777216){throw "PRECONDITION: file_replace_exact refuses files larger than 16 MiB"}
            $old=[string]$envObj.payload.old_text;$new=[string]$envObj.payload.new_text
            if([string]::IsNullOrEmpty($old)){throw "old_text missing"}
            $expectedCount=if($null-ne$envObj.payload.expected_match_count){[int]$envObj.payload.expected_match_count}else{1}
            $text=[IO.File]::ReadAllText($path,[Text.Encoding]::UTF8)
            $count=0;$index=0
            while(($index=$text.IndexOf($old,$index,[StringComparison]::Ordinal))-ge0){$count++;$index+=$old.Length}
            if($count-ne$expectedCount){throw "PRECONDITION: exact match count expected=$expectedCount actual=$count"}
            Write-FileAtomic -Path $path -Content ($text.Replace($old,$new))
            $evidence.path=$path;$evidence.match_count=$count;$evidence.sha256=Get-Sha256 -Path $path
            $status="SUCCEEDED";$exitCode=0
        }
        "privileged_broker"{
            if([string]$envObj.safety_class-cne"approved_admin"-or[string]$envObj.authorization-cne"USER_APPROVED_FULL_CONTROL"){throw "privileged_broker authorization invalid"}
            $b=Invoke-PrivilegedBrokerAction -Envelope $envObj -CaptureBase ($LogPath+".privileged-broker")
            $exitCode=$b.native.exit_code;Add-NativeEvidence $evidence $b.native
            $evidence.broker_request_id=$b.request_id;$evidence.broker_generation_token=$b.generation_token;$evidence.broker_operation=$b.operation
            $evidence.broker_request_path=$b.request_path;$evidence.broker_request_sha256=Get-Sha256 -Path $b.request_path
            if($exitCode-eq0){$evidence.broker_replayed=[bool]$b.response.replayed;$evidence.broker_result=$b.response.result;$status="SUCCEEDED"}else{$status="FAILED"}
        }        "health"{
            $evidence.computer=$env:COMPUTERNAME;$evidence.user=$env:USERNAME;$evidence.powershell=$PSVersionTable.PSVersion.ToString()
            $evidence.git=(Get-Command git.exe -ErrorAction SilentlyContinue).Source;$evidence.gh=(Get-Command gh.exe -ErrorAction SilentlyContinue).Source
            $evidence.python=(Get-Command python.exe -ErrorAction SilentlyContinue).Source;$evidence.node=(Get-Command node.exe -ErrorAction SilentlyContinue).Source
            $evidence.codex_process_count=@((Get-Process -Name codex -ErrorAction SilentlyContinue)).Count;$evidence.timestamp=(Get-Date -Format o)
            $status="SUCCEEDED";$exitCode=0
        }
        default{throw "unsupported action_kind: $kind"}
    }
}catch{
    $message=$_.Exception.Message
    if($message.StartsWith("PRECONDITION:",[StringComparison]::Ordinal)){$status="PRECONDITION_FAILED";$exitCode=3}
    elseif($message.StartsWith("START_GATE_TIMEOUT:",[StringComparison]::Ordinal)){$status="BLOCKED";$exitCode=4}
    else{$status="FAILED";$exitCode=1}
    $evidence.exception_type=$_.Exception.GetType().FullName;$evidence.exception=$message
}

$finished=[DateTimeOffset]::Now
$result=[ordered]@{
    protocol_version=$ProtocolVersion
    task_id=if($null-ne$envObj){[string]$envObj.task_id}else{$null}
    issue_number=if($null-ne$envObj-and$null-ne$envObj.issue_number){[int]$envObj.issue_number}else{$null}
    claim_token=if($null-ne$envObj){[string]$envObj.claim_token}else{$null}
    action_id=if($null-ne$envObj){[string]$envObj.action_id}else{$null}
    action_kind=if($null-ne$envObj){[string]$envObj.action_kind}else{$null}
    status=$status;exit_code=$exitCode;message=$message
    started_at=$started.ToString("o");finished_at=$finished.ToString("o");duration_ms=[int64]($finished-$started).TotalMilliseconds
    evidence=$evidence;runner_log_path=$LogPath
}
Write-JsonAtomic -Path $ResultPath -Value $result
Write-Utf8NoBom -Path $LogPath -Text ("$(Get-Date -Format o) action=$($result.action_id) kind=$($result.action_kind) status=$status exit=$exitCode`r`n"+($result|ConvertTo-Json -Depth 24))
exit $exitCode