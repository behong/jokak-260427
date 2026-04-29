$ErrorActionPreference = "Stop"

& schtasks.exe /Run /TN "TelegramMonitor" | Out-Null
& schtasks.exe /Run /TN "TelegramDashboard" | Out-Null

Write-Output "Started scheduled tasks: TelegramMonitor, TelegramDashboard"
