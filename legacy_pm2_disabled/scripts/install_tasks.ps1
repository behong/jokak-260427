$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$MonitorScript = Join-Path $PSScriptRoot "run_monitor.ps1"
$DashboardScript = Join-Path $PSScriptRoot "run_dashboard.ps1"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

function Register-LoggerTask {
    param(
        [string] $TaskName,
        [string] $ScriptPath
    )

    $Command = "`"$PowerShell`" -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
    & schtasks.exe /Create /TN $TaskName /SC ONLOGON /TR $Command /F | Out-Null
}

Register-LoggerTask -TaskName "TelegramMonitor" -ScriptPath $MonitorScript
Register-LoggerTask -TaskName "TelegramDashboard" -ScriptPath $DashboardScript

Write-Output "Installed scheduled tasks: TelegramMonitor, TelegramDashboard"
