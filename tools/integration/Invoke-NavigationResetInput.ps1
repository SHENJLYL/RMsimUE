param([Parameter(Mandatory=$true)][int]$ServerProcessId,[Parameter(Mandatory=$true)][string]$EvidenceDirectory)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$evidence=[IO.Path]::GetFullPath($EvidenceDirectory)
$ready=Join-Path $evidence 'ros/ready-for-reset.json'
if(-not(Test-Path -LiteralPath $ready)){throw 'The owned navigation fixture is not ready for reset.'}
$process=Get-Process -Id $ServerProcessId
$details=Get-CimInstance Win32_Process -Filter ('ProcessId='+$ServerProcessId)
$expected=(Join-Path $evidence 'runtime.log').Replace('\','/').ToLowerInvariant()
$command=([string]$details.CommandLine).Replace('\','/').ToLowerInvariant()
if($details.Name -ne 'UnrealEditor.exe' -or -not $command.Contains('/rmsim/rmsim.uproject') -or -not $command.Contains($expected) -or -not $command.Contains('-game')){
 throw 'Reset input is restricted to this exact owned RMSim test process.'
}
$window=$process.MainWindowHandle
if($window -eq [IntPtr]::Zero){throw 'No interactive RMSim window.'}
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class RMSimResetInput {
 [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left,Top,Right,Bottom; }
 [StructLayout(LayoutKind.Sequential)] public struct Keyboard { public ushort VirtualKey,Scan; public uint Flags,Time; public UIntPtr Extra; }
 [StructLayout(LayoutKind.Sequential)] public struct Mouse { public int X,Y; public uint Data,Flags,Time; public UIntPtr Extra; }
 [StructLayout(LayoutKind.Explicit)] public struct Union { [FieldOffset(0)] public Keyboard Key; [FieldOffset(0)] public Mouse Mouse; }
 [StructLayout(LayoutKind.Sequential)] public struct Input { public uint Type; public Union Data; }
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr window,int action);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr window,out Rect rect);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window,out uint process);
 [DllImport("user32.dll",SetLastError=true)] public static extern uint SendInput(uint count,Input[] inputs,int size);
 public static uint R(bool release) { var input=new Input(); input.Type=1;input.Data.Key.VirtualKey=0x52;input.Data.Key.Flags=release?2u:0u;return SendInput(1,new[]{input},Marshal.SizeOf(typeof(Input))); }
}
'@
[void][RMSimResetInput]::SetProcessDPIAware()
[uint32]$owner=0;[void][RMSimResetInput]::GetWindowThreadProcessId($window,[ref]$owner)
if($owner -ne $ServerProcessId){throw 'Window ownership changed.'}
[void][RMSimResetInput]::ShowWindow($window,9)
[void][RMSimResetInput]::SetForegroundWindow($window)
Start-Sleep -Milliseconds 250
if([RMSimResetInput]::GetForegroundWindow() -ne $window){throw 'RMSim did not gain foreground; no input was sent.'}
$out=Join-Path $evidence 'gui-reset'
if(Test-Path -LiteralPath $out){throw 'Reset evidence already exists; refusing a second key injection.'}
New-Item -ItemType Directory -Path $out | Out-Null
Add-Type -AssemblyName System.Drawing
$rect=New-Object RMSimResetInput+Rect
if(-not [RMSimResetInput]::GetWindowRect($window,[ref]$rect)){throw 'Cannot read owned window bounds.'}
$image=[Drawing.Bitmap]::new($rect.Right-$rect.Left,$rect.Bottom-$rect.Top)
$graphics=[Drawing.Graphics]::FromImage($image)
try{$graphics.CopyFromScreen($rect.Left,$rect.Top,0,0,$image.Size);$image.Save((Join-Path $out 'before-reset.png'),[Drawing.Imaging.ImageFormat]::Png)}finally{$graphics.Dispose();$image.Dispose()}
$down=0;$up=0
try{
 if([RMSimResetInput]::GetForegroundWindow() -ne $window){throw 'Foreground changed; no input was sent.'}
 $down=[RMSimResetInput]::R($false)
 if($down -ne 1){throw 'R keydown was not accepted.'}
 Start-Sleep -Milliseconds 120
}finally{if($down -eq 1){$up=[RMSimResetInput]::R($true)}}
@{status=if($down -eq 1 -and $up -eq 1){'input_sent'}else{'failed'};pid=$ServerProcessId;window_handle=$window.ToInt64();key='R';keydown_count=$down;keyup_count=$up;scope='OS input to verified owned foreground test window; epoch/navigation results remain independently checked by ROS fixture'} |
 ConvertTo-Json | Set-Content -LiteralPath (Join-Path $out 'verification.json') -Encoding utf8
if($up -ne 1){throw 'R key release failed.'}
Write-Output 'One R keypress delivered to the verified owned RMSim test window.'
