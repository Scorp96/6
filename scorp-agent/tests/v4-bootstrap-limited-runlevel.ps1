param([string]$Bootstrap=(Join-Path $PSScriptRoot '..\bootstrap-v4.ps1'))
$ErrorActionPreference='Stop'
$path=(Resolve-Path $Bootstrap).Path
$tokens=$null;$errors=$null
[System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)|Out-Null
if($errors.Count-ne0){throw "BOOTSTRAP_PARSER_INVALID"}
$source=[IO.File]::ReadAllText($path)
$pre=$source.IndexOf('if([string]$task.Principal.RunLevel-cne"Limited")',[StringComparison]::Ordinal)
$stop=$source.IndexOf('    Stop-ScheduledTask -TaskName $TaskName',[StringComparison]::Ordinal)
$backup=$source.IndexOf('    $priorXml=Export-ScheduledTask',[StringComparison]::Ordinal)
$enforce=$source.IndexOf('if($runLevel-cne"Limited")',[StringComparison]::Ordinal)
$verify=$source.IndexOf('if([string]$after.Principal.RunLevel-cne"Limited")',[StringComparison]::Ordinal)
if($pre-lt0 -or $stop-lt0 -or $pre-ge$stop -or $pre-ge$backup){throw 'UNSAFE_RUNLEVEL_PRECHECK_NOT_BEFORE_MUTATION'}
if($enforce-lt$stop -or $verify-lt$enforce){throw 'UNSAFE_RUNLEVEL_POST_SWITCH_NOT_VERIFIED'}
if($source -match '\$runLevel-notin@\("Highest","Limited"\)'){throw 'OLD_HIGHEST_ALLOWED'}
Write-Output 'P0_BOOTSTRAP_LIMITED_RUNLEVEL_FENCE_PASS'
