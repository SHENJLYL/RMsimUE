param([Parameter(Mandatory=$true)][string]$EvidenceDirectory)
. (Join-Path $PSScriptRoot 'SceneMap.ps1')
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$evidence=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $evidence){throw 'Use a fresh evidence directory.'}
[void][IO.Directory]::CreateDirectory($evidence)
$encoding=[Text.UTF8Encoding]::new($false)
$fixture=Join-Path $evidence 'fixture-project'
[void][IO.Directory]::CreateDirectory($fixture)
$project=Join-Path $fixture 'RMSim.uproject'
[IO.File]::WriteAllText($project,'{}',$encoding)
$template=Get-Content -LiteralPath (Join-Path $repo 'assets/maps/wt_vlaanderen/map.json') -Raw -Encoding UTF8
$script:checks=@()
$script:runtimeArgumentEvidence=[ordered]@{}
function Check {
    param([string]$Name,[scriptblock]$Action)
    & $Action
    $script:checks += [ordered]@{name=$Name;passed=$true}
}
function Expect-Rejected {
    param([scriptblock]$Action,[string]$Message)
    $rejected=$false
    try { & $Action | Out-Null } catch {
        if($_.Exception.Message -notlike ('*'+$Message+'*')){throw}
        $rejected=$true
    }
    if(-not $rejected){throw 'Invalid scene unexpectedly passed.'}
}
function Write-Case {
    param([string]$Name,[scriptblock]$Change)
    $map=$template | ConvertFrom-Json
    if($null -ne $Change){& $Change $map}
    $path=Join-Path $evidence ($Name+'.json')
    [IO.File]::WriteAllText($path,($map | ConvertTo-Json -Depth 20),$encoding)
    return $path
}
try {
    $valid=Write-Case 'valid' $null
    Check 'preserves_rmuc_entry_world' {
        $result=Get-RMSimMapLaunch -MapFile (Join-Path $repo 'assets/maps/rmuc2027/map.json') -Project $project
        if($result.IsSavedScene -or $result.WorldUrl -cne '/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode'){
            throw 'RMUC launch changed.'
        }
        if(@($result.RuntimeArguments).Count -ne 0){throw 'RMUC must retain its default runtime budget.'}
        $script:runtimeArgumentEvidence['rmuc2027']=@($result.RuntimeArguments)
    }
    Check 'missing_saved_level_rejected' {
        Expect-Rejected {Get-RMSimMapLaunch -MapFile $valid -Project $project} 'Run tools/unreal/Import-WTScene.ps1 first'
    }
    # A zero-byte fixture tests path selection only; it is never supplied to an Unreal process.
    $umap=Join-Path $fixture 'Content/RMSim/Maps/wt_vlaanderen/World.umap'
    [void][IO.Directory]::CreateDirectory((Split-Path -Parent $umap))
    [IO.File]::WriteAllBytes($umap,[byte[]]@())
    Check 'selects_declared_scene_world' {
        $result=Get-RMSimMapLaunch -MapFile $valid -Project $project
        if(-not $result.IsSavedScene -or $result.WorldUrl -cne '/Game/RMSim/Maps/wt_vlaanderen/World?game=/Script/RMSim.ImportLabGameMode'){
            throw 'Scene world URL differs.'
        }
    }
    Check 'wt_process_texture_pool_budget_4096' {
        $result=Get-RMSimMapLaunch -MapFile $valid -Project $project
        if(@($result.RuntimeArguments).Count -ne 1 -or
            $result.RuntimeArguments[0] -cne '-ForceDPCVars=r.Streaming.PoolSize=4096'){
            throw 'WT must request its selected 4096 MiB per-process texture budget.'
        }
        $script:runtimeArgumentEvidence['wt_vlaanderen']=@($result.RuntimeArguments)
    }
    Check 'other_saved_scene_keeps_default_runtime_budget' {
        $path=Write-Case 'other-scene' {param($map) $map.id='other_scene';$map.level_asset='/Game/RMSim/Maps/other_scene/World'}
        $otherWorld=Join-Path $fixture 'Content/RMSim/Maps/other_scene/World.umap'
        [void][IO.Directory]::CreateDirectory((Split-Path -Parent $otherWorld))
        [IO.File]::WriteAllBytes($otherWorld,[byte[]]@())
        $result=Get-RMSimMapLaunch -MapFile $path -Project $project
        if(@($result.RuntimeArguments).Count -ne 0){throw 'Other saved scenes must retain their default runtime budget.'}
        $script:runtimeArgumentEvidence['other_scene']=@($result.RuntimeArguments)
    }
    Check 'different_scene_level_rejected' {
        $path=Write-Case 'other-level' {param($map) $map.level_asset='/Game/RMSim/Maps/other_map/World'}
        Expect-Rejected {Get-RMSimMapLaunch -MapFile $path -Project $project} 'level_asset'
    }
    Check 'url_options_rejected' {
        $path=Write-Case 'url-options' {param($map) $map.level_asset+='?game=/Script/Other.GameMode'}
        Expect-Rejected {Get-RMSimMapLaunch -MapFile $path -Project $project} 'level_asset'
    }
    Check 'path_traversal_rejected' {
        $path=Write-Case 'path-traversal' {param($map) $map.id='../other';$map.level_asset='/Game/RMSim/Maps/../other/World'}
        Expect-Rejected {Get-RMSimMapLaunch -MapFile $path -Project $project} 'identity'
    }
    Check 'unknown_spawn_rejected' {
        Expect-Rejected {Get-RMSimMapLaunch -MapFile $valid -Project $project -SpawnId 'absent'} 'Unknown scene spawn'
    }
    Check 'duplicate_spawn_rejected' {
        $path=Write-Case 'duplicate-spawn' {param($map) $map.spawns+=@($map.spawns[0])}
        Expect-Rejected {Get-RMSimMapLaunch -MapFile $path -Project $project} 'duplicated'
    }
    Check 'outside_spawn_rejected' {
        $path=Write-Case 'outside-spawn' {param($map) $map.spawns[0].position_m[0]=90000}
        Expect-Rejected {Get-RMSimMapLaunch -MapFile $path -Project $project} 'outside source bounds'
    }
    Check 'invalid_source_hash_rejected' {
        $path=Write-Case 'invalid-hash' {param($map) $map.source_sha256='old'}
        Expect-Rejected {Get-RMSimMapLaunch -MapFile $path -Project $project} 'SHA256'
    }
    Check 'unknown_fields_rejected' {
        $path=Write-Case 'unknown-field' {param($map) $map | Add-Member -NotePropertyName arbitrary_script -NotePropertyValue 'ignored.ps1'}
        Expect-Rejected {Get-RMSimMapLaunch -MapFile $path -Project $project} 'fields'
    }
    $status='passed'
} catch {
    $status='failed';$failure=$_.Exception.Message
    [Console]::Error.WriteLine($failure)
} finally {
    $result=[ordered]@{status=$status;scope='launch configuration validation only; no engine or driving execution';
        checks=$script:checks;runtime_arguments=$script:runtimeArgumentEvidence;native_runtime_verified=$false}
    if($status -eq 'failed'){$result.error=$failure}
    [IO.File]::WriteAllText((Join-Path $evidence 'verification.json'),($result | ConvertTo-Json -Depth 8),$encoding)
}
if($status -ne 'passed'){exit 3}
Write-Output ('Scene launch checks passed: '+$script:checks.Count)
