param(
    [Parameter(Mandatory=$true)][string]$EngineRoot,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [string]$InputFile='',
    [double]$LoadN=100,
    [double]$StepSeconds=.005,
    [int]$TimeoutSeconds=180
)
. (Join-Path $PSScriptRoot 'Common.ps1')
try {
    $ue=Get-UnrealTools -EngineRoot $EngineRoot
    $root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
    if(-not $InputFile){$InputFile=Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'}
    $inputPath=(Resolve-Path -LiteralPath $InputFile).Path
    $mapPath=Join-Path $root 'assets/maps/import_lab/map.json'
    $evidence=[IO.Path]::GetFullPath($EvidenceDirectory)
    if(Test-Path -LiteralPath $evidence){throw 'Use a fresh evidence directory.'}
    if($LoadN -le 0 -or $LoadN -gt 150 -or $StepSeconds -lt .0005 -or $StepSeconds -gt .02 -or $TimeoutSeconds -lt 1){throw 'Invalid fixture arguments.'}
    $live=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'UnrealEditor.exe' -and $_.CommandLine -like '*RMSim.uproject*'})
    if($live.Count){throw 'Close the existing RMSim instance before measuring suspension.'}
    New-Item -ItemType Directory -Path $evidence | Out-Null
    $rig=Get-Content -LiteralPath $inputPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $profile=Join-Path (Split-Path -Parent $inputPath) $rig.drive_profile_file
    $module=Join-Path $root 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll'
    $hashes=@{}
    foreach($pair in @(@('module',$module),@('rig',$inputPath),@('profile',$profile),@('map',$mapPath))){$hashes[$pair[0]]=(Get-FileHash -LiteralPath $pair[1] -Algorithm SHA256).Hash}
    @{started_utc=[DateTime]::UtcNow.ToString('o');hashes=$hashes;load_n=$LoadN;fixed_step_s=$StepSeconds} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $evidence 'runtime-provenance.json') -Encoding utf8
    $invariant=[Globalization.CultureInfo]::InvariantCulture
    $arguments=@((Quote-UnrealArgument $ue.Project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
        '-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-ContactProbe','-SuspensionProbe',
        ('-MapFile='+(Quote-UnrealArgument $mapPath)),('-MeshRigFile='+(Quote-UnrealArgument $inputPath)),
        ('-EvidenceDir='+(Quote-UnrealArgument $evidence)),('-abslog='+(Quote-UnrealArgument (Join-Path $evidence 'runtime.log'))),
        ('-SuspensionLoadN='+$LoadN.ToString($invariant)),('-SimStepSeconds='+$StepSeconds.ToString($invariant)))
    $process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru
    [void]$process.Handle
    Write-Output "Suspension probe PID $($process.Id)"
    if(-not $process.WaitForExit($TimeoutSeconds*1000)){Stop-Process -Id $process.Id -Force;throw 'Suspension probe timed out; evidence retained.'}
    $process.WaitForExit();$process.Refresh()
    if($null -eq $process.ExitCode -or $process.ExitCode -ne 0){throw "Suspension process failed: $($process.ExitCode)"}
    foreach($pair in @(@('module',$module),@('rig',$inputPath),@('profile',$profile),@('map',$mapPath))){if((Get-FileHash -LiteralPath $pair[1] -Algorithm SHA256).Hash -ne $hashes[$pair[0]]){throw 'Test input changed during execution.'}}
    $report=Get-Content -LiteralPath (Join-Path $evidence 'control-result.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $expected=@('suspension_baseline_settled','suspension_loaded_equilibrium','suspension_release_recovered','suspension_impulse_recovered')
    if($report.status -ne 'passed' -or -not $report.complete -or @($report.checks).Count -ne 4){throw 'Incomplete suspension report.'}
    foreach($name in $expected){$matches=@($report.checks | Where-Object name -eq $name);if($matches.Count -ne 1 -or -not $matches[0].passed){throw "Missing check: $name"}}
    Add-Type -AssemblyName System.Drawing
    foreach($phase in @('suspension-rest','suspension-loaded','suspension-recovered')){foreach($camera in @('overview','aim_camera')){
        $image=[Drawing.Image]::FromFile((Join-Path $evidence "$phase/$camera.png"))
        try{if($image.Width -lt 1 -or $image.RawFormat.Guid -ne [Drawing.Imaging.ImageFormat]::Png.Guid){throw 'Invalid evidence PNG.'}}finally{$image.Dispose()}
    }}
    & python (Join-Path $root 'tools/integration/analyze_suspension.py') $evidence
    if($LASTEXITCODE -ne 0){throw 'Suspension trace analysis failed.'}
    Write-Output 'Suspension probe PASS: 4 native checks, 6 PNGs, analyzed time series.'
} catch {[Console]::Error.WriteLine($_.Exception.Message);exit 3}
