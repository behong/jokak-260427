$ErrorActionPreference = "SilentlyContinue"

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$Item = Get-ItemProperty -Path $RunKey

Write-Output "Startup entries:"
Write-Output "TelegramMonitor=$($Item.TelegramMonitor)"
Write-Output "TelegramDashboard=$($Item.TelegramDashboard)"

Write-Output ""
Write-Output "Python processes:"
Get-Process python -ErrorAction SilentlyContinue |
    Select-Object Id, ProcessName, StartTime, Path

Write-Output ""
Write-Output "PowerShell runner processes:"
Get-Process powershell -ErrorAction SilentlyContinue |
    Select-Object Id, ProcessName, StartTime, Path
