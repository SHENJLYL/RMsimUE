param(
    [string]$EngineRoot='E:\simulator-tools\UE_5.8',
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [string]$MapFile='',
    [ValidateRange(60,900)][int]$TimeoutSeconds=240
)
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'SceneMap.ps1')
$encoding=[Text.UTF8Encoding]::new($false)
$process=$null;$mutex=$null;$locked=$false;$record=$null;$runExit=3
function Save-MapUiRecord {
    param([object]$Value,[string]$Path)
    [IO.File]::WriteAllText($Path+'.tmp',($Value|ConvertTo-Json -Depth 30),$encoding)
    Move-Item -LiteralPath ($Path+'.tmp') -Destination $Path -Force
}
function Get-MapUiHash {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Assert-FreshMapUiFile {
    param([string]$Path,[DateTime]$Started)
    if(-not (Test-Path -LiteralPath $Path -PathType Leaf)){throw ('Missing native evidence: '+$Path)}
    if((Get-Item -LiteralPath $Path).LastWriteTimeUtc -lt $Started.AddSeconds(-2)){
        throw ('Evidence predates the owned native process: '+$Path)
    }
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
    $canonical=(Resolve-Path -LiteralPath (Join-Path $repo 'assets/maps/wt_vlaanderen/map.json')).Path
    if(-not $MapFile){$MapFile=$canonical}
    $sourceMap=(Resolve-Path -LiteralPath $MapFile).Path
    if(-not $sourceMap.Equals($canonical,[StringComparison]::OrdinalIgnoreCase)){
        throw 'Map UI validation requires the canonical Flanders map.json.'
    }
    $package=Split-Path -Parent $sourceMap
    $map=[IO.File]::ReadAllText($sourceMap,[Text.Encoding]::UTF8)|ConvertFrom-Json
    if($map.kind -cne 'rm_scene_map' -or $map.id -cne 'wt_vlaanderen'){
        throw 'Map UI validation requires the saved Flanders scene.'
    }
    $project=$ue.Project.Replace('\','/').ToLowerInvariant()
    $hasher=[Security.Cryptography.SHA256]::Create()
    try{$digest=([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($project)))).Replace('-','').ToLowerInvariant()}
    finally{$hasher.Dispose()}
    $mutex=[Threading.Mutex]::new($false,('Local\RMSimSceneImport_'+$digest))
    try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
    if(-not $locked){throw 'Another launch or authoring process owns this project.'}
    $pattern='(?:^|[\s"=])'+[regex]::Escape($project)+'(?=$|[\s"])'
    $active=@(Get-CimInstance Win32_Process|Where-Object{
        if(-not $_.CommandLine){return $false}
        $command=$_.CommandLine.Replace('\','/').ToLowerInvariant()
        ($command -match $pattern) -and ($_.Name -match '^UnrealEditor' -or $command.Contains('unrealbuildtool') -or $command.Contains('/build.bat'))
    })
    if($active.Count){throw ('Project is already active: '+(($active|ForEach-Object ProcessId)-join ', '))}
    $launch=Get-RMSimMapLaunch -MapFile $sourceMap -Project $ue.Project -SpawnId 'default'
    $rig=Join-Path $repo 'assets/vehicles/aim_sentry/ue_mesh_rig.json'
    $profile=Join-Path $repo 'assets/vehicles/aim_sentry/drive-profile.json'
    $inputs=[ordered]@{
        source_map=$sourceMap;vehicle_rig=$rig;drive_profile=$profile
        overview=(Join-Path $package 'overview.json')
        world=(Join-Path $repo 'unreal/RMSim/Content/RMSim/Maps/wt_vlaanderen/World.umap')
        collision_recipe=(Join-Path $package 'generated-collision/recipe.json')
        module=(Join-Path $repo 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll')
        wrapper=$PSCommandPath
        common_wrapper=(Join-Path $PSScriptRoot 'Common.ps1')
        map_launcher=(Join-Path $PSScriptRoot 'SceneMap.ps1')
        game_config=(Join-Path $repo 'unreal/RMSim/Config/DefaultGame.ini')
        engine_config=(Join-Path $repo 'unreal/RMSim/Config/DefaultEngine.ini')
    }
    $sourceDirectory=Join-Path $repo 'unreal/RMSim/Source/RMSim'
    foreach($file in @(Get-ChildItem -LiteralPath $sourceDirectory -File|Where-Object{$_.Extension -in '.cpp','.h','.cs'}|Sort-Object Name)){
        $inputs['source/'+$file.Name]=$file.FullName
    }
    $overview=[IO.File]::ReadAllText($inputs.overview,[Text.Encoding]::UTF8)|ConvertFrom-Json
    foreach($name in @('image_file','calibration_file','mission_markers_file','ui_assets_file','ui_provenance_file')){
        $file=[IO.Path]::GetFullPath((Join-Path $package $overview.$name))
        if(-not $file.StartsWith($package.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Map UI input escapes the package.'}
        $inputs['overview/'+$name]=$file
    }
    $uiManifest=[IO.File]::ReadAllText($inputs['overview/ui_assets_file'],[Text.Encoding]::UTF8)|ConvertFrom-Json
    if($uiManifest.schema -cne 'rmsim_wt_map_ui_assets_v1' -or $uiManifest.source_sha256 -cne $overview.source_sha256){throw 'Original UI manifest identity differs.'}
    $assetReferences=@($overview.texture_asset)
    foreach($asset in $uiManifest.assets){
        $file=[IO.Path]::GetFullPath((Join-Path $package $asset.file))
        if(-not $file.StartsWith((Join-Path $package 'overview/ui').TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'UI source escapes its folder.'}
        if((Get-MapUiHash $file) -cne $asset.sha256){throw ('Original UI source hash differs: '+$asset.role)}
        $inputs['ui-source/'+$asset.role]=$file
        $assetReferences+=@($asset.asset)
        if($asset.kind -ceq 'font'){$assetReferences+=@($asset.face_asset)}
    }
    foreach($reference in $assetReferences){
        if(-not $reference.StartsWith('/Game/RMSim/Maps/wt_vlaanderen/UI/') -or $reference.Contains('..')){throw 'Unexpected map UI asset path.'}
        $assetPackage=($reference -split '\.')[0]
        $file=Join-Path $repo ('unreal/RMSim/Content/'+$assetPackage.Substring(6)+'.uasset')
        $inputs['ui-asset/'+[IO.Path]::GetFileNameWithoutExtension($file)]=$file
    }
    $before=[ordered]@{}
    foreach($key in $inputs.Keys){$before[$key]=Get-MapUiHash $inputs[$key]}
    [void][IO.Directory]::CreateDirectory($out)
    $engineLog=Join-Path $out 'engine.log'
    $arguments=@((Quote-UnrealArgument $ue.Project),$launch.WorldUrl,
        '-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-nop4',
        '-windowed','-ResX=1600','-ResY=900','-ForceRes','-d3d11','-NoShaderWorker',
        '-GyroProbe','-MapUiProbe','-RequireMapOverview','-SimStepSeconds=0.005',
        ('-MeshRigFile='+(Quote-UnrealArgument $rig)),('-MapFile='+(Quote-UnrealArgument $sourceMap)),
        '-SpawnId=default',('-EvidenceDir='+(Quote-UnrealArgument $out)),
        ('-abslog='+(Quote-UnrealArgument $engineLog)))
    $arguments+=@($launch.RuntimeArguments)
    $begin=[DateTime]::UtcNow
    $verification=Join-Path $out 'verification.json'
    $record=[ordered]@{
        schema='rmsim_native_map_ui_v1';status='running';started_utc=$begin.ToString('o')
        project=$ue.Project;executable=$ue.Editor;arguments=$arguments
        command=((Quote-UnrealArgument $ue.Editor)+' '+($arguments-join ' '))
        source_map=$sourceMap;runtime_map=$sourceMap;timeout_seconds=$TimeoutSeconds
        os_input_verified=$false;hud_pixels_verified=$false;real_viewport_hud_captures=$true
        pose_overrides_used=$false;viewport_px=@(1600,900);input_files=$inputs
        hashes_before=$before;hashes_after=$null;pid=$null;exit_code=$null;timed_out=$false
        owned_process_terminated=$false;checks_passed=0;captures=@();log_events=@();changed_inputs=@()
    }
    Save-MapUiRecord $record $verification
    $processArgs=@{FilePath=$ue.Editor;ArgumentList=$arguments;WindowStyle='Hidden';PassThru=$true
        RedirectStandardOutput=(Join-Path $out 'launcher.stdout.log');RedirectStandardError=(Join-Path $out 'launcher.stderr.log')}
    $process=Start-Process @processArgs;$null=$process.Handle;$record.pid=$process.Id
    Save-MapUiRecord $record $verification
    Write-Output ('MAP_UI_STARTED pid='+$process.Id+' evidence='+$out)
    $deadline=$begin.AddSeconds($TimeoutSeconds);$nextProgress=$begin.AddSeconds(30)
    while(-not $process.WaitForExit(1000)){
        $now=[DateTime]::UtcNow
        if($now -ge $deadline){
            $record.timed_out=$true;$process.Kill();$record.owned_process_terminated=$true
            [void]$process.WaitForExit(10000);throw 'Owned native map UI probe timed out; incomplete evidence is retained.'
        }
        if($now -ge $nextProgress){Write-Output ('MAP_UI_WAITING elapsed_s='+[int]($now-$begin).TotalSeconds);$nextProgress=$now.AddSeconds(30)}
    }
    $process.Refresh()
    if(-not $process.HasExited -or -not $process.WaitForExit(0)){throw 'Native process exit was not confirmed.'}
    $record.exit_code=$process.ExitCode;Save-MapUiRecord $record $verification
    if($null -eq $process.ExitCode -or $process.ExitCode -ne 0){throw ('Native process failed: '+$process.ExitCode)}
    $reportPath=Join-Path $out 'control-result.json';Assert-FreshMapUiFile $reportPath $begin
    $report=[IO.File]::ReadAllText($reportPath,[Text.Encoding]::UTF8)|ConvertFrom-Json
    $expected=@('original_map_ui_assets_and_supported_spawn',
        'physical_left_turn_is_screen_counterclockwise','physical_right_turn_is_screen_clockwise',
        'c_orbit_keeps_gimbal_arrow_and_separates_view_sector',
        'alt_wheel_zooms_minimap_without_steering_or_chase_zoom','minimap_zoom_clamps_at_source_ten_times',
        'minimap_zoom_out_restores_full_extent','m_opens_full_map_with_cursor',
        'full_map_wheel_preserves_cursor_world_anchor','full_map_drag_ping_and_local_point_use_display_coordinates',
        'closing_map_discards_cursor_delta_and_preserves_zoom_levels',
        'scope_alt_wheel_changes_map_without_changing_fov_or_gimbal',
        'focus_restore_discards_stale_map_keys_wheel_and_mouse')
    $phases=@('01-minimap-baseline','02-turn-left','03-turn-right','04-c-orbit','05-minimap-zoom',
        '06-minimap-max-zoom','07-full-map','08-full-map-pan-marks','09-map-closed','10-scope-map-zoom')
    if($report.status -cne 'passed' -or $report.complete -ne $true -or $report.probe -cne 'native_map_ui_v1' -or
        @($report.checks).Count -ne $expected.Count -or @($report.phase_metrics).Count -ne $expected.Count -or
        @($report.hud_screenshots).Count -ne $phases.Count -or $report.capture_sets_written -ne $phases.Count){
        throw 'Native map UI report failed, is incomplete or has the wrong schema.'
    }
    if($report.pose_overrides_used -ne $false -or $report.os_input_verified -ne $false -or
        $report.hud_pixels_verified -ne $false -or $report.real_viewport_hud_captures -ne $true -or
        $report.final_recovery_count -ne $report.initial_recovery_count -or
        $report.final_motion_generation -ne $report.initial_motion_generation){throw 'Native map UI evidence scope or motion identity is invalid.'}
    for($i=0;$i -lt $expected.Count;$i++){
        $name=$expected[$i]
        $checks=@($report.checks|Where-Object{$_.name -ceq $name})
        $metrics=@($report.phase_metrics|Where-Object{$_.name -ceq $name})
        if($checks.Count -ne 1 -or $checks[0].passed -ne $true -or $metrics.Count -ne 1 -or $metrics[0].phase -ne $i){throw ('Missing, failed, duplicate or out-of-order native check: '+$name)}
        $layout=$metrics[0].presentation
        if($layout.original_font_loaded -ne $true -or $layout.original_arrow_loaded -ne $true -or
            $layout.original_hull_loaded -ne $true -or $layout.original_panel_loaded -ne $true -or $layout.original_font_effect_loaded -ne $true -or $layout.flip_source_v -ne $true){throw 'An original UI resource or the corrected display projection was not used.'}
        $distance=[double]$layout.scale_distance_m;$pixels=[double]$layout.scale_width_px;$mpp=[double]$layout.metres_per_pixel
        if([double]::IsNaN($distance) -or [double]::IsInfinity($distance) -or $distance -le 0 -or $pixels -le 0 -or $mpp -le 0 -or
            [Math]::Abs($distance-$pixels*$mpp) -ge .01){throw ('Displayed scale is inconsistent in phase '+$name)}
    }
    $log=[IO.File]::ReadAllText($engineLog,[Text.Encoding]::UTF8)
    $record.log_events=@($log-split '\r?\n'|Where-Object{$_ -match 'MAP_UI_|CONTROL_CHECK'})
    if($log -match 'Fatal error:|Assertion failed:'){throw 'Native engine error in log.'}
    if(@($record.log_events|Where-Object{$_ -match '\bMAP_UI_PASSED\b'}).Count -ne 1){throw 'Missing or duplicate native pass marker.'}
    if(@(Get-ChildItem -LiteralPath (Join-Path $out 'hud') -File -Filter *.png).Count -ne $phases.Count){throw 'Unexpected native HUD capture count.'}
    Add-Type -AssemblyName System.Drawing
    foreach($phase in $phases){
        if(@($record.log_events|Where-Object{$_ -match ('\bMAP_UI_CAPTURE name='+[regex]::Escape($phase)+'\b')}).Count -ne 1){throw ('Missing native HUD capture marker: '+$phase)}
        $screens=@($report.hud_screenshots|Where-Object{$_.name -ceq $phase})
        if($screens.Count -ne 1 -or $screens[0].includes_hud -ne $true){throw ('Missing or duplicated actual viewport capture: '+$phase)}
        $file=[IO.Path]::GetFullPath((Join-Path $out ('hud/'+$phase+'.png')))
        if(-not $file.Equals([IO.Path]::GetFullPath($screens[0].file),[StringComparison]::OrdinalIgnoreCase)){throw 'HUD screenshot path differs from the expected owned evidence path.'}
        Assert-FreshMapUiFile $file $begin
        $picture=$null
        try{
            $picture=[Drawing.Image]::FromFile($file)
            if($picture.RawFormat.Guid -ne [Drawing.Imaging.ImageFormat]::Png.Guid -or $picture.Width -ne 1600 -or $picture.Height -ne 900){throw 'Expected a real 1600x900 viewport PNG including the Canvas HUD.'}
            $record.captures+=[ordered]@{file='hud/'+$phase+'.png';sha256=(Get-MapUiHash $file);width=$picture.Width;height=$picture.Height;includes_hud=$true}
        }finally{if($picture){$picture.Dispose()}}
    }
    $record.checks_passed=$expected.Count;$record.native_result_sha256=Get-MapUiHash $reportPath
    $record.phase_metrics=@($report.phase_metrics);$record.status='passed';$runExit=0
}catch{
    if($record){$record.status='failed';$record.error=$_.Exception.Message}
    [Console]::Error.WriteLine($_.Exception.Message)
}finally{
    if($process){
        try{
            $process.Refresh()
            if(-not $process.HasExited){$process.Kill();if($record){$record.owned_process_terminated=$true};[void]$process.WaitForExit(10000);$runExit=3}
            if($record -and $process.HasExited){$record.exit_code=$process.ExitCode}
        }catch{if($record){$record.process_status_error=$_.Exception.Message}}
    }
    if($record){
        try{
            $after=[ordered]@{}
            foreach($key in $inputs.Keys){$after[$key]=Get-MapUiHash $inputs[$key]}
            $record.hashes_after=$after
            $record.changed_inputs=@($inputs.Keys|Where-Object{$before[$_] -cne $after[$_]})
            if($record.changed_inputs.Count){$record.status='failed';$record.error='Inputs changed during native validation.';$runExit=3}
            if(Test-Path -LiteralPath $engineLog -PathType Leaf){
                $record.engine_log_sha256=Get-MapUiHash $engineLog
                $record.log_events=@([IO.File]::ReadAllLines($engineLog,[Text.Encoding]::UTF8)|Where-Object{$_ -match 'MAP_UI_|CONTROL_CHECK'})
            }
            $record.finished_utc=[DateTime]::UtcNow.ToString('o');Save-MapUiRecord $record $verification
        }catch{
            $record.status='failed';$record.error='Could not finalize map UI evidence: '+$_.Exception.Message;$runExit=3
            [Console]::Error.WriteLine($record.error)
            try{Save-MapUiRecord $record $verification}catch{[Console]::Error.WriteLine($_.Exception.Message)}
        }
    }
    if($process){$process.Dispose()};if($locked){$mutex.ReleaseMutex()};if($mutex){$mutex.Dispose()}
}
exit $runExit