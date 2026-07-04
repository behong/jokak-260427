$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Check-Path {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,
        [switch] $Required
    )

    if (Test-Path -LiteralPath $Path) {
        Write-Output "OK      $Path"
        return
    }

    if ($Required) {
        Write-Output "MISSING $Path"
        $script:HasError = $true
    } else {
        Write-Output "WARN    $Path"
    }
}

$script:HasError = $false

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Output "MISSING docker command"
    $script:HasError = $true
} else {
    docker --version
}

Check-Path ".env.docker" -Required
Check-Path "telegram_logs.sqlite3" -Required
Check-Path "telegram_monitor.session" -Required
Check-Path "youtube_token.json" -Required
Check-Path "client_secret.json" -Required
Check-Path "outputs" -Required
Check-Path "assets\backgrounds" -Required
Check-Path "assets\bgm" -Required
Check-Path "backups" -Required
Check-Path "logs" -Required
Check-Path "telegram_dashboard_refresh.session"

$dashboardHost = Select-String -LiteralPath ".env.docker" -Pattern "^DASHBOARD_HOST=" -ErrorAction SilentlyContinue
if ($dashboardHost -and $dashboardHost.Line -notmatch "DASHBOARD_HOST=0\.0\.0\.0") {
    Write-Output "WARN    .env.docker DASHBOARD_HOST is not 0.0.0.0; compose overrides it for dashboard"
}

if ($script:HasError) {
    throw "Docker preflight failed"
}

Write-Output "Docker preflight passed"
