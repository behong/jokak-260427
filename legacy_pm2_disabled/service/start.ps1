$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "TelegramMonitor.exe") start
if ($LASTEXITCODE -ne 0) { throw "Failed to start TelegramMonitor. Run this script as Administrator." }
& (Join-Path $PSScriptRoot "TelegramDashboard.exe") start
if ($LASTEXITCODE -ne 0) { throw "Failed to start TelegramDashboard. Run this script as Administrator." }

Write-Output "Started services: TelegramMonitor, TelegramDashboard"
