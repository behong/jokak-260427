$ErrorActionPreference = "Stop"

$MonitorScript = Join-Path $PSScriptRoot "run_monitor.ps1"
$DashboardScript = Join-Path $PSScriptRoot "run_dashboard.ps1"

Start-Process `
    -FilePath powershell.exe `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $MonitorScript) `
    -WindowStyle Hidden

Start-Process `
    -FilePath powershell.exe `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $DashboardScript) `
    -WindowStyle Hidden

Write-Output "Started managed monitor/dashboard runners"
