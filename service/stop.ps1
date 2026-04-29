$ErrorActionPreference = "Continue"

& (Join-Path $PSScriptRoot "TelegramMonitor.exe") stop
& (Join-Path $PSScriptRoot "TelegramDashboard.exe") stop

Write-Output "Stopped services: TelegramMonitor, TelegramDashboard"
