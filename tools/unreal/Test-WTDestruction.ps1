param(
    [Alias('Case')][ValidateSet('Geometry','Forward','Reverse','Protected','Pressure','Foliage')]
    [string]$ProbeCase='Geometry',
    [string]$EngineRoot='E:\simulator-tools\UE_5.8',
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [ValidateRange(30,1800)][int]$TimeoutSeconds=240
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

function Save-Record {
    param([object]$Value,[string]$Path)
    $temporary=$Path+'.tmp'
    [IO.File]::WriteAllText($temporary,($Value | ConvertTo-Json -Depth 30),$encoding)
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
function Hash-File {
    param([string]$Path)
    if(-not (Test-Path -LiteralPath $Path -PathType Leaf)){throw ('Missing evidence input: '+$Path)}
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Read-RmiTransform {
    param([string]$Path,[int]$Index)
    $bytes=[IO.File]::ReadAllBytes($Path)
    if($bytes.Length -lt 8 -or [Text.Encoding]::ASCII.GetString($bytes,0,4) -cne 'RMI1'){
        throw 'Invalid source RMI header.'
    }
    $count=[BitConverter]::ToUInt32($bytes,4)
    if($Index -lt 0 -or $Index -ge $count -or $bytes.LongLength -ne (8+[long]$count*40)){
        throw 'Invalid source RMI instance index/size.'
    }
    $values=@()
    for($field=0;$field -lt 10;$field++){
        $value=[double][BitConverter]::ToSingle($bytes,8+$Index*40+$field*4)
        if([double]::IsNaN($value) -or [double]::IsInfinity($value)){throw 'Nonfinite source RMI transform.'}
        $values+=$value
    }
    return [pscustomobject]@{
        index=$Index
        source_count=$count
        position_ros_m=@($values[0..2])
        quaternion_xyzw=@($values[3..6])
        scale_xyz=@($values[7..9])
    }
}
function Require-IntegerField {
    param([string]$Text,[string]$Name,[long]$Minimum,[long]$Maximum)
    $matchesFound=[regex]::Matches($Text,'\b'+[regex]::Escape($Name)+'=(\d+)\b')
    if($matchesFound.Count -ne 1){throw ('Missing/duplicated native result field: '+$Name)}
    $value=[long]::Parse($matchesFound[0].Groups[1].Value,$culture)
    if($value -lt $Minimum -or $value -gt $Maximum){throw ('Native result field outside expected range: '+$Name)}
    return $value
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
    $project=$ue.Project.Replace('\','/').ToLowerInvariant()
    $hasher=[Security.Cryptography.SHA256]::Create()
    try {
        $projectHash=([BitConverter]::ToString(
            $hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($project)))).Replace('-','').ToLowerInvariant()
    } finally {$hasher.Dispose()}
    # The same project mutex and process guard as Run-WTNativeScript.
    $mutex=[Threading.Mutex]::new($false,('Local\RMSimSceneImport_'+$projectHash))
    try {$locked=$mutex.WaitOne(0)}
    catch [Threading.AbandonedMutexException] {$locked=$true}
    if(-not $locked){throw 'Another launch or authoring process owns this project.'}
    $pattern='(?:^|[\s"=])'+[regex]::Escape($project)+'(?=$|[\s"])'
    $active=@(Get-CimInstance Win32_Process | Where-Object {
        if(-not $_.CommandLine){return $false}
        $command=$_.CommandLine.Replace('\','/').ToLowerInvariant()
        ($command -match $pattern) -and (
            $_.Name -match '^UnrealEditor' -or
            $command.Contains('unrealbuildtool') -or $command.Contains('/build.bat'))
    })
    if($active.Count){
        $active | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress | Write-Output
        throw ('Project is active: '+(($active | ForEach-Object ProcessId)-join ', '))
    }

    [void][IO.Directory]::CreateDirectory($out)
    $captureDirectory=Join-Path $out 'captures'
    [void][IO.Directory]::CreateDirectory($captureDirectory)
    $fixtureDirectory=Join-Path $out 'fixture'
    [void][IO.Directory]::CreateDirectory($fixtureDirectory)
    $sourceMap=Join-Path $repo 'assets/maps/wt_vlaanderen/map.json'
    $sceneSource=Join-Path $repo 'assets/maps/wt_vlaanderen/scene-source.json'
    $rigFile=Join-Path $repo 'assets/vehicles/aim_sentry/ue_mesh_rig.json'
    $profileFile=Join-Path $repo 'assets/vehicles/aim_sentry/drive-profile.json'
    $map=Get-Content -LiteralPath $sourceMap -Raw -Encoding UTF8 | ConvertFrom-Json
    $scene=Get-Content -LiteralPath $sceneSource -Raw -Encoding UTF8 | ConvertFrom-Json
    if($map.kind -cne 'rm_scene_map' -or $map.id -cne 'wt_vlaanderen' -or
       $map.level_asset -cne '/Game/RMSim/Maps/wt_vlaanderen/World' -or
       $map.source_sha256 -cne $scene.source_sha256 -or $scene.id -cne $map.id){
        throw 'Canonical source map identity differs from the prepared Flanders scene.'
    }
    $spawn=@($map.spawns | Where-Object {$_.id -ceq 'default'})
    if($spawn.Count -ne 1){throw 'Expected exactly one canonical default spawn.'}
    $target=$null
    $sourceTransform=$null
    $requestedSpeed=$null
    $expectedMesh=$null
    $expectedInstance=$null
    $probeArguments=@()
    $passMarker='WT_DESTRUCTION_PROBE_PASSED'
    if($ProbeCase -eq 'Geometry'){
        $probeArguments+='-DestructionProbe'
    } else {
        if($ProbeCase -eq 'Protected'){
            $target=@(1163.004272,-1214.974609,-0.701529)
            $spawn[0].position_m=@(1163.0043,-1210.,-0.7015)
            $spawn[0].yaw_rad=0.
            $expectedMesh='col_prop_wood_fence_h'
            $expectedInstance=833
            $rmiFile=Join-Path $repo 'assets/maps/wt_vlaanderen/instances/prop_wood_fence_h.rmi'
            $requestedSpeed=5.
            $probeArguments+=@('-DestructionDriveFast','-DestructionExpectFixed')
            $passMarker='WT_DESTRUCTION_FIXED_PASSED'
        } elseif($ProbeCase -eq 'Foliage'){
            $target=@(643.9259033203125,-111.2221450805664,-2.521958112716675)
            $spawn[0].position_m=@(643.9259033203125,-114.7221450805664,-2.519264515144581)
            $spawn[0].yaw_rad=1.5707963267948966
            $expectedMesh='prop_tree_tilia_bush_b'
            $expectedInstance=91
            $rmiFile=Join-Path $repo 'assets/maps/wt_vlaanderen/instances/prop_tree_tilia_bush_b.rmi'
            $requestedSpeed=1.
            $probeArguments+='-DestructionDriveFoliage'
            $passMarker='WT_FOLIAGE_DRIVE_PASSED'
        } else {
            $target=@(569.6423950195312,-103.1281509399414,-2.521958112716675)
            $expectedMesh='col_prop_wood_fence_h_plank'
            $expectedInstance=3
            $rmiFile=Join-Path $repo 'assets/maps/wt_vlaanderen/instances/prop_wood_fence_h_plank.rmi'
            if($ProbeCase -eq 'Reverse'){
                $spawn[0].position_m=@(573.628899622979,-103.45645117857884,-2.519264515144581)
                $spawn[0].yaw_rad=3.0594251662271215
            } else {
                $spawn[0].position_m=@(565.6558904160834,-102.79985070130397,-2.519264515144581)
                $spawn[0].yaw_rad=-0.08216748736267168
            }
            if($ProbeCase -eq 'Pressure'){
                $requestedSpeed=1.2
                $probeArguments+='-DestructionDriveHold'
            } else {
                $requestedSpeed=4.5
                $probeArguments+='-DestructionDriveFast'
            }
            $passMarker='WT_DESTRUCTION_DRIVE_PASSED'
        }
        $sourceTransform=Read-RmiTransform -Path $rmiFile -Index $expectedInstance
        for($axis=0;$axis -lt 3;$axis++){
            if([Math]::Abs($sourceTransform.position_ros_m[$axis]-$target[$axis]) -gt 0.000001){
                throw 'Source target moved from the audited fixture.'
            }
        }
        $probeArguments+=@(
            ('-DestructionDriveSpeed='+$requestedSpeed.ToString('R',$culture)),
            ('-DestructionExpectMesh='+$expectedMesh),
            ('-DestructionExpectedInstance='+$expectedInstance),
            ('-DestructionTargetX='+$target[0].ToString('R',$culture)),
            ('-DestructionTargetY='+$target[1].ToString('R',$culture)),
            ('-DestructionTargetZ='+$target[2].ToString('R',$culture))
        )
    }
    # Runtime resolves generated collision cells relative to MapFile. Keep
    # the disposable spawn map self-contained without changing source assets.
    $sourcePackage=Split-Path -Parent $sourceMap
    $runtimeInputs=[ordered]@{}
    $runtimeCopies=@()
    foreach($relative in @('generated-collision','overview.json')){
        $origin=Join-Path $sourcePackage $relative
        if(-not (Test-Path -LiteralPath $origin)){throw ('Missing fixture runtime dependency: '+$origin)}
        Copy-Item -LiteralPath $origin -Destination (Join-Path $fixtureDirectory $relative) -Recurse
        $files=if(Test-Path -LiteralPath $origin -PathType Container){
            @(Get-ChildItem -LiteralPath $origin -Recurse -File)
        } else {@(Get-Item -LiteralPath $origin)}
        foreach($file in $files){
            $local=$file.FullName.Substring($sourcePackage.Length+1)
            $copy=Join-Path $fixtureDirectory $local
            $identity=Hash-File $file.FullName
            if((Hash-File $copy) -cne $identity){throw ('Fixture dependency copy differs: '+$local)}
            $runtimeInputs['runtime/'+$local.Replace('\','/')]=$file.FullName
            $runtimeCopies+=[ordered]@{relative=$local.Replace('\','/');sha256=$identity}
        }
    }
    $fixtureMap=Join-Path $fixtureDirectory 'map.json'
    [IO.File]::WriteAllText($fixtureMap,($map | ConvertTo-Json -Depth 20),$encoding)
    $launch=Get-RMSimMapLaunch -MapFile $fixtureMap -Project $ue.Project -SpawnId 'default'
    if(-not $launch.IsSavedScene){throw 'Destruction fixtures require the saved Flanders World.'}
    $engineLog=Join-Path $out 'engine.log'
    $arguments=@(
        (Quote-UnrealArgument $ue.Project),$launch.WorldUrl,
        '-game','-RenderOffScreen','-unattended','-NoSplash','-nosound','-nop4',
        '-windowed','-ResX=1280','-ResY=720','-ContactPhysics','-d3d11','-NoShaderWorker',
        '-GimbalYaw=0','-GimbalPitch=0',
        ('-MeshRigFile='+(Quote-UnrealArgument $rigFile)),
        ('-MapFile='+(Quote-UnrealArgument $fixtureMap)),
        '-SpawnId=default',
        ('-CaptureDir='+(Quote-UnrealArgument $captureDirectory)),
        ('-AbsLog='+(Quote-UnrealArgument $engineLog))
    )
    $arguments+=@($launch.RuntimeArguments)
    $arguments+=$probeArguments

    $inputs=[ordered]@{
        map=$sourceMap
        scene_source=$sceneSource
        instances=(Join-Path $repo 'assets/maps/wt_vlaanderen/instances.json')
        source_rmi_plank=(Join-Path $repo 'assets/maps/wt_vlaanderen/instances/prop_wood_fence_h_plank.rmi')
        source_rmi_fence=(Join-Path $repo 'assets/maps/wt_vlaanderen/instances/prop_wood_fence_h.rmi')
        world=(Join-Path $repo 'unreal/RMSim/Content/RMSim/Maps/wt_vlaanderen/World.umap')
        module=(Join-Path $repo 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll')
        vehicle_rig=$rigFile
        drive_profile=$profileFile
        game_config=(Join-Path $repo 'unreal/RMSim/Config/DefaultGame.ini')
        engine_config=(Join-Path $repo 'unreal/RMSim/Config/DefaultEngine.ini')
    }
    foreach($name in @('WTFoliageCrushComponent.cpp','WTFoliageCrushComponent.h',
            'WTBreakableGeometry.cpp','WTBreakableGeometry.h','DestructionProbe.cpp','ImportLabGameMode.cpp',
            'ImportLabGameMode.h','VehicleRig.h','VehicleContactPhysics.cpp')){
        $inputs[$name]=Join-Path $repo ('unreal/RMSim/Source/RMSim/'+$name)
    }
    foreach($key in $runtimeInputs.Keys){$inputs[$key]=$runtimeInputs[$key]}
    if($ProbeCase -ne 'Geometry'){$inputs['target_rmi']=$rmiFile}
    $before=[ordered]@{}
    foreach($key in $inputs.Keys){$before[$key]=Hash-File $inputs[$key]}
    $begin=[DateTime]::UtcNow
    $record=[ordered]@{
        schema='rmsim_wt_destruction_native_v1'
        status='running'
        case=$ProbeCase
        started_utc=$begin.ToString('o')
        project=$ue.Project
        executable=$ue.Editor
        arguments=$arguments
        command=((Quote-UnrealArgument $ue.Editor)+' '+($arguments -join ' '))
        timeout_seconds=$TimeoutSeconds
        fixture_map=$fixtureMap
        fixture_map_sha256=(Hash-File $fixtureMap)
        fixture_spawn=$spawn[0]
        fixture_runtime_dependency_copies=$runtimeCopies
        target_ros_m=$target
        expected_mesh=$expectedMesh
        expected_instance=$expectedInstance
        source_transform=$sourceTransform
        requested_speed_mps=$requestedSpeed
        expected_pass_marker=$passMarker
        actual_contact_validation=($ProbeCase -notin @('Geometry','Foliage'))
        pose_overrides_used=$false
        source_world_mutation_allowed=$false
        input_files=$inputs
        hashes_before=$before
        hashes_after=$null
        pid=$null
        exit_code=$null
        timed_out=$false
        owned_process_terminated=$false
        captures=@()
        log_events=@()
    }
    $verification=Join-Path $out 'verification.json'
    Save-Record $record $verification
    $launchOptions=@{
        FilePath=$ue.Editor
        ArgumentList=$arguments
        WindowStyle='Hidden'
        RedirectStandardOutput=(Join-Path $out 'launcher.stdout.log')
        RedirectStandardError=(Join-Path $out 'launcher.stderr.log')
        PassThru=$true
    }
    $process=Start-Process @launchOptions
    $null=$process.Handle
    $record.pid=$process.Id
    Save-Record $record $verification
    Write-Output ('WT_DESTRUCTION_TEST_STARTED case='+$ProbeCase+' pid='+$process.Id+' evidence='+$out)
    $deadline=$begin.AddSeconds($TimeoutSeconds)
    $nextProgress=$begin.AddSeconds(30)
    while(-not $process.WaitForExit(1000)){
        $now=[DateTime]::UtcNow
        if($now -ge $deadline){
            $record.timed_out=$true
            # This Process object owns its original handle; no name-wide or
            # PID-discovery termination is used.
            $process.Kill()
            $record.owned_process_terminated=$true
            [void]$process.WaitForExit(10000)
            throw ('Owned destruction probe timed out after '+$TimeoutSeconds+' seconds.')
        }
        if($now -ge $nextProgress){
            Write-Output ('WT_DESTRUCTION_TEST_WAITING case='+$ProbeCase+' elapsed_s='+[int]($now-$begin).TotalSeconds)
            $nextProgress=$now.AddSeconds(30)
        }
    }
    # The finite wait above confirms the owned engine process exited.
    # Parameterless WaitForExit also drains redirected pipes; a child such as
    # the shared Zen server can keep those pipe handles open after UE exits.
    # Inspect the retained process handle without waiting on descendant I/O.
    $process.Refresh()
    if(-not $process.HasExited -or -not $process.WaitForExit(0)){
        throw 'Owned native process exit was not confirmed by its process handle.'
    }
    $record.exit_code=$process.ExitCode
    Write-Output ('WT_DESTRUCTION_TEST_STAGE native_exit case='+$ProbeCase+' exit_code='+$record.exit_code)
    Save-Record $record $verification
    if($null -eq $process.ExitCode -or $process.ExitCode -ne 0){
        throw ('Native destruction probe returned '+$process.ExitCode)
    }
    if(-not (Test-Path -LiteralPath $engineLog -PathType Leaf)){throw 'Native engine log is missing.'}
    # Plain .NET strings avoid PowerShell 5 Get-Content metadata (PSProvider
    # and PSDrive), which otherwise expands recursively in ConvertTo-Json.
    $log=[IO.File]::ReadAllText($engineLog,[Text.Encoding]::UTF8)
    $record.log_events=@($log -split '\r?\n' | Where-Object {$_ -match 'WT_DESTRUCTION_|WT_DEBRIS_|WT_FOLIAGE_'})
    if($log -match 'WT_DESTRUCTION_VALIDATION_FAILED|Fatal error:|Assertion failed:'){
        throw 'Native destruction log contains a failed validation or fatal error.'
    }
    $record.pass_lines=@($record.log_events | Where-Object {$_ -match ('\b'+[regex]::Escape($passMarker)+'\b')})
    if($record.pass_lines.Count -ne 1){throw ('Missing/duplicated native result marker: '+$passMarker)}
    $passedLine=$record.pass_lines[0]
    if($ProbeCase -eq 'Protected'){
        [void](Require-IntegerField $passedLine 'actual_target_contacts' 1 ([long]::MaxValue))
        [void](Require-IntegerField $passedLine 'broken' 0 0)
        [void](Require-IntegerField $passedLine 'captures' 2 2)
    } elseif($ProbeCase -eq 'Foliage'){
        [void](Require-IntegerField $passedLine 'patches' 1 ([long]::MaxValue))
        [void](Require-IntegerField $passedLine 'hard_broken' 0 0)
        [void](Require-IntegerField $passedLine 'live' 0 0)
        [void](Require-IntegerField $passedLine 'captures' 8 8)
        $travelMatch=[regex]::Match($passedLine,'\bbody_travel_cm=([0-9]+(?:\.[0-9]+)?)\b')
        if(-not $travelMatch.Success){throw 'Foliage result lacks actual body travel.'}
        $travel=[double]::Parse($travelMatch.Groups[1].Value,$culture)
        if([double]::IsNaN($travel) -or [double]::IsInfinity($travel) -or $travel -le 0.5){
            throw 'Foliage probe did not establish actual vehicle movement.'
        }
        $patchPattern='\bWT_FOLIAGE_PATCH\b.*\bmesh='+[regex]::Escape($expectedMesh)+
            '\b.*\binstance='+$expectedInstance+'\b'
        $patchLines=@($record.log_events | Where-Object {$_ -match $patchPattern})
        if($patchLines.Count -lt 1){throw 'Expected source shrub instance was not locally deformed.'}
        foreach($patchLine in $patchLines){
            [void](Require-IntegerField $patchLine 'touched_triangles' 1 ([long]::MaxValue))
            [void](Require-IntegerField $patchLine 'retained_triangles' 1 ([long]::MaxValue))
        }
        $record.target_foliage_patch_events=$patchLines
        $record.actual_body_travel_cm=$travel
    } elseif($ProbeCase -ne 'Geometry'){
        [void](Require-IntegerField $passedLine 'target_contacts' 1 ([long]::MaxValue))
        $debris=Require-IntegerField $passedLine 'debris' 1 ([long]::MaxValue)
        [void](Require-IntegerField $passedLine 'positive_motion' $debris $debris)
        [void](Require-IntegerField $passedLine 'reverse_initial' 0 0)
        [void](Require-IntegerField $passedLine 'live' 0 0)
        [void](Require-IntegerField $passedLine 'captures' 8 8)
    }
    if($ProbeCase -eq 'Geometry'){
        [void](Require-IntegerField $passedLine 'live' 0 0)
        [void](Require-IntegerField $passedLine 'captures' 8 8)
    }
    if($ProbeCase -notin @('Geometry','Foliage')){
        if($passedLine -notmatch ('\bexpected_mesh='+[regex]::Escape($expectedMesh)+'\b')){
            throw 'Native result did not verify the exact expected collision mesh.'
        }
        [void](Require-IntegerField $passedLine 'expected_instance' $expectedInstance $expectedInstance)
    }
    $expectedPhases=if($ProbeCase -eq 'Protected'){
        @('before','protected_after')
    } else {@('before','post_010','post_030','post_060','post_120','post_250','post_450','post_830')}
    $record.expected_capture_phases=$expectedPhases
    foreach($phase in $expectedPhases){
        $overview=Join-Path $captureDirectory ($phase+'/overview.png')
        if(-not (Test-Path -LiteralPath $overview -PathType Leaf)){
            throw ('Missing native overview capture phase: '+$phase)
        }
        $phasePattern='\bWT_DESTRUCTION_CAPTURE\b.*\bphase='+[regex]::Escape($phase)+'\b'
        if(@($record.log_events | Where-Object {$_ -match $phasePattern}).Count -ne 1){
            throw ('Missing/duplicated native capture event: '+$phase)
        }
    }
    Add-Type -AssemblyName System.Drawing
    foreach($file in @(Get-ChildItem -LiteralPath $captureDirectory -Recurse -File -Filter '*.png')){
        $picture=$null
        try {
            $picture=[Drawing.Image]::FromFile($file.FullName)
            if($picture.RawFormat.Guid -ne [Drawing.Imaging.ImageFormat]::Png.Guid -or
               $picture.Width -le 0 -or $picture.Height -le 0){throw 'Invalid native PNG capture.'}
            $record.captures+=[ordered]@{
                file=$file.FullName.Substring($captureDirectory.Length+1).Replace('\','/')
                phase=$file.Directory.Name
                camera=$file.BaseName
                sha256=(Hash-File $file.FullName)
                width=$picture.Width
                height=$picture.Height
            }
        } finally {if($null -ne $picture){$picture.Dispose()}}
    }
    if($record.captures.Count -lt 1){throw 'Native probe produced no PNG captures.'}
    Write-Output ('WT_DESTRUCTION_TEST_STAGE validation_passed case='+$ProbeCase+' captures='+$record.captures.Count)
    $record.status='passed'
    $runExitCode=0
} catch {
    $message=$_.Exception.Message
    if($record){$record.status='failed';$record.error=$message}
    [Console]::Error.WriteLine($message)
} finally {
    if($record){
        if($process){
            try {
                $process.Refresh()
                if($process.HasExited){$record.exit_code=$process.ExitCode}
            } catch {$record.process_status_error=$_.Exception.Message}
        }
        if(Test-Path -LiteralPath $engineLog -PathType Leaf){
            $record.engine_log_sha256=Hash-File $engineLog
            $record.log_events=@([IO.File]::ReadAllLines($engineLog,[Text.Encoding]::UTF8) |
                Where-Object {$_ -match 'WT_DESTRUCTION_|WT_DEBRIS_|WT_FOLIAGE_'})
        }
        $after=[ordered]@{}
        try {
            foreach($key in $inputs.Keys){$after[$key]=Hash-File $inputs[$key]}
            $record.hashes_after=$after
            $changed=@($inputs.Keys | Where-Object {$before[$_] -cne $after[$_]})
            $record.changed_inputs=$changed
            $record.source_module_profile_world_unchanged=($changed.Count -eq 0)
            if($changed.Count){
                $record.status='failed'
                $record.error='Probe inputs changed during execution: '+($changed -join ', ')
                $runExitCode=3
            }
        } catch {
            $record.status='failed'
            $record.hash_error=$_.Exception.Message
            $runExitCode=3
        }
        $record.finished_utc=[DateTime]::UtcNow.ToString('o')
        $record.duration_seconds=([DateTime]::UtcNow-$begin).TotalSeconds
        Write-Output ('WT_DESTRUCTION_TEST_STAGE finalizing_report case='+$ProbeCase+' status='+$record.status)
        Save-Record $record $verification
        [pscustomobject]$record | Select-Object case,status,pid,exit_code,duration_seconds,error |
            ConvertTo-Json -Compress | Write-Output
    }
    if($process){$process.Dispose()}
    if($locked){$mutex.ReleaseMutex()}
    if($mutex){$mutex.Dispose()}
}
exit $runExitCode
