$ErrorActionPreference = "Continue"

& (Join-Path $PSScriptRoot "TelegramMonitor.exe") status
& (Join-Path $PSScriptRoot "TelegramDashboard.exe") status
