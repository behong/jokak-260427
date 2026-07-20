$ErrorActionPreference = "SilentlyContinue"

& schtasks.exe /Query /TN "TelegramMonitor" /FO LIST
Write-Output ""
& schtasks.exe /Query /TN "TelegramDashboard" /FO LIST

Write-Output ""
Write-Output "Python processes:"
Get-Process python -ErrorAction SilentlyContinue |
    Select-Object Id, ProcessName, StartTime, Path
