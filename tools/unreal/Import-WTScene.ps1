param(
    [Parameter(Mandatory = $true)][string]$EngineRoot,
    [string]$SourceFile = '',
    [string]$EvidenceDirectory = '',
    [int]$TimeoutSeconds = 7200
)
. (Join-Path $PSScriptRoot 'Common.ps1')
$process = $null
$record = $null
$evidence = ''
$importMutex = $null
$locked = $false
try {
    $ue = Get-UnrealTools -EngineRoot $EngineRoot
    $project = [IO.Path]::GetFullPath($ue.Project).Replace('\','/').ToLowerInvariant()
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $projectHash = ([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($project)))).Replace('-','').ToLowerInvariant() }
    finally { $hasher.Dispose() }
    $mutexName = 'Local\RMSimSceneImport_' + $projectHash
    $importMutex = [Threading.Mutex]::new($false, $mutexName)
    try { $locked = $importMutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'A scene import is already active for this project.' }
    $projectPattern = '(?:^|[\s"=])' + [regex]::Escape($project) + '(?=$|[\s"])'
    $active = @(Get-CimInstance Win32_Process -Filter "Name='UnrealEditor.exe' OR Name='UnrealEditor-Cmd.exe' OR Name='dotnet.exe' OR Name='UnrealBuildTool.exe' OR Name='cmd.exe'" | Where-Object {
        if (-not $_.CommandLine) { return $false }
        $command = $_.CommandLine.Replace('\','/').ToLowerInvariant()
        $sameProject = $command -match $projectPattern
        $editor = $_.Name -in @('UnrealEditor.exe','UnrealEditor-Cmd.exe')
        $build = $command.Contains('unrealbuildtool') -or $command.Contains('/build.bat')
        $sameProject -and ($editor -or $build)
    })
    if ($active.Count) { throw ('Project has an active editor, import or build: PID ' + (($active | ForEach-Object ProcessId) -join ', ')) }
    $root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
    if (-not $SourceFile) { $SourceFile = Join-Path $root 'assets/maps/wt_vlaanderen/scene-source.json' }
    $source = (Resolve-Path -LiteralPath $SourceFile).Path
    $manifest = Get-Content -LiteralPath $source -Raw | ConvertFrom-Json
    if ($manifest.schema -ne 'rm_scene_map_source_v1' -or $manifest.id -notmatch '^[a-z][a-z0-9_]*$') { throw 'Invalid scene source identity.' }
    if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt [int]::MaxValue / 1000) { throw 'TimeoutSeconds must be 1..2147483.' }
    if (-not $EvidenceDirectory) { $EvidenceDirectory = Join-Path $root ('artifacts/maps/wt-scene/import-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')) }
    $evidence = [IO.Path]::GetFullPath($EvidenceDirectory)
    if (Test-Path -LiteralPath $evidence) { throw 'EvidenceDirectory must be new; existing import evidence is preserved.' }
    [void](New-Item -ItemType Directory -Path $evidence)
    $importer = Join-Path $PSScriptRoot 'import_wt_scene.py'
    $arguments = @((Quote-UnrealArgument $ue.Project), '-run=pythonscript',
        ('-script=' + (Quote-UnrealArgument $importer)),
        ('-SceneSource=' + (Quote-UnrealArgument $source)),
        ('-AbsLog=' + (Quote-UnrealArgument (Join-Path $evidence 'engine.log'))),
        '-NullRHI', '-NoShaderWorker', '-unattended', '-nop4', '-NoSplash', '-nosound')
    $started = [DateTime]::UtcNow
    $record = [ordered]@{schema='rm_wt_scene_execution_v1'; status='running'; started_utc=$started.ToString('o'); project=$ue.Project; source=$source; source_manifest_sha256=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLower(); arguments=$arguments; runtime_verified=$false; mutex_name=$mutexName}
    $process = Start-Process -FilePath $ue.EditorCmd -ArgumentList $arguments -WindowStyle Hidden -PassThru
    [void]$process.Handle
    $record.pid = $process.Id
    $record | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidence 'execution.json') -Encoding UTF8
    Write-Output ('WT_SCENE_IMPORT_STARTED pid=' + $process.Id + ' evidence=' + $evidence)
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $record.timed_out = $true
        throw 'Scene import timed out; source-hashed imports can be resumed.'
    }
    $process.WaitForExit(); $process.Refresh()
    $record.exit_code = $process.ExitCode
    if ($null -eq $process.ExitCode -or $process.ExitCode -ne 0) { throw ('Scene import failed: ' + $process.ExitCode) }
    $reportPath = Join-Path (Split-Path -Parent $source) 'ue-scene-import-report.json'
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    $finish = [DateTimeOffset]::Parse([string]$report.finished_utc).UtcDateTime
    if ($finish -lt $started -or $report.status -ne 'imported_pending_runtime_validation' -or $report.manifest_sha256 -ne $record.source_manifest_sha256) { throw 'Missing, stale or mismatched scene import report.' }
    $mapFile = Join-Path (Split-Path -Parent $ue.Project) ('Content/RMSim/Maps/' + $manifest.id + '/World.umap')
    if (-not (Test-Path -LiteralPath $mapFile -PathType Leaf)) { throw 'Saved World.umap is missing.' }
    if ((Get-FileHash -LiteralPath $mapFile -Algorithm SHA256).Hash.ToLower() -ne $report.map_file_sha256) { throw 'Saved map hash differs from import report.' }
    $record.status = 'imported_pending_runtime_validation'
    $record.report = $reportPath
    $record.map_file_sha256 = $report.map_file_sha256
    Write-Output ('WT_SCENE_IMPORT_SAVED ' + $mapFile)
} catch {
    $failure = $_.Exception.Message
    if ($null -ne $record) { $record.status = 'failed'; $record.error = $failure }
    if ($null -ne $process) {
        try {
            $process.Refresh()
            if (-not $process.HasExited) {
                # Only the process object returned by this invocation is owned here.
                $process.Kill()
                if (-not $process.WaitForExit(10000)) { throw 'Owned import process did not exit within 10 seconds after termination.' }
                if ($null -ne $record) { $record.owned_process_terminated = $true }
            }
            if ($null -ne $record) { $record.exit_code = $process.ExitCode }
        } catch {
            if ($null -ne $record) { $record.cleanup_error = $_.Exception.Message }
            [Console]::Error.WriteLine('Owned import cleanup failed: ' + $_.Exception.Message)
        }
    }
    [Console]::Error.WriteLine($failure)
    exit 3
} finally {
    try {
        if ($null -ne $record -and $evidence) {
            $record.finished_utc = [DateTime]::UtcNow.ToString('o')
            $record | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidence 'execution.json') -Encoding UTF8
        }
    } catch {
        [Console]::Error.WriteLine('Cannot save scene execution evidence: ' + $_.Exception.Message)
        exit 3
    } finally {
        if ($null -ne $process) { $process.Dispose() }
        if ($locked) { $importMutex.ReleaseMutex() }
        if ($null -ne $importMutex) { $importMutex.Dispose() }
    }
}
