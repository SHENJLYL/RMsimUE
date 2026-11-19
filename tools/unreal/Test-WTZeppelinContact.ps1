param(
    [string]$EngineRoot='E:\simulator-tools\UE_5.8',
    [string]$MapFile='',
    [string]$FixtureFile='',
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [ValidateRange(0.1,0.6)][double]$SpeedMps=0.35,
    [ValidateRange(60,1800)][int]$TimeoutSeconds=360
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

function Save-ZeppelinRecord {
    param([object]$Value,[string]$Path)
    $temporary=$Path+'.tmp'
    [IO.File]::WriteAllText($temporary,($Value | ConvertTo-Json -Depth 24),$encoding)
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
function Hash-ZeppelinInput {
    param([string]$Path)
    if(-not (Test-Path -LiteralPath $Path -PathType Leaf)){throw ('Missing evidence input: '+$Path)}
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Assert-ZeppelinVector {
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
    $missionPackage=Join-Path $repo 'assets/maps/wt_vlaanderen/mission-objects'
    if(-not $FixtureFile){$FixtureFile=Join-Path $missionPackage 'collision-probes.json'}
    $auditPath=(Resolve-Path -LiteralPath $FixtureFile).Path
    $probes=[IO.File]::ReadAllText($auditPath,[Text.Encoding]::UTF8) | ConvertFrom-Json
    $missionSource=Join-Path $missionPackage 'mission-source.json'
    $missionScene=Join-Path $missionPackage 'scene-source.json'
    if($probes.schema -cne 'wt_mission_collision_probes_v1' -or @($probes.hits).Count -lt 1 -or
       $probes.source_manifest_sha256 -cne (Hash-ZeppelinInput $missionSource)){
        throw 'Source zeppelin collision probe identity is invalid or stale.'
    }
    $hit=$probes.hits[0]
    if($hit.source_instance -cne 'zeppelin_116_00' -or
       $hit.resource -cne 'us_zeppelin_whale_frame_truss_a_damaged_a' -or
       $hit.prepared_mesh_id -cne 'zep_frame_truss_a_damaged_a_c1c96be4'){
        throw 'Re-audit the original fixed truss before changing this copied-map spawn fixture.'
    }
    Assert-ZeppelinVector $hit.impact_ue_cm 'impact_ue_cm'
    Assert-ZeppelinVector $hit.start_ue_cm 'start_ue_cm'
    Assert-ZeppelinVector $hit.end_ue_cm 'end_ue_cm'
    $mission=[IO.File]::ReadAllText($missionScene,[Text.Encoding]::UTF8) | ConvertFrom-Json
    $sourceCollision=Join-Path $mission.extraction_root ('collision-original/'+$hit.resource+'/collision.npz')
    $collisionMetadata=Join-Path $mission.extraction_root ('collision-original/'+$hit.resource+'/collision.json')
    $collider=[IO.File]::ReadAllText($collisionMetadata,[Text.Encoding]::UTF8) | ConvertFrom-Json
    if($collider.name -cne $hit.resource -or $collider.mesh_sha256 -cne (Hash-ZeppelinInput $sourceCollision)){
        throw 'Original source collision geometry changed after extraction.'
    }
    $mesh='col_'+$hit.prepared_mesh_id
    $fixture=[pscustomobject][ordered]@{
        schema='wt_zeppelin_vehicle_contact_fixture_v1'
        expected_mesh=$mesh
        expected_asset=('/Game/RMSim/Maps/wt_vlaanderen/MissionObjects/Collision/'+$mesh+'.'+$mesh)
        expected_instance=0
        source_instance=$hit.source_instance
        target_ros_m=@(([double]$hit.impact_ue_cm[0]/100.),(-[double]$hit.impact_ue_cm[1]/100.),([double]$hit.impact_ue_cm[2]/100.))
        ray_start_ue_cm=@($hit.start_ue_cm)
        ray_end_ue_cm=@($hit.end_ue_cm)
        spawn_position_m=@(477.5904980023005,-1035.6520387328023,4.821499215373528)
        spawn_yaw_rad=-2.381329646097284
        spawn_semantics='Audited source HM2 ground seed; normal map initialization and SnapToTerrain own physical placement.'
        source_file=$sourceCollision
        source_npz_sha256=$collider.mesh_sha256
        source_manifest_sha256=(Hash-ZeppelinInput $missionSource)
        source_probes_sha256=(Hash-ZeppelinInput $auditPath)
        source_surface_ground_clearance_m=$hit.ground_clearance_m
        ownership_tag='RMSim.WTMissionObjects.Zeppelin'
        fixed_tag='RMSim.WTFixedLandmark'
    }
    Assert-ZeppelinVector $fixture.target_ros_m 'target_ros_m'
    Assert-ZeppelinVector $fixture.spawn_position_m 'spawn_position_m'
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
    # Preserve the saved-world runtime collision/overview dependencies; change only copied spawn.
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
            $identity=Hash-ZeppelinInput $file.FullName
            if((Hash-ZeppelinInput $copy) -cne $identity){throw ('Fixture dependency differs: '+$local)}
            $runtimeInputs['runtime/'+$local.Replace('\','/')]=$file.FullName
            $runtimeInputs['fixture-runtime/'+$local.Replace('\','/')]=$copy
            $runtimeCopies+=[ordered]@{relative=$local.Replace('\','/');sha256=$identity}
        }
    }
    $spawn[0].position_m=@($fixture.spawn_position_m)
    $spawn[0].yaw_rad=[double]$fixture.spawn_yaw_rad
    $fixtureMap=Join-Path $fixtureDirectory 'map.json'
    [IO.File]::WriteAllText($fixtureMap,($map | ConvertTo-Json -Depth 20),$encoding)
    $nativeFixturePath=Join-Path $fixtureDirectory 'zeppelin-contact.json'
    Save-ZeppelinRecord $fixture $nativeFixturePath
    $launch=Get-RMSimMapLaunch -MapFile $fixtureMap -Project $ue.Project -SpawnId 'default'
    if(-not $launch.IsSavedScene){throw 'Zeppelin fixture requires the saved Flanders World.'}
    $rigFile=Join-Path $repo 'assets/vehicles/aim_sentry/ue_mesh_rig.json'
    $profileFile=Join-Path $repo 'assets/vehicles/aim_sentry/drive-profile.json'
    $engineLog=Join-Path $out 'engine.log'
    $arguments=@(
        (Quote-UnrealArgument $ue.Project),$launch.WorldUrl,
        '-game','-RenderOffScreen','-unattended','-NoSplash','-nosound','-nop4',
        '-windowed','-ResX=1280','-ResY=720','-d3d11','-NoShaderWorker',
        '-ContactProbe','-ZeppelinContactProbe','-GimbalYaw=0','-GimbalPitch=0',
        ('-MeshRigFile='+(Quote-UnrealArgument $rigFile)),
        ('-MapFile='+(Quote-UnrealArgument $fixtureMap)),'-SpawnId=default',
        ('-EvidenceDir='+(Quote-UnrealArgument $out)),
        ('-AbsLog='+(Quote-UnrealArgument $engineLog)),
        ('-ZeppelinFixture='+(Quote-UnrealArgument $nativeFixturePath)),
        ('-ZeppelinSpeedMps='+$SpeedMps.ToString('R',$culture))
    )
    $arguments+=@($launch.RuntimeArguments)
    $inputs=[ordered]@{
        map=$sourceMap;scene_source=$sceneSource;audit_fixture=$auditPath;native_fixture=$nativeFixturePath
        mission_source=$missionSource;mission_scene=$missionScene;source_collision_metadata=$collisionMetadata
        source_npz=$fixture.source_file;fixture_map=$fixtureMap
        world=(Join-Path $repo 'unreal/RMSim/Content/RMSim/Maps/wt_vlaanderen/World.umap')
        zeppelin_asset=(Join-Path $repo ('unreal/RMSim/Content/RMSim/Maps/wt_vlaanderen/MissionObjects/Collision/'+$fixture.expected_mesh+'.uasset'))
        module=(Join-Path $repo 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll')
        vehicle_rig=$rigFile;drive_profile=$profileFile
        game_config=(Join-Path $repo 'unreal/RMSim/Config/DefaultGame.ini')
        engine_config=(Join-Path $repo 'unreal/RMSim/Config/DefaultEngine.ini')
        wrapper=$PSCommandPath
    }
    foreach($name in @('ImportLabGameMode.cpp','ImportLabGameMode.h','ContactProbe.cpp',
        'ZeppelinContactProbe.cpp','GyroProbe.cpp','VehicleRig.h','VehicleContactPhysics.cpp','VehicleMotorDrive.cpp',
        'VehicleDrive.cpp','WTGeneratedCollisionComponent.cpp','WTGeneratedCollisionComponent.h')){
        $inputs[$name]=Join-Path $repo ('unreal/RMSim/Source/RMSim/'+$name)
    }
    foreach($key in $runtimeInputs.Keys){$inputs[$key]=$runtimeInputs[$key]}
    foreach($name in @('Common.ps1','SceneMap.ps1')){$inputs['launcher/'+$name]=Join-Path $PSScriptRoot $name}
    foreach($name in @('source-provenance.json','preparation-summary.json','preparation-verification.json','materials.json','instances.json')){
        $inputs['mission-package/'+$name]=Join-Path $missionPackage $name
    }
    $nativeMission=Join-Path $repo 'unreal/RMSim/Content/RMSim/Maps/wt_vlaanderen/MissionObjects'
    foreach($file in Get-ChildItem -LiteralPath $nativeMission -Recurse -File -Filter '*.uasset'){
        $relative=$file.FullName.Substring($nativeMission.Length+1).Replace('\','/')
        $inputs['mission-native/'+$relative]=$file.FullName
    }
    $before=[ordered]@{}
    foreach($key in $inputs.Keys){$before[$key]=Hash-ZeppelinInput $inputs[$key]}
    $begin=[DateTime]::UtcNow
    $record=[ordered]@{
        schema='rmsim_native_source_zeppelin_contact_v1';status='running'
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
    Save-ZeppelinRecord $record $verification
    $launchOptions=@{
        FilePath=$ue.Editor;ArgumentList=$arguments;WindowStyle='Hidden';PassThru=$true
        RedirectStandardOutput=(Join-Path $out 'launcher.stdout.log')
        RedirectStandardError=(Join-Path $out 'launcher.stderr.log')
    }
    $process=Start-Process @launchOptions
    $null=$process.Handle
    $record.pid=$process.Id
    Save-ZeppelinRecord $record $verification
    Write-Output ('WT_ZEPPELIN_TEST_STARTED pid='+$process.Id+' evidence='+$out)
    $deadline=$begin.AddSeconds($TimeoutSeconds)
    $nextProgress=$begin.AddSeconds(30)
    while(-not $process.WaitForExit(1000)){
        $now=[DateTime]::UtcNow
        if($now -ge $deadline){
            $record.timed_out=$true
            $process.Kill();$record.owned_process_terminated=$true
            [void]$process.WaitForExit(10000)
            throw ('Owned zeppelin probe timed out after '+$TimeoutSeconds+' seconds.')
        }
        if($now -ge $nextProgress){
            Write-Output ('WT_ZEPPELIN_TEST_WAITING elapsed_s='+[int]($now-$begin).TotalSeconds)
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
    Save-ZeppelinRecord $record $verification
    if($null -eq $process.ExitCode -or $process.ExitCode -ne 0){
        throw ('Native zeppelin probe returned '+$process.ExitCode)
    }
    $reportPath=Join-Path $out 'control-result.json'
    $report=[IO.File]::ReadAllText($reportPath,[Text.Encoding]::UTF8) | ConvertFrom-Json
    $expected=@('physical_settle','source_zeppelin_surface_alignment','actual_zeppelin_contact',
        'zeppelin_blocks_low_speed_drive','physical_brake_after_zeppelin','physical_reverse_from_zeppelin',
        'physical_stop_after_reverse','source_zeppelin_survives_drive')
    if($report.status -cne 'passed' -or $report.complete -ne $true -or
       $report.probe -cne 'native_source_zeppelin_contact_v1' -or @($report.checks).Count -ne $expected.Count){
        throw 'Native zeppelin report is incomplete or failed.'
    }
    foreach($name in $expected){
        $matches=@($report.checks | Where-Object {$_.name -ceq $name})
        if($matches.Count -ne 1 -or $matches[0].passed -ne $true){
            throw ('Missing/failed/duplicated native zeppelin check: '+$name)
        }
    }
    if($report.expected_mesh -cne $fixture.expected_mesh -or $report.expected_instance -ne 0 -or
       $report.positive_target_contacts -lt 3 -or $report.forward_blocking_contacts -lt 2 -or
       $report.peak_impulse_n_s -le 0 -or @($report.contacting_vehicle_bodies).Count -lt 1 -or
       $report.source_surface_error_cm -ge 2 -or $report.pose_overrides_used -ne $false -or
       $report.initial_motion_generation -ne $report.final_motion_generation -or
       $report.contact_recovery_count -ne 0 -or $report.vehicle_cameras_repositioned -ne $false -or
       $report.captures -ne 7 -or
       $report.physical_outcome -cne 'blocked_then_reversed'){
        throw 'Native result lacks exact source zeppelin, real contact, or unchanged physical pose generation.'
    }
    # Plain .NET strings avoid PS5 Get-Content provider metadata recursion.
    $log=[IO.File]::ReadAllText($engineLog,[Text.Encoding]::UTF8)
    $record.log_events=@($log -split '\r?\n' | Where-Object {$_ -match 'WT_ZEPPELIN_CONTACT_|CONTROL_CHECK'})
    if($log -match 'Fatal error:|Assertion failed:'){throw 'Native log contains a fatal engine error.'}
    $passLines=@($record.log_events | Where-Object {$_ -match '\bWT_ZEPPELIN_CONTACT_PASSED\b'})
    if($passLines.Count -ne 1){throw 'Missing/duplicated native zeppelin pass marker.'}
    $record.native_result_sha256=Hash-ZeppelinInput $reportPath
    $record.checks_passed=$expected.Count
    $record.native_capture_phases=$report.captures
    $record.contact_recovery_count=$report.contact_recovery_count
    $record.positive_target_contacts=$report.positive_target_contacts
    $record.forward_blocking_contacts=$report.forward_blocking_contacts
    $record.contacting_vehicle_bodies=@($report.contacting_vehicle_bodies)
    $record.peak_impulse_n_s=$report.peak_impulse_n_s
    $record.held_drive_advance_cm=$report.held_drive_advance_cm
    $record.reverse_clearance_cm=$report.reverse_clearance_cm
    $record.source_surface_error_cm=$report.source_surface_error_cm
    Add-Type -AssemblyName System.Drawing
    foreach($phase in @('zeppelin-before','zeppelin-contact','zeppelin-blocked','zeppelin-reverse','zeppelin-overview','zeppelin-cabins','zeppelin-frame')){
        if(@($record.log_events | Where-Object {$_ -match ('\bWT_ZEPPELIN_CONTACT_CAPTURE phase='+[regex]::Escape($phase)+'\b')}).Count -ne 1){
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
                    file=$phase+'/'+$camera+'.png';sha256=(Hash-ZeppelinInput $file)
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
            $record.engine_log_sha256=Hash-ZeppelinInput $engineLog
            $record.log_events=@([IO.File]::ReadAllLines($engineLog,[Text.Encoding]::UTF8) |
                Where-Object {$_ -match 'WT_ZEPPELIN_CONTACT_|CONTROL_CHECK'})
        }
        try {
            $after=[ordered]@{}
            foreach($key in $inputs.Keys){$after[$key]=Hash-ZeppelinInput $inputs[$key]}
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
        Save-ZeppelinRecord $record $verification
        [pscustomobject]$record | Select-Object status,pid,exit_code,checks_passed,duration_seconds,error |
            ConvertTo-Json -Compress | Write-Output
    }
    if($process){$process.Dispose()}
    if($locked){$mutex.ReleaseMutex()}
    if($mutex){$mutex.Dispose()}
}
exit $runExitCode
