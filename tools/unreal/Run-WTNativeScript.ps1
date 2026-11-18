param(
 [Parameter(Mandatory=$true)][string]$EngineRoot,
 [Parameter(Mandatory=$true)][string]$PythonScript,
 [Parameter(Mandatory=$true)][string]$ReportArgument,
 [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
 [string]$SourceFile='',
 [string[]]$ExtraArguments=@(),
 [string[]]$ExpectedStatus=@('passed'),
 [switch]$Rendering,
 [switch]$AllowWorldMutation,
 [int]$ShaderWorkers=4,
 [int]$TimeoutSeconds=1800
)
. (Join-Path $PSScriptRoot 'Common.ps1')
$process=$null;$record=$null;$mutex=$null;$locked=$false
try {
 $ue=Get-UnrealTools -EngineRoot $EngineRoot
 $root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
 $script=(Resolve-Path -LiteralPath $PythonScript).Path
 $scriptRoot=([IO.Path]::GetFullPath($PSScriptRoot)).TrimEnd('\')+'\'
 if(-not $script.StartsWith($scriptRoot,[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetExtension($script) -ne '.py'){throw 'PythonScript must be a Python file in tools/unreal.'}
 if($ReportArgument -notmatch '^[A-Za-z][A-Za-z0-9]*$'){throw 'Invalid report argument name.'}
 if($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 7200){throw 'TimeoutSeconds must be 1..7200.'}
 if($ShaderWorkers -lt 1 -or $ShaderWorkers -gt [Environment]::ProcessorCount){throw 'Invalid shader worker budget.'}
 if(-not $SourceFile){$SourceFile=Join-Path $root 'assets/maps/wt_vlaanderen/scene-source.json'}
 $source=(Resolve-Path -LiteralPath $SourceFile).Path
 $manifest=Get-Content -LiteralPath $source -Raw -Encoding UTF8 | ConvertFrom-Json
 if($manifest.id -notmatch '^[a-z][a-z0-9_]*$'){throw 'Invalid source map identity.'}
 $out=[IO.Path]::GetFullPath($EvidenceDirectory)
 if(Test-Path -LiteralPath $out){throw 'EvidenceDirectory must be new.'}
 $project=$ue.Project.Replace('\','/').ToLowerInvariant()
 $hasher=[Security.Cryptography.SHA256]::Create()
 try{$projectHash=([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($project)))).Replace('-','').ToLowerInvariant()}finally{$hasher.Dispose()}
 $mutex=[Threading.Mutex]::new($false,('Local\RMSimSceneImport_'+$projectHash))
 try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
 if(-not $locked){throw 'Another launch or authoring process owns this project.'}
 $pattern='(?:^|[\s"=])'+[regex]::Escape($project)+'(?=$|[\s"])'
 $active=@(Get-CimInstance Win32_Process | Where-Object {
  if(-not $_.CommandLine){return $false}
  $command=$_.CommandLine.Replace('\','/').ToLowerInvariant()
  ($command -match $pattern) -and ($_.Name -match '^UnrealEditor' -or $command.Contains('unrealbuildtool') -or $command.Contains('/build.bat'))
 })
 if($active.Count){
  $active | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress | Write-Output
  throw ('Project is active: '+(($active | ForEach-Object ProcessId)-join ', '))
 }
 [void](New-Item -ItemType Directory -Path $out)
 function Hash([string]$path){if(Test-Path -LiteralPath $path -PathType Leaf){return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()};return $null}
 $world=Join-Path (Split-Path $ue.Project -Parent) ('Content/RMSim/Maps/'+$manifest.id+'/World.umap')
 $module=Join-Path (Split-Path $ue.Project -Parent) 'Binaries/Win64/UnrealEditor-RMSim.dll'
 $arguments=@((Quote-UnrealArgument $ue.Project),'-run=pythonscript',
  ('-script='+(Quote-UnrealArgument $script)),
  ('-SceneSource='+(Quote-UnrealArgument $source)),
  ('-'+$ReportArgument+'='+(Quote-UnrealArgument (Join-Path $out 'audit'))),
  ('-AbsLog='+(Quote-UnrealArgument (Join-Path $out 'engine.log'))),
  '-unattended','-nop4','-NoSplash','-nosound')
 $arguments+= $ExtraArguments
 if($Rendering){
  $unused=[Math]::Max(0,[Environment]::ProcessorCount-$ShaderWorkers)
  $arguments+=@('-AllowCommandletRendering','-d3d11','-ForceDPCVars=r.Streaming.PoolSize=4096',
   ('-ini:Engine:[DevOptions.Shaders]:ShaderCompilerCoreCountThreshold=1024,[DevOptions.Shaders]:NumUnusedShaderCompilingThreads='+$unused+',[DevOptions.Shaders]:NumUnusedShaderCompilingThreadsDuringGame='+$unused))
 }else{$arguments+='-NullRHI'}
 $begin=[DateTime]::UtcNow
 $record=[ordered]@{schema='rmsim_wt_native_execution_v2';status='running';started_utc=$begin.ToString('o');project=$ue.Project;script=$script;arguments=$arguments;timeout_seconds=$TimeoutSeconds;shader_workers=$ShaderWorkers;rendering=[bool]$Rendering;world_mutation_allowed=[bool]$AllowWorldMutation;world_sha256_before=(Hash $world);source_sha256_before=(Hash $source);module_sha256=(Hash $module);scripts_sha256=@{}}
 Get-ChildItem $PSScriptRoot -File -Filter '*wt*.py' | ForEach-Object {$record.scripts_sha256[$_.Name]=(Hash $_.FullName)}
 $process=Start-Process -FilePath $ue.EditorCmd -ArgumentList $arguments -WindowStyle Hidden -PassThru
 $null=$process.Handle
 $record.pid=$process.Id
 $record|ConvertTo-Json -Depth 10|Set-Content -LiteralPath (Join-Path $out 'execution.json') -Encoding UTF8
 Write-Output ('WT_NATIVE_STARTED pid='+$process.Id+' evidence='+$out)
 $deadline=$begin.AddSeconds($TimeoutSeconds)
 while(-not $process.WaitForExit(30000)){if([DateTime]::UtcNow -ge $deadline){throw 'Owned native execution timed out.'}}
 $process.WaitForExit();$process.Refresh()
 $record.exit_code=$process.ExitCode
 if($null -eq $process.ExitCode -or $process.ExitCode -ne 0){throw ('Native process failed: '+$process.ExitCode)}
 $reportFile=Join-Path $out 'audit/verification.json'
 $report=Get-Content -LiteralPath $reportFile -Raw -Encoding UTF8 | ConvertFrom-Json
 if($report.status -notin $ExpectedStatus){throw ('Native report status was '+$report.status)}
 $record.report_status=$report.status
 $record.verification_sha256=(Hash $reportFile)
 $record.status='passed'
}catch{
 $failure=$_.Exception.Message
 if($record){$record.status='failed';$record.error=$failure}
 if($process){try{$process.Refresh();if(-not $process.HasExited){$process.Kill();[void]$process.WaitForExit(10000);if($record){$record.owned_process_terminated=$true}}}catch{if($record){$record.cleanup_error=$_.Exception.Message}}}
 [Console]::Error.WriteLine($failure)
}finally{
 if($record){
  $record.world_sha256_after=(Hash $world);$record.source_sha256_after=(Hash $source)
  $record.module_sha256_after=(Hash $module)
  if($record.source_sha256_before -ne $record.source_sha256_after -or $record.module_sha256 -ne $record.module_sha256_after){$record.status='failed';$record.error='Source or native module changed during execution.'}
  if(-not $AllowWorldMutation -and $record.world_sha256_before -ne $record.world_sha256_after){$record.status='failed';$record.error='Read-only native execution changed the World.'}
  $record.finished_utc=[DateTime]::UtcNow.ToString('o')
  $record.duration_seconds=([DateTime]::UtcNow-$begin).TotalSeconds
  $record|ConvertTo-Json -Depth 10|Set-Content -LiteralPath (Join-Path $out 'execution.json') -Encoding UTF8
  [pscustomobject]$record|Select-Object status,pid,exit_code,duration_seconds,error|ConvertTo-Json -Compress
 }
 if($process){$process.Dispose()}
 if($locked){$mutex.ReleaseMutex()}
 if($mutex){$mutex.Dispose()}
}
if(-not $record -or $record.status -ne 'passed'){exit 3}
