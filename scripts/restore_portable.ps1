$ErrorActionPreference = "Stop"

param(
    [Parameter(Mandatory = $true)]
    [string]$Archive,

    [string]$Destination = (Join-Path $env:USERPROFILE "glbanjang-video")
)

$resolvedArchive = Resolve-Path -LiteralPath $Archive
if (Test-Path -LiteralPath $Destination) {
    $existing = Get-ChildItem -LiteralPath $Destination -Force -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($existing) {
        throw "Destination is not empty: $Destination"
    }
} else {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
}

Expand-Archive -LiteralPath $resolvedArchive -DestinationPath $Destination -Force

@"
Restore complete: $Destination

Next commands:
  cd "$Destination"
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install -r requirements.txt
  .\scripts\start_managed.ps1

Open dashboard:
  http://127.0.0.1:8050
"@
