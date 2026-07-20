$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "download_winsw.ps1")

& (Join-Path $PSScriptRoot "TelegramMonitor.exe") install
if ($LASTEXITCODE -ne 0) { throw "Failed to install TelegramMonitor. Run this script as Administrator." }
& (Join-Path $PSScriptRoot "TelegramDashboard.exe") install
if ($LASTEXITCODE -ne 0) { throw "Failed to install TelegramDashboard. Run this script as Administrator." }

Write-Output "Installed services: TelegramMonitor, TelegramDashboard"
