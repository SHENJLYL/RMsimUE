param([Parameter(Mandatory=$true)][string]$SessionDirectory,[ValidateSet('Goal','Button','Capture','ResetView')][string]$Action='Capture',[string]$ButtonName='Cancel',[double]$X=0,[double]$Y=0,[string]$CaptureName='rviz')
$ErrorActionPreference='Stop'
$session=[IO.Path]::GetFullPath($SessionDirectory)
$state=Get-Content -LiteralPath (Join-Path $session 'status.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$targetPid=[int]$state.pids.rviz
if($targetPid -le 0){throw 'This console has no live RViz process'}
$item=Get-CimInstance Win32_Process -Filter "ProcessId=$targetPid"
if(-not $item -or $item.Name -ne 'rviz2.exe' -or -not $item.CommandLine.Contains($session)){throw 'Target does not match this owned RViz session.'}
$p=Get-Process -Id $targetPid
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,System.Drawing
Add-Type @'
using System; using System.Runtime.InteropServices;
public class RvizUiNative {
 [StructLayout(LayoutKind.Sequential)] public struct Point {public int X,Y;public Point(int x,int y){X=x;Y=y;}}
 [StructLayout(LayoutKind.Sequential)] public struct Rect {public int Left,Top,Right,Bottom;}
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(Point point);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint flags,uint dx,uint dy,uint data,UIntPtr extra);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out Rect r);
 [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr h,ref Point p);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint message,UIntPtr flags,IntPtr point);
 public static bool Mouse(IntPtr h,uint message,int x,int y,bool held){return PostMessage(h,message,(UIntPtr)(held?1u:0u),(IntPtr)((y<<16)|(x&65535)));}
}
'@
[void][RvizUiNative]::SetProcessDPIAware()
$root=[System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
function NamedElement([string]$name) {
 $condition=New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::NameProperty),$name
 return $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$condition)
}
$inputRoute='read_only_capture'
if($Action -eq 'ResetView'){
 $button=NamedElement 'Zero'
 if(-not $button -or -not $button.Current.IsEnabled){throw 'RViz view-reset control unavailable'}
 $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
 $inputRoute='UIAutomation_reset_camera_view_only'
} elseif($Action -eq 'Button'){
 if($ButtonName -notin @('Cancel','Waypoint / Nav Through Poses Mode','Start Waypoint Following','Start Nav Through Poses','Cancel Accumulation')){throw 'Unsupported acceptance button'}
 $button=NamedElement $ButtonName
 if(-not $button -or -not $button.Current.IsEnabled){throw ('RViz button unavailable: '+$ButtonName)}
 $pattern=$button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
 $pattern.Invoke();$inputRoute='UIAutomation_InvokePattern_to_verified_owned_window'
} elseif($Action -eq 'Goal') {
 # Save the live camera view into THIS disposable session config. The user can
 # pan/zoom/rotate after launch; never compute clicks from stale startup values.
 $wshell=New-Object -ComObject WScript.Shell
 [void]$wshell.AppActivate($targetPid);Start-Sleep -Milliseconds 300
 if([RvizUiNative]::GetForegroundWindow() -ne $p.MainWindowHandle){throw 'RViz is not foreground; no keys or mouse input sent.'}
 $wshell.SendKeys('^s');Start-Sleep -Milliseconds 300
 if([RvizUiNative]::GetForegroundWindow() -ne $p.MainWindowHandle){throw 'RViz focus changed while saving the live view; no mouse input sent.'}
 $text=Get-Content -LiteralPath (Join-Path $session 'navigation.rviz') -Raw -Encoding UTF8
 try{
  $doc=$text | ConvertFrom-Json
  $view=$doc.'Visualization Manager'.Views.Current
  if($view.Class -ne 'rviz_default_plugins/TopDownOrtho'){throw 'Wrong view'}
  $scale=[double]$view.Scale;$vx=[double]$view.X;$vy=[double]$view.Y;$angle=[double]$view.Angle
 }catch{
  $view=[regex]::Match($text,'(?s)Views:\s+Current:(.*?)\n\s+Saved:').Groups[1].Value
  if(-not $view.Contains('TopDownOrtho')){throw 'Acceptance coordinates require the saved TopDownOrtho view'}
  $culture=[Globalization.CultureInfo]::InvariantCulture
  $scale=[double]::Parse([regex]::Match($view,'(?m)^\s+Scale: ([^\r\n]+)').Groups[1].Value,$culture)
  $vx=[double]::Parse([regex]::Match($view,'(?m)^\s+X: ([^\r\n]+)').Groups[1].Value,$culture)
  $vy=[double]::Parse([regex]::Match($view,'(?m)^\s+Y: ([^\r\n]+)').Groups[1].Value,$culture)
  $angle=[double]::Parse([regex]::Match($view,'(?m)^\s+Angle: ([^\r\n]+)').Groups[1].Value,$culture)
 }
 if([double]::IsNaN($angle) -or [double]::IsInfinity($angle) -or [double]::IsNaN($scale) -or [double]::IsInfinity($scale) -or $scale -le 0){throw 'Acceptance clicks require a finite live orthographic view'}
 $pane=NamedElement 'rviz2';if(-not $pane){throw 'Cannot find the RViz render viewport'}
 $viewport=[IntPtr]$pane.Current.NativeWindowHandle
 [uint32]$owner=0;[void][RvizUiNative]::GetWindowThreadProcessId($viewport,[ref]$owner)
 if($viewport -eq [IntPtr]::Zero -or $owner -ne $targetPid){throw 'Viewport belongs to a different process; no messages sent'}
 $rect=$pane.Current.BoundingRectangle
 # Inverse camera Z rotation, matching RViz Humble FixedOrientationOrthoViewController.
 $dx=$X-$vx;$dy=$Y-$vy;$cos=[Math]::Cos($angle);$sin=[Math]::Sin($angle)
 $px=$rect.X+$rect.Width/2+($dx*$cos+$dy*$sin)*$scale
 $py=$rect.Y+$rect.Height/2-(-$dx*$sin+$dy*$cos)*$scale
 if($px -lt $rect.X+10 -or $px -gt $rect.Right-35 -or $py -lt $rect.Y+10 -or $py -gt $rect.Bottom-10){throw 'Point is outside the visible map viewport'}
 $tool=NamedElement 'Nav2 Goal';if(-not $tool){throw 'Nav2 Goal tool missing'}
 # Invoke the real QAction. TogglePattern changes only the checkmark and can
 # leave Move Camera active. InvokePattern dispatches Qt's press action.
 if([RvizUiNative]::GetForegroundWindow() -ne $p.MainWindowHandle){throw 'RViz focus changed; no tool activation sent.'}
 $tool.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
 Start-Sleep -Milliseconds 200
 $move=NamedElement 'Move Camera'
 if($move -and $move.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Current.ToggleState -eq [System.Windows.Automation.ToggleState]::On){throw 'Move Camera is still active; no map click sent.'}
 $point=[RvizUiNative+Point]::new([Math]::Round($px),[Math]::Round($py));[void][RvizUiNative]::ScreenToClient($viewport,[ref]$point)
 # Normal activation only, then verify both foreground and the exact pixel owner.
 # No input is sent if an unrelated window covers the requested viewport.
 $wshell=New-Object -ComObject WScript.Shell
 [void]$wshell.AppActivate($targetPid);Start-Sleep -Milliseconds 300
 if([RvizUiNative]::GetForegroundWindow() -ne $p.MainWindowHandle){throw 'RViz is not the foreground window; no mouse input sent.'}
 $screenPoint=[RvizUiNative+Point]::new([Math]::Round($px),[Math]::Round($py))
 $hit=[RvizUiNative]::WindowFromPoint($screenPoint);[uint32]$hitOwner=0
 [void][RvizUiNative]::GetWindowThreadProcessId($hit,[ref]$hitOwner)
 if($hitOwner -ne $targetPid){throw 'Target point is covered by another application; no mouse input sent.'}
 [void][RvizUiNative]::SetCursorPos($screenPoint.X,$screenPoint.Y)
 [RvizUiNative]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
 try {
  Start-Sleep -Milliseconds 120
  [void][RvizUiNative]::SetCursorPos($screenPoint.X+25,$screenPoint.Y)
  Start-Sleep -Milliseconds 120
 } finally {[RvizUiNative]::mouse_event(4,0,0,0,[UIntPtr]::Zero)}
 $inputRoute='actual_OS_mouse_after_foreground_and_pixel_owner_validation'
 # Retain the former targeted-message experiment only as historical evidence.

}
Start-Sleep -Milliseconds 250
$r=New-Object RvizUiNative+Rect;[void][RvizUiNative]::GetWindowRect($p.MainWindowHandle,[ref]$r)
$image=New-Object Drawing.Bitmap ($r.Right-$r.Left),($r.Bottom-$r.Top);$graphics=[Drawing.Graphics]::FromImage($image)
try{$graphics.CopyFromScreen($r.Left,$r.Top,0,0,$image.Size);$image.Save((Join-Path $session ($CaptureName+'.png')))}finally{$graphics.Dispose();$image.Dispose()}
@{utc=[DateTime]::UtcNow.ToString('o');pid=$targetPid;action=$Action;button=if($Action -eq 'Button'){$ButtonName}else{$null};x=$X;y=$Y;input_route=$inputRoute;foreground=([RvizUiNative]::GetForegroundWindow() -eq $p.MainWindowHandle)} | ConvertTo-Json -Compress | Add-Content -LiteralPath (Join-Path $session 'ui-events.jsonl') -Encoding UTF8
Write-Output ('RViz UI action completed: '+$Action+'; button='+$ButtonName+'; goal='+$X+','+$Y)
