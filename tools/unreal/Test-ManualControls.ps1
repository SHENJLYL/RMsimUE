param(
    [Parameter(Mandatory = $true)][string]$EngineRoot,
    [Parameter(Mandatory = $true)][string]$EvidenceDirectory,
    [string]$InputFile = '',
    [string]$MapFile = '',
    [string]$SpawnId = 'default',
    [switch]$Terrain,
    [switch]$Combat,
    [switch]$Contact,
    [double]$ContactDistanceCm = 340,
    [double]$ContactSpeedMps = 1,
    [switch]$ContactIntegration,
    [switch]$Gyro,
    [switch]$CameraDrive,
    [switch]$GyroRoute,
    [double]$GyroSpinRadS=2,
    [switch]$SteeringResponse,
    [double]$ResponseSpeedMps=.6,
    [double]$ResponseSpinRadS=0,
    [switch]$PrecisionTrace,
    [switch]$PrecisionKeys,
    [switch]$Traction,
    [switch]$WithTraining,
    [double]$TraversalDistanceCm = 0,
    [switch]$MapOverview,
    [int]$TimeoutSeconds = 180
)
. (Join-Path $PSScriptRoot 'Common.ps1')
try {
    $ue = Get-UnrealTools -EngineRoot $EngineRoot
    if (-not $InputFile) { $InputFile = Join-Path $PSScriptRoot '../../assets/vehicles/aim_sentry/ue_mesh_rig.json' }
    if (-not $MapFile) { $MapFile = Join-Path $PSScriptRoot '../../assets/maps/import_lab/map.json' }
    $inputPath = (Resolve-Path -LiteralPath $InputFile).Path
    $evidence = [IO.Path]::GetFullPath($EvidenceDirectory)
    if (Test-Path -LiteralPath $evidence) { throw 'Use a new evidence directory; old results must not be reused.' }
    if ($TimeoutSeconds -lt 1) { throw 'TimeoutSeconds must be positive.' }
    New-Item -ItemType Directory -Path $evidence | Out-Null
    if (@($Terrain,$Combat,$Contact,$Gyro,$Traction | Where-Object { $_ }).Count -gt 1) { throw 'Choose one probe type.' }
    $probeFlag = if ($Traction) { '-TractionProbe' } elseif ($Gyro) { '-GyroProbe' } elseif ($Contact) { '-ContactProbe' } elseif ($Combat) { '-CombatProbe' } elseif ($Terrain) { '-TerrainProbe' } else { '-ControlProbe' }
    $arguments = @((Quote-UnrealArgument $ue.Project),
        '/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
        '-game', '-RenderOffScreen', '-unattended', '-NoSplash', '-NoSound', $probeFlag,
        ('-MapFile=' + (Quote-UnrealArgument (Resolve-Path -LiteralPath $MapFile).Path)),
        ('-SpawnId=' + (Quote-UnrealArgument $SpawnId)),
        ('-MeshRigFile=' + (Quote-UnrealArgument $inputPath)),
        ('-EvidenceDir=' + (Quote-UnrealArgument $evidence)),
        ('-abslog=' + (Quote-UnrealArgument (Join-Path $evidence 'runtime.log'))))
    if ($MapOverview) { $arguments += '-MapOverview' }
    if ($WithTraining) { $arguments += '-TrainingMode' }
    if ($ContactIntegration) {
        if (-not $Contact -or -not $WithTraining) { throw 'ContactIntegration needs Contact and WithTraining.' }
        $arguments += '-ContactIntegration'
    }
    if ($Contact -or $Gyro) { $arguments += '-ContactDistanceCm=' + $ContactDistanceCm.ToString([Globalization.CultureInfo]::InvariantCulture) }
    if ($Contact -or $Gyro) { $arguments += '-ContactSpeedMps=' + $ContactSpeedMps.ToString([Globalization.CultureInfo]::InvariantCulture) }
    if ($GyroRoute) { if(-not $Gyro){throw 'GyroRoute needs Gyro.'};$arguments += '-GyroRoute' }
    if ($CameraDrive) { if(-not $Gyro){throw 'CameraDrive needs Gyro.'};$arguments += '-CameraDriveProbe' }
    if ($GyroRoute) { $arguments += '-GyroSpinRadS='+$GyroSpinRadS.ToString([Globalization.CultureInfo]::InvariantCulture) }
    if ($SteeringResponse) { if(-not $Gyro){throw 'SteeringResponse needs Gyro.'};$arguments += '-SteeringResponse' }
    if ($PrecisionTrace) { if(-not $SteeringResponse){throw 'PrecisionTrace needs SteeringResponse.'};$arguments += '-PrecisionTrace' }
    if ($SteeringResponse) { $arguments += @('-ResponseSpeedMps='+$ResponseSpeedMps.ToString([Globalization.CultureInfo]::InvariantCulture), '-ResponseSpinRadS='+$ResponseSpinRadS.ToString([Globalization.CultureInfo]::InvariantCulture)) }
    if ($PrecisionKeys) { if(-not $GyroRoute){throw 'PrecisionKeys needs GyroRoute.'};$arguments += '-PrecisionKeys' }
    if ($TraversalDistanceCm -gt 0) { $arguments += '-TraversalDistanceCm=' + $TraversalDistanceCm.ToString([Globalization.CultureInfo]::InvariantCulture) }
    $process = Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru
    [void]$process.Handle
    Write-Output "Control probe PID $($process.Id)"
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-Process -Id $process.Id -Force
        throw 'Control probe timed out; retained incomplete evidence.'
    }
    $process.WaitForExit(); $process.Refresh()
    if ($null -eq $process.ExitCode -or $process.ExitCode -ne 0) { throw "Control process failed: $($process.ExitCode)" }
    $report = Get-Content -LiteralPath (Join-Path $evidence 'control-result.json') -Raw | ConvertFrom-Json
    $expected = @('chase_roof_clearance', 'forward_throttle', 'space_brake', 'reverse_throttle', 'brake_before_turn',
        'hull_turn_preserves_world_aim', 'free_look_preserves_target', 'free_look_release', 'shift_scope',
        'scope_wheel_zoom', 'right_button_zoom', 'zoom_release_restores_selected_fov', 'scope_mouse_moves_aim',
        'return_to_chase', 'chase_wheel_distance', 'focus_loss_stops_input', 'focus_regain_discards_mouse_jump', 'rapid_aim_convergence')
    if ($Terrain) { $expected = @('supported_spawn','terrain_forward','terrain_brake','terrain_reverse','terrain_turn_and_aim','missing_ground_rejected','terrain_obstacle_or_edge_stop') }
    if ($Terrain) { $expected += @('terrain_full_turn','terrain_drive_after_turn') }
    if ($Combat) { $expected = @('training_initial_state','shared_input_ballistic_hit','fire_cadence','overheat_blocks_shots','cooling_uses_sim_time','empty_magazine','shared_reset','unfocused_fire_rejected','place_real_occluder','map_blocks_armor_damage','targets_and_victory','finished_round_rejects_fire','round_timeout') }
    if ($Combat) { $expected += 'cadence_frame_partition' }
    if ($Combat) { $expected += 'moving_muzzle_event_time' }
    if ($Terrain -and $TraversalDistanceCm -gt 0) { $expected = @('supported_spawn','source_ramp_traversal') }
    if ($Contact) { $expected = @('physical_settle','physical_forward_route','physical_brake','physical_reverse_route') }
    if ($ContactIntegration) { $expected += @('physical_shared_left_turn','physical_shared_right_turn','physical_shared_aim_fire','physical_wall_stop','physical_shared_reset','physical_fall_recovery') }
    if ($Gyro) { $expected=@('gyro_supported_spawn','normal_camera_forward_smooth','released_keys_active_brake','normal_camera_left_strafe','normal_strafe_brake','gyro_camera_forward_direction','gyro_faster_key','gyro_slower_minimum','gyro_faster_maximum','gyro_mouse_pitch_world_aim','gyro_toggle_off_brakes_spin') }
    if ($Gyro -and -not $GyroRoute) { $expected+=@('normal_forward_after_camera_turn','camera_turn_release_brake','normal_right_after_camera_turn','steering_response_rest_pose','steering_step_response','moving_turn_initial_velocity','moving_direction_response','precision_key_speed_cap') }
    if ($SteeringResponse) { $expected=@('steering_response_rest_pose','steering_step_response','moving_turn_initial_velocity','moving_direction_response','precision_key_speed_cap') }
    if ($PrecisionTrace) { $expected+='precision_steady_spin' }
    if ($GyroRoute) { $expected=@('gyro_supported_spawn','gyro_spin_up','gyro_forward_wave_route','gyro_route_brake','gyro_reverse_wave_route') }
    if ($CameraDrive) { $expected=@('camera_drive_ready','c_orbit_drives_turret_heading','shift_scope_drives_turret_heading','shift_exit_clears_sticky_scope','five_mps_speed_profile','five_mps_brake_to_stop') }
    if ($Traction) { $expected=@('traction_supported_slope','parallel_wheels_free_roll','transverse_wheels_settle','transverse_contact_grip','traction_reset_for_hold','released_keys_hold_slope','downhill_drive_before_release','downhill_key_release_brake','parked_external_impulse_wakes_body','parked_external_impulse_recovery') }
    if ($report.status -ne 'passed' -or $report.complete -ne $true -or @($report.checks).Count -ne $expected.Count) {
        throw 'Control report incomplete or failed.'
    }
    foreach ($name in $expected) {
        $matches = @($report.checks | Where-Object name -eq $name)
        if ($matches.Count -ne 1 -or $matches[0].passed -ne $true) { throw "Missing/failed control check: $name" }
    }
    Add-Type -AssemblyName System.Drawing
    $phases = if ($Terrain) { @('spawn','drive') } else { @('chase','free-look','scope','scope-zoom','aim-converged') }
    if ($Combat) { $phases = @('training-start','armor-hit','occluded','clear-lane','victory') }
    if ($Contact) { $phases = @('contact-rest','contact-end','contact-return') }
    if ($ContactIntegration) { $phases += @('contact-fire','contact-wall','contact-recovered') }
    if ($Gyro) { $phases=@('gyro-start','gyro-moving','gyro-aim','gyro-camera-relative','gyro-response','moving-response') }
    if ($SteeringResponse) { $phases=@('gyro-response','moving-response') }
    if ($GyroRoute) { $phases=@('gyro-start','gyro-end','gyro-return') }
    if ($CameraDrive) { $phases=@('gyro-start','gyro-c-free-look','gyro-scope','gyro-chase') }
    if ($Traction) { $phases=@('traction-cross','traction-hold') }
    foreach ($phase in $phases) {
        foreach ($camera in @('overview','aim_camera')) {
            $file = Join-Path $evidence "$phase/$camera.png"
            $image = [Drawing.Image]::FromFile($file)
            try {
                if ($image.RawFormat.Guid -ne [Drawing.Imaging.ImageFormat]::Png.Guid -or $image.Width -lt 1) {
                    throw "Invalid PNG: $file"
                }
            } finally { $image.Dispose() }
        }
    }
    $rigSpec=Get-Content -LiteralPath $inputPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $profileHash=''
    if($null -ne $rigSpec.PSObject.Properties['drive_profile_file']){
        $profileHash=(Get-FileHash -LiteralPath (Join-Path (Split-Path -Parent $inputPath) $rigSpec.drive_profile_file) -Algorithm SHA256).Hash
    }
    [pscustomobject]@{run_id=$report.run_id;checks=$expected.Count;decoded_captures=($phases.Count*2);passed=$true;
        os_input_verified=$false;scope=$report.scope;process_exit=$process.ExitCode;
        drive_profile_sha256=$profileHash;
        module_sha256=(Get-FileHash -LiteralPath (Join-Path (Split-Path -Parent $ue.Project) 'Binaries/Win64/UnrealEditor-RMSim.dll') -Algorithm SHA256).Hash;
        rig_sha256=(Get-FileHash -LiteralPath $inputPath -Algorithm SHA256).Hash;
        map_sha256=(Get-FileHash -LiteralPath $MapFile -Algorithm SHA256).Hash} |
        ConvertTo-Json | Set-Content -Encoding utf8 -LiteralPath (Join-Path $evidence 'verification.json')
    Write-Output "Control probe PASS: $($expected.Count) checks, $($phases.Count*2) PNGs. OS input is a separate check."
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 3
}
