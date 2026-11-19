param(
    [string]$EngineRoot='E:\simulator-tools\UE_5.8',
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [string]$MapFile='',
    [switch]$MapOnly,
    [ValidateRange(60,900)][int]$TimeoutSeconds=240
)
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'SceneMap.ps1')
$encoding=[Text.UTF8Encoding]::new($false)
$process=$null
$mutex=$null
$locked=$false
$record=$null
$runExit=3
function Save-HeadingRecord {
    param([object]$Value,[string]$Path)
    [IO.File]::WriteAllText($Path+'.tmp',($Value|ConvertTo-Json -Depth 24),$encoding)
    Move-Item -LiteralPath ($Path+'.tmp') -Destination $Path -Force
}
function Get-HeadingHash {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
try {
    $ue=Get-UnrealTools -EngineRoot $EngineRoot
    $repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
    $out=[IO.Path]::GetFullPath($EvidenceDirectory)
    $prefix=([IO.Path]::GetFullPath((Join-Path $repo 'artifacts'))).TrimEnd('\')+'\'
    if(-not $out.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)){
        throw 'EvidenceDirectory must be a fresh directory in this repository artifacts/.'
    }
    if(Test-Path -LiteralPath $out){throw 'Use a fresh evidence directory; previous failures are retained.'}
    if(-not $MapFile){
        $MapFile=Join-Path $repo $(if($MapOnly){'assets/maps/wt_vlaanderen/map.json'}else{'assets/maps/import_lab/map.json'})
    }
    $sourceMap=(Resolve-Path -LiteralPath $MapFile).Path
    $project=$ue.Project.Replace('\','/').ToLowerInvariant()
    $hasher=[Security.Cryptography.SHA256]::Create()
    try {
        $digest=([BitConverter]::ToString($hasher.ComputeHash(
            [Text.Encoding]::UTF8.GetBytes($project)))).Replace('-','').ToLowerInvariant()
    } finally {$hasher.Dispose()}
    $mutex=[Threading.Mutex]::new($false,('Local\RMSimSceneImport_'+$digest))
    try {$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException] {$locked=$true}
    if(-not $locked){throw 'Another launch or authoring process owns this project.'}
    $pattern='(?:^|[\s"=])'+[regex]::Escape($project)+'(?=$|[\s"])'
    $active=@(Get-CimInstance Win32_Process | Where-Object {
        if(-not $_.CommandLine){return $false}
        $command=$_.CommandLine.Replace('\','/').ToLowerInvariant()
        ($command -match $pattern) -and ($_.Name -match '^UnrealEditor' -or
            $command.Contains('unrealbuildtool') -or $command.Contains('/build.bat'))
    })
    if($active.Count){throw ('Project is already active: '+(($active|ForEach-Object ProcessId)-join ', '))}
    [void][IO.Directory]::CreateDirectory($out)
    $map=[IO.File]::ReadAllText($sourceMap,[Text.Encoding]::UTF8)|ConvertFrom-Json
    if($MapOnly){
        if($map.kind -cne 'rm_scene_map'){throw 'MapOnly requires a saved scene with its real overview definition.'}
        $runtimeMap=$sourceMap
    }else{
        if($map.kind -cne 'rm_flat_map'){throw 'The motion regression requires an isolated flat-map fixture.'}
        $map.obstacles=@()
        $map.PSObject.Properties.Remove('inspection_obstacle_id')
        $runtimeMap=Join-Path $out 'map.json'
        [IO.File]::WriteAllText($runtimeMap,($map|ConvertTo-Json -Depth 20),$encoding)
    }
    $launch=Get-RMSimMapLaunch -MapFile $runtimeMap -Project $ue.Project -SpawnId 'default'
    $rig=Join-Path $repo 'assets/vehicles/aim_sentry/ue_mesh_rig.json'
    $profile=Join-Path $repo 'assets/vehicles/aim_sentry/drive-profile.json'
    $engineLog=Join-Path $out 'engine.log'
    $arguments=@((Quote-UnrealArgument $ue.Project),$launch.WorldUrl,
        '-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-nop4',
        '-windowed','-ResX=1280','-ResY=720','-d3d11','-NoShaderWorker',
        '-GyroProbe','-CameraHeadingProbe','-SimStepSeconds=0.005',
        ('-MeshRigFile='+(Quote-UnrealArgument $rig)),
        ('-MapFile='+(Quote-UnrealArgument $runtimeMap)),'-SpawnId=default',
        ('-EvidenceDir='+(Quote-UnrealArgument $out)),
        ('-abslog='+(Quote-UnrealArgument $engineLog)))
    $arguments+=@($launch.RuntimeArguments)
    if($MapOnly){$arguments+=@('-CameraHeadingMapProbe','-RequireMapOverview')}
    $inputs=[ordered]@{
        source_map=$sourceMap;runtime_map=$runtimeMap;vehicle_rig=$rig;drive_profile=$profile
        module=(Join-Path $repo 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll')
        wrapper=$PSCommandPath
        game_config=(Join-Path $repo 'unreal/RMSim/Config/DefaultGame.ini')
        engine_config=(Join-Path $repo 'unreal/RMSim/Config/DefaultEngine.ini')
    }
    foreach($name in @('CameraHeadingProbe.cpp','ImportLabGameMode.cpp','ImportLabGameMode.h',
        'GyroProbe.cpp','MapOverview.cpp','MapOverview.h','VehicleDrive.cpp','VehicleRig.h',
        'VehicleContactPhysics.cpp','VehicleMotorDrive.cpp','ContactProbe.cpp')){
        $inputs[$name]=Join-Path $repo ('unreal/RMSim/Source/RMSim/'+$name)
    }
    if($MapOnly){
        $inputs['overview']=Join-Path (Split-Path -Parent $sourceMap) 'overview.json'
        if($map.id -ceq 'wt_vlaanderen'){
            $inputs['world']=Join-Path $repo 'unreal/RMSim/Content/RMSim/Maps/wt_vlaanderen/World.umap'
            $inputs['collision_recipe']=Join-Path (Split-Path -Parent $sourceMap) 'generated-collision/recipe.json'
        }
    }
    $before=[ordered]@{}
    foreach($key in $inputs.Keys){$before[$key]=Get-HeadingHash $inputs[$key]}
    $begin=[DateTime]::UtcNow
    $verification=Join-Path $out 'verification.json'
    $record=[ordered]@{
        schema='rmsim_native_camera_heading_v1';status='running'
        started_utc=$begin.ToString('o');project=$ue.Project;executable=$ue.Editor
        arguments=$arguments;command=((Quote-UnrealArgument $ue.Editor)+' '+($arguments-join ' '))
        map_only=[bool]$MapOnly;source_map=$sourceMap;runtime_map=$runtimeMap
        timeout_seconds=$TimeoutSeconds;os_input_verified=$false;hud_pixels_verified=$false
        pose_overrides_used=$false;input_files=$inputs;hashes_before=$before;hashes_after=$null
        pid=$null;exit_code=$null;timed_out=$false;owned_process_terminated=$false
        checks_passed=0;captures=@();log_events=@();changed_inputs=@()
    }
    Save-HeadingRecord $record $verification
    $processArgs=@{FilePath=$ue.Editor;ArgumentList=$arguments;WindowStyle='Hidden';PassThru=$true
        RedirectStandardOutput=(Join-Path $out 'launcher.stdout.log')
        RedirectStandardError=(Join-Path $out 'launcher.stderr.log')}
    $process=Start-Process @processArgs
    $null=$process.Handle
    $record.pid=$process.Id
    Save-HeadingRecord $record $verification
    Write-Output ('CAMERA_HEADING_STARTED pid='+$process.Id+' evidence='+$out)
    $deadline=$begin.AddSeconds($TimeoutSeconds)
    $nextProgress=$begin.AddSeconds(30)
    while(-not $process.WaitForExit(1000)){
        $now=[DateTime]::UtcNow
        if($now -ge $deadline){
            $record.timed_out=$true;$process.Kill();$record.owned_process_terminated=$true
            [void]$process.WaitForExit(10000)
            throw 'Owned native camera-heading probe timed out; incomplete evidence is retained.'
        }
        if($now -ge $nextProgress){
            Write-Output ('CAMERA_HEADING_WAITING elapsed_s='+[int]($now-$begin).TotalSeconds)
            $nextProgress=$now.AddSeconds(30)
        }
    }
    $process.Refresh()
    if(-not $process.HasExited -or -not $process.WaitForExit(0)){throw 'Native process exit was not confirmed.'}
    $record.exit_code=$process.ExitCode
    Save-HeadingRecord $record $verification
    if($null -eq $process.ExitCode -or $process.ExitCode -ne 0){throw ('Native process failed: '+$process.ExitCode)}
    $reportPath=Join-Path $out 'control-result.json'
    $report=[IO.File]::ReadAllText($reportPath,[Text.Encoding]::UTF8)|ConvertFrom-Json
    $expected=@('camera_supported_spawn','near_target_gimbal_fixture',
        'c_orbit_preserves_moving_gimbal_direction','c_release_has_no_heading_snap',
        'scope_entry_does_not_lock_world_point','scope_mouse_continuously_turns_gimbal',
        'scope_mouse_stop_keeps_angular_heading','scope_exit_keeps_angular_heading',
        'chase_mouse_resumes_live_aim','c_then_shift_keeps_observation_independent',
        'c_release_returns_to_live_scope','scope_then_c_shift_exit_keeps_heading',
        'c_stabilizes_heading_during_hull_turn','focus_restore_discards_stale_mouse',
        'reset_while_c_held_captures_new_direction','shared_reset_clears_camera_heading_state')
    $phases=@('heading-near-target','heading-c-orbit','heading-scope','heading-reset')
    if($MapOnly){
        $expected=@('map_arrow_tracks_actual_gimbal','map_arrow_ignores_c_orbit',
            'map_arrow_follows_scope_mouse','map_arrow_survives_nested_scope_exit')
        $phases=@('map-gimbal','map-orbit','map-scope')
    }
    if($report.status -cne 'passed' -or $report.complete -ne $true -or
        $report.probe -cne 'native_camera_heading_v1' -or $report.map_only -ne [bool]$MapOnly -or
        @($report.checks).Count -ne $expected.Count -or @($report.phase_metrics).Count -ne $expected.Count){
        throw 'Native camera-heading report failed, is incomplete or has a mismatched mode.'
    }
    foreach($name in $expected){
        $checks=@($report.checks|Where-Object {$_.name -ceq $name})
        $metrics=@($report.phase_metrics|Where-Object {$_.name -ceq $name})
        if($checks.Count -ne 1 -or $checks[0].passed -ne $true -or $metrics.Count -ne 1){
            throw ('Missing, failed or duplicate native check: '+$name)
        }
    }
    $resetCount=if($MapOnly){0}else{2}
    if($report.pose_overrides_used -ne $false -or $report.os_input_verified -ne $false -or
        $report.hud_pixels_verified -ne $false -or
        $report.final_recovery_count-$report.initial_recovery_count -ne $resetCount -or
        $report.final_motion_generation-$report.initial_motion_generation -ne $resetCount){
        throw 'Native motion identity, reset count or evidence scope is invalid.'
    }
    if($MapOnly){
        foreach($metric in $report.phase_metrics){
            if($metric.map_samples -le 0 -or $metric.minimum_map_arrow_dot -le 0.999999){
                throw 'A real map frame did not follow the independently sampled actual gimbal.'
            }
        }
    }else{
        if($report.near_target_distance_cm -le 100 -or $report.near_target_distance_cm -ge 700){
            throw 'Near-target stress fixture was not established.'
        }
        foreach($name in @('c_orbit_preserves_moving_gimbal_direction','c_release_has_no_heading_snap',
            'scope_entry_does_not_lock_world_point','scope_mouse_stop_keeps_angular_heading',
            'scope_exit_keeps_angular_heading')){
            $metric=@($report.phase_metrics|Where-Object {$_.name -ceq $name})[0]
            if($metric.displacement_cm -le 30 -or $metric.peak_heading_error_deg -ge .5 -or
                $metric.drive_samples -le 20 -or $metric.minimum_same_frame_drive_dot -le .9999){
                throw ('Missing actual movement or angular stability: '+$name)
            }
        }
    }
    $log=[IO.File]::ReadAllText($engineLog,[Text.Encoding]::UTF8)
    $record.log_events=@($log-split '\r?\n'|Where-Object {$_ -match 'CAMERA_HEADING_|CONTROL_CHECK'})
    if($log -match 'Fatal error:|Assertion failed:'){throw 'Native engine error in log.'}
    if(@($record.log_events|Where-Object {$_ -match '\bCAMERA_HEADING_PASSED\b'}).Count -ne 1){
        throw 'Missing or duplicate native pass marker.'
    }
    Add-Type -AssemblyName System.Drawing
    foreach($phase in $phases){
        if(@($record.log_events|Where-Object {$_ -match ('\bCAMERA_HEADING_CAPTURE phase='+[regex]::Escape($phase)+'\b')}).Count -ne 1){
            throw ('Missing native capture marker: '+$phase)
        }
        foreach($camera in @('overview','aim_camera')){
            $file=Join-Path $out ($phase+'/'+$camera+'.png')
            $picture=$null
            try{
                $picture=[Drawing.Image]::FromFile($file)
                if($picture.RawFormat.Guid -ne [Drawing.Imaging.ImageFormat]::Png.Guid -or
                    $picture.Width -le 0 -or $picture.Height -le 0){throw 'Invalid PNG capture.'}
                $record.captures+=[ordered]@{file=$phase+'/'+$camera+'.png';sha256=(Get-HeadingHash $file)
                    width=$picture.Width;height=$picture.Height}
            }finally{if($picture){$picture.Dispose()}}
        }
    }
    $record.checks_passed=$expected.Count
    $record.native_result_sha256=Get-HeadingHash $reportPath
    $record.phase_metrics=@($report.phase_metrics)
    $record.status='passed';$runExit=0
}catch{
    if($record){$record.status='failed';$record.error=$_.Exception.Message}
    [Console]::Error.WriteLine($_.Exception.Message)
}finally{
    if($process){
        try{
            $process.Refresh()
            if(-not $process.HasExited){
                $process.Kill();if($record){$record.owned_process_terminated=$true}
                [void]$process.WaitForExit(10000);$runExit=3
            }
            if($record -and $process.HasExited){$record.exit_code=$process.ExitCode}
        }catch{if($record){$record.process_status_error=$_.Exception.Message}}
    }
    if($record){
        try{
            $after=[ordered]@{}
            foreach($key in $inputs.Keys){$after[$key]=Get-HeadingHash $inputs[$key]}
            $record.hashes_after=$after
            $record.changed_inputs=@($inputs.Keys|Where-Object {$before[$_] -cne $after[$_]})
            if($record.changed_inputs.Count){$record.status='failed';$record.error='Inputs changed during native validation.';$runExit=3}
            if(Test-Path -LiteralPath $engineLog -PathType Leaf){
                $record.engine_log_sha256=Get-HeadingHash $engineLog
                $record.log_events=@([IO.File]::ReadAllLines($engineLog,[Text.Encoding]::UTF8)|
                    Where-Object {$_ -match 'CAMERA_HEADING_|CONTROL_CHECK'})
            }
            $record.finished_utc=[DateTime]::UtcNow.ToString('o')
            Save-HeadingRecord $record $verification
        }catch{
            [Console]::Error.WriteLine(('Could not finalize camera-heading evidence: '+$_.Exception.Message));$runExit=3
        }
    }
    if($process){$process.Dispose()}
    if($locked){$mutex.ReleaseMutex()}
    if($mutex){$mutex.Dispose()}
}
exit $runExit
