$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root "logs"
$OutLog = Join-Path $LogDir "dashboard-runner.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location $Root

while ($true) {
    $startedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $OutLog -Encoding UTF8 -Value "[$startedAt] starting dashboard.py"

    & cmd.exe /c "python `".\dashboard.py`" >> `"$OutLog`" 2>&1"

    $stoppedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $OutLog -Encoding UTF8 -Value "[$stoppedAt] dashboard.py stopped. restarting in 5 seconds"
    Start-Sleep -Seconds 5
}
