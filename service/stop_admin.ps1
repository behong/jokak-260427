$ErrorActionPreference = "Stop"

$Script = Join-Path $PSScriptRoot "stop.ps1"
Start-Process `
    -FilePath powershell.exe `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Script) `
    -Verb RunAs `
    -Wait
