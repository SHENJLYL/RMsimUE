param(
    [Parameter(Mandatory=$true)][string]$EngineRoot,
    [string]$PackageDirectory='',
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [int]$TimeoutSeconds=1200,
    [ValidateRange(1,4)][int]$ShaderWorkers=4,
    [switch]$ResumeAuthoredAssets
)
. (Join-Path $PSScriptRoot 'Common.ps1')
$process=$null
$record=$null
$importMutex=$null
$locked=$false
try {
    $ue=Get-UnrealTools -EngineRoot $EngineRoot
    $root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
    if(-not $PackageDirectory){$PackageDirectory=Join-Path $root 'assets/maps/wt_vlaanderen'}
    $package=(Resolve-Path -LiteralPath $PackageDirectory).Path
    $project=$ue.Project.Replace('\','/').ToLowerInvariant()
    $hasher=[Security.Cryptography.SHA256]::Create()
    try {$projectHash=([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($project)))).Replace('-','').ToLowerInvariant()} finally {$hasher.Dispose()}
    $importMutex=[Threading.Mutex]::new($false, 'Local\RMSimSceneImport_'+$projectHash)
    try {$locked=$importMutex.WaitOne(0)} catch [Threading.AbandonedMutexException] {$locked=$true}
    if(-not $locked){throw 'A scene import or grass authoring task already owns this project.'}
    $active=@(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine.Replace('\','/').ToLowerInvariant().Contains($project) -and
        ($_.Name -match '^UnrealEditor' -or $_.CommandLine -match 'UnrealBuildTool|/Build.bat')
    })
    if($active.Count){throw 'This project has an active editor or build.'}
    $evidence=[IO.Path]::GetFullPath($EvidenceDirectory)
    if(Test-Path -LiteralPath $evidence){throw 'Evidence directory must be new.'}
    if($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 86400){throw 'Invalid timeout.'}
    New-Item -ItemType Directory -Path $evidence | Out-Null
    $arguments=@((Quote-UnrealArgument $ue.Project),'-run=pythonscript',
        ('-script='+(Quote-UnrealArgument (Join-Path $PSScriptRoot 'author_wt_grass.py'))),
        ('-FoliagePackage='+(Quote-UnrealArgument $package)),
        ('-FoliageReport='+(Quote-UnrealArgument (Join-Path $evidence 'verification.json'))),
        ('-AbsLog='+(Quote-UnrealArgument (Join-Path $evidence 'engine.log'))),
        '-d3d11','-AllowCommandletRendering','-ForceDPCVars=r.Streaming.PoolSize=4096',
        '-unattended','-nop4','-NoSplash','-nosound')
    if($ShaderWorkers -eq 1){$arguments+='-NoShaderWorker'}else{
        # Limit workers for this process; engine/project INI files remain untouched.
        $unused=[Math]::Max(0,[Environment]::ProcessorCount-$ShaderWorkers)
        $arguments+=('-ini:Engine:[DevOptions.Shaders]:ShaderCompilerCoreCountThreshold=1024,[DevOptions.Shaders]:NumUnusedShaderCompilingThreads='+$unused+',[DevOptions.Shaders]:NumUnusedShaderCompilingThreadsDuringGame='+$unused+',[DevOptions.Shaders]:bLogJobCompletionTimes=True')
    }
    if($ResumeAuthoredAssets){$arguments+='-ResumeAuthoredAssets'}
    $started=[DateTime]::UtcNow
    $record=[ordered]@{schema='wt_grass_execution_v1';status='running';resume_authored_assets=[bool]$ResumeAuthoredAssets;requested_shader_workers=$ShaderWorkers;started_utc=$started.ToString('o');arguments=$arguments;project=$ue.Project;package=$package;module_sha256=(Get-FileHash (Join-Path (Split-Path $ue.Project) 'Binaries/Win64/UnrealEditor-RMSim.dll') -Algorithm SHA256).Hash.ToLower()}
    $process=Start-Process -FilePath $ue.EditorCmd -ArgumentList $arguments -WindowStyle Hidden -PassThru
    $null=$process.Handle
    $record.pid=$process.Id
    $record | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $evidence 'execution.json') -Encoding UTF8
    Write-Output ('WT_GRASS_STARTED pid='+$process.Id)
    if(-not $process.WaitForExit($TimeoutSeconds*1000)){throw 'Owned grass authoring timed out.'}
    $process.WaitForExit();$process.Refresh()
    $record.exit_code=$process.ExitCode
    if($null -eq $process.ExitCode -or $process.ExitCode -ne 0){throw ('Native grass authoring failed: '+$process.ExitCode)}
    $verification=Get-Content (Join-Path $evidence 'verification.json') -Raw | ConvertFrom-Json
    if($verification.status -ne 'passed'){throw 'Native grass verification did not pass.'}
    $record.status='passed'
    $record.verification_sha256=(Get-FileHash (Join-Path $evidence 'verification.json') -Algorithm SHA256).Hash.ToLower()
    Write-Output ('WT_GRASS_COMPLETE instances='+$verification.generated_instances)
} catch {
    if($null -ne $record){$record.status='failed';$record.error=$_.Exception.Message}
    if($null -ne $process -and -not $process.HasExited){$process.Kill();[void]$process.WaitForExit(10000);if($null -ne $record){$record.owned_process_terminated=$true}}
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 3
} finally {
    if($null -ne $record){$record.finished_utc=[DateTime]::UtcNow.ToString('o');$record | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $evidence 'execution.json') -Encoding UTF8}
    if($null -ne $process){$process.Dispose()}
    if($locked){$importMutex.ReleaseMutex()}
    if($null -ne $importMutex){$importMutex.Dispose()}
}
