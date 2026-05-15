$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $PSScriptRoot
$MonitorScript = Join-Path $PSScriptRoot "run_monitor.ps1"
$DashboardScript = Join-Path $PSScriptRoot "run_dashboard.ps1"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$StartedByTask = $true

& schtasks.exe /Run /TN "TelegramMonitor" | Out-Null
if ($LASTEXITCODE -ne 0) { $StartedByTask = $false }
& schtasks.exe /Run /TN "TelegramDashboard" | Out-Null
if ($LASTEXITCODE -ne 0) { $StartedByTask = $false }

if ($StartedByTask) {
    Write-Output "Started scheduled tasks: TelegramMonitor, TelegramDashboard"
    exit 0
}

Start-Process `
    -FilePath $PowerShell `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $MonitorScript) `
    -WindowStyle Hidden

Start-Process `
    -FilePath $PowerShell `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $DashboardScript) `
    -WindowStyle Hidden

Write-Output "Scheduled tasks unavailable. Started managed monitor/dashboard runners directly."
