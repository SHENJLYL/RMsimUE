$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$latest=Join-Path $root 'artifacts/navigation/console-live/latest.json'
if(-not(Test-Path $latest)){Write-Output 'No navigation console session has been started.';exit 0}
$info=Get-Content -LiteralPath $latest -Raw -Encoding UTF8 | ConvertFrom-Json
$folder=[IO.Path]::GetFullPath([string]$info.output_directory)
$allowed=(Join-Path $root 'artifacts/navigation').TrimEnd('\')+'\'
if(-not $folder.StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase)){throw 'Session path is outside this project navigation evidence tree.'}
$process=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$info.pid) -ErrorAction SilentlyContinue
if(-not $process){Write-Output 'Navigation console is already stopped.';exit 0}
if($process.Name -ne 'python.exe' -or $process.CommandLine -notmatch 'navigation_console.py'){throw 'Session PID no longer belongs to the console; no process was touched.'}
[IO.File]::WriteAllText((Join-Path $folder 'stop.request'),[DateTime]::UtcNow.ToString('o'))
Write-Output 'Requested shutdown of this navigation session and its owned processes.'
$p=Get-Process -Id ([int]$info.pid) -ErrorAction SilentlyContinue
if($p){try{if(-not $p.WaitForExit(20000)){throw 'Shutdown is still in progress; see the session status file.'}}finally{$p.Dispose()}}
Write-Output 'Navigation console stopped.'
