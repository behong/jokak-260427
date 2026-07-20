$ErrorActionPreference = "SilentlyContinue"

& schtasks.exe /End /TN "TelegramMonitor" | Out-Null
& schtasks.exe /End /TN "TelegramDashboard" | Out-Null

$PythonProcesses = Get-Process python -ErrorAction SilentlyContinue
if ($PythonProcesses.Count -gt 0) {
    $PythonProcesses | Stop-Process -Force
    Write-Output "Stopped scheduled tasks and running python processes."
} else {
    Write-Output "Stopped scheduled tasks: TelegramMonitor, TelegramDashboard"
}
