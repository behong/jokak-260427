$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root "logs"
$OutLog = Join-Path $LogDir "monitor-runner.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location $Root

while ($true) {
    $startedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $OutLog -Encoding UTF8 -Value "[$startedAt] starting monitor.py"

    & python ".\monitor.py" *>> $OutLog

    $stoppedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $OutLog -Encoding UTF8 -Value "[$stoppedAt] monitor.py stopped. restarting in 5 seconds"
    Start-Sleep -Seconds 5
}
