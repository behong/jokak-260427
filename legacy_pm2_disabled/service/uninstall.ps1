$ErrorActionPreference = "Continue"

& (Join-Path $PSScriptRoot "TelegramMonitor.exe") stop
& (Join-Path $PSScriptRoot "TelegramDashboard.exe") stop
& (Join-Path $PSScriptRoot "TelegramMonitor.exe") uninstall
& (Join-Path $PSScriptRoot "TelegramDashboard.exe") uninstall

Write-Output "Uninstalled services: TelegramMonitor, TelegramDashboard"
