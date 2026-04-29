$ErrorActionPreference = "Stop"

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$MonitorScript = Join-Path $PSScriptRoot "run_monitor.ps1"
$DashboardScript = Join-Path $PSScriptRoot "run_dashboard.ps1"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

$MonitorCommand = "`"$PowerShell`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$MonitorScript`""
$DashboardCommand = "`"$PowerShell`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$DashboardScript`""

New-Item -Path $RunKey -Force | Out-Null
Set-ItemProperty -Path $RunKey -Name "TelegramMonitor" -Value $MonitorCommand
Set-ItemProperty -Path $RunKey -Name "TelegramDashboard" -Value $DashboardCommand

Write-Output "Installed startup entries: TelegramMonitor, TelegramDashboard"
