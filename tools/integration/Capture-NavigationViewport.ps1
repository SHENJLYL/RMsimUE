param(
 [Parameter(Mandatory=$true)][string]$SessionDirectory,
 [string]$OutputDirectory='',
 [ValidateRange(2,12)][int]$Frames=4,
 [ValidateRange(250,5000)][int]$IntervalMilliseconds=1000,
 [switch]$ActivateWindow
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$session=(Resolve-Path -LiteralPath $SessionDirectory).Path
$session=[IO.Path]::GetFullPath($session).TrimEnd([IO.Path]::DirectorySeparatorChar)
$statePath=Join-Path $session 'status.json'
$provenancePath=Join-Path $session 'runtime-provenance.json'
$out=if($OutputDirectory){[IO.Path]::GetFullPath($OutputDirectory)}else{Join-Path $session 'viewport-capture'}
if(-not $out.StartsWith($session+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){
 throw 'Capture evidence must be in a new child directory of the specified session.'
}
if(Test-Path -LiteralPath $out){throw 'Capture directory already exists; existing evidence is preserved.'}
New-Item -ItemType Directory -Path $out | Out-Null
$utf8=[Text.UTF8Encoding]::new($false)
$report=[ordered]@{
 status='collecting';scope='Desktop pixels of the verified owned UE client area plus live console counters'
 visual_review='not_performed';visual_pass=$null
 session_directory=$session;output_directory=$out
 script_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
 requested_frames=$Frames;interval_milliseconds=$IntervalMilliseconds
 activation_requested=[bool]$ActivateWindow;input_sent=$false;samples=@()
}
$exitCode=0
function Bytes-Sha256([byte[]]$bytes) {
 $hasher=[Security.Cryptography.SHA256]::Create()
 try{return ([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()}
 finally{$hasher.Dispose()}
}
function Read-ConsoleState {
 $bytes=[IO.File]::ReadAllBytes($statePath)
 $doc=([Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xfeff)) | ConvertFrom-Json
 $age=([DateTime]::UtcNow-(Get-Item -LiteralPath $statePath).LastWriteTimeUtc).TotalSeconds
 if($age -lt -2 -or $age -gt 2){throw 'The console snapshot is stale; no pixels captured.'}
 if($doc.phase -ne 'ready' -or -not $doc.bridge.connected -or -not $doc.bridge.state_fresh){
  throw 'The selected navigation console must be ready with a fresh bridge state.'
 }
 if([IO.Path]::GetFullPath([string]$doc.output_directory).TrimEnd([IO.Path]::DirectorySeparatorChar) -ine $session){
  throw 'Console output-directory identity does not match the selected session.'
 }
 if([int]$doc.pids.ue -le 0 -or -not $doc.bridge.session -or -not $doc.bridge.rendering.renderer_enabled){
  throw 'A live owned UE process and enabled renderer are required.'
 }
 return [pscustomobject]@{State=$doc;Bytes=$bytes;Sha256=(Bytes-Sha256 $bytes);FileAgeSeconds=$age}
}
function State-Summary($snapshot) {
 $doc=$snapshot.State
 return [ordered]@{
  status_json_sha256=$snapshot.Sha256;snapshot_file_age_seconds=$snapshot.FileAgeSeconds
  snapshot_sequence=$doc.snapshot_sequence;snapshot_written_monotonic_s=$doc.snapshot_written_monotonic_s
  updated_utc=$doc.updated_utc;phase=$doc.phase;ue_pid=[int]$doc.pids.ue
  sensor_session=$doc.bridge.session;epoch=$doc.bridge.epoch;sim_time_ns=$doc.bridge.sim_time_ns
  clock_ns=$doc.clock_ns;clock_source=$doc.bridge.clock_source;fixed_step_s=$doc.bridge.fixed_step_s
  simulation_rate=$doc.bridge.simulation_rate;rendering=$doc.bridge.rendering
  estimated_pose=$doc.estimated_pose;navigation_identity=$doc.navigation.identity
 }
}
try {
 if(-not(Test-Path -LiteralPath $provenancePath)){throw 'Runtime provenance is required.'}
 $report.runtime_provenance_sha256=(Get-FileHash -LiteralPath $provenancePath -Algorithm SHA256).Hash.ToLowerInvariant()
 Add-Type -AssemblyName System.Drawing
 if(-not ('RMSimViewportCaptureNative' -as [type])){
  Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class RMSimViewportCaptureNative {
 [StructLayout(LayoutKind.Sequential)] public struct Point { public int X,Y; public Point(int x,int y){X=x;Y=y;} }
 [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left,Top,Right,Bottom; }
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window,out uint process);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr window);
 [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr window,out Rect rect);
 [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr window,ref Point point);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr window,out Rect rect);
 [DllImport("user32.dll")] public static extern IntPtr GetTopWindow(IntPtr window);
 [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr window,uint command);
 [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr window,uint flags);
 [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(Point point);
 [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
 [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr window,int attribute,out uint value,int size);
 [DllImport("shell32.dll",SetLastError=true)] private static extern IntPtr CommandLineToArgvW([MarshalAs(UnmanagedType.LPWStr)] string command,out int count);
 [DllImport("kernel32.dll")] private static extern IntPtr LocalFree(IntPtr pointer);
 public static string[] Arguments(string command) {
  int count; IntPtr pointer=CommandLineToArgvW(command,out count);
  if(pointer==IntPtr.Zero)throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
  try {var values=new string[count];for(int i=0;i<count;i++)values[i]=Marshal.PtrToStringUni(Marshal.ReadIntPtr(pointer,i*IntPtr.Size));return values;}
  finally{LocalFree(pointer);}
 }
}
'@
 }
 [void][RMSimViewportCaptureNative]::SetProcessDPIAware()
 $initial=Read-ConsoleState
 $uePid=[int]$initial.State.pids.ue
 $sensorSession=[string]$initial.State.bridge.session
 $epoch=[long]$initial.State.bridge.epoch
 $expectedProject=[IO.Path]::GetFullPath((Join-Path $root 'unreal/RMSim/RMSim.uproject'))
 $expectedLog=[IO.Path]::GetFullPath((Join-Path $session 'runtime.log'))
 function Get-OwnedProcess {
  $details=Get-CimInstance Win32_Process -Filter ('ProcessId='+$uePid)
  if(-not $details -or $details.Name -ine 'UnrealEditor.exe'){throw 'Owned UE process is no longer present.'}
  $arguments=[RMSimViewportCaptureNative]::Arguments([string]$details.CommandLine)
  $projectMatches=@($arguments | Where-Object {$_ -ieq $expectedProject -or $_.Replace('\','/') -ieq $expectedProject.Replace('\','/')})
  $logs=@($arguments | Where-Object {$_.StartsWith('-abslog=',[StringComparison]::OrdinalIgnoreCase)})
  if($projectMatches.Count -ne 1 -or $logs.Count -ne 1 -or $arguments -inotcontains '-game'){
   throw 'Process arguments do not identify this exact RMSim game instance.'
  }
  if([IO.Path]::GetFullPath($logs[0].Substring(8)) -ine $expectedLog){
   throw 'UE runtime log does not belong to the selected session.'
  }
  $process=Get-Process -Id $uePid;$process.Refresh()
  $handle=$process.MainWindowHandle
  if($handle -eq [IntPtr]::Zero -or -not [RMSimViewportCaptureNative]::IsWindowVisible($handle)){
   throw 'The owned UE process has no visible interactive window.'
  }
  [uint32]$owner=0
  [void][RMSimViewportCaptureNative]::GetWindowThreadProcessId($handle,[ref]$owner)
  if($owner -ne $uePid){throw 'UE window ownership mismatch.'}
  return [pscustomobject]@{Process=$process;Details=$details;Handle=$handle;StartedUtc=$process.StartTime.ToUniversalTime()}
 }
 $owned=Get-OwnedProcess
 $window=$owned.Handle;$startedUtc=$owned.StartedUtc
 $report.ue_identity=[ordered]@{
  pid=$uePid;window_handle=$window.ToInt64();started_utc=$startedUtc.ToString('o')
  executable_path=$owned.Details.ExecutablePath;command_line=$owned.Details.CommandLine
  project=$expectedProject;runtime_log=$expectedLog;sensor_session=$sensorSession;epoch=$epoch
 }
 if($ActivateWindow){
  $activation=New-Object -ComObject WScript.Shell
  try{$report.activation_returned=[bool]$activation.AppActivate($uePid)}
  finally{[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($activation)}
  Start-Sleep -Milliseconds 250
 }
 function Assert-OwnedForeground($snapshot) {
  if([int]$snapshot.State.pids.ue -ne $uePid -or [string]$snapshot.State.bridge.session -cne $sensorSession -or [long]$snapshot.State.bridge.epoch -ne $epoch){
   throw 'The console process/sensor session changed during capture.'
  }
  $current=Get-OwnedProcess
  if($current.Handle -ne $window -or $current.StartedUtc -ne $startedUtc){throw 'UE process or window identity changed.'}
  if([RMSimViewportCaptureNative]::GetForegroundWindow() -ne $window -or [RMSimViewportCaptureNative]::IsIconic($window)){
   throw 'The exact owned UE window is not foreground; no pixels captured.'
  }
 }
 function Get-CaptureRectangle {
  $rect=[RMSimViewportCaptureNative+Rect]::new()
  if(-not [RMSimViewportCaptureNative]::GetClientRect($window,[ref]$rect)){throw 'Cannot read UE client rectangle.'}
  $origin=[RMSimViewportCaptureNative+Point]::new(0,0)
  if(-not [RMSimViewportCaptureNative]::ClientToScreen($window,[ref]$origin)){throw 'Cannot locate UE client on desktop.'}
  $width=$rect.Right-$rect.Left;$height=$rect.Bottom-$rect.Top
  $left=[RMSimViewportCaptureNative]::GetSystemMetrics(76);$top=[RMSimViewportCaptureNative]::GetSystemMetrics(77)
  $right=$left+[RMSimViewportCaptureNative]::GetSystemMetrics(78);$bottom=$top+[RMSimViewportCaptureNative]::GetSystemMetrics(79)
  if($width -lt 100 -or $height -lt 100 -or $width -gt 8192 -or $height -gt 8192 -or $origin.X -lt $left -or $origin.Y -lt $top -or $origin.X+$width -gt $right -or $origin.Y+$height -gt $bottom){
   throw 'UE client area is invalid or partially outside the desktop.'
  }
  return [pscustomobject]@{Left=$origin.X;Top=$origin.Y;Width=$width;Height=$height}
 }
 function Assert-Uncovered($rect) {
  $handle=[RMSimViewportCaptureNative]::GetTopWindow([IntPtr]::Zero);$visited=0
  while($handle -ne [IntPtr]::Zero -and $handle -ne $window -and $visited -lt 1000){
   $visited++
   [uint32]$cloaked=0
   $cloakedResult=[RMSimViewportCaptureNative]::DwmGetWindowAttribute($handle,14,[ref]$cloaked,4)
   if([RMSimViewportCaptureNative]::IsWindowVisible($handle) -and -not [RMSimViewportCaptureNative]::IsIconic($handle) -and ($cloakedResult -ne 0 -or $cloaked -eq 0)){
    $other=[RMSimViewportCaptureNative+Rect]::new()
    if([RMSimViewportCaptureNative]::GetWindowRect($handle,[ref]$other) -and $other.Right -gt $rect.Left -and $other.Left -lt $rect.Left+$rect.Width -and $other.Bottom -gt $rect.Top -and $other.Top -lt $rect.Top+$rect.Height){
     [uint32]$coverPid=0;[void][RMSimViewportCaptureNative]::GetWindowThreadProcessId($handle,[ref]$coverPid)
     throw ('Another visible window covers the UE client area (PID '+$coverPid+'); no pixels captured.')
    }
   }
   $handle=[RMSimViewportCaptureNative]::GetWindow($handle,2)
  }
  if($handle -ne $window){throw 'Owned UE window disappeared from desktop z-order.'}
  foreach($fractionX in @(0.02,0.5,0.98)){
   foreach($fractionY in @(0.02,0.5,0.98)){
    $point=[RMSimViewportCaptureNative+Point]::new([int]($rect.Left+$rect.Width*$fractionX),[int]($rect.Top+$rect.Height*$fractionY))
    $hit=[RMSimViewportCaptureNative]::WindowFromPoint($point);[uint32]$hitPid=0
    [void][RMSimViewportCaptureNative]::GetWindowThreadProcessId($hit,[ref]$hitPid)
    if($hitPid -ne $uePid -or [RMSimViewportCaptureNative]::GetAncestor($hit,2) -ne $window){
     throw 'A sampled client-area pixel belongs to another window; no pixels captured.'
    }
   }
  }
 }
 for($index=0;$index -lt $Frames;$index++){
  if($index -gt 0){Start-Sleep -Milliseconds $IntervalMilliseconds}
  $before=Read-ConsoleState;Assert-OwnedForeground $before
  $rect=Get-CaptureRectangle;Assert-Uncovered $rect
  $name='viewport-'+($index+1).ToString('00')
  $image=[Drawing.Bitmap]::new($rect.Width,$rect.Height)
  $graphics=[Drawing.Graphics]::FromImage($image)
  try{
   $utc=[DateTime]::UtcNow.ToString('o')
   $captureStart=[Diagnostics.Stopwatch]::GetTimestamp()/[double][Diagnostics.Stopwatch]::Frequency
   $graphics.CopyFromScreen($rect.Left,$rect.Top,0,0,$image.Size,[Drawing.CopyPixelOperation]::SourceCopy)
   $captureEnd=[Diagnostics.Stopwatch]::GetTimestamp()/[double][Diagnostics.Stopwatch]::Frequency
   $after=Read-ConsoleState;Assert-OwnedForeground $after;Assert-Uncovered $rect
   $afterRect=Get-CaptureRectangle
   if($afterRect.Left -ne $rect.Left -or $afterRect.Top -ne $rect.Top -or $afterRect.Width -ne $rect.Width -or $afterRect.Height -ne $rect.Height){
    throw 'UE window moved or resized during capture; captured pixels were discarded.'
   }
   $png=Join-Path $out ($name+'.png')
   $image.Save($png,[Drawing.Imaging.ImageFormat]::Png)
  }finally{$graphics.Dispose();$image.Dispose()}
  [IO.File]::WriteAllBytes((Join-Path $out ($name+'-before-status.json')),$before.Bytes)
  [IO.File]::WriteAllBytes((Join-Path $out ($name+'-after-status.json')),$after.Bytes)
  $report.samples+=@([ordered]@{
   index=$index+1;captured_utc=$utc;png_file=$name+'.png'
   captured_interval=@{start=$captureStart;end=$captureEnd};monotonic_clock='Windows QPC seconds'
   png_sha256=(Get-FileHash -LiteralPath $png -Algorithm SHA256).Hash.ToLowerInvariant()
   client_rectangle=$rect;foreground_and_pixel_ownership_verified=$true
   before=(State-Summary $before);after=(State-Summary $after)
  })
 }
 $first=$report.samples[0].before;$last=$report.samples[-1].after
 $wallSpan=[double]$last.snapshot_written_monotonic_s-[double]$first.snapshot_written_monotonic_s
 $simSpan=([double]$last.sim_time_ns-[double]$first.sim_time_ns)/1.e9
 $drawDelta=[long]$last.rendering.scene_rendered_frames-[long]$first.rendering.scene_rendered_frames
 $report.counter_observations=[ordered]@{
  producer_wall_span_s=$wallSpan;simulation_span_s=$simSpan;scene_draw_count_delta=$drawDelta
  scene_counter_advanced=($drawDelta -gt 0);simulation_clock_advanced=($simSpan -gt 0)
  distinct_png_hashes=@($report.samples.png_sha256 | Select-Object -Unique).Count
  scope='Short capture-window observations; visual content and display scanout require actual image review.'
 }
 $report.status='collected_requires_visual_review'
}catch{
 $report.status='capture_failed';$report.error=$_.Exception.Message;$exitCode=2
}finally{
 $report.finished_utc=[DateTime]::UtcNow.ToString('o')
 [IO.File]::WriteAllText((Join-Path $out 'verification.json'),($report | ConvertTo-Json -Depth 16)+[Environment]::NewLine,$utf8)
}
[ordered]@{status=$report.status;visual_review=$report.visual_review;frames=@($report.samples).Count;verification=(Join-Path $out 'verification.json')} | ConvertTo-Json -Compress | Write-Output
exit $exitCode
