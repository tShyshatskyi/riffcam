# Drives RiffEngine's window through Windows UI Automation (JUCE exposes its controls), no mouse or screen capture.
#   ui.ps1 list                      all named controls
#   ui.ps1 click "<name>" [index]    press a button / flip a toggle (index picks among same-named ones, 0-based)
param([string]$cmd, [string]$name, [int]$index = 0)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$p = Get-Process RiffEngine | Select-Object -First 1
$c = New-Object Windows.Automation.AndCondition (
    (New-Object Windows.Automation.PropertyCondition ([Windows.Automation.AutomationElement]::ProcessIdProperty, $p.Id)),
    (New-Object Windows.Automation.PropertyCondition ([Windows.Automation.AutomationElement]::NameProperty, "RiffEngine")))
$root = [Windows.Automation.AutomationElement]::RootElement.FindFirst([Windows.Automation.TreeScope]::Children, $c)  # not the plugin's window
$all = $root.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition)
if ($cmd -eq "list") {
    foreach ($e in $all) { $n = $e.Current.Name; if ($n) { "{0,-14} {1}" -f $e.Current.ControlType.ProgrammaticName.Replace("ControlType.", ""), $n } }
    exit
}
$hits = @($all | Where-Object { $_.Current.Name -eq $name })
if ($hits.Count -le $index) { "not found: '$name' [$index] ($($hits.Count) matches)"; exit 1 }
$e = $hits[$index]
$pat = $null
if ($e.TryGetCurrentPattern([Windows.Automation.TogglePattern]::Pattern, [ref]$pat)) { $pat.Toggle(); "toggled '$name' [$index] -> $($pat.Current.ToggleState)" }
elseif ($e.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$pat)) { $pat.Invoke(); "pressed '$name' [$index]" }
else { "no click/toggle on '$name'"; exit 1 }
