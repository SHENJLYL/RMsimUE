Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-RMSimSceneFields {
    param([object]$Value,[string[]]$Names,[string]$Label)
    if($null -eq $Value -or $Value -isnot [pscustomobject]){throw "$Label must be an object."}
    $actual=@($Value.PSObject.Properties.Name)
    if($actual.Count -ne $Names.Count){throw "$Label has unknown or missing fields."}
    foreach($name in $Names){if($null -eq $Value.PSObject.Properties[$name]){throw "$Label is missing $name."}}
}

function Assert-RMSimSceneNumber {
    param([object]$Value,[double]$Minimum,[double]$Maximum,[string]$Label)
    if($Value -isnot [byte] -and $Value -isnot [int16] -and $Value -isnot [int32] -and
       $Value -isnot [int64] -and $Value -isnot [single] -and $Value -isnot [double] -and $Value -isnot [decimal]){
        throw "$Label must be numeric."
    }
    $number=[double]$Value
    if([double]::IsNaN($number) -or [double]::IsInfinity($number) -or $number -lt $Minimum -or $number -gt $Maximum){
        throw "$Label is nonfinite or out of range."
    }
}

function Assert-RMSimSceneVector {
    param([object]$Value,[string]$Label)
    if($Value -isnot [array] -or $Value.Count -ne 3){throw "$Label must have three numbers."}
    foreach($item in $Value){Assert-RMSimSceneNumber $item -100000 100000 $Label}
}

function Read-RMSimSceneMap {
    param([Parameter(Mandatory=$true)][string]$MapFile)
    $path=(Resolve-Path -LiteralPath $MapFile).Path
    $file=Get-Item -LiteralPath $path
    if($file.PSIsContainer -or $file.Length -gt 1048576){throw 'Map JSON must be a file of at most 1 MiB.'}
    $map=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if($null -eq $map -or $null -eq $map.PSObject.Properties['kind']){throw 'Map kind is missing.'}
    if($map.kind -cne 'rm_scene_map'){return $null}
    Assert-RMSimSceneFields $map @('schema_version','kind','id','display_name','units','coordinate_system',
        'source_sha256','level_asset','bounds_m','spawns','regions') 'Scene map'
    Assert-RMSimSceneNumber $map.schema_version 1 1 'schema_version'
    if($map.id -isnot [string] -or $map.id -cnotmatch '^[a-z][a-z0-9_]{0,127}$' -or
       $map.display_name -isnot [string] -or [string]::IsNullOrWhiteSpace($map.display_name) -or
       $map.units -cne 'm' -or $map.coordinate_system -cne 'ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP' -or
       $map.source_sha256 -isnot [string] -or $map.source_sha256 -cnotmatch '^[0-9a-f]{64}$'){
        throw 'Scene identity, source SHA256, units or coordinates are invalid.'
    }
    $expectedLevel='/Game/RMSim/Maps/'+$map.id+'/World'
    if($map.level_asset -isnot [string] -or $map.level_asset -cne $expectedLevel){
        throw "Scene level_asset must equal $expectedLevel."
    }
    Assert-RMSimSceneFields $map.bounds_m @('lo','hi') 'Scene bounds'
    Assert-RMSimSceneVector $map.bounds_m.lo 'bounds.lo'
    Assert-RMSimSceneVector $map.bounds_m.hi 'bounds.hi'
    for($axis=0;$axis -lt 3;$axis++){
        if($map.bounds_m.hi[$axis] -le $map.bounds_m.lo[$axis]){throw 'Scene bounds are empty or reversed.'}
    }
    if($map.spawns -isnot [array] -or $map.spawns.Count -lt 1 -or $map.spawns.Count -gt 128){
        throw 'Scene map needs 1..128 spawns.'
    }
    $ids=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach($spawn in $map.spawns){
        Assert-RMSimSceneFields $spawn @('id','team','position_m','yaw_rad') 'Scene spawn'
        if($spawn.id -isnot [string] -or $spawn.id -cnotmatch '^[a-z][a-z0-9_]{0,127}$' -or
           -not $ids.Add($spawn.id) -or @('neutral','red','blue') -cnotcontains $spawn.team){
            throw 'Scene spawn ID/team is invalid or duplicated.'
        }
        Assert-RMSimSceneVector $spawn.position_m 'spawn.position_m'
        Assert-RMSimSceneNumber $spawn.yaw_rad (-[Math]::PI) ([Math]::PI) 'spawn.yaw_rad'
        for($axis=0;$axis -lt 3;$axis++){
            if($spawn.position_m[$axis] -lt $map.bounds_m.lo[$axis] -or $spawn.position_m[$axis] -gt $map.bounds_m.hi[$axis]){
                throw 'Scene spawn is outside source bounds.'
            }
        }
    }
    if($map.regions -isnot [array] -or $map.regions.Count -ne 0){throw 'Saved-scene region bindings require an empty array.'}
    return $map
}

function Get-RMSimArenaRenderArguments {
    param([string]$MapFile, [string]$Project)
    $arenaPath = Join-Path (Split-Path -Parent $MapFile) 'arena-shell.json'
    if (-not (Test-Path -LiteralPath $arenaPath -PathType Leaf)) { return }

    $arena = Get-Content -LiteralPath $arenaPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    $profile = $arena.PSObject.Properties['render_profile']
    if (-not $profile -or $profile.Value -isnot [string] -or $profile.Value -cne 'RMSimGymLumen') { return }

    $config = Join-Path (Split-Path -Parent $Project) 'Config/Custom/RMSimGymLumen/DefaultEngine.ini'
    if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { throw 'RMSimGymLumen profile config is missing.' }
    return @('-CustomConfig=RMSimGymLumen', '-d3d12', '-sm6')
}

function Get-RMSimMapLaunch {
    param(
        [Parameter(Mandatory=$true)][string]$MapFile,
        [Parameter(Mandatory=$true)][string]$Project,
        [string]$SpawnId='default'
    )
    $path=(Resolve-Path -LiteralPath $MapFile).Path
    $map=Read-RMSimSceneMap -MapFile $path
    if($null -eq $map){
        return [pscustomobject]@{MapFile=$path;IsSavedScene=$false;LevelAsset='';WorldUrl='/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode';RuntimeArguments=@()}
    }
    if(@($map.spawns | Where-Object { $_.id -ceq $SpawnId }).Count -ne 1){throw "Unknown scene spawn: $SpawnId"}
    $content=Join-Path (Split-Path -Parent ([IO.Path]::GetFullPath($Project))) 'Content'
    $savedMap=Join-Path $content ('RMSim/Maps/'+$map.id+'/World.umap')
    if(-not (Test-Path -LiteralPath $savedMap -PathType Leaf)){
        throw "Saved scene is not imported: $savedMap. Run tools/unreal/Import-WTScene.ps1 first."
    }
    $runtimeArguments=@()
    if($map.id -ceq 'wt_vlaanderen'){
        # Fixed WT budget selected for the current 16 GiB VRAM machine.
        # ForceDPCVars applies per process before streaming, above scalability priority.
        $runtimeArguments=@('-ForceDPCVars=r.Streaming.PoolSize=4096')
    }
    return [pscustomobject]@{MapFile=$path;IsSavedScene=$true;LevelAsset=$map.level_asset;
        WorldUrl=($map.level_asset+'?game=/Script/RMSim.ImportLabGameMode');RuntimeArguments=$runtimeArguments}
}
