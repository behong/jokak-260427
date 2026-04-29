$ErrorActionPreference = "SilentlyContinue"

& schtasks.exe /End /TN "TelegramMonitor" | Out-Null
& schtasks.exe /End /TN "TelegramDashboard" | Out-Null
& schtasks.exe /Delete /TN "TelegramMonitor" /F | Out-Null
& schtasks.exe /Delete /TN "TelegramDashboard" /F | Out-Null

Write-Output "Uninstalled scheduled tasks: TelegramMonitor, TelegramDashboard"
