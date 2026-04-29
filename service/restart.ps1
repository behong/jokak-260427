$ErrorActionPreference = "Continue"

& (Join-Path $PSScriptRoot "TelegramMonitor.exe") restart
& (Join-Path $PSScriptRoot "TelegramDashboard.exe") restart

Write-Output "Restarted services: TelegramMonitor, TelegramDashboard"
