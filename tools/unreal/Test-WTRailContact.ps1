param(
    [string]$EngineRoot='E:\simulator-tools\UE_5.8',
    [string]$MapFile='',
    [Parameter(Mandatory=$true)][string]$FixtureFile,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [ValidateRange(0.1,0.6)][double]$SpeedMps=0.35,
    [ValidateRange(60,1800)][int]$TimeoutSeconds=240
)
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'SceneMap.ps1')
$process=$null
$record=$null
$mutex=$null
$locked=$false
$runExitCode=3
$encoding=[Text.UTF8Encoding]::new($false)
$culture=[Globalization.CultureInfo]::InvariantCulture

function Save-RailRecord {
    param([object]$Value,[string]$Path)
    $temporary=$Path+'.tmp'
    [IO.File]::WriteAllText($temporary,($Value | ConvertTo-Json -Depth 24),$encoding)
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
function Hash-RailInput {
    param([string]$Path)
    if(-not (Test-Path -LiteralPath $Path -PathType Leaf)){throw ('Missing evidence input: '+$Path)}
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Assert-RailVector {
    param([object]$Value,[string]$Name)
    if(@($Value).Count -ne 3){throw ('Expected three coordinates: '+$Name)}
    foreach($coordinate in $Value){
        if([double]::IsNaN([double]$coordinate) -or [double]::IsInfinity([double]$coordinate)){
            throw ('Nonfinite coordinate: '+$Name)
        }
    }
}

try {
    $ue=Get-UnrealTools -EngineRoot $EngineRoot
    $repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
    $out=[IO.Path]::GetFullPath($EvidenceDirectory)
    $artifactPrefix=([IO.Path]::GetFullPath((Join-Path $repo 'artifacts'))).TrimEnd('\')+'\'
    if(-not $out.StartsWith($artifactPrefix,[StringComparison]::OrdinalIgnoreCase)){
        throw 'EvidenceDirectory must be a new directory within this repository artifacts/.'
    }
    if(Test-Path -LiteralPath $out){throw 'EvidenceDirectory must be new; failed evidence is retained.'}
    if(-not $MapFile){$MapFile=Join-Path $repo 'assets/maps/wt_vlaanderen/map.json'}
    $sourceMap=(Resolve-Path -LiteralPath $MapFile).Path
    $sourcePackage=Split-Path -Parent $sourceMap
    $auditPath=(Resolve-Path -LiteralPath $FixtureFile).Path
    $fixture=[IO.File]::ReadAllText($auditPath,[Text.Encoding]::UTF8) | ConvertFrom-Json
    if($fixture.schema -cne 'wt_steel_rail_vehicle_contact_fixture_v1' -or
       $fixture.expected_mesh -cnotmatch '^gc_static_rail_[0-9]{4}$' -or
       $fixture.expected_instance -ne 0){
        throw 'Fixture must identify one audited original steel rail instance.'
    }
    Assert-RailVector $fixture.target_ros_m 'target_ros_m'
    Assert-RailVector $fixture.spawn_position_m 'spawn_position_m'
    if([double]::IsNaN([double]$fixture.spawn_yaw_rad) -or
       [double]::IsInfinity([double]$fixture.spawn_yaw_rad)){throw 'Nonfinite spawn yaw.'}
    if((Hash-RailInput $fixture.source_file) -cne $fixture.source_npz_sha256){
        throw 'Original source rail geometry changed after fixture audit.'
    }
    $map=[IO.File]::ReadAllText($sourceMap,[Text.Encoding]::UTF8) | ConvertFrom-Json
    $sceneSource=Join-Path $repo 'assets/maps/wt_vlaanderen/scene-source.json'
    $scene=[IO.File]::ReadAllText($sceneSource,[Text.Encoding]::UTF8) | ConvertFrom-Json
    if($map.kind -cne 'rm_scene_map' -or $map.id -cne 'wt_vlaanderen' -or
       $map.level_asset -cne '/Game/RMSim/Maps/wt_vlaanderen/World' -or
       $map.source_sha256 -cne $scene.source_sha256){
        throw 'Map identity differs from the prepared original Flanders scene.'
    }
    $spawn=@($map.spawns | Where-Object {$_.id -ceq 'default'})
    if($spawn.Count -ne 1){throw 'Expected exactly one default spawn in the input MapFile.'}

    $project=$ue.Project.Replace('\','/').ToLowerInvariant()
    $hasher=[Security.Cryptography.SHA256]::Create()
    try {
        $projectHash=([BitConverter]::ToString(
            $hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($project)))).Replace('-','').ToLowerInvariant()
    } finally {$hasher.Dispose()}
    $mutex=[Threading.Mutex]::new($false,('Local\RMSimSceneImport_'+$projectHash))
    try {$locked=$mutex.WaitOne(0)}
    catch [Threading.AbandonedMutexException] {$locked=$true}
    if(-not $locked){throw 'Another launch or authoring process owns this project.'}
    $pattern='(?:^|[\s"=])'+[regex]::Escape($project)+'(?=$|[\s"])'
    $active=@(Get-CimInstance Win32_Process | Where-Object {
        if(-not $_.CommandLine){return $false}
        $command=$_.CommandLine.Replace('\','/').ToLowerInvariant()
        ($command -match $pattern) -and ($_.Name -match '^UnrealEditor' -or
            $command.Contains('unrealbuildtool') -or $command.Contains('/build.bat'))
    })
    if($active.Count){throw ('Project is active: '+(($active | ForEach-Object ProcessId)-join ', '))}

    [void][IO.Directory]::CreateDirectory($out)
    $fixtureDirectory=Join-Path $out 'fixture'
    [void][IO.Directory]::CreateDirectory($fixtureDirectory)
    $runtimeInputs=[ordered]@{}
    $runtimeCopies=@()
    # Generated collision and overview resolve relative to MapFile.
    foreach($relative in @('generated-collision','overview.json')){
        $origin=Join-Path $sourcePackage $relative
        if(-not (Test-Path -LiteralPath $origin)){throw ('Missing runtime dependency: '+$origin)}
        Copy-Item -LiteralPath $origin -Destination (Join-Path $fixtureDirectory $relative) -Recurse
        $files=if(Test-Path -LiteralPath $origin -PathType Container){
            @(Get-ChildItem -LiteralPath $origin -Recurse -File)
        } else {@(Get-Item -LiteralPath $origin)}
        foreach($file in $files){
            $local=$file.FullName.Substring($sourcePackage.Length+1)
            $copy=Join-Path $fixtureDirectory $local
            $identity=Hash-RailInput $file.FullName
            if((Hash-RailInput $copy) -cne $identity){throw ('Fixture dependency differs: '+$local)}
            $runtimeInputs['runtime/'+$local.Replace('\','/')]=$file.FullName
            $runtimeInputs['fixture-runtime/'+$local.Replace('\','/')]=$copy
            $runtimeCopies+=[ordered]@{relative=$local.Replace('\','/');sha256=$identity}
        }
    }
    $spawn[0].position_m=@($fixture.spawn_position_m)
    $spawn[0].yaw_rad=[double]$fixture.spawn_yaw_rad
    $fixtureMap=Join-Path $fixtureDirectory 'map.json'
    [IO.File]::WriteAllText($fixtureMap,($map | ConvertTo-Json -Depth 20),$encoding)
    $launch=Get-RMSimMapLaunch -MapFile $fixtureMap -Project $ue.Project -SpawnId 'default'
    if(-not $launch.IsSavedScene){throw 'Rail fixture requires the saved Flanders World.'}
    $rigFile=Join-Path $repo 'assets/vehicles/aim_sentry/ue_mesh_rig.json'
    $profileFile=Join-Path $repo 'assets/vehicles/aim_sentry/drive-profile.json'
    $engineLog=Join-Path $out 'engine.log'
    $arguments=@(
        (Quote-UnrealArgument $ue.Project),$launch.WorldUrl,
        '-game','-RenderOffScreen','-unattended','-NoSplash','-nosound','-nop4',
        '-windowed','-ResX=1280','-ResY=720','-d3d11','-NoShaderWorker',
        '-ContactProbe','-RailContactProbe','-GimbalYaw=0','-GimbalPitch=0',
        ('-MeshRigFile='+(Quote-UnrealArgument $rigFile)),
        ('-MapFile='+(Quote-UnrealArgument $fixtureMap)),'-SpawnId=default',
        ('-EvidenceDir='+(Quote-UnrealArgument $out)),
        ('-AbsLog='+(Quote-UnrealArgument $engineLog)),
        ('-RailExpectedMesh='+$fixture.expected_mesh),
        ('-RailExpectedInstance='+$fixture.expected_instance),
        ('-RailSpeedMps='+$SpeedMps.ToString('R',$culture)),
        ('-RailTargetX='+([double]$fixture.target_ros_m[0]).ToString('R',$culture)),
        ('-RailTargetY='+([double]$fixture.target_ros_m[1]).ToString('R',$culture)),
        ('-RailTargetZ='+([double]$fixture.target_ros_m[2]).ToString('R',$culture))
    )
    $arguments+=@($launch.RuntimeArguments)
    $inputs=[ordered]@{
        map=$sourceMap;scene_source=$sceneSource;audit_fixture=$auditPath
        source_npz=$fixture.source_file;fixture_map=$fixtureMap
        world=(Join-Path $repo 'unreal/RMSim/Content/RMSim/Maps/wt_vlaanderen/World.umap')
        rail_asset=(Join-Path $repo ('unreal/RMSim/Content/RMSim/Maps/wt_vlaanderen/GeneratedCollision/Meshes/'+$fixture.expected_mesh+'.uasset'))
        module=(Join-Path $repo 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll')
        vehicle_rig=$rigFile;drive_profile=$profileFile
        game_config=(Join-Path $repo 'unreal/RMSim/Config/DefaultGame.ini')
        engine_config=(Join-Path $repo 'unreal/RMSim/Config/DefaultEngine.ini')
        wrapper=$PSCommandPath
    }
    foreach($name in @('ImportLabGameMode.cpp','ImportLabGameMode.h','ContactProbe.cpp',
        'RailContactProbe.cpp','VehicleRig.h','VehicleContactPhysics.cpp','VehicleMotorDrive.cpp',
        'VehicleDrive.cpp','WTGeneratedCollisionComponent.cpp','WTGeneratedCollisionComponent.h')){
        $inputs[$name]=Join-Path $repo ('unreal/RMSim/Source/RMSim/'+$name)
    }
    foreach($key in $runtimeInputs.Keys){$inputs[$key]=$runtimeInputs[$key]}
    $before=[ordered]@{}
    foreach($key in $inputs.Keys){$before[$key]=Hash-RailInput $inputs[$key]}
    $begin=[DateTime]::UtcNow
    $record=[ordered]@{
        schema='rmsim_native_source_rail_contact_v1';status='running'
        started_utc=$begin.ToString('o');project=$ue.Project;executable=$ue.Editor
        arguments=$arguments;command=((Quote-UnrealArgument $ue.Editor)+' '+($arguments -join ' '))
        timeout_seconds=$TimeoutSeconds;fixture_map=$fixtureMap;fixture_spawn=$spawn[0]
        source_fixture=$fixture;fixture_runtime_dependency_copies=$runtimeCopies
        requested_speed_mps=$SpeedMps;pose_overrides_used=$false;os_input_verified=$false
        input_files=$inputs;hashes_before=$before;hashes_after=$null
        pid=$null;exit_code=$null;timed_out=$false;owned_process_terminated=$false
        captures=@();log_events=@()
    }
    $verification=Join-Path $out 'verification.json'
    Save-RailRecord $record $verification
    $launchOptions=@{
        FilePath=$ue.Editor;ArgumentList=$arguments;WindowStyle='Hidden';PassThru=$true
        RedirectStandardOutput=(Join-Path $out 'launcher.stdout.log')
        RedirectStandardError=(Join-Path $out 'launcher.stderr.log')
    }
    $process=Start-Process @launchOptions
    $null=$process.Handle
    $record.pid=$process.Id
    Save-RailRecord $record $verification
    Write-Output ('WT_RAIL_TEST_STARTED pid='+$process.Id+' evidence='+$out)
    $deadline=$begin.AddSeconds($TimeoutSeconds)
    $nextProgress=$begin.AddSeconds(30)
    while(-not $process.WaitForExit(1000)){
        $now=[DateTime]::UtcNow
        if($now -ge $deadline){
            $record.timed_out=$true
            $process.Kill();$record.owned_process_terminated=$true
            [void]$process.WaitForExit(10000)
            throw ('Owned rail probe timed out after '+$TimeoutSeconds+' seconds.')
        }
        if($now -ge $nextProgress){
            Write-Output ('WT_RAIL_TEST_WAITING elapsed_s='+[int]($now-$begin).TotalSeconds)
            $nextProgress=$now.AddSeconds(30)
        }
    }
    # Finite waits confirm this retained process handle. Do not wait for
    # redirected pipe EOF from descendant Zen server processes.
    $process.Refresh()
    if(-not $process.HasExited -or -not $process.WaitForExit(0)){
        throw 'Owned native process exit was not confirmed.'
    }
    $record.exit_code=$process.ExitCode
    Save-RailRecord $record $verification
    if($null -eq $process.ExitCode -or $process.ExitCode -ne 0){
        throw ('Native rail probe returned '+$process.ExitCode)
    }
    $reportPath=Join-Path $out 'control-result.json'
    $report=[IO.File]::ReadAllText($reportPath,[Text.Encoding]::UTF8) | ConvertFrom-Json
    $expected=@('physical_settle','source_rail_surface_alignment','actual_rail_contact',
        'rail_blocks_low_speed_drive','physical_brake_after_rail','physical_reverse_from_rail',
        'physical_stop_after_reverse','source_rail_survives_drive')
    if($report.status -cne 'passed' -or $report.complete -ne $true -or
       $report.probe -cne 'native_source_rail_contact_v1' -or @($report.checks).Count -ne $expected.Count){
        throw 'Native rail report is incomplete or failed.'
    }
    foreach($name in $expected){
        $matches=@($report.checks | Where-Object {$_.name -ceq $name})
        if($matches.Count -ne 1 -or $matches[0].passed -ne $true){
            throw ('Missing/failed/duplicated native rail check: '+$name)
        }
    }
    if($report.expected_mesh -cne $fixture.expected_mesh -or $report.expected_instance -ne 0 -or
       $report.positive_target_contacts -lt 3 -or $report.forward_blocking_contacts -lt 2 -or
       $report.peak_impulse_n_s -le 0 -or @($report.contacting_vehicle_bodies).Count -lt 1 -or
       $report.source_top_height_error_cm -ge 2 -or $report.pose_overrides_used -ne $false -or
       $report.initial_motion_generation -ne $report.final_motion_generation -or
       $report.physical_outcome -cne 'blocked_then_reversed'){
        throw 'Native result lacks exact source rail, real contact, or unchanged physical pose generation.'
    }
    # Plain .NET strings avoid PS5 Get-Content provider metadata recursion.
    $log=[IO.File]::ReadAllText($engineLog,[Text.Encoding]::UTF8)
    $record.log_events=@($log -split '\r?\n' | Where-Object {$_ -match 'WT_RAIL_CONTACT_|CONTROL_CHECK'})
    if($log -match 'Fatal error:|Assertion failed:'){throw 'Native log contains a fatal engine error.'}
    $passLines=@($record.log_events | Where-Object {$_ -match '\bWT_RAIL_CONTACT_PASSED\b'})
    if($passLines.Count -ne 1){throw 'Missing/duplicated native rail pass marker.'}
    $record.native_result_sha256=Hash-RailInput $reportPath
    $record.checks_passed=$expected.Count
    $record.positive_target_contacts=$report.positive_target_contacts
    $record.forward_blocking_contacts=$report.forward_blocking_contacts
    $record.contacting_vehicle_bodies=@($report.contacting_vehicle_bodies)
    $record.peak_impulse_n_s=$report.peak_impulse_n_s
    $record.held_drive_advance_cm=$report.held_drive_advance_cm
    $record.reverse_clearance_cm=$report.reverse_clearance_cm
    $record.source_top_height_error_cm=$report.source_top_height_error_cm
    Add-Type -AssemblyName System.Drawing
    foreach($phase in @('rail-before','rail-contact','rail-blocked','rail-reverse')){
        if(@($record.log_events | Where-Object {$_ -match ('\bWT_RAIL_CONTACT_CAPTURE phase='+[regex]::Escape($phase)+'\b')}).Count -ne 1){
            throw ('Missing/duplicated native capture event: '+$phase)
        }
        foreach($camera in @('overview','aim_camera')){
            $file=Join-Path $out ($phase+'/'+$camera+'.png')
            $picture=$null
            try {
                $picture=[Drawing.Image]::FromFile($file)
                if($picture.RawFormat.Guid -ne [Drawing.Imaging.ImageFormat]::Png.Guid -or
                   $picture.Width -le 0 -or $picture.Height -le 0){throw 'Invalid native PNG capture.'}
                $record.captures+=[ordered]@{
                    file=$phase+'/'+$camera+'.png';sha256=(Hash-RailInput $file)
                    width=$picture.Width;height=$picture.Height
                }
            } finally {if($null -ne $picture){$picture.Dispose()}}
        }
    }
    $record.status='passed';$runExitCode=0
} catch {
    $message=$_.Exception.Message
    if($record){$record.status='failed';$record.error=$message}
    [Console]::Error.WriteLine($message)
} finally {
    if($process){
        try {
            $process.Refresh()
            if(-not $process.HasExited){
                $process.Kill()
                if($record){$record.owned_process_terminated=$true}
                [void]$process.WaitForExit(10000)
                $runExitCode=3
            }
            if($record -and $process.HasExited){$record.exit_code=$process.ExitCode}
        } catch {if($record){$record.process_status_error=$_.Exception.Message}}
    }
    if($record){
        if(Test-Path -LiteralPath $engineLog -PathType Leaf){
            $record.engine_log_sha256=Hash-RailInput $engineLog
            $record.log_events=@([IO.File]::ReadAllLines($engineLog,[Text.Encoding]::UTF8) |
                Where-Object {$_ -match 'WT_RAIL_CONTACT_|CONTROL_CHECK'})
        }
        try {
            $after=[ordered]@{}
            foreach($key in $inputs.Keys){$after[$key]=Hash-RailInput $inputs[$key]}
            $record.hashes_after=$after
            $changed=@($inputs.Keys | Where-Object {$before[$_] -cne $after[$_]})
            $record.changed_inputs=$changed
            $record.source_module_profile_world_unchanged=($changed.Count -eq 0)
            if($changed.Count){
                $record.status='failed';$record.error='Probe inputs changed: '+($changed -join ', ')
                $runExitCode=3
            }
        } catch {$record.status='failed';$record.hash_error=$_.Exception.Message;$runExitCode=3}
        $record.finished_utc=[DateTime]::UtcNow.ToString('o')
        $record.duration_seconds=([DateTime]::UtcNow-$begin).TotalSeconds
        Save-RailRecord $record $verification
        [pscustomobject]$record | Select-Object status,pid,exit_code,checks_passed,duration_seconds,error |
            ConvertTo-Json -Compress | Write-Output
    }
    if($process){$process.Dispose()}
    if($locked){$mutex.ReleaseMutex()}
    if($mutex){$mutex.Dispose()}
}
exit $runExitCode
