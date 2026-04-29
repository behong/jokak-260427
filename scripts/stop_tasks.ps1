$ErrorActionPreference = "SilentlyContinue"

& schtasks.exe /End /TN "TelegramMonitor" | Out-Null
& schtasks.exe /End /TN "TelegramDashboard" | Out-Null

Write-Output "Stopped scheduled tasks: TelegramMonitor, TelegramDashboard"
