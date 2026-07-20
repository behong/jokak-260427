$ErrorActionPreference = "SilentlyContinue"

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
Remove-ItemProperty -Path $RunKey -Name "TelegramMonitor"
Remove-ItemProperty -Path $RunKey -Name "TelegramDashboard"

Write-Output "Removed startup entries: TelegramMonitor, TelegramDashboard"
