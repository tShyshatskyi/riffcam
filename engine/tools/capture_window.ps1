# Saves a PNG of one process's main window only (not the whole screen): capture_window.ps1 <processName> <out.png>
param([string]$name, [string]$out, [string]$title = "", [long]$hwnd = 0)  # -title / -hwnd: another window of that process
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class Cap {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr v);
  public struct RECT { public int L, T, R, B; }
}
"@
[void][Cap]::SetProcessDpiAwarenessContext([IntPtr]-4)  # per-monitor v2: real pixel sizes
$p = Get-Process $name | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { "no window for $name"; exit 1 }
$h = if ($hwnd) { [IntPtr]$hwnd } else { $p.MainWindowHandle }
if ($title) {
    Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
    $c = New-Object Windows.Automation.PropertyCondition ([Windows.Automation.AutomationElement]::NameProperty, $title)
    $w = [Windows.Automation.AutomationElement]::RootElement.FindFirst([Windows.Automation.TreeScope]::Children, $c)
    if (-not $w) { "no window titled '$title'"; exit 1 }
    $h = [IntPtr]$w.Current.NativeWindowHandle
}
$r = New-Object Cap+RECT; [void][Cap]::GetWindowRect($h, [ref]$r)
$bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp); $hdc = $g.GetHdc()
[void][Cap]::PrintWindow($h, $hdc, 2); $g.ReleaseHdc($hdc)
$bmp.Save($out); "$($bmp.Width)x$($bmp.Height) '$($p.MainWindowTitle)'"
